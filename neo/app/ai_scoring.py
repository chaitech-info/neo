"""Strukturlaşdırılmış qiymətləndirmə (v2) — model faktları çıxarır, skoru KOD hesablayır.

v1 (app/ai_filter.py) modeldən birbaşa "score" istəyirdi. 2026-10-05 analizi:
skor səbəblə ziddiyyət təşkil edirdi ("öz mətbəxində bişirir, alıcı deyil" → 0.7),
eyni hadisə (ASELSAN zavodu) bir saytda 0.7, digərində 0.3 alırdı, restoran /
xaricdəki zavod / rəqib keçirdi, mədən və zavod xəbərləri rədd olunurdu.

v2-də model yalnız mətndə yazılanı sahələrə ayırır (siqnal növü, təşkilat,
Azərbaycandadırmı, yemək biznesidirmi, işçi sayı, sübut-sitat); skor və A/B/C
kateqoriyası `compute_score`-da deterministik qaydalarladır — eyni faktlar eyni
nəticə verir, qayda dəyişmək üçün promptu yox, kodu dəyişmək kifayətdir.
Ölçmə: scripts/eval_filter.py. Bax docs/knowledge-base/04-decisions.md → "Phase 1".
"""
from __future__ import annotations

import json
import os
import re

import structlog
from openai import OpenAI

from app.ai_filter import (
    DEFAULT_COMPANY,
    OPENAI_MAX_RETRIES,
    SCORE_THRESHOLD,
    annotate_deadline,
    today_baku,
)
from app.models import LeadStatus, RawLead

log = structlog.get_logger()

DEFAULT_MODEL = "gpt-4.1-mini"

SIGNALS = [
    "catering_tender",        # catering / yemək xidməti / qidalanma tenderi və ya sorğusu
    "food_supply_tender",     # ərzaq / qida məhsulları alışı
    "event_tender",           # tədbir TƏŞKİLİ tenderi
    "employer_meal_signal",   # işəgötürən işçilərinə nahar verir
    "new_facility",           # adı çəkilən şirkət AZ-da zavod/mədən/obyekt açır, tikir
    "expansion_hiring",       # böyük işəgötürən genişlənir, kütləvi və ya açar vəzifəyə işə qəbul
    "competitor_or_food_business",  # restoran/kafe/otel/catering — öz yeməyini bişirir və ya rəqibdir
    "works_or_goods_tender",  # tikinti/təmir işi, avadanlıq, mebel, İT, dərman alışı
    "past_event",             # keçmiş tədbirin reportajı
    "irrelevant",
]
WORKFORCE = ["unknown", "small", "medium", "large"]  # <20, 20-200, 200+

# Siqnal → (baza skor, kateqoriya). Kateqoriya: A — birbaşa yemək tələbatı indi,
# B — güclü dolayı, C — zəif/erkən. Eşik 0.6 (SCORE_THRESHOLD).
BASE = {
    "catering_tender": (0.92, "A"),
    "food_supply_tender": (0.82, "A"),
    "event_tender": (0.70, "B"),
    "employer_meal_signal": (0.70, "B"),
    "new_facility": (0.70, "B"),
    # 2026-10-05 qızıl etiketlərdə relevant expansion_hiring YOXDUR; model tək vakansiya
    # xəbərlərini (fed.az "X işçi axtarır") "kütləvi qəbul" sayırdı → eşikdən aşağı.
    "expansion_hiring": (0.50, "C"),
}
LOW = 0.10
# Pərakəndə/supermarket şəbəkələri ərzaq SATIR, işçisinə yemək bişirmir — model promptdakı
# qeydə baxmayaraq OBA-nı "food_business" sayırdı (2026-10-05). Adına görə kodla düzəlir.
_VACANCY_COUNT = re.compile(r"vakansiyalar?\s*\((\d+)\)|\((\d+)\s+vakansiya")
_RETAIL_NAME = re.compile(r"market|mağaza|magaza|maqaz|store|супермаркет|магазин", re.IGNORECASE)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["signal", "organization", "in_azerbaijan", "food_business", "workforce",
                 "food_mentioned", "evidence", "reason"],
    "properties": {
        "signal": {"type": "string", "enum": SIGNALS},
        "organization": {"type": "string"},
        "in_azerbaijan": {"type": "boolean"},
        "food_business": {"type": "boolean"},
        "workforce": {"type": "string", "enum": WORKFORCE},
        "food_mentioned": {"type": "boolean"},
        "evidence": {"type": "string"},
        "reason": {"type": "string"},
    },
}

