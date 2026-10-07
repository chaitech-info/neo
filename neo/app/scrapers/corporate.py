from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup, Tag

from app.models import LeadCategory, RawLead
from app.scrapers.base import BAKU_TZ, TENDER_KEYWORDS, BaseScraper, az_lower, parse_datetime

log = structlog.get_logger()

# Şirkət və agentliklərin öz saytlarındakı tender səhifələri (2026-09-23 yoxlanıb,
# hamısının robots.txt-i bu səhifələrə icazə verir). Əksəriyyəti etender.gov.az-a
# düşməyən korporativ alışlardır. Yeni sayt = SITES-ə yeni SiteConfig.
#
# Hələ qoşulmayıb:
# - AzerGold: tender siyahısı ayrıca portaldadır (satinalmalar.azergold.az) — ayrıca baxılmalı.
# - ABB: Next.js RSC axınında gəlir, adi HTML-də siyahı yoxdur — ayrıca parser lazımdır.
# - atb.az: robots.txt bütün crawling-i qadağan edir — qoşulmamalıdır.
# - ADY, ASCO, AZAL: bot qoruması (403) — keçməyə cəhd edilmir.
# - Bakı Limanı (bakuseaport.az), AIC (aic.az): 2026-10-07-də cavab vermədi.
# - Bakı Metropoliteni (metro.gov.az/az/procurement): robots.txt HTTP 500 qaytarır — robots.py bunu
#   "qadağan" sayır; sayt düzələndə SiteConfig(item_selector="a[href*='/az/procurement/']",
#   title_selector="img", title_attr="alt") ilə qoşula bilər.


@dataclass(frozen=True)
class SiteConfig:
    key: str
    org: str
    listing_url: str
    item_selector: str
    title_selector: str | None = None  # None: elementin öz mətni
    title_attr: str | None = None      # None: mətn; məs. "alt" — başlıq <img alt>-dadır
    link_selector: str | None = None   # None: elementin öz href-i (məs. <tr href=...>)
    link_attr: str = "href"
    id_attr: str | None = None         # link yoxdursa stabil ID üçün (məs. data-id)
    date_selector: str | None = None   # None: elementin mətnində tarix axtarılır
    deadline_selector: str | None = None
    fetch_detail: bool = False         # detal səhifəsi HTML-dirsə məqalə mətni çəkilsin


SITES: list[SiteConfig] = [
    SiteConfig(
        key="tourism",
        org="Dövlət Turizm Agentliyi",
        listing_url="https://tourism.gov.az/page/tenders",
        item_selector="table tr.clickable",
        title_selector="td:nth-of-type(1)",
        date_selector="td:nth-of-type(2)",
        deadline_selector="td:nth-of-type(3)",
        # link = <tr href="...pdf"> — PDF olduğu üçün detal çəkilmir
    ),
    SiteConfig(
        key="rabitabank",
        org="Rabitəbank",
        listing_url="https://www.rabitabank.com/individual-customer/tender-announcements",
        item_selector="div.tender__item",
        title_selector="a",
        link_selector="a",  # siyahının özünə işarə edir → başlıq hash-i ilə unikal URL
        date_selector=".tender__label.bg-light-gray",
    ),
    SiteConfig(
        key="vtb",
        org="Bank VTB (Azərbaycan)",
        listing_url="https://vtb.az/about-bank/information-centre/elanlar/",
        item_selector="a.news__item[href*='/information-centre/elanlar/']",
        title_selector=".news__title",
        fetch_detail=True,
    ),
    SiteConfig(
        key="azerishiq",
        org="Azərişıq ASC",
        listing_url="https://azerishiq.az/menu/tender-elanlari",
        item_selector="div.media.tender_modal",
        title_selector="p.tender_media_text",
        id_attr="data-id",  # link javascript:void(0) — modal açır
    ),
    SiteConfig(
        key="azerenerji",
        org="Azərenerji ASC",
        listing_url="https://azerenerji.gov.az/tenderannouncements",
        item_selector="div.row.hentry",
        title_selector="span.post-title-inner",
        link_selector="a[href*='tenderannouncementsdetail']",
        fetch_detail=True,
    ),
    SiteConfig(
        key="socar",
        org="SOCAR",
        listing_url="https://www.socar.az/az/page/satinalma-elanlari",
        item_selector="tbody#table__procurements__body tr",
        title_selector="td:nth-of-type(1)",
        link_selector="a",  # link .doc/.pdf-dir — dedup üçün sənədin özü
    ),
]

