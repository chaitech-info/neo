from __future__ import annotations

import json
import os

import structlog
from openai import OpenAI

from app.models import LeadStatus, RawLead

log = structlog.get_logger()

SCORE_THRESHOLD = 0.6

SYSTEM_PROMPT = """\
Sən Azərbaycandakı bir catering şirkəti üçün potensial müştəri (lead) qiymətləndirirsən.

Şirkətin əsas məhsulu: şirkətlərin əməkdaşları/işçiləri üçün gündəlik hazır
yemək — paketlənmiş/porsiyalı nahar (lunch box, qab yeməyi), toplu çatdırılma.
Müştərilər: ofislər, zavodlar, istehsal müəssisələri, tikinti obyektləri,
anbarlar, sənaye parkları, dövlət qurumları. İkinci dərəcəli: korporativ tədbir
və banket yeməkləri.

Sənə elan/xəbər/tender/LinkedIn post mətni veriləcək. Bu mətnin Azərbaycanda
BİZDƏN yemək ALA BİLƏCƏK bir təşkilata işarə etmə ehtimalını qiymətləndir:

0.8-1.0: təşkilat açıq şəkildə əməkdaşları üçün yemək/catering/iaşə təchizatçısı
  axtarır, və ya işçi qidalanması üzrə tender/satınalma elan edir.
0.6-0.8: korporativ tədbir/banket/konfrans üçün catering ehtiyacı; və ya çoxlu
  işçisi olan yeni zavod/ofis/tikinti layihəsinin Azərbaycanda açılması.
0.3-0.6: dolayı siqnal (yeni restoran, böyüyən şirkət), amma yemək ehtiyacı aydın deyil.
0.0-0.2: aşağıdakılardan biridirsə —
  - müəllif özü catering/yemək xidməti REKLAM EDİR və ya təklif edir (rəqibdir, alıcı deyil);
  - iş elanı (vakansiya), könüllü axtarışı, şəxsi post, sektor haqqında fikir yazısı;
  - informativ/izah yazısı (vergi, mühasibat, qanunvericilik, xəbər şərhi) — mövzu
    yemək olsa belə, konkret təşkilat yemək almaq istəmirsə;
  - yalnız avadanlıq (mətbəx avadanlığı və s.) alınır, yemək xidməti yox;
  - Azərbaycandan kənarda.

Yalnız bu JSON formatında cavab ver, başqa heç nə yazma:
{"score": 0.0-1.0 arası float, "reason": "bir cümləlik qısa əsaslandırma, Azərbaycan dilində"}
"""


class AIFilter:
    """Sənəd §6 Lead scoring & AI filtering-in tətbiqi."""

    def __init__(self, client: OpenAI | None = None, model: str = "gpt-4o-mini") -> None:
        self.client = client or OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self.model = model

    def score(self, lead: RawLead) -> RawLead:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=200,
                # Eyni post eyni skoru almalıdır — defolt temperature-da vergi
                # izahı postu bir dəfə 0.0, bir dəfə 0.6 (eşik) almışdı.
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": lead.raw_text[:4000]},
                ],
            )
            text = response.choices[0].message.content.strip()
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
