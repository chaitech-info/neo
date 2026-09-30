import pytest


@pytest.fixture(autouse=True)
def no_real_ai_triage(monkeypatch):
    """Testlər OpenAI-yə getməsin: triaj defolt olaraq "AI yoxdur" (None) qaytarır,
    scraper-lər yalnız açar sözlərlə işləyir. Triajı yoxlayan test bunu öz
    monkeypatch-i ilə əvəz edir."""
    monkeypatch.setattr("app.ai_triage.triage", lambda titles, kind="news", client=None: None)


@pytest.fixture(autouse=True)
def keywords_from_code(monkeypatch):
    """Testlər real lokal bazanın açar sözlərini oxumasın: defolt olaraq "baza yoxdur"
    — scraper-lər və AI koddakı siyahılarla işləyir. Bazadan oxumanı yoxlayan test
    `app.keywords.snapshot`-u öz fixture-i ilə əvəz edir."""
    from app import keywords

    keywords.invalidate()
    monkeypatch.setattr("app.keywords.snapshot", lambda: None)


class _FakeLock:
    def __init__(self, held):
        self.held, self.key = held, None

    def acquire(self):
        if self.key in self.held:
            return False
        self.held.add(self.key)
        return True

    def release(self):
        self.held.discard(self.key)


class FakeRedis:
    """Yaddaşda Redis kilidi — testlər real (lokal) Redis-ə getməsin."""

    def __init__(self):
        self.held = set()

    def lock(self, key, timeout=None, blocking=True):
        lock = _FakeLock(self.held)
        lock.key = key
        return lock


@pytest.fixture(autouse=True)
def no_deploy_env(monkeypatch):
    """Lokal .env production dəyişənləri (Mac worker rolu, Modal kilidi, trigger) testlərə sızmasın."""
    for name in ("NEO_SCRAPE_ROLE", "NEO_LOCK_BACKEND", "MODAL_TRIGGER_URL", "TRIGGER_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.locks._redis", lambda: fake)
    return fake


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: giriş/rol/CSRF yoxlamasını əvəz etmə (auth testləri)")


@pytest.fixture(autouse=True)
def logged_in_admin(request):
    """Admin panel testləri üçün: sanki admin daxil olub, CSRF yoxlanmır.
    Girişin özünü yoxlayan testlər `@pytest.mark.real_auth` ilə bunu söndürür."""
    if request.node.get_closest_marker("real_auth"):
        yield
        return
    from fastapi import Request

    from app import web
    from app.auth.security import REQUIRE, verify_csrf
    from app.db.models import User

    admin = User(id=1, email="test@neo.az", full_name="Test Admin", role_code="admin",
                 password_hash="x", is_active=True)

    def as_admin(request: Request) -> User:
        request.state.user = admin
        return admin

    for dependency in REQUIRE.values():
        web.app.dependency_overrides[dependency] = as_admin
    web.app.dependency_overrides[verify_csrf] = lambda: None
    yield
    web.app.dependency_overrides.clear()
