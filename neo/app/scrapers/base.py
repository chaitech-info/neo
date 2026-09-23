from __future__ import annotations

import abc
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.models import RawLead
from app.scrapers.robots import check_allowed

log = structlog.get_logger()

DEFAULT_USER_AGENT = "NeoLeadBot/1.0 (+contact: dev@company.az)"

_AZ_UPPER_I = str.maketrans({"I": "ı", "İ": "i"})


def az_lower(text: str) -> str:
    """Azərbaycan dilinə uyğun kiçik hərf. Adi str.lower() "İ"-ni "i̇"-yə
    (i + görünməz nöqtə), "I"-nı "i"-yə çevirir — nəticədə "TƏDBİR",
    "AÇILIŞ" kimi böyük hərfli başlıqlar açar sözlərlə uyğunlaşmırdı."""
    return text.translate(_AZ_UPPER_I).lower()


# Tender başlıqları üçün catering/tədbir açar sözləri (fed.az tenderlər, şirkət saytları).
TENDER_KEYWORDS = [
    "keytrinq", "keytring", "catering", "iaşə", "yemək", "qida", "ərzaq",
    "bufet", "banket", "tədbir", "qonaqlıq", "restoran", "konfrans",
    "mərasim", "festival", "sərgi", "forum",
]

# İngiliscə elanlar (Dünya Bankı, bp EDP) üçün əlavə sözlər.
EN_TENDER_KEYWORDS = [
    "catering", "food", "meal", "lunch", "canteen", "banquet", "hospitality",
    "reception", "conference", "event", "coffee break",
]


def contains_keyword(text: str, keywords: list[str]) -> bool:
    """Açar söz sözün ƏVVƏLİNDƏ olmalıdır: "tədbirlərin" ✓, "prevention"-da "event" ✗.
    Azərbaycan şəkilçiləri sonda gəldiyi üçün prefiks uyğunluğu kifayətdir."""
    text = az_lower(text)
    return any(re.search(r"(?<!\w)" + re.escape(kw), text) for kw in keywords)


# Azərbaycan 2016-dan bəri DST istifadə etmir — saat qurşağı göstərilməyən
# tarixlər (məs. emlak.gov.az-da `<time datetime="2026-09-23 16:27:30">`) +04 sayılır.
BAKU_TZ = timezone(timedelta(hours=4))

ARTICLE_MAX_CHARS = 6000


def parse_datetime(value: str | None) -> datetime | None:
    """ISO-yə yaxın tarixi naive UTC-yə çevirir (`scraped_at` ilə eyni formatda).

    "+0400" kimi iki nöqtəsiz offset-i də qəbul edir (marja.az JSON-LD) —
    Python 3.9-un fromisoformat-ı onu tanımır.
    """
    if not value:
        return None
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.strip()).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BAKU_TZ)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _published_at(soup: BeautifulSoup) -> datetime | None:
    for attr, key in (
        ("property", "article:published_time"),
        ("itemprop", "datePublished"),
        ("name", "datePublished"),
    ):
        meta = soup.find("meta", attrs={attr: key})
        if meta and (dt := parse_datetime(meta.get("content"))):
            return dt

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data])
        for node in nodes:
            if isinstance(node, dict) and (dt := parse_datetime(node.get("datePublished"))):
                return dt

    time_el = soup.find("time", attrs={"datetime": True})
    return parse_datetime(time_el["datetime"]) if time_el else None


def _article_text(soup: BeautifulSoup, body_selector: str | None) -> str:
    container = soup.select_one(body_selector) if body_selector else None
    if container is None:
        # Seçici tapılmadı (sayt dəyişib və ya təyin olunmayıb) — birbaşa <p>
        # uşaqlarında ən çox mətni olan blok məqalə gövdəsi sayılır.
        container = max(
            soup.find_all(["article", "div", "section"]),
            key=lambda el: sum(len(p.get_text(strip=True)) for p in el.find_all("p", recursive=False)),
            default=None,
        )
    paragraphs = container.find_all("p") if container is not None else []
    text = "\n".join(t for p in paragraphs if (t := p.get_text(" ", strip=True)))
    if not text:
        meta = soup.find("meta", attrs={"property": "og:description"})
        text = meta.get("content", "").strip() if meta else ""
    return text[:ARTICLE_MAX_CHARS]


