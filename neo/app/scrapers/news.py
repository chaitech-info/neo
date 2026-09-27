from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import NEWS_KEYWORDS, TENDER_KEYWORDS, BaseScraper

# RSS-lər (ilk versiyada güman edilmişdi) real deyil — brauzerlə yoxlanıb,
# hər sayt statik HTML render edir və konkret CSS seçiciləri var (aşağıda).

KEYWORDS = NEWS_KEYWORDS

_AZ_DATE = r"\d{1,2} (?:Yan|Fev|Mar|Apr|May|İyn|İyl|Avq|Sen|Okt|Noy|Dek) \d{4}"
# fed.az kartları: "Sənaye26 Sen 2026Başlıq" və "Başlıq13:0126 Sen 2026".
_FED_CATEGORY_DATE_PREFIX = re.compile(rf"^[^\W\d]*{_AZ_DATE}")
_FED_TIME_DATE_SUFFIX = re.compile(rf"\d{{1,2}}:\d{{2}}{_AZ_DATE}$")


class BaseNewsHTMLScraper(BaseScraper):
    """1news.az, Oxu.az, Fed.az üçün ortaq HTML-scraping skeleti."""

    listing_url: str = ""
    keywords: list[str] = KEYWORDS
    triage_kind: str = "news"
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

        unique: dict[str, str] = {}
        for url, title in candidates:
            if title and url not in unique:
                unique[url] = title
        keep = self.select_relevant(list(unique.values()), self.keywords, kind=self.triage_kind)

        leads: list[RawLead] = []
        for (url, title), selected in zip(unique.items(), keep):
            if not selected:
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
        # Eyni məqalə səhifədə bir neçə kartda çıxır, mətnə kateqoriya/tarix yapışır:
        # "Sənaye26 Sen 2026Başlıq", "Başlıq19:3626 Sen 2026". Təmiz başlıq həmişə
        # ən qısa variantdır — URL başına onu saxlayırıq.
        best: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not self._article_re.match(href):
                continue
            text = a.get_text(strip=True)
            text = re.sub(r"^\d{1,2}:\d{2}", "", text)
            text = _FED_CATEGORY_DATE_PREFIX.sub("", text)
            text = _FED_TIME_DATE_SUFFIX.sub("", text).strip()
            if len(text) < 15:
                continue
            url = f"{self.base_url}{href}"
            if url not in best or len(text) < len(best[url]):
                best[url] = text
        return list(best.items())


class ReportSenayeScraper(BaseNewsHTMLScraper):
    """report.az/senaye — ən böyük xəbər agentliyinin Sənaye bölməsi (2026-09-27
    yoxlanıb; robots.txt yalnız /admin/ və /search/-i qadağan edir). Zavod, sənaye
    parkı, genişlənmə xəbərləri — işçilərinə yemək lazım olacaq şirkətlər.
    Kart: a.news__item > h2.news__title (kateqoriya/tarix ayrı elementlərdədir)."""

    source_name = "report"
    base_url = "https://report.az"
    listing_url = "https://report.az/senaye/"
    category = LeadCategory.corporate

    def article_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        results = []
        for a in soup.select("a.news__item[href^='/senaye/']"):
            title = a.select_one(".news__title")
            if title:
                results.append((f"{self.base_url}{a['href']}", title.get_text(" ", strip=True)))
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
    triage_kind = "tender"
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
