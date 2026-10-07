from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import NEWS_KEYWORDS, TENDER_KEYWORDS, BaseScraper, fold

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
    # Qlobal şirkət saytları (ACWA, Masdar, TotalEnergies...) dünya xəbərləri ilə doludur: başlıq və ya URL
    # bu sözlərdən birini ehtiva etməyənlər atılır (AI triaj xərci və səs-küy olmasın).
    must_match: tuple[str, ...] = ()


AZ_GATE = (
    "azerbaijan", "azərbaycan", "azerbaycan", "baku", "bakı", "caspian", "xəzər", "absheron", "abşeron",
    "shah deniz", "shahdeniz", "socar", "azeri", "gobustan", "garadagh", "qaradağ", "karabakh", "garabagh",
)

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
    # --- Faza 5: assosiasiya/palata tədbirləri və tikinti sektoru (2026-10-07) ---
    MediaSite("amcham", "AmCham Azerbaijan", "https://amcham.az/en/event-calendar",
              r"^https://amcham\.az/en/event/\d+$", category=LeadCategory.event),
    MediaSite("ahk", "AHK Azerbaijan (Alman-Azərbaycan Ticarət Palatası)", "https://aserbaidschan.ahk.de/en/events",
              r"^https://aserbaidschan\.ahk\.de/en/events/[a-z0-9-]{10,}$", category=LeadCategory.event),
    MediaSite("atia", "ATİA — Tikinti İstehsalçıları Assosiasiyası", "https://atia-az.az/",
              r"^https://atia-az\.az/news/[a-z0-9-]{15,}$"),
    # --- Faza 6: tikinti/infrastruktur (2026-10-07) ---
    MediaSite("arxkom_news", "Dövlət Şəhərsalma və Arxitektura Komitəsi — xəbərlər", "https://arxkom.gov.az/media/xeberler",
              r"^https://arxkom\.gov\.az/media/xeberler/[a-z0-9-]{15,}$"),
    MediaSite("arxkom_notices", "Dövlət Şəhərsalma və Arxitektura Komitəsi — elanlar", "https://arxkom.gov.az/media/elanlar",
              r"^https://arxkom\.gov\.az/elanlar/[a-z0-9-]{15,}$"),
    MediaSite("azfen", "Azfen (SOCAR EPC podratçısı)", "https://www.azfen.com/Main/News",
              r"^https://www\.azfen\.com/Main/NewsDetails/\d+$"),
    # HECOTEC (hecotec.az/az/bloq) sınanıb: xəbər linklərində başlıq mətni yoxdur (yalnız ox ikonu) — qoşulmadı.
    # --- Faza 4: böyük investor/şirkətlər (2026-10-07) ---
    # Masdar (newsroom JS ilə yüklənir) və TotalEnergies (/news qlobal köhnə məqalələr verir) sınanıb, qoşulmayıb.
    MediaSite("socar_press", "SOCAR press-relizlər", "https://www.socar.az/az/page/press-relizler",
              r"^https://www\.socar\.az/az/post/[a-z0-9-]{15,}$"),
    MediaSite("acwa", "ACWA Power", "https://acwapower.com/en/media-center/latest-news/",
              r"^https://acwapower\.com/en/media-center/latest-news/[a-z0-9-]{15,}/?$", must_match=AZ_GATE),
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
    re.compile(r"\b\d{1,2}/\d{1,2}/20\d\d\b"),       # socar.az: "01/10/2026"
    re.compile(r"\b\d{1,2}/\d{1,2}\s+20\d\d\b"),   # "10/26 2021"
    re.compile(r"\bRead the article\b", re.I),
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
    if len(title) < MIN_TITLE_LEN:  # "Register now" düyməsi: başlıq kartın başlığındadır (≤3 səviyyə yuxarı)
        node = a
        for _ in range(3):
            node = node.parent
            if node is None:
                break
            heading = node.find(["h1", "h2", "h3", "h4", "h5"])
            if heading is not None and len(clean_title(heading.get_text(" ", strip=True))) >= MIN_TITLE_LEN:
                return clean_title(heading.get_text(" ", strip=True))
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

        if site.must_match:
            gate = [fold(g) for g in site.must_match]
            candidates = [(u, t) for u, t in candidates if any(g in fold(f"{t} {u}") for g in gate)]
            if not candidates:
                log.info("media_site_gated_out", site=site.key)
                return []

        # Tədbir saytlarında (palata/sərgi) tədbir sözləri də açar sözdür: forum, konfrans, sərgi, event, reception...
        keywords = TENDER_KEYWORDS if site.category == LeadCategory.event else self.keywords
        keep = self.select_relevant([f"{t} — {site.org}" for _, t in candidates], keywords, kind="news")
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