PROMPT = """\
You extract facts from Azerbaijani/Russian/English tenders, news, job ads and
posts for the sales team of an Azerbaijani catering company.

{company}

Fill the fields ONLY from what is written; never invent.
You CLASSIFY — you do NOT decide whether it is useful for catering; code scores it.
Never pick "irrelevant" because food is not mentioned: e.g. a tender to organize
a league, forum, festival or competition is event_tender even with no word about
food (organizers buy food anyway).

signal — pick the ONE that best describes the item:
- catering_tender: an organization tenders/requests catering, meals, food service,
  canteen operation, or feeding of a school/kindergarten/hospital/army unit/camp/dormitory.
- food_supply_tender: an institution buys FOOD FOR PEOPLE — food products,
  groceries, drinks, drinking water. NOT printing goods, fertilizer, seeds,
  plants, animal feed, chemicals, kitchen equipment. Careful: "mətbəə" means
  PRINTING HOUSE (printing goods) — it is NOT "mətbəx" (kitchen).
- event_tender: a TENDER/PROCUREMENT to organize/hold an UPCOMING event (holiday,
  festival, forum, conference, competition, ceremony, anniversary, team building,
  reception of guests) or to deliver training/courses/seminars for a group of
  participants — "Təlim xidmətlərinin satın alınması" (purchase of training
  services) counts: trainees get coffee breaks/lunch. If organizing the event is part of the purchase, it is
  event_tender even when gifts or decorations are bought too. A purchase of ONLY
  gifts, printing, stage or exhibition-stand construction is works_or_goods_tender.
  News about an event is not a tender: an upcoming event in news with a named
  organizer may be event_tender; a finished event is past_event.
- employer_meal_signal: the employer says it provides lunch/meals to staff
  (text may start with "[SİQNAL: ...]").
- new_facility: a NAMED company opens, builds, launches production at, or expands
  a factory, plant, mine/deposit, industrial-park facility, logistics hub, large
  office or branch network that will have its own workforce. Includes "N new jobs
  will be created" at a named site. Roads, transport lines, housing handovers,
  cable cars, transit volumes and general economic statements are irrelevant.
- expansion_hiring: a named employer hires MANY people at once (10+ positions,
  "kütləvi işə qəbul") or hires a role that implies on-site staff feeding
  (canteen/camp/facility manager, chef, cook for staff). One or a few ordinary
  vacancies (accountant, sales, engineer, teacher, intern) are irrelevant.
- competitor_or_food_business: the subject is a restaurant, cafe, hotel, bakery,
  catering or food-delivery business (opening, hiring, advertising) — they cook
  for their own staff or compete with us. Food MANUFACTURING plants (meat,
  poultry, sweets, dairy) are NOT this: use new_facility/employer_meal_signal.
- works_or_goods_tender: construction, repair, landscaping, roads, utilities,
  equipment, furniture, vehicles, IT, printing, medicine purchases.
- past_event: report about an event that already happened.
- irrelevant: everything else (politics, sports, crime, weather, prices,
  statistics, generic investment talk without a named site, single ordinary
  vacancy without a meal signal).

in_azerbaijan — WHERE the facility/site/event physically is. False if it is
abroad, even when the company or participants are Azerbaijani (an Azerbaijani
company's refinery in Congo, Azerbaijani boxers at a championship in Europe).
food_business — the organization COOKS AND SERVES MEALS TO CUSTOMERS as its
business (restaurant, cafe, hotel, catering, canteen operator, food delivery).
FALSE for food MANUFACTURERS and producers (confectionery, bakery factory, meat,
dairy, flour, brewery, agro, FMCG) — they are factories that need staff lunches.
FALSE for supermarkets, grocery/retail chains and shops (OBA, Bravo, Araz...) —
they sell groceries, they do not cook meals for their staff.
workforce — staff size of the organization/site: small (<20), medium (20-200),
large (200+), unknown. Factories, mines, plants, construction sites, state
bodies, holdings, networks with many branches are usually medium/large.
food_mentioned — the text explicitly mentions food, meals, catering, buffet,
lunch, coffee break or similar.
evidence — short exact quote from the text supporting the signal ("" if none).
reason — one sentence in AZERBAIJANI for the sales team: who, what, why it
matters (or why not).
"""


