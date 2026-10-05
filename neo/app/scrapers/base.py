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


def fold(text: str) -> str:
    """Müqayisə üçün kiçik hərf: "İ"-nin görünməz nöqtəsi silinir, "ı" → "i".

    str.lower() "VAKANSİYA"-nı "vakansi̇ya" edir, az_lower() isə "CATERING"-i
    "caterıng" — hər iki tərəfi (mətn və açar söz) fold() etmək hər ikisini tutur.
    """
    return text.lower().replace("\u0307", "").replace("ı", "i")


# Açar sözlər — "HEÇ BİR FÜRSƏT BURAXILMASIN" qaydası (2026-09-27): açar söz
# uyğunluğu mütləq daxil edir (AI işləməsə belə), AI triajı (app/ai_triage.py)
# əlavə olaraq açar söz görmədiklərini tutur. Uyğunluq sözün ƏVVƏLİNDƏ yoxlanır
# (contains_keyword) — kök yazmaq kifayətdir: "qidalanma" → "qidalanması" ✓.
# Etender testi: köhnə 5 sözlük axtarış 486 açıq tenderdən 0 real fürsət tapırdı.

# Yemək/catering — az, ru, en.
MEAL_KEYWORDS = [
    "keytrinq", "keytring", "keyterinq", "katerinq", "katering", "catering",
    "iaşə", "qidalanma", "qida məhsul", "ərzaq", "yemək", "nahar", "səhər yeməy",
    "şam yeməy", "bufet", "banket", "ziyafət", "fürşet", "furşet", "kofe-brek",
    "kofe brek", "qəhvə fasil", "çay süfrə", "süfrə", "qonaqlıq", "qonaqlama",
    "питани", "кейтеринг", "обед", "столов", "банкет", "фуршет", "продовольств",
    "food", "meal", "lunch", "canteen", "cafeteria", "coffee break", "banquet",
]

# Tədbir təşkili — təşkilatçı yemək də alır (etender testində bütün real
# fürsətlər bu növdən idi: bayram, komandaqurma, festival, yubiley).
# "müsabiqə" YOXDUR — tenderin özünün adıdır, hər başlığa uyğun gəlirdi.
EVENT_KEYWORDS = [
    "tədbir", "konfrans", "forum", "festival", "mərasim", "bayram", "yubiley",
    "komandaqurma", "teambuilding", "team building", "qəbul", "sərgi", "seminar",
    "təlim", "yarış", "turnir", "çempionat", "olimpiada", "konqres",
    "sammit", "iftar", "novruz", "mükafatlandırma", "düşərgə",
    "мероприяти", "конференц", "форум", "праздн",
    "event", "conference", "reception", "ceremony", "hospitality", "workshop",
]

# Genişlənmə — çoxlu işçisi olacaq obyekt (xəbərlər üçün). Tək "mədən"
# "mədəniyyət"-i də tutardı — ona görə dar formalar.
EXPANSION_KEYWORDS = [
    "zavod", "fabrik", "istehsal müəssisə", "sənaye park", "sənaye zona",
    "texnopark", "təməli qoyul", "yeni iş yeri", "iş yeri yaradıl", "iş yeri açıl",
    "işə götür", "işçi qəbul", "ofis açıl", "yeni ofis", "filial açıl",
    "logistika mərkəz", "anbar kompleks", "qızıl mədən", "mis mədən", "filiz",
    "yatağında istehsal", "istehsala başla", "istehsalına başla",
    "istehsalın başla", "istifadəyə veril", "tikiləcək", "inşa ediləcək",
]

# Tenderlər (etender, fed.az tenderlər, şirkət saytları, Dünya Bankı, bp EDP).
# Tender başlığında "təşkili/keçirilməsi" demək olar həmişə tədbir/xidmət təşkilidir
# (miqrasiya 0004; 2026-10-05 ölçüsü: AI triajı belə tədbir tenderlərini buraxırdı).
EVENT_ORGANIZATION_TERMS = ["təşkili", "keçirilməsi"]
TENDER_KEYWORDS = MEAL_KEYWORDS + EVENT_KEYWORDS + EVENT_ORGANIZATION_TERMS

# Xəbərlər (marja, 1news, oxu, fed, report). Tədbir sözləri xəbərlərdə dövlət
# tədbiri reportajları ilə dolur (canlı test: 16 uyğunluqdan 15-i) və hər uyğunluq
# məqalə səhifəsinin yüklənməsi deməkdir — ona görə burada yalnız dar tədbir
# ifadələri; gələcək tədbirləri AI triajı tutur.
NEWS_KEYWORDS = MEAL_KEYWORDS + EXPANSION_KEYWORDS + ["korporativ tədbir", "korporativ gecə"]

