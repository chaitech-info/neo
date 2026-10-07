from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import BAKU_TZ, TENDER_KEYWORDS, BaseScraper, parse_datetime

log = structlog.get_logger()

# Beynəlxalq təşkilatların tender portalları (2026-10-07 yoxlanıb; hər ikisinin
# robots.txt-i crawl-a icazə verir):
# - UNGM (BMT satınalmaları): POST /Public/Notice/Search — CSRF tokeni və cookie
#   lazımdır (siyahı səhifəsindən götürülür), nəticə HTML sətirləridir.
#   Azərbaycan üçün ölkə ID-si 2306.
# - EBRD ECEPP: /delta/noticeSearch.html bütün bildirişləri (~4000 sətir) bir
#   səhifədə server tərəfdə render edir; "Azerbaijan: ..." başlıqları süzülür.
#
# Qoşulmayanlar: ADB (adb.org bot qoruması, 403), EU Funding & Tenders (qrant
# çağırışlarıdır, təchizat tenderi deyil — catering/tədbir fürsəti çıxmır).

UNGM_URL = "https://www.ungm.org/Public/Notice"
UNGM_SEARCH_URL = "https://www.ungm.org/Public/Notice/Search"
UNGM_AZERBAIJAN_ID = 2306
UNGM_PAGE_SIZE = 15
UNGM_MAX_PAGES = 4

EBRD_URL = "https://ecepp.ebrd.com/delta/noticeSearch.html"

_TOKEN = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_UNGM_DATE = re.compile(r"(\d{1,2}-[A-Za-z]{3}-\d{4})")
_EBRD_DATE = re.compile(r"(\d{2}/\d{2}/\d{4})")
# Artıq bağlanmış / yalnız məlumat xarakterli EBRD bildirişləri — müraciət etmək olmur.
_EBRD_SKIP_TYPES = ("contract award", "shortlist", "general procurement")


def _ungm_day(text: str) -> date | None:
    m = _UNGM_DATE.search(text)
    try:
        return datetime.strptime(m[1], "%d-%b-%Y").date() if m else None
    except ValueError:
        return None


def _ebrd_day(text: str) -> date | None:
    m = _EBRD_DATE.search(text)
    try:
        return datetime.strptime(m[1], "%d/%m/%Y").date() if m else None
    except ValueError:
        return None


def _utc(day: date | None) -> datetime | None:
    if day is None:
        return None
    return parse_datetime(datetime(day.year, day.month, day.day, tzinfo=BAKU_TZ).isoformat())


