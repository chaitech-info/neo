"""Redis kilidi: planlı və əl ilə scrape eyni mənbədə üst-üstə düşməsin."""
from app import locks, scheduler, web
from app.pipeline import PipelineResult


def test_second_holder_is_refused(fake_redis):
    with locks.source_lock("etender") as first:
        with locks.source_lock("etender") as second:
            assert first is True and second is False
        with locks.source_lock("oxu") as other:
            assert other is True  # başqa mənbə sərbəstdir
    with locks.source_lock("etender") as again:
        assert again is True  # buraxıldı


def test_redis_down_does_not_stop_scraping(monkeypatch):
    def down():
        raise ConnectionError("redis yoxdur")

    monkeypatch.setattr(locks, "_redis", down)
    with locks.source_lock("etender") as acquired:
        assert acquired is True


def test_scheduled_task_skips_when_source_is_busy(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "run_pipeline",
                        lambda cls, session, **kw: calls.append(kw) or PipelineResult(pending=2))

    with locks.source_lock("report"):
        assert scheduler.scrape_source("report") is None   # panel işlədir → atlanır
    assert calls == []

    assert scheduler.scrape_source("report") == 2
    assert calls == [{"source_key": "report", "trigger": "schedule"}]


def test_manual_scrape_skips_when_schedule_is_running(monkeypatch):
    monkeypatch.setattr(web, "run_pipeline", lambda *a, **kw: PipelineResult(fresh=1, pending=1))
    web._claim(["report"])
    with locks.source_lock("report"):  # sanki Celery işlədir
        result = web._run_source("report")
    assert result.fresh == 0
    assert "atlandı" in web._last_run["report"]



def test_vercel_scrape_button_posts_to_modal(monkeypatch):
    sent = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"queued": 1}

    monkeypatch.setattr("app.triggers.httpx.post",
                        lambda url, json, headers, timeout: sent.append((url, json, headers)) or _Resp())
    monkeypatch.setenv("MODAL_TRIGGER_URL", "https://neo--trigger.modal.run")
    monkeypatch.setenv("TRIGGER_TOKEN", "t0k")
    monkeypatch.setattr(web, "SERVERLESS", True)

    from fastapi.testclient import TestClient
    r = TestClient(web.app).post("/scrape/report", follow_redirects=False)
    assert "err=" not in r.headers["location"]
    assert sent == [("https://neo--trigger.modal.run", {"sources": ["report"], "trigger": "manual"},
                     {"Authorization": "Bearer t0k"})]


