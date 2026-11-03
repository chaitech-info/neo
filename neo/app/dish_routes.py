"""Alətlər → Yemək şəkilləri: yemək kataloqu və şəkilləri (şəkilli qiymət təklifi üçün, app/dishes.py).

Baxmaq, yemək əlavə etmək və şəkli dəyişmək — "sales" və yuxarı; yeməyi kataloqdan silmək — menecer.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import undefer

from app import dishes
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.crm_routes import _go, _render
from app.db.models import Dish, User

router = APIRouter()

FILTERS = {"all": "Hamısı", "missing": "Şəkli yoxdur", "done": "Şəkli var"}


def _back(show: str, q: str, dish_id: int | None = None) -> str:
    path = f"/tools/dishes?show={show if show in FILTERS else 'all'}"
    if q:
        path += f"&q={quote(q)}"
    return path + (f"#d{dish_id}" if dish_id else "")


@router.get("/tools/dishes")
def dishes_page(request: Request, show: str = "all", q: str = "", me: User = Depends(REQUIRE["sales"])):
    show = show if show in FILTERS else "all"
    q = q.strip()[:200]
    with security.SessionLocal() as db:
        base = select(Dish)
        if q:
            like = f"%{q}%"
            base = base.where(or_(Dish.name.ilike(like), Dish.name_en.ilike(like), Dish.name_ru.ilike(like)))
        has = Dish.image.is_not(None)
        counts = {
            "all": db.scalar(select(func.count()).select_from(base.subquery())) or 0,
            "missing": db.scalar(select(func.count()).select_from(base.where(~has).subquery())) or 0,
            "done": db.scalar(select(func.count()).select_from(base.where(has).subquery())) or 0,
        }
        if show == "missing":
            base = base.where(~has)
        elif show == "done":
            base = base.where(has)
        rows = list(db.scalars(base.order_by(Dish.name)))
        with_image = set(db.scalars(select(Dish.id).where(has)))
    groups: dict[str, list[Dish]] = {}
    for d in rows:
        groups.setdefault(d.category or "Digər", []).append(d)
    order = {c: i for i, c in enumerate(dishes.CATEGORIES)}   # menyudakı sıra: şorba, əsas yemək, qarnir, …
    groups = dict(sorted(groups.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0])))
    return _render(request, "dishes.html", {
        "active": "dishes", "groups": groups, "show": show, "q": q, "filters": FILTERS, "counts": counts,
        "with_image": with_image, "categories": dishes.CATEGORIES, "back": _back(show, q),
        "can_delete": security.has_role(me, "manager"),
    })


@router.get("/dishes/{dish_id}/image")
def dish_image(dish_id: int, me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        d = db.scalar(select(Dish).options(undefer(Dish.image)).where(Dish.id == dish_id))
        if d is None or d.image is None:
            return Response(status_code=404)
        # URL-də ?v=<dəyişmə vaxtı> var — şəkil dəyişəndə URL də dəyişir, ona görə uzun keş təhlükəsizdir
        return Response(d.image, media_type=d.image_type or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=31536000"})


@router.post("/tools/dishes", dependencies=[Depends(verify_csrf)])
async def dishes_add(name: str = Form(""), category: str = Form(""), name_en: str = Form(""),
                     name_ru: str = Form(""), image: Optional[UploadFile] = File(None),
                     me: User = Depends(REQUIRE["sales"])):
    data = await image.read() if image is not None and image.filename else b""
    with security.SessionLocal() as db:
        try:
            d = dishes.add_dish(db, name, category, name_en, name_ru, me.id)
            if data:
                dishes.set_image(d, data, me.id)
        except dishes.DishError as exc:
            return _go("/tools/dishes", err=str(exc))
        db.commit()
        return _go(_back("all", "", d.id), msg=f"«{d.name}» əlavə olundu")


@router.post("/tools/dishes/{dish_id}/image", dependencies=[Depends(verify_csrf)])
async def dishes_set_image(dish_id: int, image: Optional[UploadFile] = File(None), show: str = Form("all"),
                           q: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    data = await image.read() if image is not None else b""
    with security.SessionLocal() as db:
        d = db.get(Dish, dish_id)
        if d is None:
            return _go(_back(show, q), err="Yemək tapılmadı")
        try:
            dishes.set_image(d, data, me.id)
        except dishes.DishError as exc:
            return _go(_back(show, q, dish_id), err=str(exc))
        db.commit()
        return _go(_back(show, q, dish_id), msg=f"«{d.name}» şəkli yeniləndi")


@router.post("/tools/dishes/{dish_id}/image/delete", dependencies=[Depends(verify_csrf)])
def dishes_clear_image(dish_id: int, show: str = Form("all"), q: str = Form(""),
                       me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        d = db.get(Dish, dish_id)
        if d is None:
            return _go(_back(show, q), err="Yemək tapılmadı")
        dishes.clear_image(d, me.id)
        db.commit()
        return _go(_back(show, q, dish_id), msg=f"«{d.name}» şəkli silindi")


@router.post("/tools/dishes/{dish_id}", dependencies=[Depends(verify_csrf)])
def dishes_update(dish_id: int, name: str = Form(""), category: str = Form(""), name_en: str = Form(""),
                  name_ru: str = Form(""), show: str = Form("all"), q: str = Form(""),
                  me: User = Depends(REQUIRE["sales"])):
    name = " ".join(name.split())[:300]
    key = dishes.dish_key(name)
    with security.SessionLocal() as db:
        d = db.get(Dish, dish_id)
        if d is None:
            return _go(_back(show, q), err="Yemək tapılmadı")
        if len(key) < 2:
            return _go(_back(show, q, dish_id), err="Yeməyin adını yaz")
        if key != d.key and db.scalar(select(Dish.id).where(Dish.key == key)) is not None:
            return _go(_back(show, q, dish_id), err=f"«{name}» kataloqda artıq var")
        d.name, d.key = name, key
        d.category = category.strip()[:60] or None
        d.name_en = name_en.strip()[:300] or None
        d.name_ru = name_ru.strip()[:300] or None
        d.updated_by_id = me.id
        db.commit()
        return _go(_back(show, q, dish_id), msg="Yadda saxlanıldı")


@router.post("/tools/dishes/{dish_id}/delete", dependencies=[Depends(verify_csrf)])
def dishes_delete(dish_id: int, show: str = Form("all"), q: str = Form(""),
                  me: User = Depends(REQUIRE["manager"])):
    with security.SessionLocal() as db:
        d = db.get(Dish, dish_id)
        if d is None:
            return _go(_back(show, q), err="Yemək tapılmadı")
        name = d.name
        db.delete(d)
        db.commit()
    return _go(_back(show, q), msg=f"«{name}» kataloqdan silindi")
