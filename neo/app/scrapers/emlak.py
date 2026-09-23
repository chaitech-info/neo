from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import BaseScraper, az_lower

# Brauzerlə yoxlanıb: emlak.gov.az əslində "Əmlak Məsələləri Dövlət Xidməti"
# saytıdır — ilk fərziyyə (yeni obyekt qeydiyyatı reyestri) mövcud deyil.
# Real, faydalı siqnal: sayt "Xəbərlər" və "Hərraclar" bölmələrini eyni
# /az/news/view/<id>-<slug> URL-i altında dərc edir; torpaq müsabiqəsi,
# hərrac, kommersiya/obyekt satışı ilə bağlı elanlar catering üçün potensial
# yer/obyekt siqnalı ola bilər (sənəd §5: "aşağı prioritetli siqnal").
# Linklərin çoxu nisbidir ("/az/...", hətta "az/..."), bəzi slug-lar Unicode-dur —
# ona görə əvvəl mütləq URL-ə çeviririk, slug-ı yoxlamırıq.
LISTING_URL = "https://emlak.gov.az/"
ARTICLE_RE = re.compile(r"^https://emlak\.gov\.az/az/news/view/\d+-")

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

        best_text: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            url = urljoin(LISTING_URL, a["href"])
            if not ARTICLE_RE.match(url):
                continue
            text = a.get_text(strip=True)
            if len(text) >= len(best_text.get(url, "")):
                best_text[url] = text

        for url, title in best_text.items():
            if not title or not any(kw in az_lower(title) for kw in KEYWORDS):
                continue

            lead = RawLead(
                source="emlak",
                source_url=url,
                title=title,
                raw_text=title,
                category=LeadCategory.real_estate,
            )
            leads.append(self.enrich(lead))

        return leads
