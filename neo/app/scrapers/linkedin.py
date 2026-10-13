from __future__ import annotations

import os
import re
from datetime import datetime

import structlog

from app.models import Author, ContactInfo, LeadCategory, RawLead
from app.scrapers.base import BaseScraper
from app.scrapers.employer_signal import (
    EMAIL_RE,
    JOB_BOARDS,
    KNOWN_COMPETITORS,
    SIGNAL_PREFIX,
    corporate_domain,
    employer_url,
    has_own_kitchen,
    mentions_company_meal,
)

log = structlog.get_logger()

# Apify actor: harvestapi/linkedin-post-search — LinkedIn hesabı/cookie lazım
# deyil, keyword üzrə post axtarır. Bax 04-decisions.md → "LinkedIn scraping
# yanaşması" (Decided: lisenziyalı 3-cü tərəf provider, birbaşa scraping YOX).
APIFY_ACTOR_ID = "harvestapi~linkedin-post-search"
APIFY_RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"

# Bax 04-decisions.md → "LinkedIn axtarış sözləri". LinkedIn uzun ifadələrə
# (4+ söz) 0 nəticə qaytarır — yalnız qısa sorğular işləyir. Ona görə sorğular
# geniş tutulur (recall), dəqiqlik isə aşağıdakı filtrlər + AI Filter-dədir.
SEARCH_QUERIES = [
    "catering axtarırıq",
    "catering Bakı",
    "korporativ qidalanma",
    "korporativ nahar",
    "iaşə xidməti",
    # İşçilərinə nahar verən işəgötürənləri tapır (vakansiya elanlarından) —
    # bax 04-decisions.md → "İşçilərinə nahar verən şirkətlər".
    "nahar şirkət tərəfindən",
    "yemək şirkət tərəfindən",
    # boss.az testində (2026-09-27) köhnə naxışın buraxdığı ən çox ifadələr.
    "pulsuz nahar",
    "nahar verilir",
]

# Post Azərbaycanla bağlı deyilsə atılır (test: türk, hind, indoneziya postları
# gəlirdi). "ə" hərfi yalnız Azərbaycan dilində var. "bakı" tam söz kimi
# yoxlanır — türkcə "bakış açısı" içində də "bakı" var.
AZ_MARKER_RE = re.compile(
    r"ə|\bbak[iı]\b|\bbaku\b|\bбаку\b|azerbaijan|азербайджан|\bsumgait\b|\bganja\b"
)

# İş elanı işarələri. "tələb olunur" bilərəkdən YOXDUR — müştəri də "catering
# xidməti tələb olunur" yaza bilər. "#vakansiya" hashtag-ı da yox — real lead
# (Azeri Steel) bu hashtag-la paylaşılmışdı; yalnız "vakansiya:" başlığı.
VACANCY_MARKERS = (
    "vakansiya:", "iş elan", "yeni elanlar", "son elanlar",
    "əməkhaqqı", "əmək haqqı", "iş qrafiki", "iş yeri:",
    "namizəd", "müraciət qaydası", "ixtisas tələbləri",
    "cv göndər", "cv-nizi", "cv-niz", "hr@",
    "вакансия", "резюме", "зарплата",
    "salary:", "apply via", "job vacancy", "send your cv", "we are hiring", "#hiring",
)
_LEGAL_FORM = r"(?:MMC|ASC|QSC|LLC|Ltd|LTD)"
EMPLOYER_NAME_RES = (
    re.compile(r"[\"“«„]([^\"”»“\n]{2,60})[\"”»“]\s*" + _LEGAL_FORM),
    re.compile(r"\b([A-ZƏÖÜĞİŞÇ][\w&.\- ]{1,40}?)\s+" + _LEGAL_FORM + r"\b"),
)


def _parse_posted_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _norm(s: str) -> str:
    # "İ".lower() == "i̇" (i + birləşdirici nöqtə) — onsuz "VAKANSİYA:" heç vaxt
    # "vakansiya:" ilə uyğun gəlmir.
    return s.lower().replace("̇", "")


def rejection_reason(content: str, author_name: str, author_info: str) -> str | None:
    """Post lead deyilsə səbəbi, lead ola bilərsə None."""
    text = _norm(content)
    author = _norm(f"{author_name} {author_info}")

    if not AZ_MARKER_RE.search(text):
        return "not_azerbaijan"
    # Rəqib yoxlaması vakansiyadan ƏVVƏL — rəqibin öz iş elanı (məs. ATS Food
    # aşpaz axtarır) "işçilərinə nahar verən şirkət" kimi lead olmamalıdır.
    if any(c in text or c in author for c in KNOWN_COMPETITORS):
        return "competitor"
    if any(m in text for m in VACANCY_MARKERS) or any(j in author for j in JOB_BOARDS):
        return "vacancy"
    return None


