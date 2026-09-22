from __future__ import annotations

import os

import httpx
import structlog

from app.models import RawLead

log = structlog.get_logger()


class CRMClient:
    """CRM-in `Opportunities` (Leads) endpoint-i ilə inteqrasiya. Sənəd §9.

    TODO(dev): dəqiq endpoint yolu və autentifikasiya (API key / OAuth)
    CRM development komandası ilə dəqiqləşdirilməlidir — bax sənəd §9
    açıq sual.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = base_url or os.environ.get("CRM_BASE_URL", "https://crm.internal/api")
        self.api_key = api_key or os.environ.get("CRM_API_KEY", "")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15.0,
        )

    def create_opportunity(self, lead: RawLead) -> str | None:
        payload = {
            "entity_name": lead.entity_name or lead.title,
            "source": lead.source.value,
            "source_url": lead.source_url,
            "category": lead.category.value,
            "ai_score": lead.ai_score,
            "ai_reason": lead.ai_reason,
            "contact_info": lead.contact_info.model_dump() if lead.contact_info else None,
            "location": lead.location,
        }
        try:
            resp = self._client.post("/opportunities", json=payload)
            resp.raise_for_status()
            data = resp.json()
            opportunity_id = data.get("id")
            log.info(
                "crm_opportunity_created",
                source=lead.source,
                url=lead.source_url,
                opportunity_id=opportunity_id,
            )
            return opportunity_id
        except httpx.HTTPError:
            log.exception("crm_opportunity_create_failed", source=lead.source, url=lead.source_url)
            return None
