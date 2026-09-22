from __future__ import annotations

from app.models import LeadCategory, RawLead
from app.scrapers.base import BaseScraper

# Real API (brauzerlə yoxlanıb): GET /api/events?...&Keyword=<söz> server-side
# axtarış dəstəkləyir. Detay səhifəsi: /main/competition/detail/<eventId>.
API_URL = "https://etender.gov.az/api/events"
DETAIL_URL = "https://etender.gov.az/main/competition/detail/{id}"

SEARCH_TERMS = ["catering", "iaşə", "yemək", "banket", "ictimai iaşə"]

BASE_PARAMS = {
    "EventType": 2,
    "PageSize": 20,
    "PageNumber": 1,
    "EventStatus": 1,  # davam edən
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
    min_delay_s = 3.0
    max_delay_s = 6.0

    def fetch_leads(self) -> list[RawLead]:
        leads: list[RawLead] = []
        seen_ids: set[int] = set()

        for term in SEARCH_TERMS:
            params = {**BASE_PARAMS, "Keyword": term}
            resp = self.get(API_URL, params=params)
            data = resp.json()

            for item in data.get("items", []):
                event_id = item.get("eventId")
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)

                leads.append(
                    RawLead(
                        source="etender",
                        source_url=DETAIL_URL.format(id=event_id),
                        title=item.get("eventName", ""),
                        raw_text=f"{item.get('eventName', '')} — {item.get('buyerOrganizationName', '')}",
                        entity_name=item.get("buyerOrganizationName") or None,
                        category=LeadCategory.tender,
                    )
                )
            self.polite_sleep()

        return leads