# Geriyə uyğunluq: köhnə adla import edənlər üçün (Dünya Bankı, bp EDP).
EN_TENDER_KEYWORDS: list[str] = []


def contains_keyword(text: str, keywords: list[str]) -> bool:
    """Açar söz sözün ƏVVƏLİNDƏ olmalıdır: "tədbirlərin" ✓, "prevention"-da "event" ✗.
    Azərbaycan şəkilçiləri sonda gəldiyi üçün prefiks uyğunluğu kifayətdir."""
    text = fold(text)
    return any(re.search(r"(?<!\w)" + re.escape(fold(kw)), text) for kw in keywords)


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


_GENERIC_TITLE_WORDS = {"tender", "elan", "elanı", "bildiriş", "bildirişi", "uzatma", "dəyişiklik",
                        "procurement", "notice", "specific", "general", "announcement", "invitation",
                        "satınalma", "satinalma", "müsabiqə", "kotirovka", "sorğusu", "açıq", "i", "ii"}


def uninformative_title(title: str) -> bool:
    """Tender başlığı nə alındığını demirsə ("T E N D E R — Azərenerji ASC", "Bildiriş-Uzatma",
    "Specific Procurement Notice") — AI də, açar söz də qərar verə bilməz: tam oxunmalıdır.
    Təşkilat adı ("— X ASC") nəzərə alınmır."""
    subject = fold(title.split(" — ")[0])
    subject = re.sub(r"(?<=\b\w) (?=\w\b)", "", subject)       # "t e n d e r" → "tender"
    words = [w for w in re.findall(r"\w+", subject) if not w.isdigit()]
    return bool(words) and all(w in _GENERIC_TITLE_WORDS for w in words)


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
        self.error: str | None = None  # run() xətanı udanda — scrape_runs-a "failed" yazılsın
        # Filtrdən əvvəl saytdan gələn elan sayı (select_relevant sayır). None = scraper
        # select_relevant çağırmır — pipeline qaytarılan sayı götürür.
        self.fetched: int | None = None
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

    def select_relevant(
        self, texts: list[str], fallback: list[str], kind: str, extra: list[str] = ()
    ) -> list[bool]:
        """Hansı başlıqlar tam yoxlamaya getsin: açar söz VƏ YA AI triajı, mənfi söz istisna.

        Açar sözlər bazadandır (paneldə redaktə olunur, app/keywords.py) — `kind`
        ("tender"|"news") həmin növə aid terminləri seçir. Baza əlçatan deyilsə
        `fallback` (koddakı siyahı) işlədilir. `extra` — mənbəyə xas əlavə sözlər
        (məs. emlak-ın hərrac sözləri), həmişə keçərlidir.
        Açar söz uyğunluğu həmişə daxil edir (AI işləməsə də heç nə itmir); AI
        triajı açar sözlərin görmədiyi fürsətləri əlavə edir; mənfi söz hər ikisini bağlayır.
        """
        from app import keywords as kw_store  # gec import: baza modulu scraper-lərdən asılı olmasın
        from app.ai_triage import triage

        self.fetched = (self.fetched or 0) + len(texts)
        db_terms = kw_store.terms_for(kind)
        extra_terms = [kw_store.Term(t) for t in extra]
        if db_terms is None:
            positive, negative = [kw_store.Term(t) for t in fallback] + extra_terms, []
        else:
            positive, negative = db_terms[0] + extra_terms, db_terms[1]

        by_keyword = [kw_store.any_match(t, positive) or (kind == "tender" and uninformative_title(t))
                      for t in texts]
        blocked = [kw_store.any_match(t, negative) for t in texts] if negative else [False] * len(texts)
        picked = triage(texts, kind=kind) if texts else set()
        by_ai = [i in picked for i in range(len(texts))] if picked is not None else [False] * len(texts)
        selected = [(k or a) and not b for k, a, b in zip(by_keyword, by_ai, blocked)]
        log.info(
            "candidates_selected",
            source=self.source_name,
            total=len(texts),
            keyword=sum(by_keyword),
            ai=sum(by_ai),
            ai_only=sum(a and not k for k, a in zip(by_keyword, by_ai)),
            negative=sum(blocked),
            selected=sum(selected),
            ai_available=picked is not None,
            keywords_from="db" if db_terms is not None else "code",
        )
        return selected

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
        except Exception as exc:
            log.exception("scraper_failed", source=self.source_name)
            self.error = f"{type(exc).__name__}: {exc}"[:500]
            return []
        finally:
            self._client.close()