def compute_score(facts: dict, text: str, is_tender: bool = True, listing: bool = False) -> tuple[float, str]:
    """Faktlardan skor və A/B/C. Kateqoriya "none" = rədd.
    `is_tender` — mənbə tender/satınalmadır; xəbərdəki tədbir (forum keçirildi/keçiriləcək)
    tender deyil — yalnız yemək açıq yazılıbsa keçir."""
    signal = facts.get("signal")
    if signal not in BASE:
        return LOW if signal != "irrelevant" else 0.0, "none"
    food_business = facts.get("food_business") and not _RETAIL_NAME.search(facts.get("organization") or "")
    if not facts.get("in_azerbaijan", True) or food_business:
        return 0.05, "none"
    score, tier = BASE[signal]
    workforce = facts.get("workforce", "unknown")
    if signal == "event_tender" and listing:
        score, tier = 0.70, "B"          # sərgi təqvimi: tədbir var, amma catering sorğusu yox — dolayı
    elif signal == "event_tender" and not is_tender:
        score, tier = (0.65, "C") if facts.get("food_mentioned") else (0.50, "C")
    elif signal == "event_tender" and facts.get("food_mentioned"):
        score, tier = 0.85, "A"          # tədbir tenderi yeməyi açıq yazır → birbaşa tələbat
    elif signal == "employer_meal_signal":
        # 2026-10-06 qızıl etiketlərlə razılaşdırılıb: ölçüsü bilinməyən tək-iki vakansiyalı
        # işəgötürən C-dir (54-dən 46-sı); B — böyük/orta və ya bir neçə vakansiyalı şirkət.
        vacancies = max((int(a or b) for a, b in _VACANCY_COUNT.findall(text)), default=1)
        if workforce == "large":
            score, tier = 0.80, "B"
        elif workforce == "medium" or vacancies >= 3:
            score, tier = 0.70, "B"
        elif workforce == "small":
            score, tier = 0.62, "C"
        else:
            score, tier = 0.65, "C"
    elif signal == "new_facility":
        if workforce == "large":
            score += 0.10
        elif workforce == "small":
            score, tier = score - 0.08, "C"
    if "[SON TARİX: BİTİB" in text:
        return min(score, LOW), "none"
    return round(min(score, 1.0), 2), tier


def chat_json(client: OpenAI, model: str, system: str, user: str, schema: dict, name: str = "out"):
    """Model ailəsinə görə parametrlər: gpt-5 — reasoning, temperature yox."""
    kwargs = {
        "model": model,
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = os.environ.get("AI_REASONING_EFFORT", "minimal")
    else:
        kwargs["temperature"] = 0
        kwargs["max_tokens"] = 400
    resp = client.chat.completions.create(**kwargs)
    return json.loads(resp.choices[0].message.content), resp.usage


class StructuredAIFilter:
    """AIFilter-in v2 əvəzi — eyni interfeys (score, score_batch)."""

    version = "v2"

    def __init__(self, client: OpenAI | None = None, model: str | None = None) -> None:
        self._client = client
        self.model = model or os.environ.get("AI_MODEL_V2") or DEFAULT_MODEL
        self.last_usage = None
        self.last_facts: dict | None = None  # ölçmə üçün (scripts/eval_filter.py)

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), max_retries=OPENAI_MAX_RETRIES)
        return self._client

    def system_prompt(self) -> str:
        from app.keywords import company_profile

        return PROMPT.replace("{company}", company_profile() or DEFAULT_COMPANY)

    def facts(self, lead: RawLead) -> dict:
        text = annotate_deadline(lead.raw_text[:4000], today_baku())
        facts, self.last_usage = chat_json(self.client, self.model, self.system_prompt(),
                                           f"TITLE: {lead.title}\n\n{text}", SCHEMA, "facts")
        facts["_text"] = text
        return facts

    @staticmethod
    def blocked_by_negative(lead: RawLead) -> str | None:
        """Paneldəki mənfi açar söz başlıqda varsa (məs. "mətbəə") — AI-yə getmədən rədd.
        Triaj da bunu edir, amma yenidən skorlama (scripts/rescore_v2.py) triajı keçmir."""
        from app import keywords

        kind = "tender" if lead.category.value == "tender" else "news"
        terms = keywords.terms_for(kind)
        if not terms:
            return None
        hit = next((t.text for t in terms[1] if keywords.matches(lead.title, t)), None)
        return hit

    def score(self, lead: RawLead) -> RawLead:
        hit = self.blocked_by_negative(lead)
        if hit:
            lead.ai_score, lead.status = 0.0, LeadStatus.rejected
            lead.ai_reason = f"Mənfi açar söz: «{hit}» — avtomatik rədd"
            return lead
        try:
            facts = self.facts(lead)
            self.last_facts = {k: v for k, v in facts.items() if k != "_text"}
            score, tier = compute_score(facts, facts.pop("_text"), is_tender=lead.category.value in ("tender", "event_listing"),
                                         listing=lead.category.value == "event_listing")
            lead.ai_score = score
            org = facts.get("organization") or ""
            lead.ai_reason = facts.get("reason", "")
            lead.tier = tier if tier != "none" and score >= SCORE_THRESHOLD else None
            if org and not lead.entity_name:
                lead.entity_name = org[:200]
            lead.status = LeadStatus.pending_review if score >= SCORE_THRESHOLD else LeadStatus.rejected
            log.info("ai_scored", source=lead.source, score=score, tier=tier, signal=facts.get("signal"),
                     status=lead.status.value, title=lead.title[:90], scorer="v2", model=self.model)
        except Exception:
            log.exception("ai_scoring_failed", source=lead.source, url=lead.source_url, scorer="v2")
            lead.ai_score = None
            lead.ai_reason = "AI qiymətləndirməsi alınmadı — əl ilə yoxlanmalıdır"
            lead.status = LeadStatus.pending_review
        return lead

    def score_batch(self, leads: list[RawLead]) -> list[RawLead]:
        return [self.score(lead) for lead in leads]