def test_vercel_scrape_without_modal_config_shows_error(monkeypatch):
    monkeypatch.delenv("MODAL_TRIGGER_URL", raising=False)
    monkeypatch.setattr(web, "SERVERLESS", True)
    from fastapi.testclient import TestClient
    r = TestClient(web.app).post("/scrape-all", follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_modal_trigger_checks_token_and_sources(monkeypatch):
    from app import triggers

    monkeypatch.setenv("TRIGGER_TOKEN", "t0k")
    spawned = []
    known = set(scheduler.SOURCE_TASKS)
    body = {"sources": ["report", "report", "../etc", "oxu"], "trigger": "scrape_all"}

    assert triggers.handle(body, "Bearer wrong", known, lambda *a: spawned.append(a))[0] == 401
    assert triggers.handle(body, None, known, lambda *a: spawned.append(a))[0] == 401
    assert triggers.handle({"sources": ["oxu"], "trigger": "schedule"}, "Bearer t0k", known,
                           lambda *a: spawned.append(a))[0] == 400
    assert spawned == []

    assert triggers.handle(body, "Bearer t0k", known, lambda *a: spawned.append(a)) == (200, {"queued": 2})
    assert spawned == [("report", "scrape_all"), ("oxu", "scrape_all")]


def test_modal_trigger_tick_requires_token(monkeypatch):
    """GitHub Actions cron-u (Modal cron limiti dolu workspace) → {"tick": true}."""
    from app import triggers

    monkeypatch.setenv("TRIGGER_TOKEN", "t0k")
    ticks = []
    assert triggers.handle({"tick": True}, "Bearer wrong", set(), print, on_tick=lambda: ticks.append(1))[0] == 401
    assert triggers.handle({"tick": True}, "Bearer t0k", set(), print, on_tick=lambda: ticks.append(1)) == (200, {"tick": True})
    assert triggers.handle({"tick": True}, "Bearer t0k", set(), print)[0] == 400
    assert ticks == [1]


def test_tick_catchup_window_covers_missed_runs():
    import pytest

    pytest.importorskip("modal")
    from datetime import datetime

    import modal_app

    now = datetime(2026, 10, 14, 12, 31)
    assert modal_app.catchup_window(now, None) == 10
    assert modal_app.catchup_window(now, "2026-10-14T12:21:00") == 10
    assert modal_app.catchup_window(now, "2026-10-14T12:01:00") == 30   # GitHub bir çağırışı buraxdı
    assert modal_app.catchup_window(now, "2026-10-14T12:31:00") == 0    # bu dəqiqə artıq işlənib
    assert modal_app.catchup_window(now, "2026-10-13T12:31:00") == 120  # uzun fasilə — limit


def test_modal_trigger_refuses_everything_without_token(monkeypatch):
    from app import triggers

    monkeypatch.delenv("TRIGGER_TOKEN", raising=False)
    assert triggers.handle({"sources": ["oxu"], "trigger": "manual"}, "Bearer ", {"oxu"}, print)[0] == 401


def test_due_sources_follow_beat_schedule():
    from datetime import datetime

    # 10 dəqiqəlik pəncərə: (06:21, 06:30] — emlak 06:30, report */2 saat :25 (6 cüt)
    due = set(scheduler.due_sources(datetime(2026, 10, 1, 6, 30), 10))
    assert {"emlak", "report"} <= due and not {"marja", "etender", "boss"} & due
    # (07:51, 08:00] — marja/1news hər saat :00
    assert {"marja", "1news"} <= set(scheduler.due_sources(datetime(2026, 10, 1, 8, 0), 10))
    # (07:01, 07:10] — heç nə (worldbank/linkedin 07:00 əvvəlki pəncərədədir)
    assert not {"worldbank", "linkedin", "bp_edp"} & set(scheduler.due_sources(datetime(2026, 10, 1, 7, 10), 10))


def test_modal_lock_refuses_second_holder(monkeypatch):
    class _Dict(dict):
        def put(self, key, value, skip_if_exists=False):
            if skip_if_exists and key in self:
                return False
            self[key] = value
            return True

    fake = _Dict()
    monkeypatch.setenv("NEO_LOCK_BACKEND", "modal")
    monkeypatch.setattr(locks, "_modal_dict", lambda: fake)
    with locks.source_lock("etender") as first:
        with locks.source_lock("etender") as second:
            assert first is True and second is False
    assert "etender" not in fake
    fake["oxu"] = 0.0                       # ölmüş konteynerdən qalan köhnə kilid
    with locks.source_lock("oxu") as stale:
        assert stale is True


def test_blocked_sources_run_on_mac_not_modal(monkeypatch):
    from datetime import datetime

    at_0640 = datetime(2026, 10, 1, 6, 40)  # (06:31, 06:40] — fed (Mac) + banker (Modal)
    assert {"fed", "banker"} <= set(scheduler.due_sources(at_0640, 10))
    monkeypatch.setenv("NEO_SCRAPE_ROLE", "modal")
    assert "fed" not in scheduler.due_sources(at_0640, 10) and "banker" in scheduler.due_sources(at_0640, 10)
    assert scheduler.scrape_source("etender") is None                 # işə düşmür
    monkeypatch.setenv("NEO_SCRAPE_ROLE", "local")
    assert scheduler.runs_here("etender") and not scheduler.runs_here("marja")

    queued = []
    monkeypatch.setattr(scheduler.scrape_source, "apply_async", lambda args: queued.append(tuple(args)))
    scheduler._dispatch_local("oxu", "manual")
    scheduler._dispatch_local("marja", "manual")                       # Modal-ın işidir
    scheduler._dispatch_local("oxu", "schedule")                       # yalnız paneldən gələn
    assert queued == [("oxu", "manual")]


def test_scraper_error_marks_run_failed(tmp_path):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.db.migrate import upgrade_to_head
    from app.db.models import ScrapeRun
    from app.pipeline import run_pipeline
    from app.scrapers.base import BaseScraper

    class Blocked(BaseScraper):
        source_name = "blocked"

        def fetch_leads(self):
            raise RuntimeError("403 Forbidden")

    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    upgrade_to_head(engine)
    with Session(engine) as session:
        result = run_pipeline(Blocked, session, source_key="oxu", trigger="schedule")
        run = session.scalars(select(ScrapeRun)).one()
        assert result.scraped == 0
        assert run.status == "failed" and "403 Forbidden" in run.error
    engine.dispose()


def test_local_bridge_survives_idle_timeouts(monkeypatch):
    import queue as stdqueue

    from app import triggers

    items = [stdqueue.Empty(), stdqueue.Empty(), ("oxu", "manual"), SystemExit()]

    class _Queue:
        def get(self, timeout):
            item = items.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr(triggers, "local_queue", lambda: _Queue())
    monkeypatch.setattr(triggers.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("xəta yolu")))
    got = []
    try:
        triggers._bridge_loop(lambda k, t: got.append((k, t)))
    except SystemExit:
        pass
    assert got == [("oxu", "manual")]


