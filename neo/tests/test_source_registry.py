"""Hər mənbə dörd yerdə qeyd olunmalıdır: LeadSource, scheduler, admin panel, `sources` cədvəli."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import scheduler, web
from app.db.migrate import upgrade_to_head
from app.db.repository import LeadRepository
from app.models import LeadSource, RawLead
from app.scrapers import ALL_SCRAPERS

NEW_SOURCES = ("media", "intl_tender", "jobsearch")


def test_registries_agree():
    # azerweb qəsdən scheduler-də və paneldə yoxdur (robots.txt qadağası)
    enum_keys = {s.value for s in LeadSource} - {"azerweb"}
    assert set(web.SOURCES) == set(scheduler.SOURCE_TASKS)
    assert set(web.SOURCES) == enum_keys | {"fed_tenders"}  # fed_tenders: LeadSource.fed ilə saxlanılır
    assert {cls for _, cls in web.SOURCES.values()} == set(ALL_SCRAPERS)


def test_every_source_has_a_beat_schedule_entry():
    scheduled = {entry["args"][0] for entry in scheduler.celery_app.conf.beat_schedule.values()}
    assert scheduled == set(scheduler.SOURCE_TASKS)


def test_new_sources_are_seeded_and_leads_can_reference_them():
    engine = create_engine("sqlite://")
    upgrade_to_head(engine)
    with engine.connect() as c:
        seeded = set(c.execute(text("SELECT key FROM sources WHERE is_active")).scalars())
    assert set(NEW_SOURCES) <= seeded

    session = sessionmaker(bind=engine)()
    repo = LeadRepository(session)
    for source in NEW_SOURCES:  # leads.source FK sources.key — seed olmasa IntegrityError
        lead = RawLead(source=source, source_url=f"https://x.az/{source}", title="T", raw_text="x")
        repo.save(lead.dedupe_key(), lead)
    assert session.execute(text("SELECT count(*) FROM leads")).scalar() == 3