class IntlTenderScraper(BaseScraper):
    """UNGM və EBRD ECEPP-dən Azərbaycana aid açıq tenderlər."""

    source_name = "intl_tender"
    keywords: list[str] = TENDER_KEYWORDS

    @staticmethod
    def today() -> date:
        return datetime.now(BAKU_TZ).date()

    def fetch_leads(self) -> list[RawLead]:
        candidates: list[dict] = []
        for name, fetch in (("ungm", self.fetch_ungm), ("ebrd", self.fetch_ebrd)):
            try:
                found = fetch()
                log.info("intl_portal_scraped", portal=name, notices=len(found))
                candidates.extend(found)
            except Exception:
                # Bir portalın çökməsi digərini dayandırmır.
                log.exception("intl_portal_failed", portal=name)

        keep = self.select_relevant(
            [f"{c['title']} — {c['org']}" for c in candidates], self.keywords, kind="tender"
        )
        leads: list[RawLead] = []
        for c, selected in zip(candidates, keep):
            if not selected:
                continue
            raw_text = f"{c['title']}\n\nSifarişçi: {c['org']}\nPortal: {c['portal']}"
            if c.get("kind"):
                raw_text += f"\nNövü: {c['kind']}"
            if c.get("deadline"):
                raw_text += f"\nSon tarix: {c['deadline'].strftime('%d.%m.%Y')}"
            leads.append(RawLead(
                source="intl_tender",
                source_url=c["url"],
                title=c["title"],
                raw_text=raw_text,
                entity_name=c["org"],
                category=LeadCategory.tender,
                posted_at=_utc(c.get("posted")),
            ))
        return leads

    # --- UNGM -------------------------------------------------------------

    def fetch_ungm(self) -> list[dict]:
        page = self.get(UNGM_URL).text
        token = _TOKEN.search(page)
        if not token:
            raise RuntimeError("UNGM CSRF tokeni tapılmadı — sayt dəyişib")
        today = self.today()
        found: list[dict] = []
        for index in range(UNGM_MAX_PAGES):
            if index:
                self.polite_sleep()
            resp = self.post(
                UNGM_SEARCH_URL,
                json={
                    "PageIndex": index, "PageSize": UNGM_PAGE_SIZE, "Title": "", "Description": "",
                    "Reference": "", "PublishedFrom": "", "PublishedTo": "", "DeadlineFrom": "",
                    "DeadlineTo": "", "Countries": [UNGM_AZERBAIJAN_ID], "Agencies": [], "UNSPSCs": [],
                    "NoticeTypes": [], "SortField": "DatePublished", "SortAscending": False,
                    "isPicker": False, "IsSustainable": False, "IsActive": True,
                    "NoticeDisplayType": None, "NoticeSearchTotalLabelId": "noticeSearchTotal",
                    "TypeOfCompetitions": [],
                },
                headers={"RequestVerificationToken": token[1], "X-Requested-With": "XMLHttpRequest"},
            )
            rows = BeautifulSoup(resp.text, "lxml").select("div.tableRow.dataRow")
            for row in rows:
                notice = self._ungm_row(row, today)
                if notice:
                    found.append(notice)
            if len(rows) < UNGM_PAGE_SIZE:
                break
        return found

    @staticmethod
    def _ungm_row(row, today: date) -> dict | None:
        link = row.select_one(".resultTitle a[href^='/Public/Notice/']")
        title_el = row.select_one(".resultTitle .ungm-title")  # linkin öz mətni "Open in a new window"-dur
        if link is None or title_el is None:
            return None
        title = title_el.get_text(" ", strip=True)
        deadline_el = row.select_one(".deadline")
        deadline = _ungm_day(deadline_el.get_text(" ", strip=True)) if deadline_el else None
        if not title or (deadline and deadline < today):
            return None
        cells = [c.get_text(" ", strip=True) for c in row.select("div.tableCell")]
        agency_el = row.select_one(".resultAgency")
        return {
            "portal": "UNGM",
            "title": title,
            "org": agency_el.get_text(" ", strip=True) if agency_el else "BMT",
            "url": urljoin(UNGM_URL, link["href"]),
            "deadline": deadline,
            "posted": _ungm_day(cells[3]) if len(cells) > 3 else None,
            "kind": cells[5] if len(cells) > 5 else None,
        }

    # --- EBRD ECEPP ---------------------------------------------------------

    def fetch_ebrd(self) -> list[dict]:
        soup = BeautifulSoup(self.get(EBRD_URL).text, "lxml")
        today = self.today()
        found: list[dict] = []
        for row in soup.select("table tr"):
            cells = [td.get_text(" ", strip=True) for td in row.select("td")]
            link = row.select_one("a[href*='viewNotice']")
            if len(cells) < 6 or link is None or not cells[0].startswith("Azerbaijan"):
                continue
            kind, state = cells[1], cells[5]
            if kind.lower().startswith(_EBRD_SKIP_TYPES) or state.lower() != "open":
                continue
            deadline = _ebrd_day(cells[4])
            if deadline and deadline < today:
                continue
            title = re.sub(r"^Azerbaijan:\s*", "", cells[0])
            project = cells[9].strip("[] ").split(",")[0] if len(cells) > 9 else ""
            found.append({
                "portal": "EBRD ECEPP",
                "title": title,
                "org": project or "EBRD",
                "url": urljoin(EBRD_URL, link["href"]),
                "deadline": deadline,
                "posted": _ebrd_day(cells[3]),
                "kind": kind,
            })
        return found
