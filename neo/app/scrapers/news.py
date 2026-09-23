from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import TENDER_KEYWORDS, BaseScraper, az_lower

# RSS-lər (ilk versiyada güman edilmişdi) real deyil — brauzerlə yoxlanıb,
# hər sayt statik HTML render edir və konkret CSS seçiciləri var (aşağıda).

KEYWORDS = [
    "tədbir", "konfrans", "açılış", "korporativ", "banket", "forum",
    "sərgi", "restoran", "qonaqpərvərlik", "catering",
]


class BaseNewsHTMLScraper(BaseScraper):
    """1news.az, Oxu.az, Fed.az üçün ortaq HTML-scraping skeleti."""

    listing_url: str = ""
    keywords: list[str] = KEYWORDS
    category: LeadCategory = LeadCategory.event

    def listing_urls(self) -> list[str]:
        """Oxunacaq siyahı səhifələri; çoxsəhifəli bölmələr bunu override edir."""
        return [self.listing_url] if self.listing_url else []

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        """Alt-sinif təyin edir: [(url, title), ...] qaytarır."""
        raise NotImplementedError

    def fetch_leads(self) -> list[RawLead]:
        candidates: list[tuple[str, str]] = []
        for i, listing_url in enumerate(self.listing_urls()):
            if i:
                self.polite_sleep()
            soup = BeautifulSoup(self.get(listing_url).text, "lxml")
            candidates.extend(self.article_links(soup))

        leads: list[RawLead] = []
        seen: set[str] = set()
        for url, title in candidates:
            if url in seen or not title:
                continue
            seen.add(url)

            if not any(kw in az_lower(title) for kw in self.keywords):
                continue

            lead = RawLead(
                source=self.source_name,  # type: ignore[arg-type]
                source_url=url,
                title=title,
                raw_text=title,
                category=self.category,
            )
            leads.append(self.enrich(lead))

        return leads


class News1Scraper(BaseNewsHTMLScraper):
    """1news.az — h3.title a. `.desc h3.title a` yalnız üstdəki ~5 xəbəri tuturdu;
    lentin qalanı başqa bloklardakı h3.title-lardadır (2026-09-23 yoxlanıb)."""

    source_name = "1news"
    base_url = "https://1news.az"
    listing_url = "https://1news.az/az/xronika/"
    article_body_selector = "div.content"

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        results = []
        for a in soup.select("h3.title a[href]"):
            href = a["href"]
            if "/az/news/" not in href:
                continue
            url = href if href.startswith("http") else f"{self.base_url}{href}"
            results.append((url, a.get_text(strip=True)))
        return results


class OxuScraper(BaseNewsHTMLScraper):
    """oxu.az — href /<kateqoriya>/<slug> formatındadır. `a.umami--click--flash`
    yalnız "flash" blokunu (~15 xəbər) tuturdu; lentin qalanında bu class yoxdur.
    Eyni xəbərin şəkil-linki mətnsiz olur — ən uzun mətn başlıq kimi götürülür."""

    source_name = "oxu"
    base_url = "https://oxu.az"
    listing_url = "https://oxu.az"
    article_body_selector = ".post-detail-content-inner"
    _article_re = re.compile(r"^(?:https://oxu\.az)?/(?!tag/)[a-z-]+/[a-z0-9-]{15,}$")

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        best: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not self._article_re.match(href):
                continue
            url = href if href.startswith("http") else f"{self.base_url}{href}"
            text = a.get_text(strip=True)
            if len(text) > len(best.get(url, "")):
                best[url] = text
        return [(url, text) for url, text in best.items() if len(text) >= 15]


class FedScraper(BaseNewsHTMLScraper):
    """fed.az — href (nisbi!) /az/<kateqoriya>/<slug>-<id> formatındadır."""

    source_name = "fed"
    base_url = "https://fed.az"
    listing_url = "https://fed.az"
    article_body_selector = ".news-text"
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


class FedTenderScraper(FedScraper):
    """fed.az/az/tenderler — dövlət tenderləri və müqavilə nəticələri haqqında
    redaksiya yazıları (əsasən etender.gov.az-dan, amma qalibi/məbləği də yazır).
    Məs. "Dövlət qurumu keytring xidməti alır – TENDER ELANI".

    Səhifələmə path-ladır (/az/tenderler/2) — robots.txt yalnız `?`-li URL-ləri
    qadağan edir. source="fed" saxlanılır ki, eyni məqalə ana səhifə
    scraper-indən də gəlsə dedup onu tutsun.
    """

    listing_url = "https://fed.az/az/tenderler"
    max_pages = 2  # 48 yazı/səhifə ≈ 1 həftə; 2 saatlıq cədvəldə 2 səhifə artıqlaması ilə kifayətdir
    category = LeadCategory.tender
    keywords = TENDER_KEYWORDS
    _tender_re = re.compile(r"^/az/tenderler/(?!\d+$)[a-z0-9-]+$")

    def listing_urls(self) -> list[str]:
        return [self.listing_url] + [f"{self.listing_url}/{n}" for n in range(2, self.max_pages + 1)]

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        results = []
        for card in soup.select(".news"):
            a = card.find("a", href=True)
            heading = card.select_one(".heading h5")
            if a is None or heading is None:
                continue
            href = a["href"] if a["href"].startswith("/") else f"/{a['href']}"
            if not self._tender_re.match(href):
                continue
            results.append((f"{self.base_url}{href}", heading.get_text(" ", strip=True)))
        return results
