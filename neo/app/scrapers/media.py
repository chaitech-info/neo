from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import NEWS_KEYWORDS, BaseScraper

log = structlog.get_logger()

# Azərbaycan biznes/dövlət/assosiasiya mediası və sərgi saytları (2026-10-07
# yoxlanıb; hamısının robots.txt-i ana/bölmə səhifələrinə icazə verir). Hər sayt
# SSR HTML-dir — brauzer lazım deyil. Yeni sayt = MEDIA_SITES-ə yeni MediaSite.
#
# Hələ qoşulmayıb (səbəbləri docs/knowledge-base/03-site-research.md-də):
# - abc.az, bakuseaport.az, aic.az, cabmin.gov.az, energetika.gov.az: yoxlama zamanı cavab vermədi;
# - ady.az, asco.az, azal.az, bp.com, adb.org: bot qoruması (403) — keçməyə cəhd edilmir;
# - caliber.az: geosiyasi analitika, biznes siqnalı yoxdur;
# - sosial şəbəkələr və Google Maps: ToS/robots icazə vermir, rəsmi API lazımdır.


@dataclass(frozen=True)
class MediaSite:
    key: str
    org: str
    listing_url: str
    href_pattern: str  # məqalə linkini seçən regex (mütləq URL-ə çevrildikdən SONRA tətbiq olunur)
    category: LeadCategory = LeadCategory.corporate
    body_selector: str | None = None  # məqalə mətni konteyneri; None: ümumi evristika
    max_items: int = 40               # siyahının ən yeni N elementi — hər uyğunluq səhifə yükləməsi deməkdir


MEDIA_SITES: list[MediaSite] = [
    # Əsas xəbər/rəsmi saytlar (APA, Trend, AZƏRTAC, Banker, İqtisadiyyat Nazirliyi, AZPROMO, AFEZ, ASK...)
    # app/sources.py-də RSS/listing mənbələridir — burada yalnız onlarda olmayanlar.
    MediaSite("businesstime", "BusinessTime.az", "https://businesstime.az/",
              r"^https://businesstime\.az/[a-z0-9-]{25,}$"),
    MediaSite("tabib", "TƏBİB", "https://tabib.gov.az/media/xeberler", r"^https://tabib\.gov\.az/media/xeberler/\d+$"),
    MediaSite("tuib", "TÜİB", "https://tuib.az/news", r"^https://tuib\.az/news/[a-z0-9-]{10,}$",
              category=LeadCategory.event),
    # Sərgi və konfranslar: iştirakçı şirkətlər və təşkilatçı (yemək alan tərəf) siqnalı.
    MediaSite("caspianoilgas", "Caspian Oil & Gas", "https://caspianoilgas.az/en/news",
              r"^https://caspianoilgas\.az/en/news/[a-z0-9-]{10,}$", category=LeadCategory.event),
    MediaSite("bakubuild", "BakuBuild", "https://bakubuild.az/en/news",
              r"^https://bakubuild\.az/en/news(?:-for-all)?/[a-z0-9-]{10,}$", category=LeadCategory.event),
    MediaSite("translogistica", "TransLogistica Caspian", "https://translogistica.az/en/news",
              r"^https://translogistica\.az/en/news/[a-z0-9-]{10,}$", category=LeadCategory.event),
    MediaSite("rebuild", "Rebuild Karabakh", "https://rebuildgarabagh.az/en/news",
              r"^https://rebuildgarabagh\.az/en/news(?:-for-all)?/[a-z0-9-]{10,}$", category=LeadCategory.event),
]

# Anchor mətninə qarışan tarix/saat/kateqoriya qırıntıları (başlıqda görünməsin).
_DATE_FRAGMENTS = [
    re.compile(r"\(UTC[+-]\d{2}\)(?:\s+[A-Za-z ]+?News)?"),  # trend.az: "(UTC+04) Azerbaijan News"
    re.compile(r"\b\d{1,2}:\d{2}\b"),
    re.compile(r"\b\d{1,2}[.-]\d{1,2}[.-]\d{4}\b"),
    re.compile(r"\b\d{1,2}\s+[A-Za-zƏəİıÖöÜüĞğÇçŞş]+,?\s+20\d\d\b"),
    re.compile(r"\b[A-Za-z]+\s+\d{1,2},\s+20\d\d\b"),
]
MIN_TITLE_LEN = 15


def clean_title(text: str) -> str:
    for rx in _DATE_FRAGMENTS:
        text = rx.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" -–—|·")


def _anchor_title(a) -> str:
    title = clean_title(a.get_text(" ", strip=True))
    if len(title) < MIN_TITLE_LEN:  # şəkil-linki: başlıq <img alt>-dadır
        img = a.find("img", alt=True)
        title = clean_title(img["alt"]) if img else title
    return title


class MediaScraper(BaseScraper):
    """MEDIA_SITES siyahısındakı xəbər/assosiasiya/sərgi saytlarını oxuyur.

    Hər sayt: siyahı səhifəsi → məqalə linkləri (href_pattern) → açar söz VƏ YA AI
    triajı → seçilənlərin məqaləsi. Bir saytın çökməsi digərlərini dayandırmır.
    """

    source_name = "media"
    sites: list[MediaSite] = MEDIA_SITES
    keywords: list[str] = NEWS_KEYWORDS

    def fetch_leads(self) -> list[RawLead]:
        leads: list[RawLead] = []
        for i, site in enumerate(self.sites):
            if i:
                self.polite_sleep()
            try:
                leads.extend(self.fetch_site(site))
            except Exception:
                log.exception("media_site_failed", site=site.key)
        return leads

    def article_links(self, site: MediaSite, soup: BeautifulSoup) -> list[tuple[str, str]]:
        pattern = re.compile(site.href_pattern)
        best: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            url = urljoin(site.listing_url, a["href"]).split("#")[0]
            if not pattern.search(url):
                continue
            title = _anchor_title(a)
            # Eyni məqalə bir neçə linkdə təkrarlanır — ən uzun təmiz başlıq saxlanılır.
            if len(title) > len(best.get(url, "")):
                best[url] = title
        return [(u, t) for u, t in best.items() if len(t) >= MIN_TITLE_LEN][: site.max_items]

    def fetch_site(self, site: MediaSite) -> list[RawLead]:
        soup = BeautifulSoup(self.get(site.listing_url).text, "lxml")
        candidates = self.article_links(site, soup)
        if not candidates:
            log.warning("media_site_no_items", site=site.key, pattern=site.href_pattern)
            return []

        keep = self.select_relevant([f"{t} — {site.org}" for _, t in candidates], self.keywords, kind="news")
        leads: list[RawLead] = []
        for (url, title), selected in zip(candidates, keep):
            if not selected:
                continue
            lead = RawLead(
                source="media",
                source_url=url,
                title=title,
                raw_text=f"{title}\n\nMənbə: {site.org}",
                category=site.category,
            )
            self.article_body_selector = site.body_selector
            leads.append(self.enrich(lead))
        log.info("media_site_scraped", site=site.key, items=len(candidates), leads=len(leads))
        return leads