def employer_identity(content: str) -> tuple[str, str] | None:
    """Vakansiya elanından işəgötürəni tap: (göstəriləcək ad, sabit URL).

    URL dedup açarıdır — eyni şirkətin 5 elanı panelə 1 lead kimi düşür.
    Korporativ email domeni ən etibarlıdır (canlı testdə 32 elandan 27-si);
    iş elanı saytlarının öz domenləri (hr@vakansiya.biz və s.) sayılmır.
    """
    domain = corporate_domain(content)
    if domain:
        return domain, employer_url(domain, domain)
    for rx in EMPLOYER_NAME_RES:
        m = rx.search(content)
        if m:
            name = m.group(1).strip()
            return name, employer_url(None, name)
    return None


class LinkedInScraper(BaseScraper):
    """Apify (`harvestapi/linkedin-post-search`) vasitəsilə LinkedIn post axtarışı.

    Hədəf: Azərbaycanda əməkdaşları üçün gündəlik yemək (paket/porsiya) təchizatçısı
    axtaran şirkətlər. `APIFY_TOKEN` .env-də olmalıdır; yoxdursa scraper boş
    siyahı qaytarır və loglayır.
    """

    source_name = "linkedin"
    respect_robots = False  # lisenziyalı Apify API (token ilə) — robots.txt yox, Apify ToS tətbiq olunur
    base_url = "https://www.linkedin.com"
    min_delay_s = 1.0
    max_delay_s = 2.0

    def __init__(
        self,
        client=None,
        api_token: str | None = None,
        max_posts: int = 30,
        posted_limit: str = "week",
    ) -> None:
        super().__init__(client=client)
        self.api_token = api_token or os.environ.get("APIFY_TOKEN")
        self.max_posts = max_posts
        self.posted_limit = posted_limit

    def fetch_leads(self) -> list[RawLead]:
        if not self.api_token:
            log.warning("linkedin_scraper_skipped", reason="APIFY_TOKEN .env-də yoxdur")
            return []

        payload = {
            "searchQueries": SEARCH_QUERIES,
            "postedLimit": self.posted_limit,
            "sortBy": "date",
            "maxPosts": self.max_posts,
        }
        resp = self.post(
            APIFY_RUN_SYNC_URL,
            params={"token": self.api_token},
            json=payload,
            timeout=120.0,
        )
        items = resp.json()

        leads: list[RawLead] = []
        seen_employers: set[str] = set()
        rejected: dict[str, int] = {}
        employer_leads = 0
        for item in items:
            if item.get("type") != "post":
                continue

            url = item.get("linkedinUrl")
            content = (item.get("content") or "").strip()
            if not url or not content:
                continue

            author = item.get("author") or {}
            author_name = author.get("name") or ""
            author_info = author.get("info") or ""
            posted_at = _parse_posted_at((item.get("postedAt") or {}).get("date"))
            email = EMAIL_RE.search(content)
            person = Author(name=author_name or None, title=author_info or None, url=author.get("linkedinUrl"))

            reason = rejection_reason(content, author_name, author_info)
            if reason == "vacancy" and mentions_company_meal(content):
                employer = employer_identity(content)
                # Restoran/otel/kafe öz mətbəxində yedizdirir — alıcı deyil (boss.az ilə eyni qayda).
                if employer and has_own_kitchen(employer[0]):
                    rejected["own_kitchen"] = rejected.get("own_kitchen", 0) + 1
                    continue
                if employer and employer[1] not in seen_employers:
                    seen_employers.add(employer[1])
                    leads.append(self._employer_lead(employer, url, content, email, posted_at, person))
                    employer_leads += 1
                    continue
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue

            leads.append(
                RawLead(
                    source="linkedin",
                    source_url=url,
                    title=content.splitlines()[0][:200],
                    # Müəllifin vəzifəsi AI-yə (rəqib/alıcı ayrımı) və satış
                    # komandasına (kimlə danışmalı) lazımdır.
                    raw_text=f"{content}\n\n— Müəllif: {author_name} ({author_info})",
                    entity_name=author_name or None,
                    contact_info=ContactInfo(
                        website=author.get("linkedinUrl"),
                        email=email.group(0) if email else None,
                    ),
                    origin_url=url,
                    author=person,
                    category=LeadCategory.corporate,
                    location="Azərbaycan",
                    posted_at=posted_at,
                )
            )

        log.info(
            "linkedin_filtered",
            kept=len(leads),
            employer_leads=employer_leads,
            rejected=rejected,
        )
        return leads

    @staticmethod
    def _employer_lead(employer, post_url, content, email, posted_at, author: Author | None = None) -> RawLead:
        name, company_url = employer
        return RawLead(
            source="linkedin",
            # Şirkətin saytı — post URL-i yox. Dedup şirkət üzrə olsun deyə:
            # eyni işəgötürənin sonrakı elanları yeni lead yaratmır.
            source_url=company_url,
            title=f"{name} — işçilərinə nahar verir (vakansiya elanından)",
            raw_text=f"{SIGNAL_PREFIX}\nElan: {post_url}\n\n{content}",
            entity_name=name,
            contact_info=ContactInfo(
                website=company_url,
                email=email.group(0) if email else None,
            ),
            # Post linki ayrıca (paneldə "LinkedIn-də aç"); elanı yazan çox vaxt HR-dır — kontakt.
            origin_url=post_url,
            author=author,
            category=LeadCategory.corporate,
            location="Azərbaycan",
            posted_at=posted_at,
        )
