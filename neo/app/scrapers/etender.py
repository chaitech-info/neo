from __future__ import annotations

from datetime import datetime

import structlog

from app.models import LeadCategory, RawLead
from app.scrapers.base import TENDER_KEYWORDS, BAKU_TZ, BaseScraper, parse_datetime

# Real API (brauzerlə yoxlanıb): GET /api/events. robots.txt yoxdur (SPA HTML
# qaytarır). Detay səhifəsi: /main/competition/detail/<eventId>.
#
# 2026-09-27 dəyişikliyi: əvvəl 5 sözlə server axtarışı edilirdi ("catering",
# "iaşə", "yemək", "banket", "ictimai iaşə") — 1707 aktiv tenderdən cəmi 4-ü
# qayıdırdı, heç biri real fürsət deyildi (avadanlıq, təmir, fevralda bitmiş).
# Bütün tədbir təşkili tenderləri (bayram, komandaqurma, festival — təşkilatçı
# yemək alır) buraxılırdı. İndi BÜTÜN aktiv tenderlər oxunur (100-lük səhifə,
# ~18 sorğu), son tarixi keçənlər atılır (EventStatus=1 onları da qaytarır!),
# qalanı açar söz + AI triajı ilə seçilir (BaseScraper.select_relevant).
log = structlog.get_logger()

API_URL = "https://etender.gov.az/api/events"
DETAIL_URL = "https://etender.gov.az/main/competition/detail/{id}"
PAGE_SIZE = 100

BASE_PARAMS = {
    "EventType": 2,
    "PageSize": PAGE_SIZE,
    "PageNumber": 1,
    "EventStatus": 1,  # davam edən
    "Keyword": "",
    "buyerOrganizationName": "",
    "documentNumber": "",
    "publishDateFrom": "",
    "publishDateTo": "",
    "AwardedparticipantName": "",
    "AwardedparticipantVoen": "",
    "DocumentViewType": "",
    "IsArchived": "false",
}


class EtenderScraper(BaseScraper):
    source_name = "etender"
    base_url = "https://etender.gov.az"
    min_delay_s = 2.0
    max_delay_s = 4.0
    max_pages = 40

    @staticmethod
    def now() -> datetime:
        return datetime.now(BAKU_TZ).replace(tzinfo=None)

    def fetch_leads(self) -> list[RawLead]:
        now = self.now()
        open_items: list[dict] = []
        seen: set[int] = set()
        expired = total = 0

        for page in range(1, self.max_pages + 1):
            if page > 1:
                self.polite_sleep()
            data = self.get(API_URL, params={**BASE_PARAMS, "PageNumber": page}).json()
            for item in data.get("items", []):
                event_id = item.get("eventId")
                if event_id in seen:
                    continue
                seen.add(event_id)
                total += 1
                # endDate Bakı vaxtıdır (saat qurşağısız). Son tarix keçibsə — müraciət olmur.
                end = _naive(item.get("endDate"))
                if end and end < now:
                    expired += 1
                    continue
                open_items.append(item)
            if not data.get("hasNextPage"):
                break

        keep = self.select_relevant(
            [f"{i.get('eventName', '')} — {i.get('buyerOrganizationName', '')}" for i in open_items],
            TENDER_KEYWORDS,
            kind="tender",
        )
        leads = [self._to_lead(item) for item, selected in zip(open_items, keep) if selected]
        log.info("etender_scanned", source=self.source_name, total=total, expired=expired, open=len(open_items), leads=len(leads))
        return leads

    @staticmethod
    def _to_lead(item: dict) -> RawLead:
        name = (item.get("eventName") or "").strip()
        buyer = (item.get("buyerOrganizationName") or "").strip()
        end = _naive(item.get("endDate"))
        raw_text = f"{name}\n\nSifarişçi: {buyer}"
        if end:
            raw_text += f"\nSon tarix: {end.strftime('%d.%m.%Y %H:%M')}"
        return RawLead(
            source="etender",
            source_url=DETAIL_URL.format(id=item.get("eventId")),
            title=name,
            raw_text=raw_text,
            entity_name=buyer or None,
            category=LeadCategory.tender,
            posted_at=parse_datetime(item.get("publishDate")),
        )


def _naive(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:19])
    except ValueError:
        return None
