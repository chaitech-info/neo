"""Phase 0: qırılmış run-dan sonra özünü bərpa — skorsuz lead-lər, yarımçıq run-lar,
filtrdən əvvəlki say, baza bağlantısı düşəndə təkrar."""
from datetime import datetime, timedelta

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import scheduler
from app.db.migrate import upgrade_to_head
from app.db.models import LeadRecord, ScrapeRun
from app.models import LeadStatus, RawLead
from app.pipeline import PipelineResult, run_pipeline
from app.scrapers import News1Scraper
from app.scrapers.base import BaseScraper


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    upgrade_to_head(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture()
def fake_ai(monkeypatch):
    scored = []

    def score_batch(self, leads):
        for lead in leads:
            lead.ai_score, lead.ai_reason, lead.status = 0.8, "test", LeadStatus.pending_review
            scored.append(lead.source_url)
        return leads

    monkeypatch.setattr("app.pipeline.AIFilter.score_batch", score_batch)
    return scored


class Quiet(BaseScraper):
    """Saytda 3 başlıq var, heç biri filtrdən keçmir."""
    source_name = "oxu"

    def fetch_leads(self):
        self.select_relevant(["Futbol", "Hava", "İdman"], [], kind="news")
        return []


def _stuck(session, url, minutes_ago):
    lead = RawLead(source="oxu", source_url=url, title="Zavod açılır", raw_text="Zavod açılır")
    session.add(LeadRecord(dedupe_key=lead.dedupe_key(), source="oxu", source_url=url, title=lead.title,
                           raw_text=lead.raw_text, category="other", status="new",
                           scraped_at=datetime.utcnow() - timedelta(minutes=minutes_ago)))
    session.commit()


def test_unscored_leads_from_broken_run_are_scored_next_time(session, fake_ai):
    _stuck(session, "https://oxu.az/old", minutes_ago=90)
    _stuck(session, "https://oxu.az/fresh", minutes_ago=1)   # paralel run-ın ola bilər — toxunma

    result = run_pipeline(Quiet, session, source_key="oxu")

    assert result.recovered == 1 and result.pending == 1
    assert fake_ai == ["https://oxu.az/old"]
    statuses = {r.source_url: r.status for r in session.scalars(select(LeadRecord))}
    assert statuses == {"https://oxu.az/old": "pending_review", "https://oxu.az/fresh": "new"}


def test_fetched_counts_items_before_filter(session, fake_ai):
    result = run_pipeline(Quiet, session, source_key="oxu")
    run = session.scalars(select(ScrapeRun)).one()
    assert (result.fetched, result.scraped) == (3, 0)
    assert (run.items_fetched, run.items_seen, run.status) == (3, 0, "ok")


def test_interrupted_running_runs_are_closed(session, fake_ai):
    session.add(ScrapeRun(source_key="oxu", trigger="schedule", status="running",
                          started_at=datetime.utcnow() - timedelta(hours=5)))
    session.commit()
    run_pipeline(Quiet, session, source_key="oxu")
    old, new = session.scalars(select(ScrapeRun).order_by(ScrapeRun.id)).all()
    assert old.status == "failed" and "Yarımçıq" in old.error and old.finished_at
    assert new.status == "ok"


def test_scheduled_scrape_retries_once_when_db_connection_drops(monkeypatch):
    calls = []

    def flaky(cls, session, **kw):
        calls.append(kw)
        if len(calls) == 1:
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
        return PipelineResult(pending=1)

    monkeypatch.setattr(scheduler, "run_pipeline", flaky)
    monkeypatch.setattr(scheduler, "DB_RETRY_DELAY_S", 0)
    assert scheduler.scrape_source("report") == 1
    assert len(calls) == 2


def test_scheduled_scrape_does_not_retry_other_db_errors(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    def broken(cls, session, **kw):
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    monkeypatch.setattr(scheduler, "run_pipeline", broken)
    with pytest.raises(IntegrityError):
        scheduler.scrape_source("report")


def test_1news_reads_lenta_feed():
    html = """<div class="newsList">
      <a href="/az/news/20261005110625859-Agdamdan"><figure></figure>
         <time class="date">11:06</time><span class="title">Ağdamdan yeni xətt</span></a>
      <a href="/az/1news-tv/">TV</a></div>"""
    links = News1Scraper().article_links(BeautifulSoup(html, "lxml"))
    assert links == [("https://1news.az/az/news/20261005110625859-Agdamdan", "Ağdamdan yeni xətt")]
    assert News1Scraper.listing_url == "https://1news.az/az/lenta/"


def test_source_health_states(session):
    from celery.schedules import crontab

    from app.health import source_health

    now = datetime(2026, 10, 5, 12, 0)
    hourly, daily = crontab(minute=0), crontab(minute=0, hour=6)

    def run(key, hours_ago, status="ok", fetched=10, error=None):
        session.add(ScrapeRun(source_key=key, trigger="schedule", status=status, items_fetched=fetched,
                              started_at=now - timedelta(hours=hours_ago), error=error))

    for h in (1, 2, 3):
        run("1news", h, "failed", error="HTTPStatusError: 404 Not Found\nmore")
        run("marja", h, fetched=0)          # işləyir, amma saytdan heç nə gəlmir
        run("oxu", h)
    run("etender", 60)                      # gündəlik, 2.5 gündür işləməyib (Mac sönük)
    run("worldbank", 20, fetched=12)        # gündəlik, normal; uyğun elan olmaması "boş" deyil
    run("worldbank", 30, "failed", error='OperationalError: (psycopg2.OperationalError) connection to server at "ep-x.neon.tech"')
    session.commit()

    health = source_health(session, {"1news": hourly, "marja": hourly, "oxu": hourly,
                                     "etender": daily, "worldbank": daily, "boss": daily}, now=now)
    assert {k: h.state for k, h in health.items()} == {
        "1news": "error", "marja": "empty", "oxu": "ok", "etender": "stale", "worldbank": "ok", "boss": "stale"}
    assert "404 Not Found" in health["1news"].detail and "\n" not in health["1news"].detail
    assert health["1news"].label == "Xəta"
    assert "baza bağlantısı kəsildi" in health["worldbank"].detail and "neon.tech" not in health["worldbank"].detail


def test_dashboard_shows_health_column():
    from fastapi.testclient import TestClient

    from app import web

    page = TestClient(web.app).get("/")
    assert page.status_code == 200 and "Vəziyyət" in page.text
