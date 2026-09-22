from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import BaseScraper

# RSS-lər (ilk versiyada güman edilmişdi) real deyil — brauzerlə yoxlanıb,
# hər sayt statik HTML render edir və konkret CSS seçiciləri var (aşağıda).

KEYWORDS = [
    "tədbir", "konfrans", "açılış", "korporativ", "banket", "forum",
    "sərgi", "restoran", "qonaqpərvərlik", "catering",
]


class BaseNewsHTMLScraper(BaseScraper):
    """1news.az, Oxu.az, Fed.az üçün ortaq HTML-scraping skeleti."""

    listing_url: str = ""

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        """Alt-sinif təyin edir: [(url, title), ...] qaytarır."""
        raise NotImplementedError

    def fetch_leads(self) -> list[RawLead]:
        if not self.listing_url:
            return []

        leads: list[RawLead] = []
        resp = self.get(self.listing_url)
        soup = BeautifulSoup(resp.text, "lxml")

        seen: set[str] = set()
        for url, title in self.article_links(soup):
            if url in seen or not title:
                continue
            seen.add(url)

            if not any(kw in title.lower() for kw in KEYWORDS):
                continue

            leads.append(
                RawLead(
                    source=self.source_name,  # type: ignore[arg-type]
                    source_url=url,
                    title=title,
                    raw_text=title,
                    category=LeadCategory.event,
                )
            )
            self.polite_sleep()

        return leads


class News1Scraper(BaseNewsHTMLScraper):
    """1news.az — .desc h3.title a (bax app/scrapers/news.py başlığı)."""

    source_name = "1news"
    base_url = "https://1news.az"
    listing_url = "https://1news.az/az/xronika/"

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        results = []
        for a in soup.select(".desc h3.title a[href]"):
            href = a["href"]
            url = href if href.startswith("http") else f"{self.base_url}{href}"
            results.append((url, a.get_text(strip=True)))
        return results


class OxuScraper(BaseNewsHTMLScraper):
    """oxu.az — a.umami--click--flash (real xəbər linkləri bu class-dadır)."""

    source_name = "oxu"
    base_url = "https://oxu.az"
    listing_url = "https://oxu.az"

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        results = []
        for a in soup.select("a.umami--click--flash[href]"):
            text = a.get_text(strip=True)
            if len(text) < 15:
                continue
            href = a["href"]
            url = href if href.startswith("http") else f"{self.base_url}{href}"
            results.append((url, text))
        return results


class FedScraper(BaseNewsHTMLScraper):
    """fed.az — href (nisbi!) /az/<kateqoriya>/<slug>-<id> formatındadır."""

    source_name = "fed"
    base_url = "https://fed.az"
    listing_url = "https://fed.az"
    _article_re = re.compile(r"^/az/[a-z-]+/[a-z0-9-]+-\d+$")

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        results = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not self._article_re.match(href):
                continue
            text = a.get_text(strip=True)
            # Bəzi kartlarda "16:47Başlıq..." kimi vaxt prefiksi mətnə yapışır.
            text = re.sub(r"^\d{1,2}:\d{2}", "", text).strip()
            if len(text) < 15:
                continue
            results.append((f"{self.base_url}{href}", text))
        return results
