import logging
import threading
import time

import structlog
from fastapi.testclient import TestClient

from app import logbuffer, web
from app.pipeline import PipelineResult


def _wait_idle(timeout=5.0):
    deadline = time.time() + timeout
    while web._running and time.time() < deadline:
        time.sleep(0.02)
    assert not web._running


def test_secrets_in_urls_are_masked():
    logbuffer.setup()
    logging.getLogger("httpx").info(
        'HTTP Request: POST https://api.apify.com/v2/acts/x/run-sync?token=apify_api_SECRET123 "HTTP/1.1 200 OK"'
    )
    last = logbuffer.entries()[-1]
    assert "apify_api_SECRET123" not in last["details"]
    assert "token=***" in last["details"]


def test_logs_in_a_worker_thread_are_tagged_with_its_source():
    logbuffer.setup()
    before = logbuffer.entries()[-1]["id"] if logbuffer.entries() else 0

    def work():
        logbuffer.bind_source("boss")
        structlog.get_logger().info("boss_scanned", scanned=660)
        logging.getLogger("httpx").info("HTTP Request: POST https://boss.az/graphql")
        logbuffer.unbind_source()

    t = threading.Thread(target=work)
    t.start()
    t.join()

    new = logbuffer.entries(after=before)
    assert [(e["source"], e["event"]) for e in new] == [("boss", "boss_scanned"), ("boss", "httpx")]
    assert logbuffer.entries(after=before, source="boss") == new


def test_scrape_all_runs_every_source_in_parallel_and_reports_status(monkeypatch):
    barrier = threading.Barrier(2, timeout=5)

    class FakeA:
        source_name = "a"

    class FakeB:
        source_name = "b"

    def fake_pipeline(scraper_cls, session, **kwargs):
        barrier.wait()  # iki mənbə eyni vaxtda işləməsə, burada ilişib qalar
        structlog.get_logger().info("fake_run", source=scraper_cls.source_name)
        fresh = {"a": 3, "b": 0}[scraper_cls.source_name]
        return PipelineResult(scraped=fresh, fresh=fresh, pending=fresh)

    monkeypatch.setattr(web, "SOURCES", {"src_a": ("A", FakeA), "src_b": ("B", FakeB)})
    monkeypatch.setattr(web, "run_pipeline", fake_pipeline)
    web._last_run.clear()
    client = TestClient(web.app)

    resp = client.post("/scrape-all", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/logs?msg=2")
    _wait_idle()
    assert "3 yeni lead" in web._last_run["src_a"]
    assert "0 yeni lead" in web._last_run["src_b"]
    tagged = {(e["source"], e["event"]) for e in logbuffer.entries() if e["event"] == "fake_run"}
    assert tagged == {("src_a", "fake_run"), ("src_b", "fake_run")}

    api = client.get("/api/logs", params={"source": "src_a"}).json()
    assert api["running"] == []
    assert all(e["source"] == "src_a" for e in api["entries"])


def test_scrape_all_does_not_start_a_source_twice(monkeypatch):
    class FakeA:
        source_name = "a"

    monkeypatch.setattr(web, "SOURCES", {"src_a": ("A", FakeA)})
    web._running.add("src_a")
    try:
        resp = TestClient(web.app).post("/scrape-all", follow_redirects=False)
        assert resp.status_code == 303
        assert "err=" in resp.headers["location"]
    finally:
        web._running.discard("src_a")


def test_failed_source_is_reported_not_raised(monkeypatch):
    class Broken:
        source_name = "x"

    def boom(scraper_cls, session, **kwargs):
        raise RuntimeError("site down")

    monkeypatch.setattr(web, "SOURCES", {"broken": ("Broken", Broken)})
    monkeypatch.setattr(web, "run_pipeline", boom)

    TestClient(web.app).post("/scrape-all", follow_redirects=False)
    _wait_idle()

    assert "XƏTA" in web._last_run["broken"]
    err = [e for e in logbuffer.entries(source="broken") if e["level"] == "error"][-1]
    assert "RuntimeError: site down" in err["details"]
