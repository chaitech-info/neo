from types import SimpleNamespace

from app.ai_filter import AIFilter
from app.models import LeadStatus, RawLead


class FakeOpenAI:
    def __init__(self, content: str) -> None:
        self.calls = []
        create = lambda **kwargs: self.calls.append(kwargs) or SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _lead() -> RawLead:
    return RawLead(source="oxu", source_url="https://oxu.az/a/b", title="T", raw_text="200 nəfərlik banket")


def test_high_score_goes_to_pending_review():
    client = FakeOpenAI('{"score": 0.85, "reason": "Banket"}')
    lead = AIFilter(client=client, model="gpt-4o-mini").score(_lead())

    assert (lead.ai_score, lead.ai_reason, lead.status) == (0.85, "Banket", LeadStatus.pending_review)
    assert client.calls[0]["model"] == "gpt-4o-mini"
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_low_score_is_rejected():
    lead = AIFilter(client=FakeOpenAI('{"score": 0.1, "reason": "Əlaqəsiz"}')).score(_lead())

    assert lead.status == LeadStatus.rejected


def test_missing_api_key_keeps_lead_for_manual_review(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    lead = AIFilter().score(_lead())

    assert lead.ai_score is None
    assert lead.status == LeadStatus.pending_review


def test_system_prompt_carries_todays_date():
    from datetime import date

    client = FakeOpenAI('{"score": 0.05, "reason": "Son tarix keçib"}')
    ai = AIFilter(client=client)
    ai.today = lambda: date(2026, 9, 23)

    ai.score(_lead())

    system = client.calls[0]["messages"][0]["content"]
    assert "Bu gün: 23.09.2026" in system
    assert '{"score"' in system  # JSON nümunəsi yerində qalıb
