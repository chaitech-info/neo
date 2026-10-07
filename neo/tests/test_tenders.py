from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.backfill import backfill
from app.db.migrate import upgrade_to_head
from app.db.models import LeadRecord, Organization, ScrapeRun, Tender
from app.models import LeadCategory, LeadStatus, RawLead
from app.pipeline import PipelineResult, run_pipeline
from app.tenders import extract_voen, fingerprint, link_tender, normalize_org, normalize_title, reference_for

DEADLINE = datetime(2026, 10, 7, 19, 59)


def test_normalization_ignores_legal_form_quotes_and_noise():
    assert normalize_org('"ƏSASLI TİKİNTİ VƏ TƏCHİZAT" MƏHDUD MƏSULİYYƏTLİ CƏMİYYƏTİ') == "əsasli tikinti və təchizat"
    assert normalize_org("Əsaslı Tikinti və Təchizat MMC") == "əsasli tikinti və təchizat"
    assert normalize_title("Hazır yemək (keytrinq) xidmətlərinin satın alınması üzrə TENDER ELANI") == \
        normalize_title("hazır yemək keytrinq xidmətlərinin satınalınması")


def test_short_titles_get_no_fingerprint():
    assert fingerprint("Azəriqaz", "Kabel") is None
    assert fingerprint("Azəriqaz", "Kabel və naqillərin satın alınması") is not None


