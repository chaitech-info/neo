"""RSS-i olmayan saytlar: siyahı səhifəsi + məqalə URL nümunəsi (Phase 2, 2026-10-05).

Hər sayt üçün CSS selector yazmaq əvəzinə: siyahıdakı bütün linklərdən URL-i
`link_pattern`-ə uyğun gələnlər götürülür, başlıq = həmin URL-in ən uzun link
mətni (tarix prefiksi təmizlənir). Sayt dizaynı dəyişsə də URL nümunəsi adətən
qalır. Seçilənlərin detal səhifəsi oxunur (fetch_article).

`kind="tender"` — satınalma siyahıları (sifarişçi `org`), `kind="news"` — dövlət
qurumlarının xəbərləri (yeni zavod, sənaye parkı, investor, tədbir).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import NEWS_KEYWORDS, TENDER_KEYWORDS, BaseScraper

log = structlog.get_logger()

MAX_ITEMS = 40

# "04 AVQUST / 2026 / 21:00 …", "30-09-2026 | 18:22 …", "29 September, 2026 …", "Satınalma və hərraclar …"
_DATE_PREFIX = re.compile(
    r"^\s*(\d{1,2}[\s./-]+[^\s\d|/]{3,12}[\s,./-]+\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{4})"
    r"(\s*[/|,]?\s*\d{1,2}:\d{2})?\s*[/|,–-]?\s*", re.IGNORECASE)


@dataclass(frozen=True)
class LinkListSite:
    key: str
    listing_url: str
    link_pattern: str                 # regex — mütləq URL-ə tətbiq olunur
    kind: str = "news"                # news | tender
    org: str | None = None            # tender sifarişçisi
    strip_prefix: str | None = None   # başlıqdan atılacaq sabit söz (məs. bölmə adı)
    body_selector: str | None = None
    min_title: int = 15


def clean_title(text: str, strip_prefix: str | None = None) -> str:
    text = " ".join(text.split())
    if strip_prefix and text.lower().startswith(strip_prefix.lower()):
        text = text[len(strip_prefix):].strip(" :–-|")
    return _DATE_PREFIX.sub("", text, count=1).strip()


class LinkListScraper(BaseScraper):
    site: LinkListSite

    def fetch_leads(self) -> list[RawLead]:
        site = self.site
        soup = BeautifulSoup(self.get(site.listing_url).text, "lxml")
        pattern = re.compile(site.link_pattern)
        titles: dict[str, str] = {}
        for a in soup.select("a[href]"):
            url = urljoin(site.listing_url, a["href"]).split("#")[0]
            if not pattern.search(url) or url.rstrip("/") == site.listing_url.rstrip("/"):
                continue
            title = clean_title(a.get_text(" ", strip=True), site.strip_prefix)
            if len(title) >= site.min_title and len(title) > len(titles.get(url, "")):
                titles[url] = title
        items = list(titles.items())[:MAX_ITEMS]
        if not items:
            log.warning("listing_no_items", source=self.source_name, url=site.listing_url)

        is_tender = site.kind == "tender"
        texts = [f"{t} — {site.org}" if is_tender and site.org else t for _, t in items]
        keep = self.select_relevant(texts, TENDER_KEYWORDS if is_tender else NEWS_KEYWORDS,
                                    kind="tender" if is_tender else "news")
        leads: list[RawLead] = []
        for (url, title), selected in zip(items, keep):
            if not selected:
                continue
            header = f"{title}\n\nSifarişçi: {site.org}" if is_tender and site.org else title
            lead = RawLead(source=self.source_name, source_url=url, title=title, raw_text=header,
                           entity_name=site.org if is_tender else None,
                           category=LeadCategory.tender if is_tender else LeadCategory.event)
            body, posted_at = self.fetch_article(url)
            if body:
                lead.raw_text = f"{header}\n\n{body}"
            lead.posted_at = posted_at
            leads.append(lead)
        return leads


def listing_scraper(site: LinkListSite) -> type[LinkListScraper]:
    return type(f"List_{site.key}", (LinkListScraper,), {"site": site, "source_name": site.key,
                                                         "article_body_selector": site.body_selector})
