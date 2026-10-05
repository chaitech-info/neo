"""v2: model faktları verir, skoru kod hesablayır."""
import json
from types import SimpleNamespace

import pytest

from app.ai_scoring import StructuredAIFilter, compute_score
from app.models import LeadStatus, RawLead


def facts(signal, **kw):
    base = {"signal": signal, "organization": "X", "in_azerbaijan": True, "food_business": False,
            "workforce": "unknown", "food_mentioned": False, "evidence": "", "reason": "r"}
    return base | kw


@pytest.mark.parametrize("f, expected", [
    (facts("catering_tender"), (0.92, "A")),
    (facts("food_supply_tender"), (0.82, "A")),
    (facts("event_tender"), (0.7, "B")),
    (facts("event_tender", food_mentioned=True), (0.85, "A")),
    (facts("employer_meal_signal", workforce="large"), (0.8, "B")),
    (facts("employer_meal_signal", workforce="medium"), (0.7, "B")),
    (facts("employer_meal_signal"), (0.65, "C")),                       # ölçü bilinmir
    (facts("employer_meal_signal", workforce="small"), (0.62, "C")),
    (facts("new_facility", workforce="large"), (0.8, "B")),
    (facts("expansion_hiring"), (0.5, "C")),
    (facts("expansion_hiring", workforce="large"), (0.5, "C")),   # tək vakansiya böyük şirkətdə — keçmir
    # 2026-10-05 analizindəki səhvlər: restoran, xaricdə zavod, rəqib, tikinti tenderi
    (facts("employer_meal_signal", food_business=True), (0.05, "none")),
    (facts("new_facility", in_azerbaijan=False, workforce="large"), (0.05, "none")),
    (facts("competitor_or_food_business"), (0.1, "none")),
    (facts("works_or_goods_tender"), (0.1, "none")),
    (facts("irrelevant"), (0.0, "none")),
])
def test_compute_score(f, expected):
    assert compute_score(f, "mətn") == expected


def test_event_in_news_is_not_a_tender():
    assert compute_score(facts("event_tender"), "x", is_tender=False) == (0.5, "C")
    assert compute_score(facts("event_tender", food_mentioned=True), "x", is_tender=False) == (0.65, "C")
    assert compute_score(facts("event_tender"), "x", is_tender=True) == (0.7, "B")


def test_many_vacancies_lift_unknown_employer_to_b():
    text = "[SİQNAL]\nboss.az-da nahar qeyd olunan aktiv vakansiyalar (4): fəhlə, operator"
    assert compute_score(facts("employer_meal_signal"), text) == (0.7, "B")


def test_retail_chain_is_not_a_food_business():
    f = facts("employer_meal_signal", food_business=True, workforce="large", organization="OBA marketlər şəbəkəsi")
    assert compute_score(f, "x") == (0.8, "B")
    assert compute_score(facts("employer_meal_signal", food_business=True, organization="Lezzet Restoran"), "x")[0] == 0.05


def test_event_listing_is_indirect_even_with_food():
    assert compute_score(facts("event_tender", food_mentioned=True), "x", is_tender=True, listing=True) == (0.7, "B")


def test_expired_deadline_caps_score():
    assert compute_score(facts("catering_tender"), "x\n\n[SON TARİX: BİTİB — 3 gün əvvəl]") == (0.1, "none")


class _Client:
    def __init__(self, payload):
        self.calls = []
        content = json.dumps(payload)

        def create(**kw):
            self.calls.append(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                                   usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _lead():
    return RawLead(source="etender", source_url="https://etender.gov.az/1", title="Bayram tədbirlərinin təşkili", category="tender",
                   raw_text="Bayram tədbirlərinin təşkili, iştirakçılar üçün fürşet")


def test_structured_filter_scores_and_tags_tier():
    client = _Client(facts("event_tender", food_mentioned=True, organization="Şabran RİH", reason="Bayram tədbiri, fürşet"))
    lead = StructuredAIFilter(client=client, model="gpt-4.1-mini").score(_lead())
    assert (lead.ai_score, lead.status) == (0.85, LeadStatus.pending_review)
    assert (lead.ai_reason, lead.tier) == ("Bayram tədbiri, fürşet", "A") and lead.entity_name == "Şabran RİH"
    call = client.calls[0]
    assert call["response_format"]["type"] == "json_schema" and call["temperature"] == 0


def test_gpt5_models_use_reasoning_not_temperature():
    client = _Client(facts("irrelevant"))
    lead = StructuredAIFilter(client=client, model="gpt-5-mini").score(_lead())
    assert lead.status == LeadStatus.rejected
    assert "temperature" not in client.calls[0] and client.calls[0]["reasoning_effort"] == "minimal"


def test_ai_failure_sends_lead_to_human():
    class Broken:
        chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: 1 / 0))
    lead = StructuredAIFilter(client=Broken()).score(_lead())
    assert lead.ai_score is None and lead.status == LeadStatus.pending_review


def test_pipeline_uses_v2_when_configured(monkeypatch):
    from app import pipeline

    monkeypatch.setenv("AI_SCORER", "v2")
    assert isinstance(pipeline.make_scorer(), StructuredAIFilter)
    monkeypatch.setenv("AI_SCORER", "v1")
    assert isinstance(pipeline.make_scorer(), pipeline.AIFilter)


def test_negative_keyword_rejects_without_calling_ai(monkeypatch):
    from app import keywords

    monkeypatch.setattr(keywords, "terms_for",
                        lambda kind: ([], [keywords.Term("mətbəə")]) if kind == "tender" else ([], []))
    client = _Client(facts("food_supply_tender"))
    lead = RawLead(source="etender", source_url="https://etender.gov.az/9", title="Mətbəə malları",
                   raw_text="Mətbəə malları", category="tender")
    lead = StructuredAIFilter(client=client).score(lead)
    assert (lead.ai_score, lead.status) == (0.0, LeadStatus.rejected) and "mətbəə" in lead.ai_reason
    assert client.calls == []