def test_references_and_voen():
    assert reference_for("etender", "https://etender.gov.az/main/competition/detail/345340") == "etender:345340"
    assert reference_for("bp_edp", "https://edp.az/Tender/Announcements#EDP-4-9/30/2026") == "bp_edp:EDP-4-9/30/2026"
    assert reference_for("oxu", "https://oxu.az/x") is None
    assert extract_voen("Rəsm Qalereyası (VÖEN: 1301255821) tender elan edib") == "1301255821"


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    upgrade_to_head(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _record(session, source, url, title, org=None, deadline=DEADLINE, category="tender", scraped_at=None):
    lead = RawLead(source=source, source_url=url, title=title, raw_text=title, entity_name=org,
                   category=category, deadline_at=deadline)
    rec = LeadRecord(dedupe_key=lead.dedupe_key(), source=source, source_url=url, title=title, raw_text=title,
                     entity_name=org, category=category, scraped_at=scraped_at or datetime.utcnow(),
                     status="new", deadline_at=deadline)
    session.add(rec)
    session.flush()
    return rec


TITLE = '"Şuşa 2026" çərçivəsində bağlanış mərasiminin təşkili ilə bağlı xidmətlərin satın alınması'


def test_same_tender_on_two_sources_becomes_duplicate(session):
    first = _record(session, "corporate", "https://tourism.gov.az/a.pdf", TITLE, "Dövlət Turizm Agentliyi")
    assert link_tender(session, first) is False
    second = _record(session, "etender", "https://etender.gov.az/main/competition/detail/1",
                     TITLE + " – TENDER ELANI", "DÖVLƏT TURİZM AGENTLİYİ", deadline=DEADLINE + timedelta(hours=10))

    assert link_tender(session, second) is True
    assert second.status == "duplicate" and second.duplicate_of == first.dedupe_key
    assert second.tender_id == first.tender_id
    assert session.execute(select(Tender)).scalars().one().reference_no == "etender:1"  # sonradan doldurulur


def test_different_deadline_is_a_different_tender(session):
    first = _record(session, "corporate", "https://x/a", TITLE, "Agentlik")
    link_tender(session, first)
    again = _record(session, "etender", "https://etender.gov.az/main/competition/detail/2", TITLE, "Agentlik",
                    deadline=DEADLINE + timedelta(days=30))
    assert link_tender(session, again) is False
    assert again.tender_id != first.tender_id


def test_same_reference_matches_even_with_different_title(session):
    a = _record(session, "etender", "https://etender.gov.az/main/competition/detail/7", "Yemək xidməti", "Nazirlik")
    link_tender(session, a)
    tender = session.get(Tender, a.tender_id)
    b = _record(session, "fed", "https://fed.az/x", "Nazirlik keytrinq alır", None)
    tender.reference_no = "etender:7"
    b_ref_record = _record(session, "etender", "https://etender.gov.az/main/competition/detail/7?v=2",
                           "Başqa ad", "Nazirlik")
    assert link_tender(session, b_ref_record) is True
    assert link_tender(session, b) is False  # fed məqaləsi: nə nömrə, nə eyni başlıq — ayrı qalır (bilinən hədd)


def test_non_tender_leads_are_ignored_and_voen_creates_organization(session):
    news = _record(session, "oxu", "https://oxu.az/n", TITLE, category="event")
    assert link_tender(session, news) is False and news.tender_id is None

    fed = _record(session, "fed", "https://fed.az/t", "Dövlət qurumu keytring xidməti alır – TENDER ELANI")
    fed.raw_text = "Başlıq\n\nMədəniyyət Nazirliyinin Rəsm Qalereyası (VÖEN: 1301255821) tender elan edib."
    link_tender(session, fed)
    org = session.execute(select(Organization)).scalars().one()
    assert org.voen == "1301255821" and "Rəsm Qalereyası" in org.name


class _FakeAI:
    scored = []

    def score_batch(self, leads):
        for lead in leads:
            _FakeAI.scored.append(lead.source_url)
            lead.ai_score, lead.ai_reason, lead.status = 0.9, "test", LeadStatus.pending_review
        return leads


def _scraper(source, url, title, org):
    class S:
        source_name = source

        def run(self):
            return [RawLead(source=source, source_url=url, title=title, entity_name=org,
                            raw_text=f"{title}\n\nSifarişçi: {org}\nSon tarix: 07.10.2026",
                            category=LeadCategory.tender)]
    return S


def test_pipeline_skips_ai_for_cross_source_duplicate_and_records_runs(session, monkeypatch):
    monkeypatch.setattr("app.pipeline.make_scorer", lambda: _FakeAI())
    _FakeAI.scored = []

    r1 = run_pipeline(_scraper("corporate", "https://tourism.gov.az/a.pdf", TITLE, "Dövlət Turizm Agentliyi"),
                      session, source_key="corporate")
    r2 = run_pipeline(_scraper("etender", "https://etender.gov.az/main/competition/detail/9", TITLE,
                               "Dövlət Turizm Agentliyi"), session, source_key="etender", trigger="scrape_all")
    r3 = run_pipeline(_scraper("etender", "https://etender.gov.az/main/competition/detail/9", TITLE,
                               "Dövlət Turizm Agentliyi"), session, source_key="etender")

    assert (r1.fresh, r1.pending, r1.tender_duplicates) == (1, 1, 0)
    assert (r2.fresh, r2.pending, r2.tender_duplicates) == (1, 0, 1)
    assert (r3.fresh, r3.url_duplicates) == (0, 1)
    assert _FakeAI.scored == ["https://tourism.gov.az/a.pdf"]  # dublikat AI-yə getmədi

    lead = session.execute(select(LeadRecord).where(LeadRecord.source == "etender")).scalars().one()
    assert lead.status == "duplicate" and lead.deadline_at == DEADLINE and lead.original.source == "corporate"

    runs = session.execute(select(ScrapeRun).order_by(ScrapeRun.id)).scalars().all()
    assert [(r.source_key, r.trigger, r.status, r.leads_new, r.leads_duplicate) for r in runs] == [
        ("corporate", "manual", "ok", 1, 0), ("etender", "scrape_all", "ok", 1, 1), ("etender", "manual", "ok", 0, 1)]
    assert r2.summary() == "1 yeni lead — 0 validasiyaya, 0 AI rədd etdi, 1 başqa mənbədə artıq var"


def test_failed_run_is_recorded(session):
    class Broken:
        source_name = "oxu"

        def run(self):
            raise RuntimeError("sayt çökdü")

    with pytest.raises(RuntimeError):
        run_pipeline(Broken, session, source_key="oxu")
    run = session.execute(select(ScrapeRun)).scalars().one()
    assert run.status == "failed" and "sayt çökdü" in run.error and run.finished_at


def test_backfill_fills_old_leads_once(session):
    old = LeadRecord(dedupe_key="k1", source="corporate", source_url="https://x/1", title=TITLE,
                     raw_text=f"{TITLE}\nSon tarix: 07.10.2026", category="tender",
                     scraped_at=datetime(2026, 9, 23), status="pending_review")
    session.add(old)
    session.commit()

    assert backfill(session) == {"deadlines": 1, "tenders_linked": 1, "duplicates": 0}
    assert old.deadline_at == DEADLINE and old.tender_id is not None and old.status == "pending_review"
    assert backfill(session) == {"deadlines": 0, "tenders_linked": 0, "duplicates": 0}


def test_summary_text():
    assert PipelineResult().summary() == "0 yeni lead"
    assert PipelineResult(fresh=3, rejected=3).summary() == "3 yeni lead — 0 validasiyaya, 3 AI rədd etdi"
    assert PipelineResult(url_duplicates=2).summary() == "0 yeni lead — 2 təkrar (artıq bazada)"
