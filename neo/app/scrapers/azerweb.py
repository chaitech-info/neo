from __future__ import annotations

from app.models import LeadCategory, RawLead
from app.scrapers.base import BaseScraper

# azerweb.az əslində "İş elanları, Tenderlər və Təhsil" platformasıdır (React
# SPA + JSON API — ilk fərziyyə "elan saytı" səhv idi, brauzerlə yoxlanıb
# düzəldilib). API-nin öz `q=` axtarışından istifadə edirik (server-side
# filtr, keyword-lə HTML qazmaqdan daha etibarlı). `type=tender` ilə
# məhdudlaşdırırıq — "catering" sözü VACANCY elanlarında da çıxır (məs.
# "Chef", "Housekeeper" iş elanları), amma bunlar bizim üçün lead deyil, iş
# axtaran işçidir. Yalnız tender/elan (potensial sifarişçi) maraqlıdır.
API_URL = "https://azerweb.az/api/announcements/"
DETAIL_URL = "https://azerweb.az/announcements/{id}"

SEARCH_TERMS = ["catering", "ictimai iaşə", "yemək təchizatı", "banket"]
RELEVANT_TYPES = {"tender", "announcement"}


class AzerwebScraper(BaseScraper):
    source_name = "azerweb"
    base_url = "https://azerweb.az"

    def fetch_leads(self) -> list[RawLead]:
        leads: list[RawLead] = []
        seen_ids: set[int] = set()

        for term in SEARCH_TERMS:
            resp = self.get(API_URL, params={"page_size": 30, "q": term})
            data = resp.json()

            for item in data.get("results", []):
                item_id = item.get("id")
                if item_id in seen_ids or item.get("type") not in RELEVANT_TYPES:
                    continue
                seen_ids.add(item_id)

                leads.append(
                    RawLead(
                        source="azerweb",
                        source_url=DETAIL_URL.format(id=item_id),
                        title=item.get("title", ""),
                        raw_text=f"{item.get('title', '')} — {item.get('company', '')}",
                        entity_name=item.get("company") or None,
                        category=(
                            LeadCategory.tender
                            if item.get("type") == "tender"
                            else LeadCategory.corporate
                        ),
                        location=item.get("location") or None,
                    )
                )
            self.polite_sleep()

        return leads
