"""Mənbələr arası eyni hekayə / eyni şirkət (2026-10-05 analizindəki real hallar)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.migrate import upgrade_to_head
from app.db.models import LeadRecord
from app.story_dedup import find_story_duplicate, mark_story_duplicate, overlap, title_tokens

NOW = datetime(2026, 10, 5, 12)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    upgrade_to_head(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _lead(session, source, title, org=None, status="pending_review", hours_ago=1, category="event"):
    rec = LeadRecord(dedupe_key=f"{source}:{title}", source=source, source_url=f"https://{source}/{len(title)}",
                     title=title, raw_text=title, entity_name=org, category=category, status=status,
                     scraped_at=NOW - timedelta(hours=hours_ago))
    session.add(rec)
    session.commit()
    return rec


def test_same_story_by_organization(session):
    first = _lead(session, "marja", "ASELSAN Naxçıvanda zavod açacaq", org="ASELSAN", hours_ago=5)
    second = _lead(session, "fed", "Türkiyənin ASELSAN şirkəti Naxçıvanda - Zavod açacaq", org='"ASELSAN" ASC')
    assert find_story_duplicate(session, second, now=NOW) is first
    assert mark_story_duplicate(session, second)
    assert (second.status, second.duplicate_of) == ("duplicate", first.dedupe_key)


def test_same_story_by_title_when_no_organization(session):
    first = _lead(session, "marja", "ASELSAN Naxçıvanda zavod açacaq", hours_ago=5)
    second = _lead(session, "oxu", '"ASELSAN" Naxçıvanda zavod yaradacaq')
    assert overlap(title_tokens(first.title), title_tokens(second.title)) >= 0.75
    assert find_story_duplicate(session, second, now=NOW) is first


def test_same_employer_twice_on_boss(session):
    first = _lead(session, "boss", "Caucasian Vav Group — işçilərinə nahar verir (1 vakansiya, boss.az)", hours_ago=30)
    second = _lead(session, "boss", "Caucasian Vav Group — işçilərinə nahar verir (1 vakansiya, boss.az) ")
    assert find_story_duplicate(session, second, now=NOW) is first


def test_different_stories_rejected_old_and_tenders_are_kept(session):
    _lead(session, "marja", "Pandora 150 milyon dollarlıq zavod açıb", org="Pandora", hours_ago=3)
    _lead(session, "fed", "ASELSAN Naxçıvanda zavod açacaq", org="ASELSAN", status="rejected", hours_ago=3)
    _lead(session, "oxu", "ASELSAN Naxçıvanda zavod açacaq", org="ASELSAN", hours_ago=24 * 20)   # 14 gündən köhnə
    new = _lead(session, "marja", "ASELSAN Naxçıvanda zavod açacaq", org="ASELSAN")
    assert find_story_duplicate(session, new, now=NOW) is None
    tender = _lead(session, "etender", "ASELSAN Naxçıvanda zavod açacaq", org="ASELSAN", category="tender")
    assert find_story_duplicate(session, tender, now=NOW) is None  # tenderlər app/tenders.py-da
