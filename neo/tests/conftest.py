import pytest


@pytest.fixture(autouse=True)
def no_real_ai_triage(monkeypatch):
    """Testlər OpenAI-yə getməsin: triaj defolt olaraq "AI yoxdur" (None) qaytarır,
    scraper-lər yalnız açar sözlərlə işləyir. Triajı yoxlayan test bunu öz
    monkeypatch-i ilə əvəz edir."""
    monkeypatch.setattr("app.ai_triage.triage", lambda titles, kind="news", client=None: None)
