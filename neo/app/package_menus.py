"""Paketin menyu planı — hansı yemək hansı həftə / gün / yemək vaxtı / kateqoriyada verilir (Alətlər → Paketlər).

Plan Neo-nun bazasında JSON kimi saxlanılır (package_menus, 0015). Agentdəki paketin `menu`-su plandan yaradılır
(`agent_menu`): bölmə — gün ("Bazar ertəsi", rotasiyada "Bazar ertəsi · 2-ci həftə"), maddə — "Kateqoriya: yemək"
(bir neçə yemək vaxtı varsa "Nahar — Şorba: …"). Belə agent də, offer_pdf-in mətn oxuyan hissəsi də dəyişmir.

Plan sxemi (`clean`):
    days     — [0..6], həftənin günləri (0 = Bazar ertəsi)
    weeks    — 1..4, rotasiya həftələrinin sayı
    meals    — [{id, name, time?}] — yemək vaxtları ("Nahar", "12:00–14:00")
    courses  — [{id, name}] — kateqoriyalar, menyudakı sırada
    fields   — [{id, name, unit?}] — istəyə görə əlavə sahələr (porsiya, kalori, allergenlər, qeyd …)
    same     — ["m1:c5", …] — hər gün eyni olan sətirlər (çörək, içki)
    cells    — {"w1:d0:m1:c1": [{dish, x: {field_id: dəyər}}]}; "hər gün eyni" sətirdə gün "*"
    note     — PDF-də kiçik qeyd ("Şəkillər illüstrativ xarakter daşıyır …"), istəyə görə
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import dishes as dish_catalog
from app.db.models import Dish, PackageMenu
from app.offer_pdf import WEEKDAYS, Block, Group, Package, _CAT_RE, _money, _weekday, section_name

MAX_WEEKS, MAX_MEALS, MAX_COURSES, MAX_FIELDS, MAX_PER_CELL = 4, 6, 15, 8, 12
ID_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
KEY_RE = re.compile(r"^w([1-4]):(\*|[0-6]):([a-z0-9_-]{1,24}):([a-z0-9_-]{1,24})$")

# Kateqoriya və yemək vaxtlarının tərcüməsi (agentə en/ru mətni də getsin); tanınmayan ad yalnız az qalır
COURSE_I18N = {
    "Şorba": ("Soup", "Суп"), "Əsas yemək": ("Main course", "Основное блюдо"), "Qarnir": ("Side dish", "Гарнир"),
    "Salat": ("Salad", "Салат"), "Desert": ("Dessert", "Десерт"), "İçki": ("Drink", "Напиток"),
    "Çörək": ("Bread", "Хлеб"),
}
MEAL_I18N = {
    "Səhər yeməyi": ("Breakfast", "Завтрак"), "Nahar": ("Lunch", "Обед"), "Şam yeməyi": ("Dinner", "Ужин"),
    "Ara yemək": ("Snack", "Перекус"), "Kofe-brejk": ("Coffee break", "Кофе-брейк"),
}
FIELD_PRESETS = [  # UI-da "+ Sahə" menyusunda
    {"id": "weight", "name": "Porsiya", "unit": "q"}, {"id": "kcal", "name": "Kalori", "unit": "kkal"},
    {"id": "allergens", "name": "Allergenlər", "unit": ""}, {"id": "note", "name": "Qeyd", "unit": ""},
]
DEFAULT_NOTE = "Şəkillər illüstrativ xarakter daşıyır; real təqdimat cüzi fərqlənə bilər."


class PlanError(ValueError):
    """İstifadəçiyə göstərilən xəta (Azərbaycan dilində)."""


def default_plan() -> dict:
    return {
        "days": [0, 1, 2, 3, 4], "weeks": 1,
        "meals": [{"id": "m1", "name": "Nahar", "time": ""}],
        "courses": [{"id": f"c{i + 1}", "name": n} for i, n in enumerate(["Şorba", "Əsas yemək", "Qarnir", "Salat", "Çörək"])],
        "fields": [], "same": ["m1:c5"], "cells": {}, "note": DEFAULT_NOTE,
    }


def _s(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _items(raw: Any, what: str, limit: int, extra: tuple[str, ...] = ()) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > limit:
        raise PlanError(f"{what} siyahısı yanlışdır (ən çox {limit})")
    out, ids = [], set()
    for it in raw:
        if not isinstance(it, dict) or not ID_RE.match(str(it.get("id") or "")) or it["id"] in ids:
            raise PlanError(f"{what} siyahısı yanlışdır")
        name = _s(it.get("name"), 60)
        if not name:
            raise PlanError(f"{what}: ad boş ola bilməz")
        ids.add(it["id"])
        out.append({"id": it["id"], "name": name, **{k: _s(it.get(k), 40) for k in extra}})
    return out


def clean(raw: Any, db: Session) -> dict:
    """Brauzerdən gələn planı yoxlayır və normal formaya salır; səhvdə PlanError."""
    if not isinstance(raw, dict):
        raise PlanError("Menyu planı yanlışdır")
    days = raw.get("days")
    if not isinstance(days, list) or not days or any(not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in days):
        raise PlanError("Ən azı bir gün seç")
    weeks = raw.get("weeks", 1)
    if not isinstance(weeks, int) or isinstance(weeks, bool) or not 1 <= weeks <= MAX_WEEKS:
        raise PlanError(f"Həftə sayı 1–{MAX_WEEKS} olmalıdır")
    meals = _items(raw.get("meals"), "Yemək vaxtları", MAX_MEALS, ("time",))
    courses = _items(raw.get("courses"), "Kateqoriyalar", MAX_COURSES)
    fields = _items(raw.get("fields") or [], "Əlavə sahələr", MAX_FIELDS, ("unit",))
    if not meals:
        raise PlanError("Ən azı bir yemək vaxtı əlavə et")
    if not courses:
        raise PlanError("Ən azı bir kateqoriya əlavə et")
    meal_ids, course_ids, field_ids = {m["id"] for m in meals}, {c["id"] for c in courses}, {f["id"] for f in fields}
    same = []
    for row in raw.get("same") or []:
        if isinstance(row, str) and row.count(":") == 1:
            m, c = row.split(":")
            if m in meal_ids and c in course_ids and row not in same:
                same.append(row)

    cells: dict[str, list[dict]] = {}
    raw_cells = raw.get("cells") or {}
    if not isinstance(raw_cells, dict):
        raise PlanError("Menyu planı yanlışdır")
    wanted: set[int] = set()
    for key, entries in raw_cells.items():
        m = KEY_RE.match(str(key))
        if not m or not isinstance(entries, list):
            raise PlanError("Menyu planı yanlışdır")
        w, d, meal, course = int(m.group(1)), m.group(2), m.group(3), m.group(4)
        row_same = f"{meal}:{course}" in same
        # Silinmiş həftə / gün / vaxt / kateqoriyanın qalıqları və "hər gün eyni" ilə ziddiyyətli xanalar atılır
        if w > weeks or meal not in meal_ids or course not in course_ids:
            continue
        if (d == "*") != row_same or (d != "*" and int(d) not in days):
            continue
        if len(entries) > MAX_PER_CELL:
            raise PlanError(f"Bir xanada ən çox {MAX_PER_CELL} yemək ola bilər")
        clean_entries = []
        for e in entries:
            dish = e.get("dish") if isinstance(e, dict) else None
            if not isinstance(dish, int) or isinstance(dish, bool):
                raise PlanError("Menyu planı yanlışdır")
            x = e.get("x") if isinstance(e.get("x"), dict) else {}
            extras = {k: _s(v, 60) for k, v in x.items() if k in field_ids and _s(v, 60)}
            clean_entries.append({"dish": dish, "x": extras} if extras else {"dish": dish})
            wanted.add(dish)
        if clean_entries:
            cells[key] = clean_entries
    if wanted:
        known = set(db.scalars(select(Dish.id).where(Dish.id.in_(wanted))))
        if wanted - known:
            raise PlanError("Menyuda kataloqda olmayan yemək var — səhifəni yeniləyib yenidən cəhd et")
    return {"days": sorted(set(days)), "weeks": weeks, "meals": meals, "courses": courses, "fields": fields,
            "same": same, "cells": cells, "note": _s(raw.get("note"), 300)}


# --- Baza ------------------------------------------------------------------------------------------


def load(db: Session, brand: str, package_id: str) -> dict | None:
    row = db.scalar(select(PackageMenu).where(PackageMenu.brand == brand, PackageMenu.package_id == package_id))
    return json.loads(row.plan) if row else None


def load_all(db: Session, brand: str) -> dict[str, dict]:
    return {r.package_id: json.loads(r.plan) for r in db.scalars(select(PackageMenu).where(PackageMenu.brand == brand))}


def save(db: Session, brand: str, package_id: str, plan: dict, user_id: int | None) -> None:
    row = db.scalar(select(PackageMenu).where(PackageMenu.brand == brand, PackageMenu.package_id == package_id))
    if row is None:
        row = PackageMenu(brand=brand, package_id=package_id, plan="{}")
        db.add(row)
    row.plan = json.dumps(plan, ensure_ascii=False)
    row.updated_by_id = user_id
    row.updated_at = datetime.utcnow()


def delete(db: Session, brand: str, package_id: str) -> None:
    row = db.scalar(select(PackageMenu).where(PackageMenu.brand == brand, PackageMenu.package_id == package_id))
    if row is not None:
        db.delete(row)


def dish_map(db: Session, plan: dict) -> dict[int, Dish]:
    ids = {e["dish"] for entries in plan["cells"].values() for e in entries}
    return {d.id: d for d in db.scalars(select(Dish).where(Dish.id.in_(ids)))} if ids else {}


# --- Plan → menyu --------------------------------------------------------------------------------


def _cell(plan: dict, w: int, d: int, meal: str, course: str) -> list[dict]:
    if f"{meal}:{course}" in plan["same"]:
        return plan["cells"].get(f"w{w}:*:{meal}:{course}", [])
    return plan["cells"].get(f"w{w}:{d}:{meal}:{course}", [])


def _week_suffix(plan: dict, w: int) -> tuple[str, str, str]:
    if plan["weeks"] < 2:
        return "", "", ""
    return f" · {w}-ci həftə", f" · Week {w}", f" · неделя {w}"


def _note(plan: dict, entry: dict) -> str:
    """"250 q · 420 kkal · Allergenlər: süd" — vahidi olan sahə rəqəm + vahid, qalanı "Ad: dəyər"."""
    parts = []
    for f in plan["fields"]:
        v = (entry.get("x") or {}).get(f["id"])
        if v:
            parts.append(f"{v} {f['unit']}" if f.get("unit") else f"{f['name']}: {v}")
    return " · ".join(parts)


def every_day(plan: dict) -> bool:
    """Bütün dolu sətirlər "hər gün eyni"dir (seçimli menyu, məs. Medium plus: hər gün 5 şorba, 8 əsas yemək …) —
    menyu gün-gün yox, bir blok kimi göstərilir."""
    rows = {k.split(":", 2)[2] for k in plan["cells"]}
    return plan["weeks"] == 1 and bool(rows) and all(r in plan["same"] for r in rows)


def _flat_agent_menu(plan: dict, dishes: dict[int, Dish]) -> list[dict]:
    out = []
    many_meals = len(plan["meals"]) > 1
    for meal in plan["meals"]:
        meal_en, meal_ru = MEAL_I18N.get(meal["name"], (meal["name"], meal["name"]))
        for course in plan["courses"]:
            items = [dishes[e["dish"]] for e in _cell(plan, 1, 0, meal["id"], course["id"]) if e["dish"] in dishes]
            if not items:
                continue
            az, en = section_name(course["name"])
            ru = COURSE_I18N.get(dish_catalog.category_name(course["name"]), ("", course["name"]))[1]
            title = {"az": az, "en": en or az, "ru": ru}
            if many_meals:
                title = {"az": f"{meal['name']} — {az}", "en": f"{meal_en} — {en or az}", "ru": f"{meal_ru} — {ru}"}
            out.append({"title": title, "items": [{"az": d.name, "en": d.name_en or d.name, "ru": d.name_ru or d.name} for d in items]})
    return out


def agent_menu(plan: dict, dishes: dict[int, Dish]) -> list[dict]:
    """Agentə gedən paketin `menu`-su: hər həftə × gün bir bölmə, maddə "Kateqoriya: yemək" (az/en/ru).
    Hər gün eyni menyuda — kateqoriya bölmələri ("Şorbalar": yeməklər), nümunə Medium plus PDF-i kimi."""
    if every_day(plan):
        return _flat_agent_menu(plan, dishes)
    out = []
    many_meals = len(plan["meals"]) > 1
    for w in range(1, plan["weeks"] + 1):
        sfx = _week_suffix(plan, w)
        for d in plan["days"]:
            day = WEEKDAYS[d]
            items = []
            for meal in plan["meals"]:
                meal_en, meal_ru = MEAL_I18N.get(meal["name"], (meal["name"], meal["name"]))
                for course in plan["courses"]:
                    c_en, c_ru = COURSE_I18N.get(dish_catalog.category_name(course["name"]), (course["name"], course["name"]))
                    for e in _cell(plan, w, d, meal["id"], course["id"]):
                        dish = dishes.get(e["dish"])
                        if dish is None:
                            continue
                        az = f"{course['name']}: {dish.name}"
                        en = f"{c_en}: {dish.name_en or dish.name}"
                        ru = f"{c_ru}: {dish.name_ru or dish.name}"
                        if many_meals:
                            az, en, ru = f"{meal['name']} — {az}", f"{meal_en} — {en}", f"{meal_ru} — {ru}"
                        items.append({"az": az, "en": en, "ru": ru})
            if items:
                out.append({"title": {"az": day[0] + sfx[0], "en": day[1] + sfx[1], "ru": day[2] + sfx[2]}, "items": items})
    return out


def pdf_package(plan: dict, dishes: dict[int, Dish], package: dict, lang: str = "az", currency: str = "AZN") -> Package:
    """offer_pdf üçün paket: hər həftə × gün × yemək vaxtı bir blok; yeməyin əlavə sahələri adın altında."""
    def tr(v: Any) -> str:
        if isinstance(v, dict):
            return str(v.get(lang) or v.get("az") or "").strip()
        return str(v or "").strip()

    days = plan["days"]
    span = (f"{WEEKDAYS[min(days)][0]} – {WEEKDAYS[max(days)][0]}", f"{WEEKDAYS[min(days)][1]} – {WEEKDAYS[max(days)][1]}") \
        if len(days) > 1 else (WEEKDAYS[days[0]][0], WEEKDAYS[days[0]][1])
    flat = every_day(plan)
    blocks = []
    for w in range(1, plan["weeks"] + 1):
        sfx = _week_suffix(plan, w)
        for d in (plan["days"][:1] if flat else plan["days"]):   # hər gün eyni — bir blok
            day = WEEKDAYS[d]
            for meal in plan["meals"]:
                groups = []
                for course in plan["courses"]:
                    g = Group(section_name(course["name"])[0] if flat else course["name"])
                    for e in _cell(plan, w, d, meal["id"], course["id"]):
                        dish = dishes.get(e["dish"])
                        if dish is None:
                            continue
                        name = {"en": dish.name_en, "ru": dish.name_ru}.get(lang) or dish.name
                        if name not in g.items:
                            g.items.append(name)
                            note = _note(plan, e)
                            if note:
                                g.notes[name] = note
                    if g.items:
                        groups.append(g)
                if not groups:
                    continue
                meal_label = " ".join(x for x in (meal["name"] if len(plan["meals"]) > 1 or meal.get("time") else "",
                                                   meal.get("time") or "") if x)
                if flat:
                    blocks.append(Block(span[0], " · ".join(x for x in (span[1], meal_label) if x), groups))
                    continue
                accent = " · ".join(x for x in (day[1] + sfx[1], meal_label) if x)
                blocks.append(Block((day[2] if lang == "ru" else day[0]) + (sfx[2] if lang == "ru" else sfx[0]), accent, groups))
    name = tr(package.get("name")) or str(package.get("id") or package.get("package_id") or "")
    name_en = str((package.get("name") or {}).get("en") or "").strip() if isinstance(package.get("name"), dict) else ""
    price = package.get("unit_price", package.get("price"))
    return Package(name=name, name_en=name_en, weekly=not flat, blocks=blocks, price=_money(price, currency),
                   unit=tr(package.get("unit")), description=tr(package.get("description")), days_span=span,
                   daily=len(plan["meals"]) == 1)


def same_menu(a: Any, b: Any) -> bool:
    """Agentdəki/təklifdəki menyu plandan yaradılanla eynidirmi (az mətnlərə görə)."""
    def norm(menu: Any) -> list:
        out = []
        for s in menu or []:
            if isinstance(s, dict):
                title = (s.get("title") or {}).get("az") if isinstance(s.get("title"), dict) else s.get("title")
                out.append((title, [(i.get("az") if isinstance(i, dict) else i) for i in s.get("items") or []]))
        return out
    return norm(a) == norm(b)


def plans_for_snapshot(db: Session, snapshot: dict) -> dict[int, Package]:
    """Təklifdəki paketlərdən menyu planı olanlar (təklifdəki menyu plandakı ilə hələ eynidirsə — köhnə təklif
    sonradan dəyişmiş planla göstərilməsin)."""
    brand = snapshot.get("brand") or "seaport"
    lang = snapshot.get("language") if snapshot.get("language") in ("az", "en", "ru") else "az"
    currency = (snapshot.get("totals") or {}).get("currency") or "AZN"
    plans = load_all(db, brand)
    out: dict[int, Package] = {}
    lines = [l for l in snapshot.get("lines") or [] if isinstance(l, dict)]
    for i, line in enumerate(lines):
        plan = plans.get(str(line.get("package_id") or ""))
        if plan is None:
            continue
        dishes = dish_map(db, plan)
        if same_menu(agent_menu(plan, dishes), line.get("menu")):
            out[i] = pdf_package(plan, dishes, line, lang, currency)
    return out


# --- Köhnə mətn menyusundan plan (ilk dəfə açanda) ----------------------------------------------


def from_agent_menu(db: Session, menu: Any) -> dict:
    """Agentdəki mətn menyusunu ("Bazar ertəsi" → "Şorba: Mərcimək şorbası") plana çevirir. Kataloqda olmayan
    yeməklər kataloqa əlavə olunur (şəkilsiz). Gün olmayan bölmələr (sadə menyu) "hər gün eyni" sayılır."""
    plan = default_plan()
    plan["courses"], plan["same"], plan["cells"] = [], [], {}
    sections = [s for s in menu or [] if isinstance(s, dict)]
    if not sections:
        return default_plan()
    days = [_weekday(s.get("title")) for s in sections]
    weekly = all(d is not None for d in days)
    course_ids: dict[str, str] = {}
    seen_days: list[int] = []

    def course_id(name: str) -> str:
        label = dish_catalog.category_name(name) or name
        if label not in course_ids:
            course_ids[label] = f"c{len(course_ids) + 1}"
            plan["courses"].append({"id": course_ids[label], "name": label})
        return course_ids[label]

    entries: list[tuple[str, str, str]] = []   # (gün, kateqoriya id, yemək adı)
    for sec, d in zip(sections, days):
        title = sec.get("title") or {}
        title_az = (title.get("az") if isinstance(title, dict) else title) or ""
        for it in sec.get("items") or []:
            text = (it.get("az") if isinstance(it, dict) else it) or ""
            text = " ".join(str(text).split())
            if not text:
                continue
            if weekly:
                m = _CAT_RE.match(text)
                label, dish = (m.group(1).strip(), m.group(2).strip()) if m else ("Digər", text)
                if " — " in label:          # "Nahar — Şorba"
                    label = label.split(" — ", 1)[1]
                if d not in seen_days:
                    seen_days.append(d)
                entries.append((str(d), course_id(label), dish))
            else:
                entries.append(("*", course_id(title_az or "Digər"), text))
    if not plan["courses"]:
        return default_plan()
    names = [n for _, _, n in entries]
    dish_catalog.remember(db, names, {n: next((c["name"] for c in plan["courses"] if c["id"] == cid), "")
                                      for _, cid, n in entries})
    db.flush()
    by_key = {d.key: d.id for d in db.scalars(select(Dish).where(Dish.key.in_({dish_catalog.dish_key(n) for n in names})))}
    if weekly:
        plan["days"] = sorted(seen_days)
        # Hər gün eyni olan kateqoriya (çörək) — "hər gün eyni" sətri
        for c in plan["courses"]:
            per_day = [sorted(n for dd, cid, n in entries if dd == str(day) and cid == c["id"]) for day in plan["days"]]
            if len(plan["days"]) > 1 and per_day[0] and all(p == per_day[0] for p in per_day):
                plan["same"].append(f"m1:{c['id']}")
    for dd, cid, n in entries:
        same = f"m1:{cid}" in plan["same"]
        key = f"w1:{'*' if same or dd == '*' else dd}:m1:{cid}"
        if dd == "*" and f"m1:{cid}" not in plan["same"]:
            plan["same"].append(f"m1:{cid}")
        dish_id = by_key.get(dish_catalog.dish_key(n))
        cell = plan["cells"].setdefault(key, [])
        if dish_id and all(e["dish"] != dish_id for e in cell) and len(cell) < MAX_PER_CELL:
            cell.append({"dish": dish_id})
    return plan
