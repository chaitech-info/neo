from __future__ import annotations

import json
import os

import structlog
from anthropic import Anthropic

from app.models import LeadStatus, RawLead

log = structlog.get_logger()

SCORE_THRESHOLD = 0.6

SYSTEM_PROMPT = """\
Sən catering şirkəti üçün potensial müştəri (lead) qiymətləndirən köməkçisən.
Sənə bir elan/xəbər/tender mətni veriləcək. Bu mətnin catering (yemək təchizatı,
banket, korporativ tədbir, konfrans yeməkləri və s.) ehtiyacına nə dərəcədə
işarə etdiyini qiymətləndir.

Yalnız bu JSON formatında cavab ver, başqa heç nə yazma:
{"score": 0.0-1.0 arası float, "reason": "bir cümləlik qısa əsaslandırma, Azərbaycan dilində"}
"""


class AIFilter:
    """Sənəd §6 Lead scoring & AI filtering-in tətbiqi."""

    def __init__(self, client: Anthropic | None = None, model: str = "claude-sonnet-5") -> None:
        self.client = client or Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model

    def score(self, lead: RawLead) -> RawLead:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": lead.raw_text[:4000]}],
            )
            text = response.content[0].text.strip()
            parsed = json.loads(text)
            lead.ai_score = float(parsed["score"])
            lead.ai_reason = str(parsed["reason"])
            lead.status = (
                LeadStatus.pending_review
                if lead.ai_score >= SCORE_THRESHOLD
                else LeadStatus.rejected
            )
        except Exception:
            # AI qiymətləndirməsi alınmadı (API key yoxdur, LLM xətası və s.) —
            # lead SƏSSİZCƏ RƏDD EDİLMİR: insan özü baxsın deyə pending_review-a
            # düşür, ai_score=None qalır ("—" kimi göstərilir admin paneldə).
            log.exception("ai_scoring_failed", source=lead.source, url=lead.source_url)
            lead.ai_score = None
            lead.ai_reason = "AI qiymətləndirməsi alınmadı — əl ilə yoxlanmalıdır"
            lead.status = LeadStatus.pending_review

        return lead

    def score_batch(self, leads: list[RawLead]) -> list[RawLead]:
        return [self.score(lead) for lead in leads]