def test_az_proxy_moves_local_only_sources_to_modal(monkeypatch):
    """AZ_PROXY_URL varsa etender/oxu və s. Modal-da işləyir (proxy ilə), Mac worker lazım deyil."""
    monkeypatch.setenv("NEO_SCRAPE_ROLE", "modal")
    monkeypatch.delenv("AZ_PROXY_URL", raising=False)
    assert not scheduler.runs_here("etender") and scheduler.runs_here("marja")
    monkeypatch.setenv("AZ_PROXY_URL", "http://u:p@proxy.example:8000")
    assert scheduler.runs_here("etender") and scheduler.runs_here("marja")

    from app.sources import proxy_for
    assert proxy_for("etender") == "http://u:p@proxy.example:8000"
    assert proxy_for("marja") is None  # pullu proxy yalnız AZ mənbələri üçün


def test_scraper_client_uses_proxy_only_inside_context(monkeypatch):
    import httpx

    from app.scrapers.base import BaseScraper, use_proxy

    seen = []
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: seen.append(kw.get("proxy")) or real())

    class S(BaseScraper):
        def fetch_leads(self):
            return []

    S()
    with use_proxy("http://u:p@proxy.example:8000"):
        s = S()
        with use_proxy(None):
            S()
    S()
    assert seen == [None, "http://u:p@proxy.example:8000", None, None]
    assert s.via_proxy


def test_pipeline_runs_local_only_source_through_proxy(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import pipeline
    from app.db.migrate import upgrade_to_head
    from app.scrapers.base import _PROXY, BaseScraper

    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    upgrade_to_head(engine)
    seen = {}

    class Etender(BaseScraper):
        source_name = "etender"

        def fetch_leads(self):
            seen["proxy"] = _PROXY.get()
            return []

    monkeypatch.setenv("AZ_PROXY_URL", "http://u:p@proxy.example:8000")
    with sessionmaker(bind=engine)() as s:
        pipeline.run_pipeline(Etender, s, source_key="etender")
    assert seen["proxy"] == "http://u:p@proxy.example:8000"
    assert _PROXY.get() is None  # run bitəndən sonra kontekst təmizlənir
