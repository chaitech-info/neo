import pytest


@pytest.fixture(autouse=True)
def no_real_ai_triage(monkeypatch):
    """Testlər OpenAI-yə getməsin: triaj defolt olaraq "AI yoxdur" (None) qaytarır,
    scraper-lər yalnız açar sözlərlə işləyir. Triajı yoxlayan test bunu öz
    monkeypatch-i ilə əvəz edir."""
    monkeypatch.setattr("app.ai_triage.triage", lambda titles, kind="news", client=None: None)


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
