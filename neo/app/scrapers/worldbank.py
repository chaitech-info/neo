from __future__ import annotations

from datetime import date, datetime

from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import (
    ARTICLE_MAX_CHARS,
    BAKU_TZ,
    EN_TENDER_KEYWORDS,
    TENDER_KEYWORDS,
    BaseScraper,
    contains_keyword,
    parse_datetime,
)

# Dünya Bankının açıq procurement API-si (2026-09-23 yoxlanıb; robots.txt yoxdur).
# Azərbaycan üzrə ildə ~50 elan, əksəriyyəti konsaltinq; catering nadirdir
# (tarixdə 2: 2020, 2021), amma layihə tədbirləri/təlimləri üçün çıxa bilər.
# Açıq elanlarda `submission_date` = təklif son tarixi.
API_URL = "https://search.worldbank.org/api/v2/procnotices"
DETAIL_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"

# Elanlar ingiliscədir — ingilis açar sözləri + ortaq Azərbaycan siyahısı.
KEYWORDS = TENDER_KEYWORDS + EN_TENDER_KEYWORDS
SKIPPED_TYPES = {"Contract Award"}  # artıq bağlanıb — müraciət etmək olmur


class WorldBankScraper(BaseScraper):
    source_name = "worldbank"
    base_url = "https://search.worldbank.org"
    page_size = 50  # ən son 50 elan ≈ bir il

    @staticmethod
    def today() -> date:
        return datetime.now(BAKU_TZ).date()

    def fetch_leads(self) -> list[RawLead]:
        resp = self.get(API_URL, params={
            "format": "json",
            "project_ctry_name": "Azerbaijan",
            "rows": self.page_size,
            "srt": "submission_date",
            "order": "desc",
        })
        today = self.today().isoformat()

        candidates = []
        for notice in resp.json().get("procnotices") or []:
            if notice.get("notice_type") in SKIPPED_TYPES:
                continue
            deadline = (notice.get("submission_date") or "")[:10]
            if deadline and deadline < today:
                continue
            title = (notice.get("bid_description") or "").strip()
            if title:
                candidates.append((notice, title, deadline))

        keep = self.select_relevant(
            [f"{title} — {notice.get('project_name') or ''}" for notice, title, _ in candidates],
            KEYWORDS,
            kind="tender",
        )
        leads: list[RawLead] = []
        for (notice, title, deadline), selected in zip(candidates, keep):
            if not selected:
                continue

            project = notice.get("project_name") or ""
            body = BeautifulSoup(notice.get("notice_text") or "", "lxml").get_text("\n", strip=True)
            raw_text = f"{title}\n\nLayihə: {project}\nNövü: {notice.get('notice_type')}"
            if deadline:
                raw_text += f"\nSon tarix: {deadline}"
            if body:
                raw_text += f"\n\n{body}"

            leads.append(
                RawLead(
                    source="worldbank",
                    source_url=DETAIL_URL.format(id=notice["id"]),
                    title=title,
                    raw_text=raw_text[:ARTICLE_MAX_CHARS],
                    entity_name=project or None,
                    category=LeadCategory.tender,
                    posted_at=_parse_notice_date(notice.get("noticedate")),
                )
            )
        return leads


def _parse_notice_date(value: str | None) -> datetime | None:
    """"24-Aug-2026" → naive UTC (Bakı gün başı)."""
    if not value:
        return None
    try:
        day = datetime.strptime(value, "%d-%b-%Y")
    except ValueError:
        return None
    return parse_datetime(day.replace(tzinfo=BAKU_TZ).isoformat())
