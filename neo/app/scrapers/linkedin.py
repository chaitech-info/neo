from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import quote

import structlog

from app.models import ContactInfo, LeadCategory, RawLead
from app.scrapers.base import BaseScraper

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
# İş elanı saytları və işə qəbul agentlikləri — həm müəllif adında, həm email
# domenində yoxlanır. Agentlik (humanpro.az) gizli müştəri üçün elan verir,
# onun domeni işəgötürən deyil.
JOB_BOARDS = ("azvak", "jobsearch", "vakansiya", "hellojob", "boss.az", "azjob", "humanpro")

# Eyni məhsulu satan rəqiblər (canlı testdə tapılıb). Onların reklam postları
# lead deyil. Yeni rəqib görsəniz bura əlavə edin (kiçik hərflə).
KNOWN_COMPETITORS = (
    "plan&eat", "plan & eat", "planandeat", "doydum", "catering 145",
    "lunchbox.az", "tais ikf", "atsfood",
)

EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")

# Vakansiya elanında "Nahar şirkət tərəfindən təmin olunur" — işəgötürən artıq
# hər gün işçilərini yedizdirir, yəni yemək alır. Bu, elanı rədd etmək yerinə
# işəgötürənin özünü lead edir.
MEAL_PROVIDED_RE = re.compile(
    r"(nahar|yemək|qidalanma)[^.\n]{0,40}(şirkət tərəfindən|pulsuz|ödənişsiz|təmin)"
)
FREE_EMAIL_DOMAINS = {
    "gmail.com", "mail.ru", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "yandex.ru", "yandex.com", "inbox.ru", "bk.ru", "list.ru", "rambler.ru",
    "mail.com", "usa.com", "gmx.com", "aol.com", "live.com", "msn.com", "ymail.com",
    "proton.me", "protonmail.com",
}
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
    for m in EMAIL_RE.finditer(content):
        domain = m.group(1).lower().rstrip(".")
        if domain in FREE_EMAIL_DOMAINS or any(j in domain for j in JOB_BOARDS):
            continue
        return domain, f"https://{domain}"
    for rx in EMPLOYER_NAME_RES:
        m = rx.search(content)
        if m:
            name = m.group(1).strip()
            return name, f"https://www.linkedin.com/search/results/companies/?keywords={quote(name)}"
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

            reason = rejection_reason(content, author_name, author_info)
            if reason == "vacancy" and MEAL_PROVIDED_RE.search(_norm(content)):
                employer = employer_identity(content)
                if employer and employer[1] not in seen_employers:
                    seen_employers.add(employer[1])
                    leads.append(self._employer_lead(employer, url, content, email, posted_at))
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
    def _employer_lead(employer, post_url, content, email, posted_at) -> RawLead:
        name, company_url = employer
        return RawLead(
            source="linkedin",
            # Şirkətin saytı — post URL-i yox. Dedup şirkət üzrə olsun deyə:
            # eyni işəgötürənin sonrakı elanları yeni lead yaratmır.
            source_url=company_url,
            title=f"{name} — işçilərinə nahar verir (vakansiya elanından)",
            raw_text=(
                "[SİQNAL: işəgötürən vakansiya elanında işçilərinə nahar/yemək "
                "verdiyini yazır — şirkət artıq hər gün yemək təşkil edir]\n"
                f"Elan: {post_url}\n\n{content}"
            ),
            entity_name=name,
            contact_info=ContactInfo(
                website=company_url,
                email=email.group(0) if email else None,
            ),
            category=LeadCategory.corporate,
            location="Azərbaycan",
            posted_at=posted_at,
        )
