from __future__ import annotations

import os
from dataclasses import dataclass

import structlog

from app.models import ContactInfo, LeadCategory, RawLead
from app.scrapers.base import NEWS_KEYWORDS, BaseScraper, fold
from app.scrapers.linkedin import _parse_posted_at
from app.scrapers.media import AZ_GATE

log = structlog.get_logger()

# Faza 7 / variant A (docs/knowledge-base/07-social-and-maps-options.md): izləmə siyahısındakı
# şirkət səhifələrinin son postları. Apify actor `harvestapi/linkedin-company-posts` (lisenziyalı
# üçüncü tərəf, "from $1.50 / 1000 posts"; çıxış sahələri post-search actor-u ilə eynidir).
# APIFY_TOKEN yoxdursa scraper boş siyahı qaytarır (linkedin.py ilə eyni qayda).
APIFY_ACTOR_ID = "harvestapi~linkedin-company-posts"
APIFY_RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"


@dataclass(frozen=True)
class Company:
    name: str
    url: str            # linkedin.com/company/<slug>
    az_only: bool = False  # qlobal şirkət: yalnız Azərbaycanla bağlı postlar (media.AZ_GATE)


# Slug-lar 2026-10-07-də ictimai axtarış nəticələri ilə təsdiqlənib (LinkedIn-in özünə sorğu göndərilmədi);
# actor-un özü tokensiz işə salınmayıb. Yanlış/dəyişmiş slug 0 post qaytarır — scraper bunu
# `linkedin_companies_empty` kimi loglayır. Siyahı kodda saxlanılır ki, dəyişiklik PR ilə izlənsin.
WATCHLIST: list[Company] = [
    Company("SOCAR", "https://www.linkedin.com/company/socarofficial"),
    Company("bp", "https://www.linkedin.com/company/bp", az_only=True),
    Company("ACWA Power", "https://www.linkedin.com/company/acwa-power-international", az_only=True),
    Company("TotalEnergies", "https://www.linkedin.com/company/totalenergies", az_only=True),
    Company("Masdar", "https://www.linkedin.com/company/masdar-abu-dhabi-future-energy-company", az_only=True),
    Company("AZPROMO", "https://www.linkedin.com/company/azpromopr"),
    Company("AFEZ", "https://www.linkedin.com/company/afeza"),
]


class LinkedInCompaniesScraper(BaseScraper):
    """İzləmə siyahısındakı şirkətlərin son LinkedIn postları (yeni zavod, layihə, işçi qəbulu, tədbir)."""

    source_name = "linkedin_companies"
    respect_robots = False  # lisenziyalı Apify API (token ilə) — robots.txt yox, Apify ToS tətbiq olunur
    base_url = "https://www.linkedin.com"
    watchlist: list[Company] = WATCHLIST
    keywords: list[str] = NEWS_KEYWORDS

    def __init__(self, client=None, api_token: str | None = None, max_posts: int = 10, posted_limit: str = "week") -> None:
        super().__init__(client=client)
        self.api_token = api_token or os.environ.get("APIFY_TOKEN")
        self.max_posts = max_posts
        self.posted_limit = posted_limit

    def fetch_leads(self) -> list[RawLead]:
        if not self.api_token:
            log.warning("linkedin_companies_skipped", reason="APIFY_TOKEN .env-də yoxdur")
            return []

        resp = self.post(
            APIFY_RUN_SYNC_URL,
            params={"token": self.api_token},
            json={
                "targetUrls": [c.url for c in self.watchlist],
                "maxPosts": self.max_posts,
                "postedLimit": self.posted_limit,
            },
            timeout=180.0,
        )
        by_url = {c.url.rstrip("/").lower(): c for c in self.watchlist}
        posts: list[tuple[Company | None, dict]] = []
        seen_companies: set[str] = set()
        for item in resp.json():
            if item.get("type", "post") != "post" or not item.get("linkedinUrl") or not (item.get("content") or "").strip():
                continue
            author = item.get("author") or {}
            company = by_url.get(str(author.get("linkedinUrl") or "").split("?")[0].rstrip("/").lower())
            if company:
                seen_companies.add(company.url)
            posts.append((company, item))

        empty = [c.name for c in self.watchlist if c.url not in seen_companies]
        if empty:
            log.warning("linkedin_companies_empty", companies=empty)

        candidates = []
        for company, item in posts:
            content = item["content"].strip()
            if company and company.az_only and not any(fold(g) in fold(content) for g in AZ_GATE):
                continue
            candidates.append((company, item, content))

        # Qlobal şirkətin Azərbaycan süzgəcindən keçən postu artıq dardır (ingiliscə ola bilər, Azərbaycan dilli açar
        # sözlər uyğun gəlmir) — birbaşa keçir, uyğunluğu AI skoru qiymətləndirir. Digərləri açar söz/AI triajı ilə.
        regular = [c for c in candidates if not (c[0] and c[0].az_only)]
        picked = iter(self.select_relevant([c[2][:300] for c in regular], self.keywords, kind="news") if regular else [])
        keep = [True if (c[0] and c[0].az_only) else next(picked) for c in candidates]
        leads: list[RawLead] = []
        for (company, item, content), selected in zip(candidates, keep):
            if not selected:
                continue
            author = item.get("author") or {}
            name = company.name if company else (author.get("name") or None)
            leads.append(RawLead(
                source="linkedin_companies",
                source_url=item["linkedinUrl"],
                title=content.splitlines()[0][:200],
                raw_text=f"{content}\n\n— Şirkət səhifəsi: {name} (LinkedIn)",
                entity_name=name,
                contact_info=ContactInfo(website=author.get("linkedinUrl")),
                origin_url=item["linkedinUrl"],
                category=LeadCategory.corporate,
                location="Azərbaycan",
                posted_at=_parse_posted_at((item.get("postedAt") or {}).get("date")),
            ))
        log.info("linkedin_companies_done", posts=len(posts), candidates=len(candidates), leads=len(leads))
        return leads
