from __future__ import annotations

from datetime import date, datetime

from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import (
    BAKU_TZ,
    EN_TENDER_KEYWORDS,
    TENDER_KEYWORDS,
    BaseScraper,
    contains_keyword,
)
from app.scrapers.corporate import parse_listing_day

# bp Azərbaycanın yerli təchizatçı portalı — "Public Bid Announcement System"
# (2026-09-23 yoxlanıb). Siyahı JS ilə POST /Tender/AnnouncementsData
# (count, page) çağırışından HTML cədvəl kimi gəlir, ən yenilər əvvəldə.
# robots.txt yoxdur (login səhifəsinə yönləndirir → məhdudiyyət yoxdur).
# Siyahı açıqdır; müraciət üçün portalda qeydiyyat (bp şərtləri) lazımdır.
# ~1-2 elan/ay, əsasən neft-qaz xidmətləri; offshore catering çıxsa dəyəri böyükdür.
LISTING_URL = "https://edp.az/Tender/Announcements"
DATA_URL = "https://edp.az/Tender/AnnouncementsData"
ORG = "bp Azərbaycan (EDP portalı)"
KEYWORDS = TENDER_KEYWORDS + EN_TENDER_KEYWORDS


class BpEdpScraper(BaseScraper):
    source_name = "bp_edp"
    base_url = "https://edp.az"
    page_size = 20  # 1-ci səhifə ≈ son bir il; aktiv elanlar həmişə əvvəldədir

    @staticmethod
    def today() -> date:
        return datetime.now(BAKU_TZ).date()

    def fetch_leads(self) -> list[RawLead]:
        resp = self.post(
            DATA_URL,
            data={"count": self.page_size, "page": 1},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        soup = BeautifulSoup(resp.text, "lxml")
        today = self.today()

        leads: list[RawLead] = []
        for row in soup.select("#table-body-tender-list > tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 5:
                continue
            bid_no = cells[1].get_text(strip=True)
            title = cells[2].get_text(" ", strip=True)
            scope = cells[3].get_text(" ", strip=True)
            deadline = parse_listing_day(cells[4].get_text(" ", strip=True))

            if deadline and deadline < today:
                continue
            # Başlıq qısadır ("Provision of ... Services") — işin təsviri də yoxlanılır.
            if not title or not contains_keyword(f"{title} {scope}", KEYWORDS):
                continue

            raw_text = f"{title}\n\nSifarişçi: {ORG}\nNömrə: {bid_no}"
            if deadline:
                raw_text += f"\nSon tarix: {deadline.strftime('%d.%m.%Y')}"
            raw_text += f"\n\nİşin təsviri: {scope}"

            leads.append(
                RawLead(
                    source="bp_edp",
                    # Detal səhifəsi yoxdur — bid nömrəsi unikaldır.
                    source_url=f"{LISTING_URL}#{bid_no}",
                    title=title,
                    raw_text=raw_text,
                    entity_name=ORG,
                    category=LeadCategory.tender,
                )
            )
        return leads