_MONTHS = {
    # az (tam və qısa) + en
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6, "iyul": 7,
    "avqust": 8, "sentyabr": 9, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
    "yan": 1, "fev": 2, "mar": 3, "apr": 4, "iyn": 6, "iyl": 7, "avq": 8,
    "sen": 9, "okt": 10, "noy": 11, "dek": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")  # 18.09.2026, 30/09/2026 (gün əvvəl)
_WORD_DATE = re.compile(r"\b(\d{1,2})\s+([a-zəıöüğçş]+)\s*[,/]?\s*(\d{4})\b")  # "5 oktyabr / 2026" də


def parse_listing_day(text: str) -> date | None:
    """"18.09.2026", "17 Sentyabr 2026", "23 September, 2026" → təqvim günü."""
    text = az_lower(text)
    if m := _NUMERIC_DATE.search(text):
        day, month, year = int(m[1]), int(m[2]), int(m[3])
    elif (m := _WORD_DATE.search(text)) and m[2] in _MONTHS:
        day, month, year = int(m[1]), _MONTHS[m[2]], int(m[3])
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_listing_date(text: str) -> datetime | None:
    """Siyahıdakı tarix → naive UTC (Bakı gün başı), `posted_at` üçün."""
    day = parse_listing_day(text)
    if day is None:
        return None
    return parse_datetime(datetime(day.year, day.month, day.day, tzinfo=BAKU_TZ).isoformat())


class CorporateTenderScraper(BaseScraper):
    """Bir neçə şirkət/agentlik tender səhifəsini SITES konfiqurasiyası ilə oxuyur."""

    source_name = "corporate"
    sites: list[SiteConfig] = SITES
    keywords: list[str] = TENDER_KEYWORDS

    @staticmethod
    def today() -> date:
        return datetime.now(BAKU_TZ).date()

    def fetch_leads(self) -> list[RawLead]:
        leads: list[RawLead] = []
        for i, site in enumerate(self.sites):
            if i:
                self.polite_sleep()
            try:
                leads.extend(self.fetch_site(site))
            except Exception:
                # Bir saytın dəyişməsi/çökməsi digərlərini dayandırmır (§3).
                log.exception("corporate_site_failed", site=site.key)
        return leads

    def fetch_site(self, site: SiteConfig) -> list[RawLead]:
        soup = BeautifulSoup(self.get(site.listing_url).text, "lxml")
        items = soup.select(site.item_selector)
        if not items:
            log.warning("corporate_site_no_items", site=site.key, selector=site.item_selector)

        candidates = []
        seen: set[str] = set()
        expired = 0
        today = self.today()
        for item in items:
            # Son tarixi keçmiş tenderə müraciət etmək olmur — lead deyil.
            # Son tarix günü özü hələ aktivdir; tarixi oxunmayan elan saxlanılır.
            deadline_el = item.select_one(site.deadline_selector) if site.deadline_selector else None
            deadline = parse_listing_day(deadline_el.get_text(" ", strip=True)) if deadline_el else None
            if deadline and deadline < today:
                expired += 1
                continue

            title_el = item.select_one(site.title_selector) if site.title_selector else item
            if title_el is None:
                title = ""
            elif site.title_attr:
                title = (title_el.get(site.title_attr) or "").strip()
            else:
                title = title_el.get_text(" ", strip=True)
            if not title:
                continue
            url = self._item_url(site, item, title)
            if url in seen:
                continue
            seen.add(url)
            candidates.append((item, title, url, deadline))

        keep = self.select_relevant(
            [f"{title} — {site.org}" for _, title, _, _ in candidates], self.keywords, kind="tender"
        )
        leads: list[RawLead] = []
        for (item, title, url, deadline), selected in zip(candidates, keep):
            if not selected:
                continue

            date_el = item.select_one(site.date_selector) if site.date_selector else item
            posted_at = parse_listing_date(date_el.get_text(" ", strip=True)) if date_el else None

            raw_text = f"{title}\n\nSifarişçi: {site.org}"
            if deadline:
                raw_text += f"\nSon tarix: {deadline.strftime('%d.%m.%Y')}"

            lead = RawLead(
                source="corporate",
                source_url=url,
                title=title,
                raw_text=raw_text,
                entity_name=site.org,
                category=LeadCategory.tender,
                posted_at=posted_at,
            )
            if site.fetch_detail:
                lead = self._enrich_detail(lead)
            leads.append(lead)

        log.info("corporate_site_scraped", site=site.key, items=len(items), expired=expired, leads=len(leads))
        return leads

    @staticmethod
    def _item_url(site: SiteConfig, item: Tag, title: str) -> str:
        link_el = item.select_one(site.link_selector) if site.link_selector else item
        href = (link_el.get(site.link_attr) or "") if link_el else ""
        url = urljoin(site.listing_url, href) if href and not href.startswith("javascript") else ""
        if url and url.rstrip("/") != site.listing_url.rstrip("/"):
            return url
        # Detal linki yoxdur — dedup üçün stabil unikal URL: siyahı + ID/başlıq hash-i.
        ident = item.get(site.id_attr) if site.id_attr else None
        ident = ident or hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
        return f"{site.listing_url}#{ident}"

    def _enrich_detail(self, lead: RawLead) -> RawLead:
        header = lead.raw_text
        body, posted_at = self.fetch_article(lead.source_url)
        if body:
            lead.raw_text = f"{header}\n\n{body}"
        lead.posted_at = lead.posted_at or posted_at
        return lead
