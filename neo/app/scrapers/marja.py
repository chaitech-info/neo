from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import NEWS_KEYWORDS, BaseScraper

# marja.az əslində iqtisadi/biznes XƏBƏR saytıdır (elan saytı deyil — ilk
# fərziyyə səhv idi, brauzerlə yoxlanıb düzəldilib). Məqalə linkləri:
# https://marja.az/<id>/<slug> formatındadır.
LISTING_URL = "https://marja.az"
ARTICLE_RE = re.compile(r"^https://marja\.az/\d+/[a-z0-9-]+$")

KEYWORDS = NEWS_KEYWORDS


class MarjaScraper(BaseScraper):
    source_name = "marja"
    base_url = "https://marja.az"
    article_body_selector = ".content-news"

    def fetch_leads(self) -> list[RawLead]:
        leads: list[RawLead] = []
        resp = self.get(LISTING_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        seen_urls: set[str] = set()
        best_text: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not ARTICLE_RE.match(href):
                continue
            text = a.get_text(strip=True)
            if text and len(text) > len(best_text.get(href, "")):
                best_text[href] = text
            seen_urls.add(href)

        candidates = [(url, best_text[url]) for url in sorted(seen_urls) if best_text.get(url)]
        keep = self.select_relevant([title for _, title in candidates], KEYWORDS, kind="news")
        for (url, title), selected in zip(candidates, keep):
            if not selected:
                continue

            lead = RawLead(
                source="marja",
                source_url=url,
                title=title,
                raw_text=title,
                category=LeadCategory.corporate,
            )
            leads.append(self.enrich(lead))

        return leads
