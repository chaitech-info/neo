"""Yemək kataloqu, şəkillər və şəkilli qiymət təklifi."""
import base64
import importlib.util
import re
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import dishes, offer_client as agent, offer_pdf, offer_routes, web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import Dish, OfferRecord
from tests.test_offers import OK, WEEKLY, _pages


def _png(color=(200, 80, 40), size=(1600, 1200), alpha=False) -> bytes:
    img = Image.new("RGBA" if alpha else "RGB", size, color + ((128,) if alpha else ()))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'd.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    with factory() as s:
        create_user(s, email="test@neo.az", full_name="Test Admin", role_code="admin", password="x" * 12)
    web.app.dependency_overrides[offer_routes.verify_csrf_header] = lambda: None
    yield factory
    engine.dispose()


def _images(pdf: bytes) -> int:
    """PDF-dəki ayrı şəkil obyektləri (loqo + yemək şəkilləri; eyni şəkil bir dəfə saxlanılır)."""
    return len(re.findall(rb"/Subtype\s*/Image", pdf))


def _dish(factory, name):
    with factory() as s:
        return s.scalars(select(Dish).where(Dish.key == dishes.dish_key(name))).one()


def test_dish_key_matches_migration_copy():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "0014_dishes.py"
    spec = importlib.util.spec_from_file_location("m0014", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("Göbələk krem-şorbası", "İçki", "BALIQ  (qril)", "Balqabaq, ağ pendir və qoz salatı", "Ice Tea"):
        assert dishes.dish_key(name) == mod._key(name)
    assert dishes.dish_key("Göbələk krem-şorbası") == dishes.dish_key("göbələk krem şorbası")
    assert dishes.dish_key("IŞIQ") == "ışıq"  # az: I → ı


def test_catalogue_is_seeded_from_sample_menus(db):
    with db() as s:
        rows = s.scalars(select(Dish)).all()
        assert len(rows) >= 55 and all(r.image is None for r in rows)
    assert _dish(db, "Mərcimək şorbası").category == "Şorba"
    assert _dish(db, "Lazanya Bolonez").category == "Əsas yemək"


def test_prepare_image_makes_small_jpeg_and_rejects_non_images():
    jpg = dishes.prepare_image(_png(alpha=True))
    img = Image.open(io.BytesIO(jpg))
    assert img.format == "JPEG" and max(img.size) == dishes.MAX_SIDE and img.mode == "RGB"
    with pytest.raises(dishes.DishError):
        dishes.prepare_image(b"not an image")
    with pytest.raises(dishes.DishError):
        dishes.prepare_image(b"")


def test_upload_change_and_remove_dish_photo(db):
    c = TestClient(web.app)
    d = _dish(db, "Mərcimək şorbası")
    r = c.post(f"/tools/dishes/{d.id}/image", files={"image": ("a.png", _png(), "image/png")},
               data={"show": "missing"}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert r.headers["location"].startswith("/tools/dishes?show=missing") and r.headers["location"].endswith(f"#d{d.id}")
    img = c.get(f"/dishes/{d.id}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    page = c.get("/tools/dishes?show=done").text
    assert "Mərcimək şorbası" in page and f"/dishes/{d.id}/image?v=" in page and "Yemək şəkilləri" in page
    assert "Lazanya Bolonez" not in page  # şəkli yoxdur

    bad = c.post(f"/tools/dishes/{d.id}/image", files={"image": ("a.txt", b"hello", "text/plain")},
                 follow_redirects=False)
    assert "err=" in bad.headers["location"] and c.get(f"/dishes/{d.id}/image").status_code == 200

    c.post(f"/tools/dishes/{d.id}/image/delete")
    assert c.get(f"/dishes/{d.id}/image").status_code == 404


def test_add_edit_and_delete_dish(db):
    c = TestClient(web.app)
    r = c.post("/tools/dishes", data={"name": "Qutab", "category": "Əsas yemək", "name_en": "Qutab"},
               files={"image": ("q.png", _png(), "image/png")}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    d = _dish(db, "Qutab")
    assert d.category == "Əsas yemək"
    dup = c.post("/tools/dishes", data={"name": "qutab"}, follow_redirects=False)
    assert "err=" in dup.headers["location"]
    c.post(f"/tools/dishes/{d.id}", data={"name": "Göyərti qutabı", "category": "Əsas yemək", "name_ru": "Кутаб"})
    assert _dish(db, "Göyərti qutabı").name_ru == "Кутаб"
    c.post(f"/tools/dishes/{d.id}/delete")
    with db() as s:
        assert s.get(Dish, d.id) is None


def test_sales_cannot_delete_dish_from_catalogue(db):
    from app.auth.security import REQUIRE
    from app.db.models import User

    sales = User(id=1, email="s@neo.az", full_name="S", role_code="sales", password_hash="x", is_active=True)
    web.app.dependency_overrides[REQUIRE["manager"]] = lambda: (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(403))
    for role in ("viewer", "sales"):
        web.app.dependency_overrides[REQUIRE[role]] = lambda: sales
    d = _dish(db, "Langet")
    c = TestClient(web.app)
    assert c.post(f"/tools/dishes/{d.id}/delete").status_code == 403
    assert c.get("/tools/dishes").status_code == 200
    with db() as s:
        assert s.get(Dish, d.id) is not None


def test_pictured_offer_uses_dish_photos_and_remembers_new_dishes(db, monkeypatch):
    with db() as s:
        for name in ("Mərcimək şorbası", "Çoban qovurması", "Toyuq tabaka"):
            dishes.set_image(s.scalars(select(Dish).where(Dish.key == dishes.dish_key(name))).one(), _png(), None)
        s.add(Dish(key=dishes.dish_key("Kotlet"), name="Kotlet", name_en="Pendirli burger",
                   image=dishes.prepare_image(_png((10, 120, 60)))))
        s.commit()
    snapshot = {**WEEKLY, "lines": [{**WEEKLY["lines"][0], "menu": WEEKLY["lines"][0]["menu"] + [
        {"title": {"az": "Cümə"}, "items": [{"az": "Əsas yemək: Təzə plov"}]}]}]}
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: {**OK, **snapshot, "status": "ok",
                                                                          "offer_id": OK["offer_id"]})
    monkeypatch.setattr(agent, "get_offer", lambda oid, client=None: snapshot)
    c = TestClient(web.app)

    plain = base64.b64decode(c.post("/api/offers", json={"brand": "seaport", "message": "a"}).json()["pdf_base64"])

    with db() as s:
        s.query(OfferRecord).delete()
        s.commit()
    d = c.post("/api/offers", json={"brand": "seaport", "message": "a", "pictures": True}).json()
    pictured = base64.b64decode(d["pdf_base64"])
    assert pictured.startswith(b"%PDF") and _images(pictured) == _images(plain) + 2  # 2 fərqli yemək şəkli
    assert _pages(pictured) >= _pages(plain)
    with db() as s:
        assert s.scalars(select(OfferRecord)).one().pictures is True
        assert s.scalar(select(Dish).where(Dish.key == dishes.dish_key("Təzə plov"))).category == "Əsas yemək"

    # Tarixçədən açanda da şəkilli; ?pictures=0 ilə şəkilsiz variant
    again = c.get(f"/api/offers/{OK['offer_id']}/pdf").content
    assert _images(again) == _images(pictured)
    assert _images(c.get(f"/api/offers/{OK['offer_id']}/pdf?pictures=0").content) == _images(plain)
    assert "Şəkilli" in c.get("/tools/offers").text


def test_photos_match_by_any_language_name(db):
    with db() as s:
        s.add(Dish(key="kotlet", name="Kotlet", name_en="Cutlet", name_ru="Котлета", image=b"x"))
        s.commit()
        assert set(dishes.photos_for(s, ["Cutlet", "котлета", "Yoxdur"])) == {"cutlet", "котлета"}


def test_render_without_photos_is_unchanged():
    assert _pages(offer_pdf.render(WEEKLY, {})) == _pages(offer_pdf.render(WEEKLY)) == 3
