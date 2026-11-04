"""Paketin menyu planı: yoxlama, agent menyusu, köhnə menyudan çevirmə, menyu kitabçası PDF-i."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import dishes, offer_client as agent, offer_pdf, offer_routes, package_menus, web
from app.db.models import Dish, PackageMenu
from tests.test_dishes import _images, _png, db  # noqa: F401  (fixture)
from tests.test_offers import WEEKLY, _pages


def _id(s, name):
    return s.scalar(select(Dish.id).where(Dish.key == dishes.dish_key(name)))


def _plan(s, **over):
    soup, beef, chicken, rice, bread = (_id(s, n) for n in ("Mərcimək şorbası", "Mal əti stroqonofu", "Toyuq kordon-blyu",
                                                             "Basmati düyüsü", "Çörək çeşidləri"))
    plan = {
        "days": [0, 1], "weeks": 1,
        "meals": [{"id": "m1", "name": "Nahar", "time": "12:00–14:00"}],
        "courses": [{"id": "c1", "name": "Şorba"}, {"id": "c2", "name": "Əsas yemək"}, {"id": "c3", "name": "Qarnir"},
                    {"id": "c4", "name": "Çörək"}],
        "fields": [{"id": "weight", "name": "Porsiya", "unit": "q"}, {"id": "allergens", "name": "Allergenlər", "unit": ""}],
        "same": ["m1:c4"],
        "cells": {"w1:0:m1:c1": [{"dish": soup, "x": {"weight": "300", "bogus": "x"}}],
                  "w1:0:m1:c2": [{"dish": beef, "x": {"allergens": "süd"}}],
                  "w1:1:m1:c2": [{"dish": chicken}], "w1:1:m1:c3": [{"dish": rice}],
                  "w1:*:m1:c4": [{"dish": bread}],
                  "w1:5:m1:c1": [{"dish": soup}],          # seçilməmiş gün — atılır
                  "w1:0:m1:c4": [{"dish": soup}]},         # "hər gün eyni" sətirdə gün xanası — atılır
        "note": "Şəkillər illüstrativdir",
    }
    plan.update(over)
    return plan


def test_clean_drops_leftovers_and_unknown_fields(db):
    with db() as s:
        plan = package_menus.clean(_plan(s), s)
    assert set(plan["cells"]) == {"w1:0:m1:c1", "w1:0:m1:c2", "w1:1:m1:c2", "w1:1:m1:c3", "w1:*:m1:c4"}
    assert plan["cells"]["w1:0:m1:c1"][0]["x"] == {"weight": "300"}


@pytest.mark.parametrize("bad, msg", [
    ({"days": []}, "gün"), ({"weeks": 9}, "Həftə"), ({"meals": []}, "yemək vaxtı"),
    ({"courses": [{"id": "c1", "name": ""}]}, "ad boş"),
    ({"cells": {"w1:0:m1:c1": [{"dish": 999999}]}}, "kataloqda olmayan"),
])
def test_clean_rejects_bad_plans(db, bad, msg):
    with db() as s:
        with pytest.raises(package_menus.PlanError, match=msg):
            package_menus.clean(_plan(s, **bad), s)


def test_agent_menu_is_day_by_day_text_in_three_languages(db):
    with db() as s:
        s.get(Dish, _id(s, "Mal əti stroqonofu")).name_en = "Beef stroganoff"
        plan = package_menus.clean(_plan(s), s)
        menu = package_menus.agent_menu(plan, package_menus.dish_map(s, plan))
    assert [m["title"]["az"] for m in menu] == ["Bazar ertəsi", "Çərşənbə axşamı"]
    assert [i["az"] for i in menu[0]["items"]] == ["Şorba: Mərcimək şorbası", "Əsas yemək: Mal əti stroqonofu",
                                                   "Çörək: Çörək çeşidləri"]
    assert menu[0]["items"][1]["en"] == "Main course: Beef stroganoff"
    # offer_pdf köhnə yolla da həftəlik menyu kimi oxuyur
    assert offer_pdf._package({"name": {"az": "X"}, "menu": menu}, "az", "AZN").weekly


def test_more_meals_and_rotation_weeks(db):
    with db() as s:
        raw = _plan(s, weeks=2, meals=[{"id": "m1", "name": "Səhər yeməyi", "time": "09:00"},
                                       {"id": "m2", "name": "Nahar", "time": ""}], same=[])
        raw["cells"] = {"w2:1:m2:c1": [{"dish": _id(s, "Mərcimək şorbası")}],
                        "w1:0:m1:c3": [{"dish": _id(s, "Basmati düyüsü")}]}
        plan = package_menus.clean(raw, s)
        rows = package_menus.dish_map(s, plan)
        menu = package_menus.agent_menu(plan, rows)
        pkg = package_menus.pdf_package(plan, rows, {"id": "p", "name": {"az": "Medium plus"}, "price": 10})
    assert [m["title"]["az"] for m in menu] == ["Bazar ertəsi · 1-ci həftə", "Çərşənbə axşamı · 2-ci həftə"]
    assert menu[0]["items"][0] == {"az": "Səhər yeməyi — Qarnir: Basmati düyüsü", "en": "Breakfast — Side dish: Basmati düyüsü",
                                   "ru": "Завтрак — Гарнир: Basmati düyüsü"}
    assert offer_pdf._weekday(menu[1]["title"]) == 1
    assert [(b.title, b.accent) for b in pkg.blocks] == [("Bazar ertəsi · 1-ci həftə", "Monday · Week 1 · Səhər yeməyi 09:00"),
                                                       ("Çərşənbə axşamı · 2-ci həftə", "Tuesday · Week 2 · Nahar")]


def test_import_from_text_menu_marks_bread_as_every_day(db):
    with db() as s:
        plan = package_menus.from_agent_menu(s, WEEKLY["lines"][0]["menu"])
        s.commit()
        clean = package_menus.clean(plan, s)
        rows = package_menus.dish_map(s, clean)
        menu = package_menus.agent_menu(clean, rows)
    assert clean["days"] == [0, 1, 2, 3, 4]
    assert [c["name"] for c in clean["courses"]] == ["Şorba", "Əsas yemək", "Qarnir", "Salat", "Çörək"]
    bread = next(c["id"] for c in clean["courses"] if c["name"] == "Çörək")
    assert clean["same"] == [f"m1:{bread}"]
    # Mətn eyni qalır (yalnız "Salatlar:" → "Salat:")
    original = [i["az"].replace("Salatlar:", "Salat:") for i in WEEKLY["lines"][0]["menu"][0]["items"]]
    assert [i["az"] for i in menu[0]["items"]] == original


def test_menu_booklet_pdf_has_photo_pages(db):
    with db() as s:
        for i, n in enumerate(("Mərcimək şorbası", "Mal əti stroqonofu", "Toyuq kordon-blyu", "Basmati düyüsü")):
            dishes.set_image(s.get(Dish, _id(s, n)), _png((40 * i, 90, 120)), None)
        s.commit()
        plan = package_menus.clean(_plan(s), s)
        rows = package_menus.dish_map(s, plan)
        pkg = package_menus.pdf_package(plan, rows, {"id": "mp", "name": {"az": "Medium plus", "en": "Medium Plus"}, "price": 10})
        photos = dishes.photos_for(s, [i for b in pkg.blocks for g in b.groups for i in g.items])
    assert pkg.blocks[0].groups[0].notes == {"Mərcimək şorbası": "300 q"}
    assert pkg.blocks[0].groups[1].notes == {"Mal əti stroqonofu": "Allergenlər: süd"}
    full = offer_pdf.render_menu(pkg, photos, note=plan["note"])
    only_photos = offer_pdf.render_menu(pkg, photos, schedule=False)
    no_photos = offer_pdf.render_menu(pkg, {})
    assert _images(full) == _images(no_photos) + 4
    assert _pages(only_photos) == 3 and _pages(full) > _pages(no_photos) == 2
    assert b"Medium Plus Corporate Lunch Menu" in full


def test_save_plan_through_package_route_and_offer_uses_it(db, monkeypatch):
    sent = {}
    monkeypatch.setattr(agent, "update_package", lambda brand, pid, body, client=None: sent.update(body) or body)
    c = TestClient(web.app)
    with db() as s:
        raw = _plan(s)
    body = {"id": "mp", "name": {"az": "Medium plus"}, "price": 10, "unit": {"az": "porsiya"}, "active": True,
            "menu": [{"title": {"az": "köhnə"}, "items": [{"az": "x"}]}], "plan": raw}
    r = c.put("/api/offer-packages/seaport/mp", json=body)
    assert r.status_code == 200, r.text
    assert "plan" not in sent and sent["menu"][0]["title"]["az"] == "Bazar ertəsi"
    with db() as s:
        assert package_menus.load(s, "seaport", "mp")["cells"]["w1:*:m1:c4"]
    # Plan yenidən açılır; səhv plan → 422, agentə getmir
    assert c.get("/api/offer-packages/seaport/mp/plan").json()["imported"] is False
    sent.clear()
    assert c.put("/api/offer-packages/seaport/mp", json={**body, "plan": {**raw, "days": []}}).status_code == 422
    assert not sent

    # Təklifdəki menyu plandakı ilə eynidirsə, PDF plandan qurulur (vaxt, əlavə sahələr)
    snapshot = {**WEEKLY, "lines": [{**WEEKLY["lines"][0], "package_id": "mp", "menu": sent_menu(db)}]}
    with db() as s:
        plans = package_menus.plans_for_snapshot(s, snapshot)
        assert plans[0].blocks[0].accent == "Monday · Nahar 12:00–14:00"
        stale = {**snapshot, "lines": [{**snapshot["lines"][0], "menu": WEEKLY["lines"][0]["menu"]}]}
        assert package_menus.plans_for_snapshot(s, stale) == {}

    # Menyu kitabçası: saxlanmış plandan və redaktordakı plandan
    monkeypatch.setattr(agent, "list_packages", lambda brand, client=None: [body])
    pdf = c.get("/tools/packages/seaport/mp/menu.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    prev = c.post("/api/offer-packages/seaport/mp/menu.pdf", json={"package": body, "plan": raw, "schedule": False})
    assert prev.status_code == 200 and prev.content.startswith(b"%PDF")
    assert c.get("/tools/packages/seaport/none/menu.pdf").status_code == 404

    monkeypatch.setattr(agent, "delete_package", lambda brand, pid, client=None: None)
    assert c.delete("/api/offer-packages/seaport/mp").status_code == 200
    with db() as s:
        assert s.scalar(select(PackageMenu)) is None


def sent_menu(db):
    with db() as s:
        plan = package_menus.load(s, "seaport", "mp")
        return package_menus.agent_menu(plan, package_menus.dish_map(s, plan))


def test_plan_for_package_without_plan_is_imported(db, monkeypatch):
    monkeypatch.setattr(agent, "list_packages", lambda brand, client=None: [{"id": "standart", **WEEKLY["lines"][0]}])
    c = TestClient(web.app)
    j = c.get("/api/offer-packages/seaport/standart/plan").json()
    assert j["imported"] is True and j["plan"]["days"] == [0, 1, 2, 3, 4]
    assert c.get("/api/offer-packages/seaport/_new/plan").json()["plan"]["meals"][0]["name"] == "Nahar"
    with db() as s:   # "Salatlar" kateqoriyalı yeni yeməklər yoxdur — hamısı kataloqda idi
        assert s.scalar(select(Dish).where(Dish.key == dishes.dish_key("Toyuq tabaka"))) is not None


def test_dish_catalog_api(db):
    c = TestClient(web.app)
    j = c.get("/api/dish-catalog").json()
    assert len(j["dishes"]) >= 57 and j["categories"][0] == "Şorba"
    r = c.post("/api/dish-catalog", json={"name": "Təndir çörəyi", "category": "Çörək"})
    assert r.status_code == 200 and r.json()["dish"]["img"] is None
    assert c.post("/api/dish-catalog", json={"name": "təndir  çörəyi"}).status_code == 422


def test_plan_ids_and_stale_plan_is_reimported(db, monkeypatch):
    c = TestClient(web.app)
    with db() as s:
        plan = package_menus.clean(_plan(s), s)
        package_menus.save(s, "seaport", "standart", plan, None)
        s.commit()
    assert c.get("/api/offer-packages/seaport/plans").json() == {"ids": ["standart"]}
    # Agentdəki menyu JSON ilə dəyişib → plan mətndən yenidən qurulur
    monkeypatch.setattr(agent, "list_packages", lambda brand, client=None: [{"id": "standart", **WEEKLY["lines"][0]}])
    j = c.get("/api/offer-packages/seaport/standart/plan").json()
    assert j["imported"] is True and len(j["plan"]["days"]) == 5
