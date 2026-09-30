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
