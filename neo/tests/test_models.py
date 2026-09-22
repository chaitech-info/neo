from app.models import LeadCategory, RawLead


def test_dedupe_key_is_stable_for_same_source_and_url():
    lead1 = RawLead(
        source="marja",
        source_url="https://marja.az/elan/123",
        title="Korporativ tədbir",
        raw_text="...",
        category=LeadCategory.event,
    )
    lead2 = RawLead(
        source="marja",
        source_url="https://marja.az/elan/123",
        title="Fərqli başlıq amma eyni link",
        raw_text="fərqli mətn",
        category=LeadCategory.other,
    )

    assert lead1.dedupe_key() == lead2.dedupe_key()


def test_dedupe_key_differs_for_different_url():
    lead1 = RawLead(
        source="marja", source_url="https://marja.az/elan/1", title="A", raw_text="x"
    )
    lead2 = RawLead(
        source="marja", source_url="https://marja.az/elan/2", title="A", raw_text="x"
    )

    assert lead1.dedupe_key() != lead2.dedupe_key()