class BaseScraper(abc.ABC):
    """Hər mənbə üçün ortaq skelet. Bax: sənəd §3 Architecture, §7 Scheduling.

    Alt-siniflər `source_name` və `fetch_leads()` təyin edir. Bir scraper
    xəta versə də scheduler digərlərini dayandırmır (§3: "biri sınarsa
    digərləri davam edir").
    """

    source_name: str = "unknown"
    base_url: str = ""
    min_delay_s: float = 2.0
    max_delay_s: float = 5.0
    # robots.txt ictimai saytların crawl edilməsi üçündür. Müqavilə/token ilə
    # işlədilən API-lərdə (məs. Apify) ToS tətbiq olunur — orada False qoyulur;
    # həm də URL-dəki token robots logu vasitəsilə sızmasın.
    respect_robots: bool = True
    # Məqalə səhifəsində mətn konteyneri; None olarsa ümumi evristika işləyir.
    article_body_selector: str | None = None

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=20.0,
            follow_redirects=True,
        )

    def polite_sleep(self) -> None:
        """Sorğular arası jitter. Sənəd §7: "2-5 saniyə random jitter"."""
        time.sleep(random.uniform(self.min_delay_s, self.max_delay_s))

    def get(self, url: str, **kwargs) -> httpx.Response:
        """robots.txt icazə verirsə GET (sənəd §7). Qadağandırsa RobotsDisallowed —
        təkrar cəhd edilmir, run()/fetch_article onu digər xətalar kimi udur."""
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        """GET kimi: robots.txt yoxlaması + təkrar cəhd (məs. bp EDP siyahısı POST-ladır)."""
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        if self.respect_robots:
            check_allowed(str(httpx.URL(url, params=kwargs.get("params"))), self._client)
        return self._request_with_retry(method, url, **kwargs)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        # client.get/post (request() yox) — test dublikatları yalnız bunları təqlid edir.
        resp = getattr(self._client, method.lower())(url, **kwargs)
        resp.raise_for_status()
        return resp

    def fetch_article(self, url: str) -> tuple[str, datetime | None]:
        """Məqalə səhifəsindən (tam mətn, dərc vaxtı). Sənəd §6: AI başlıq yox,
        mətn üzərində qiymətləndirsin. Xəta olsa ("", None) — lead itmir,
        çağıran tərəf başlıqla davam edir."""
        self.polite_sleep()
        try:
            soup = BeautifulSoup(self.get(url).text, "lxml")
        except Exception:
            log.warning("article_fetch_failed", source=self.source_name, url=url)
            return "", None
        return _article_text(soup, self.article_body_selector), _published_at(soup)

    def enrich(self, lead: RawLead) -> RawLead:
        """Lead-in raw_text-ini məqalə mətni ilə, posted_at-ı dərc vaxtı ilə doldurur."""
        body, posted_at = self.fetch_article(lead.source_url)
        if body:
            lead.raw_text = f"{lead.title}\n\n{body}"
        lead.posted_at = lead.posted_at or posted_at
        return lead

    @abc.abstractmethod
    def fetch_leads(self) -> list[RawLead]:
        """Bu mənbədən tapılan bütün RawLead-ləri qaytarır."""
        raise NotImplementedError

    def run(self) -> list[RawLead]:
        """Scheduler-in çağırdığı giriş nöqtəsi — xətanı udur, loglayır."""
        try:
            leads = self.fetch_leads()
            log.info("scraper_completed", source=self.source_name, count=len(leads))
            return leads
        except Exception:
            log.exception("scraper_failed", source=self.source_name)
            return []
        finally:
            self._client.close()
