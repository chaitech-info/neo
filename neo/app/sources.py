"""Mənbə reyestri — YEGANƏ yer. Yeni mənbə = bu siyahıya bir `SourceSpec`.

Hamısı buradan törəyir: Celery beat və Modal cədvəli (app/scheduler.py), Modal/Mac
bölgüsü (LOCAL_ONLY), panel siyahısı və növü (app/web.py), `sources` cədvəli
(`sync_sources` — miqrasiyadan sonra avtomatik), yenidən skorlama skripti.
Əvvəl bir mənbə 7 yerdə əl ilə qeyd olunurdu (Phase 2, 2026-10-05).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.scrapers import (
    BossAzScraper,
    BpEdpScraper,
    CorporateTenderScraper,
    EmlakScraper,
    EtenderScraper,
    FedScraper,
    FedTenderScraper,
    LinkedInScraper,
    MarjaScraper,
    News1Scraper,
    OxuScraper,
    ReportSenayeScraper,
    WorldBankScraper,
)
from app.scrapers.base import BaseScraper
from app.scrapers.events import ExpoCenterScraper
from app.scrapers.jobboards import HELLOJOB, POSITION, jobboard_scraper
from app.scrapers.jobsearch import JobSearchScraper
from app.scrapers.listing import LinkListSite, listing_scraper
from app.scrapers.rss import RssSite, rss_scraper

PANEL_KINDS = {"tender_portal": "tender", "corporate": "tender", "intl_api": "tender",
               "jobs": "employer", "news": "news", "social": "news"}


@dataclass(frozen=True)
class SourceSpec:
    key: str                       # scrape_runs.source_key, panel, cədvəl
    label: str                     # paneldə görünən ad
    scraper: Callable[[], BaseScraper]
    kind: str                      # sources.kind: tender_portal | news | jobs | corporate | intl_api | social
    minute: str                    # cron dəqiqə (Bakı vaxtı)
    hour: str                      # cron saat, məs. "*", "*/2", "6", "8,16"
    base_url: str | None = None
    local_only: bool = False       # Modal IP-lərini bloklayır — Mac worker (Azərbaycan IP) işlədir
    notes: str | None = None

    @property
    def cron(self) -> str:
        return f"{self.minute} {self.hour} * * *"

    @property
    def panel_kind(self) -> str:
        return PANEL_KINDS.get(self.kind, "news")


SOURCES: list[SourceSpec] = [
    SourceSpec("marja", "Marja.az", MarjaScraper, "news", "0", "*", "https://marja.az"),
    SourceSpec("1news", "1news.az", News1Scraper, "news", "0", "*", "https://1news.az"),
    SourceSpec("oxu", "Oxu.az", OxuScraper, "news", "20", "*", "https://oxu.az", local_only=True),
    SourceSpec("fed", "Fed.az", FedScraper, "news", "40", "*", "https://fed.az", local_only=True),
    SourceSpec("fed_tenders", "Fed.az Tenderlər", FedTenderScraper, "tender_portal", "50", "*/2", "https://fed.az",
               local_only=True),
    SourceSpec("report", "Report.az Sənaye", ReportSenayeScraper, "news", "25", "*/2", "https://report.az",
               local_only=True, notes="ABŞ IP-dən 403, AB-dən qeyri-sabit"),
    SourceSpec("etender", "Etender.gov.az", EtenderScraper, "tender_portal", "0", "6", "https://etender.gov.az",
               local_only=True, notes="Xarici IP-dən timeout"),
    SourceSpec("emlak", "Emlak.gov.az", EmlakScraper, "tender_portal", "30", "6", "https://emlak.gov.az"),
    SourceSpec("corporate", "Şirkət/agentlik tenderləri", CorporateTenderScraper, "corporate", "10", "8,16"),
    SourceSpec("worldbank", "Dünya Bankı", WorldBankScraper, "intl_api", "0", "7", "https://search.worldbank.org"),
    SourceSpec("bp_edp", "bp EDP", BpEdpScraper, "corporate", "15", "7", "https://edp.az"),
    SourceSpec("boss", "boss.az — işçilərinə nahar verən şirkətlər", BossAzScraper, "jobs", "45", "7",
               "https://boss.az", notes="~28 GraphQL səhifəsi; elanlar 30 gün aktiv — gündə 1"),
    SourceSpec("linkedin", "LinkedIn (Apify)", LinkedInScraper, "jobs", "0", "7", "https://www.linkedin.com",
               notes="Apify post başına ödənişlidir"),
]

# --- Phase 2: RSS lentli xəbər/rəsmi saytlar (app/scrapers/rss.py) ---------------------
def _rss(key, label, feed, minute, hour="*", body=None, local_only=False, notes=None):
    return SourceSpec(key, label, rss_scraper(RssSite(key, feed, body)), "news", minute, hour,
                      feed.split("/", 3)[0] + "//" + feed.split("/")[2], local_only=local_only, notes=notes)


SOURCES += [
    _rss("trend", "Trend.az", "https://az.trend.az/rss", "5", body="div.article-content"),
    _rss("apa", "APA.az", "https://apa.az/rss", "15"),
    _rss("azertag", "AZƏRTAC", "https://azertag.az/rss", "25"),
    _rss("banker", "Banker.az", "https://banker.az/feed/", "35", "*/2"),
    _rss("minval", "Minval.az (rus)", "https://minval.az/feed/", "45", "*/2", body="div.content-inner"),
    _rss("caliber", "Caliber.az (rus)", "https://caliber.az/rss.xml", "55", "*/2", local_only=True,
         notes="Modal-a 403 (2026-10-05)"),
    _rss("president", "President.az (fərmanlar)", "https://president.az/az/rss", "10", "*/2",
         body="div.news_paragraph-block", notes="Fərman/sərəncam: yeni zavod, sənaye parkı, tədbir"),
    _rss("bizim", "Bizim.media", "https://bizim.media/feed", "50", "*/2"),
    _rss("ask", "ASK (Sahibkarlar Konfederasiyası)", "https://ask.org.az/feed/", "20", "8,14,20",
         notes="Biznes forumları, tədbirlər"),
]

# --- Phase 2: RSS-siz siyahı səhifələri (app/scrapers/listing.py) ------------------------
def _list(key, label, site: LinkListSite, minute, hour, kind="news", local_only=False, notes=None):
    base = "/".join(site.listing_url.split("/")[:3])
    return SourceSpec(key, label, listing_scraper(site), kind, minute, hour, base, local_only=local_only, notes=notes)


SOURCES += [
    _list("port_tenders", "Bakı Limanı — satınalma",
          LinkListSite("port_tenders", "https://portofbaku.com/az/page/media-ve-xeberler/elanlar", r"/az/elanlar/\d+",
                       "tender", "Bakı Beynəlxalq Dəniz Ticarət Limanı"),
          "20", "9,17", kind="corporate", local_only=True, notes="Modal-a 403 (2026-10-05)"),
    _list("economy", "İqtisadiyyat Nazirliyi — xəbərlər",
          LinkListSite("economy", "https://economy.gov.az/az/page/media/news", r"/az/post/\d+"), "30", "9,13,17"),
    _list("azpromo", "AZPROMO — xəbər və tədbirlər",
          LinkListSite("azpromo", "https://azpromo.az/az/page/media/xeberler-ve-tedbirler", r"/az/posts/\d+"),
          "40", "9,13,17", notes="İxracatçı missiyaları, biznes forumları"),
    _list("afez", "Ələt Azad İqtisadi Zonası (ing)",
          LinkListSite("afez", "https://afez.az/media/news", r"/media/news/[^/?]+$"), "50", "9,17",
          notes="Zonaya gələn investorlar = yeni zavodlar"),
    _list("minenergy", "Energetika Nazirliyi — xəbərlər",
          LinkListSite("minenergy", "https://minenergy.gov.az/az/xeberler-arxivi", r"/az/xeberler-arxivi/\d+"),
          "10", "10,16"),
]
# --- Phase 2: iş elanı saytları — işçilərinə nahar verən şirkətlər ---------------------------
SOURCES += [
    SourceSpec("jobsearch", "jobsearch.az — işçilərinə nahar verən şirkətlər", JobSearchScraper, "jobs", "5", "8",
               "https://jobsearch.az", notes="JSON API; son 3 günün elanları, ~100 detal sorğusu"),
    SourceSpec("position", "position.az — işçilərinə nahar verən şirkətlər", jobboard_scraper(POSITION), "jobs",
               "25", "8", "https://position.az", notes="HTML; ilk 3 səhifə, yalnız yeni elanların detalı"),
    SourceSpec("hellojob", "hellojob.az — işçilərinə nahar verən şirkətlər", jobboard_scraper(HELLOJOB), "jobs",
               "45", "8", "https://www.hellojob.az", notes="HTML; ilk 3 səhifə, yalnız yeni elanların detalı"),
]

# --- Phase 2: sərgi təqvimi ------------------------------------------------------------------
SOURCES += [
    SourceSpec("expo_center", "Baku Expo Center — sərgi təqvimi", ExpoCenterScraper, "corporate", "0", "9",
               "https://www.pashaproperty.az", notes="ITECA sərgiləri; hər buraxılış bir lead (URL#il)"),
]

# Yoxlanılıb, qoşulmayıb (2026-10-05): metro.gov.az (robots.txt 500 → RFC 9309: qadağan say),
# socar.az satınalma, tabib.gov.az elanlar, azercosmos (siyahı JS ilə yüklənir), airport.az (link yoxdur),
# ady.az / asco.az / azal.az (Mac-dan da 403 — bot qoruması), cabmin/aic/abif/ezda/araz-valley (domen tapılmadı).

BY_KEY: dict[str, SourceSpec] = {s.key: s for s in SOURCES}
LOCAL_ONLY = frozenset(s.key for s in SOURCES if s.local_only)


def sync_sources(connection) -> int:
    """`sources` cədvəlini reyestrlə uyğunlaşdır (yeni mənbə əlavə et, ad/cədvəli yenilə).
    Silmir: köhnə run/lead-lər FK ilə bağlıdır; reyestrdə olmayan mənbə `is_active=false`."""
    from sqlalchemy import text

    existing = {row[0] for row in connection.execute(text("SELECT key FROM sources"))}
    for s in SOURCES:
        params = {"key": s.key, "name": s.label, "kind": s.kind, "base_url": s.base_url,
                  "cron": s.cron, "notes": s.notes, "active": True}
        if s.key in existing:
            connection.execute(text(
                "UPDATE sources SET name=:name, kind=:kind, base_url=:base_url, schedule_cron=:cron, "
                "notes=COALESCE(:notes, notes), is_active=:active WHERE key=:key"), params)
        else:
            connection.execute(text(
                "INSERT INTO sources (key, name, kind, base_url, schedule_cron, respect_robots, is_active, notes) "
                "VALUES (:key, :name, :kind, :base_url, :cron, :robots, :active, :notes)"), params | {"robots": True})
    stale = existing - BY_KEY.keys()
    for key in stale:
        connection.execute(text("UPDATE sources SET is_active=:f WHERE key=:key"), {"key": key, "f": False})
    return len(SOURCES)
