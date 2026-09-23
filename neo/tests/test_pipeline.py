import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repository import LeadRepository
from app.models import RawLead
from app.pipeline import reject_lead, restore_lead


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _add(session, url: str, status: str) -> str:
    lead = RawLead(source="oxu", source_url=url, title="T", raw_text="x", status=status)
    key = lead.dedupe_key()
    LeadRepository(session).save(key, lead)
    return key


@pytest.mark.parametrize(
    "status, rejected",
    [("pending_review", True), ("sent_to_crm", False), ("rejected", False), ("new", False)],
)
def test_reject_only_from_pending_review(session, status, rejected):
    key = _add(session, "https://oxu.az/a/1", status)

    assert reject_lead(key, session) is rejected
    assert LeadRepository(session).get(key).status == ("rejected" if rejected else status)


def test_reject_unknown_lead(session):
    assert reject_lead("0" * 64, session) is False


def test_restore_only_from_rejected(session):
    sent = _add(session, "https://oxu.az/a/1", "sent_to_crm")
    rejected = _add(session, "https://oxu.az/a/2", "rejected")

    assert restore_lead(sent, session) is False
    assert restore_lead(rejected, session) is True
    assert LeadRepository(session).get(rejected).status == "pending_review"
