from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import BaseScraper

# Brauzerlə yoxlanıb: emlak.gov.az əslində "Əmlak Məsələləri Dövlət Xidməti"
# saytıdır — ilk fərziyyə (yeni obyekt qeydiyyatı reyestri) mövcud deyil.
# Real, faydalı siqnal: sayt "Xəbərlər" və "Hərraclar" bölmələrini eyni
# /az/news/view/<id>-<slug> URL-i altında dərc edir; torpaq müsabiqəsi,
# hərrac, kommersiya/obyekt satışı ilə bağlı elanlar catering üçün potensial
# yer/obyekt siqnalı ola bilər (sənəd §5: "aşağı prioritetli siqnal").
LISTING_URL = "https://emlak.gov.az"
ARTICLE_RE = re.compile(r"^https://emlak\.gov\.az/az/news/view/\d+-[a-z0-9-]+$")

KEYWORDS = [
    "hərrac", "müsabiqə", "obyekt", "restoran", "kommersiya",
    "ticarət sahəsi", "ictimai iaşə", "icarə",
]


class EmlakScraper(BaseScraper):
    source_name = "emlak"
    base_url = "https://emlak.gov.az"
    min_delay_s = 3.0
    max_delay_s = 6.0

    def fetch_leads(self) -> list[RawLead]:
        leads: list[RawLead] = []
        resp = self.get(LISTING_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not ARTICLE_RE.match(href) or href in seen:
                continue
            seen.add(href)

            title = a.get_text(strip=True)
            if not title or not any(kw in title.lower() for kw in KEYWORDS):
                continue

            leads.append(
                RawLead(
                    source="emlak",
                    source_url=href,
                    title=title,
                    raw_text=title,
                    category=LeadCategory.real_estate,
                )
            )
            self.polite_sleep()

        return leads
