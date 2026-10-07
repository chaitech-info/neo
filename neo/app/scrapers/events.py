"""Sərgi və tədbir təqvimləri — hər yaxınlaşan sərgi bir lead (Phase 2, 2026-10-05).

Baku Expo Center-in təqvimi (bakuexpocenter.az → PASHA Property səhifəsi, server
HTML, robots yalnız admin/login qadağandır): "14 - 16 Okt BakuBuild 31-ci Azərbaycan
Beynəlxalq İnşaat Sərgisi" + sərginin öz saytı. Sərgi = tədbir catering-i (təşkilatçı
ITECA Caspian, iştirakçı stendləri, VIP/forum qəbulları); kateqoriya `event_listing`
— v2 qiymətləndiricisi bunu xəbərdəki tədbir yox, tender kimi sayır.
iteca.az-ın illik səhifələri JS ilə yüklənir, "təqvimi yüklə" linki işləmir (2026-10-05).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import BAKU_TZ, BaseScraper

log = structlog.get_logger()

CALENDAR_URL = "https://www.pashaproperty.az/properties/baku-expo-centre"
VENUE = "Baku Expo Center"
MONTHS = {"yan": 1, "fev": 2, "mar": 3, "apr": 4, "may": 5, "iyn": 6, "iyl": 7, "avq": 8,
          "sen": 9, "okt": 10, "noy": 11, "dek": 12}
_ITEM = re.compile(r"^(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-zƏəİıÖöÜüĞğÇçŞş]{3})\w*\s+(.+)$")


def parse_event(text: str, today: date) -> tuple[date, date, str] | None:
    """"14 - 16 Okt BakuBuild 31-ci ..." → (başlanğıc, son, ad). İl göstərilmir:
    bu il bitibsə növbəti il. Ay ilin sonundan əvvələ keçirsə (28 - 02) son tarix növbəti aydadır."""
    m = _ITEM.match(" ".join(text.split()))
    if not m:
        return None
    start_day, end_day, month_name, name = int(m[1]), int(m[2]), m[3].lower(), m[4].strip()
    month = MONTHS.get(month_name.replace("i̇", "i")[:3])
    if not month:
        return None
    for year in (today.year, today.year + 1):
        try:
            start = date(year, month, start_day)
            end_month = month if end_day >= start_day else month % 12 + 1
            end = date(year + (1 if end_month < month else 0), end_month, end_day)
        except ValueError:
            return None
        if end >= today:
            return start, end, name
    return None


class ExpoCenterScraper(BaseScraper):
    source_name = "expo_center"
    base_url = "https://www.pashaproperty.az"

    @staticmethod
    def today() -> date:
        return datetime.now(BAKU_TZ).date()

    def fetch_leads(self) -> list[RawLead]:
        soup = BeautifulSoup(self.get(CALENDAR_URL).text, "lxml")
        today = self.today()
        seen: set[str] = set()
        leads: list[RawLead] = []
        found = 0
        for a in soup.select("a[href]"):
            parsed = parse_event(a.get_text(" ", strip=True), today)
            site = urljoin(CALENDAR_URL, a["href"])
            if parsed is None or site in seen:
                continue
            seen.add(site)
            found += 1
            start, end, name = parsed
            short = name.split(" ", 2)
            dates = f"{start:%d.%m.%Y} - {end:%d.%m.%Y}"
            leads.append(RawLead(
                source=self.source_name,
                source_url=f"{site}#{start.year}",          # hər il ayrıca buraxılış = ayrıca lead
                title=f"{name} — {dates}, {VENUE}",
                raw_text=(f"Yaxınlaşan sərgi/tədbir: {name}\nTarix: {dates}\nMəkan: {VENUE}, Bakı\n"
                          f"Sərginin saytı: {site}\nTəşkilatçı adətən ITECA Caspian-dır."),
                entity_name=" ".join(short[:2]) if short else name,
                category=LeadCategory.event_listing,
                location="Bakı",
                deadline_at=datetime(start.year, start.month, start.day),
            ))
        self.fetched = found
        if not found:
            log.warning("expo_calendar_empty", url=CALENDAR_URL)
        return leads
