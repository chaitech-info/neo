"""RSS lentli saytlar — bir sayt = bir `RssSite` (Phase 2, 2026-10-05).

Lent strukturlaşdırılmış datadır: HTML selector-u sınmır, sayt dizaynını dəyişəndə
də işləyir. Axın: lentdəki başlıqlar → select_relevant (açar söz + AI triajı) →
seçilənlərin tam məqaləsi (fetch_article, ümumi evristika və ya `body_selector`)
→ pipeline. Lentdə mətn (description) varsa, məqalə çəkilmədikdə ehtiyat kimi qalır.

Yoxlanılıb (2026-10-05, Mac və Modal-dan): trend.az, apa.az, azertag.az, banker.az,
minval.az (rus), caliber.az (ingilis; Modal-a 403 → Mac), president.az, bizim.media,
ask.org.az. Bax docs/knowledge-base/03-site-research.md → "RSS mənbələri".
"""
from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import structlog
from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import ARTICLE_MAX_CHARS, NEWS_KEYWORDS, BaseScraper, parse_datetime

log = structlog.get_logger()

MAX_ITEMS = 60  # böyük lentlərdən (caliber: 100) yalnız ən yenilər — saatlıq işləyir


@dataclass(frozen=True)
class RssSite:
    key: str
    feed_url: str
    body_selector: str | None = None   # None: ümumi məqalə evristikası
    fetch_body: bool = True


class RssNewsScraper(BaseScraper):
    site: RssSite
    triage_kind = "news"

    def fetch_leads(self) -> list[RawLead]:
        soup = BeautifulSoup(self.get(self.site.feed_url).content, "xml")
        entries = soup.find_all(["item", "entry"])[:MAX_ITEMS]
        items: list[tuple[str, str, str, str | None]] = []
        seen: set[str] = set()
        for entry in entries:
            title = _text(entry.find("title")) or _embedded_heading(entry)
            link = _link(entry)
            if not title or not link or link in seen:
                continue
            seen.add(link)
            summary = _text(entry.find("description") or entry.find("summary") or entry.find("content"))
            published = _text(entry.find("pubDate") or entry.find("published") or entry.find("updated"))
            items.append((title, link, summary, published))
        if not entries:
            log.warning("rss_empty", source=self.source_name, url=self.site.feed_url)

        keep = self.select_relevant([title for title, *_ in items], NEWS_KEYWORDS, kind=self.triage_kind)
        leads: list[RawLead] = []
        for (title, link, summary, published), selected in zip(items, keep):
            if not selected:
                continue
            summary_text = BeautifulSoup(summary, "lxml").get_text(" ", strip=True) if summary else ""
            lead = RawLead(
                source=self.source_name,
                source_url=link,
                title=title,
                raw_text=f"{title}\n\n{summary_text}".strip()[:ARTICLE_MAX_CHARS],
                category=LeadCategory.event,
                posted_at=_parse_date(published),
            )
            if self.site.fetch_body:
                body, posted_at = self.fetch_article(link)
                if body and len(body) > len(summary_text):
                    lead.raw_text = f"{title}\n\n{body}"
                lead.posted_at = lead.posted_at or posted_at
            leads.append(lead)
        return leads


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""


def _embedded_heading(entry) -> str:
    """Yandex "turbo" lentləri (caliber.az): <title> yoxdur, başlıq turbo:content-dəki <h1>-dədir."""
    for tag in ("turbo:content", "content", "encoded"):
        node = entry.find(tag)
        if node is not None:
            h1 = BeautifulSoup(node.get_text(), "lxml").find("h1")
            if h1 is not None:
                return h1.get_text(" ", strip=True)
    return ""


def _link(entry) -> str:
    link = entry.find("link")
    if link is None:
        return ""
    return (link.get("href") or link.get_text(strip=True) or "").strip()


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return parse_datetime(parsedate_to_datetime(value).isoformat())   # RSS: RFC 822
    except (TypeError, ValueError):
        return parse_datetime(value)                                        # Atom: ISO 8601


def rss_scraper(site: RssSite) -> type[RssNewsScraper]:
    """Hər sayt üçün ayrıca sinif — source_name = açar (leads.source, scrape_runs, sağlamlıq)."""
    return type(f"Rss_{site.key}", (RssNewsScraper,), {"site": site, "source_name": site.key,
                                                       "article_body_selector": site.body_selector})
