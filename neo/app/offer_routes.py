"""Alətlər → Qiymət təklifi və Paketlər.

Brauzer yalnız bu route-lara müraciət edir; agentə sorğunu `app/offer_client.py` göndərir
(açarlar serverdə qalır). Təklif yaratmaq/baxmaq: "sales" və yuxarı; paket redaktəsi: yalnız admin.
JSON çağırışlarında CSRF tokeni `X-CSRF-Token` başlığı ilə gəlir.
"""
from __future__ import annotations

import base64
import hmac
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app import offer_client as agent
from app import dishes, offer_pdf, package_menus
from app.auth import security
from app.auth.security import REQUIRE, csrf_token
from app.db.models import Dish, LeadRecord, OfferRecord, User

log = structlog.get_logger()
router = APIRouter()

HISTORY_LIMIT = 50
MAX_PACKAGES = 200
MAX_LEAD_TEXT = agent.MAX_MESSAGE_LENGTH


async def verify_csrf_header(request: Request) -> None:
    sent = request.headers.get("x-csrf-token", "")
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Sessiya vaxtı bitib. Səhifəni yeniləyin.")


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _agent_error(exc: agent.OfferAgentError) -> JSONResponse:
    return _error(exc.message, exc.status)


async def _body(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError:
        return None


# --- Təklif yaratmaq -----------------------------------------------------------


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def record_offer(db, data: dict, body: dict, user: User, lead_id: str | None,
                 pictures: bool = False) -> OfferRecord:
    totals = data.get("totals") or {}
    record = OfferRecord(
        offer_id=str(data["offer_id"]), brand=body["brand"],
        customer_name=(data.get("customer_name") or body.get("customer_name") or None),
        total=_to_decimal(totals.get("total")), currency=totals.get("currency"),
        sample=bool(data.get("sample")), created_by_id=user.id, lead_id=lead_id, pictures=pictures,
    )
    db.add(record)
    db.commit()
    return record


def render_pdf(snapshot: dict, pictures: bool = False) -> bytes:
    """Neo dizaynında PDF. Şəkilli təklifdə yeməklərin şəkilləri kataloqdan (app/dishes.py) götürülür;
    kataloqda olmayan yeməklər şəkilsiz əlavə olunur ki, "Yemək şəkilləri"ndə şəkli gözləyən kimi görünsün."""
    with security.SessionLocal() as db:
        plans = package_menus.plans_for_snapshot(db, snapshot)   # menyu planı olan paket: vaxtlar, əlavə sahələr
    if not pictures:
        return offer_pdf.render(snapshot, plans=plans)
    items = offer_pdf.menu_items(snapshot)
    names = [name for name, _ in items]
    with security.SessionLocal() as db:
        try:
            if snapshot.get("language", "az") == "az" and dishes.remember(db, names, {name: dishes.category_name(cat) for name, cat in items}):
                db.commit()
        except Exception:
            db.rollback()
            log.exception("dish_remember_failed")
        dishes.seed_starter_photos(db)
        photos = dishes.photos_for(db, names + [i for p in plans.values() for b in p.blocks for g in b.groups for i in g.items])
    return offer_pdf.render(snapshot, photos, plans)


def _render_base64(data: dict, pictures: bool = False) -> str:
    snapshot = data if data.get("lines") else agent.get_offer(str(data["offer_id"]))
    return base64.b64encode(render_pdf(snapshot, pictures)).decode()


@router.post("/api/offers")
async def offers_create(request: Request, me: User = Depends(REQUIRE["sales"]),
                  _csrf: None = Depends(verify_csrf_header)):
    raw = await _body(request)
    try:
        body = agent.validate_offer_request(raw)
        body = agent.with_package(body, raw.get("package_id"), raw.get("package_name"))
    except ValueError as exc:
        return _error(str(exc), 422)
    lead_id = raw.get("lead_id") if isinstance(raw.get("lead_id"), str) else None
    pictures = raw.get("pictures") is True

    # Agent çağırışı (60 s-ə qədər) və DB sinxrondur — threadpool-da, yoxsa async route bütün paneli dondururdu.
    try:
        data = await run_in_threadpool(agent.create_offer, body)
    except agent.OfferAgentError as exc:
        return _agent_error(exc)
    if data["status"] != "ok":
        return JSONResponse(data)
    # Önizləmə/yükləmə də Neo-nun dizaynında olsun (agentin pdf_base64-ü əvəzinə; alınmasa agentinki qalır).
    try:
        data["pdf_base64"] = await run_in_threadpool(_render_base64, data, pictures)
    except Exception:
        log.exception("offer_pdf_render_failed", offer_id=data.get("offer_id"))

    def save() -> None:
        nonlocal lead_id
        with security.SessionLocal() as db:
            if lead_id and db.get(LeadRecord, lead_id) is None:
                lead_id = None
            if db.scalar(select(OfferRecord.id).where(OfferRecord.offer_id == str(data["offer_id"]))) is None:
                record_offer(db, data, body, me, lead_id, pictures)

    try:
        await run_in_threadpool(save)
    except Exception:
        # Təklif agentdə artıq yaranıb — istifadəçi onu görsün, yalnız tarixçəyə düşmədiyi loglansın.
        log.exception("offer_record_failed", offer_id=data.get("offer_id"))
    log.info("offer_created", offer_id=data["offer_id"], brand=body["brand"], user=me.email,
             total=(data.get("totals") or {}).get("total"), sample=bool(data.get("sample")), lead=lead_id,
             pictures=pictures)
    return JSONResponse(data)


def _offer_record(offer_id: str) -> OfferRecord | None:
    if not agent.OFFER_ID_RE.match(offer_id):
        return None
    with security.SessionLocal() as db:
        return db.scalar(select(OfferRecord).where(OfferRecord.offer_id == offer_id))


def _known_offer(offer_id: str) -> bool:
    return _offer_record(offer_id) is not None


@router.get("/api/offers/{offer_id}/pdf")
def offers_pdf(offer_id: str, pictures: str = "", me: User = Depends(REQUIRE["sales"])):
    record = _offer_record(offer_id)
    if record is None:
        return _error("Təklif tapılmadı", 404)
    # Şəkilli/şəkilsiz: təklif necə yaradılıbsa elə; ?pictures=1/0 ilə o biri variantı da almaq olar.
    with_pictures = pictures == "1" if pictures in ("0", "1") else bool(record.pictures)
    # PDF Neo-da qurulur (Seaport korporativ menyu dizaynı, app/offer_pdf.py) — agentin snapshot-undan.
    # Qurmaq alınmasa, agentin öz PDF-i verilir ki, istifadəçi boş qalmasın.
    try:
        pdf = render_pdf(agent.get_offer(offer_id), with_pictures)
    except agent.OfferAgentError as exc:
        return _agent_error(exc)
    except Exception:
        log.exception("offer_pdf_render_failed", offer_id=offer_id)
        try:
            pdf = agent.get_offer_pdf(offer_id)
        except agent.OfferAgentError as exc:
            return _agent_error(exc)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{offer_id}.pdf"'})


@router.get("/api/offers/{offer_id}")
def offers_snapshot(offer_id: str, me: User = Depends(REQUIRE["sales"])):
    if not _known_offer(offer_id):
        return _error("Təklif tapılmadı", 404)
    try:
        return JSONResponse(agent.get_offer(offer_id))
    except agent.OfferAgentError as exc:
        return _agent_error(exc)


# --- Səhifə --------------------------------------------------------------------


def _history(db) -> list[dict]:
    rows = db.execute(
        select(OfferRecord, User.full_name).outerjoin(User, User.id == OfferRecord.created_by_id)
        .order_by(OfferRecord.created_at.desc(), OfferRecord.id.desc()).limit(HISTORY_LIMIT)).all()
    return [{"offer_id": o.offer_id, "brand": o.brand, "customer_name": o.customer_name,
             "total": o.total, "currency": o.currency or "AZN", "sample": o.sample,
             "created_by": name or "—", "created_at": o.created_at, "lead_id": o.lead_id,
             "pictures": bool(o.pictures)}
            for o, name in rows]


def lead_prefill(lead: LeadRecord) -> dict:
    """Lead-in mövcud sahələrindən (başlıq, mətn, qurum) sorğu mətni — yeni məlumat uydurulmur."""
    text = lead.title.strip()
    body = (lead.raw_text or "").strip()
    if body and body != text:
        text = f"{text}\n\n{body}"
    return {"lead_id": lead.dedupe_key, "message": text[:MAX_LEAD_TEXT], "customer_name": (lead.entity_name or "")[:200]}


@router.get("/tools/offers")
def offers_page(request: Request, lead: str = "", brand: str = "seaport", me: User = Depends(REQUIRE["sales"])):
    prefill = None
    with security.SessionLocal() as db:
        history = _history(db)
        if lead:
            found = db.get(LeadRecord, lead)
            if found is not None:
                prefill = lead_prefill(found)
    return request.app.state.templates.TemplateResponse(request, "offers.html", {
        "request": request, "active": "offers", "brands": agent.BRAND_NAMES,
        "brand": brand if brand in agent.BRANDS else "seaport", "history": history, "prefill": prefill,
        "csrf": csrf_token(request), "can_manage": security.has_role(me, "admin"),
    })


@router.get("/api/offers/packages/{brand}")
def offers_packages(brand: str, me: User = Depends(REQUIRE["sales"])):
    """Təklif formundakı "Paket" seçimi üçün aktiv paketlər (yalnız ad, id, qiymət — redaktə admin-dədir)."""
    if (bad := _brand_or_error(brand)):
        return bad
    try:
        data = agent.list_packages(brand)
    except agent.OfferAgentError as exc:
        return _agent_error(exc)
    items = data if isinstance(data, list) else (data.get("packages") if isinstance(data, dict) else None) or []
    out = []
    for p in items:
        if not isinstance(p, dict) or not p.get("active", True) or not isinstance(p.get("id"), str):
            continue
        name = p.get("name")
        name = (name.get("az") or p["id"]) if isinstance(name, dict) else (name or p["id"])
        out.append({"id": p["id"], "name": str(name)[:120], "price": p.get("price"), "example": bool(p.get("example")),
                    "sort_order": p.get("sort_order") or 0})
    out.sort(key=lambda x: (x["sort_order"], x["name"]))
    return JSONResponse({"packages": out})


# --- Paketlər (yalnız admin) -----------------------------------------------------


@router.get("/tools/packages")
def packages_page(request: Request, me: User = Depends(REQUIRE["admin"])):
    return request.app.state.templates.TemplateResponse(request, "offer_packages.html", {
        "request": request, "active": "packages", "brands": agent.BRAND_NAMES, "csrf": csrf_token(request),
    })


def _brand_or_error(brand: str) -> JSONResponse | None:
    try:
        agent.validate_brand(brand)
    except ValueError as exc:
        return _error(str(exc), 404)
    return None


@router.get("/api/offer-packages/{brand}")
def packages_list(brand: str, me: User = Depends(REQUIRE["admin"])):
    if (bad := _brand_or_error(brand)):
        return bad
    try:
        return JSONResponse(agent.list_packages(brand))
    except agent.OfferAgentError as exc:
        return _agent_error(exc)


@router.put("/api/offer-packages/{brand}")
async def packages_replace(brand: str, request: Request, me: User = Depends(REQUIRE["admin"]),
                  _csrf: None = Depends(verify_csrf_header)):
    if (bad := _brand_or_error(brand)):
        return bad
    raw = await _body(request)
    packages = raw.get("packages") if isinstance(raw, dict) else None
    if not isinstance(packages, list) or not packages or len(packages) > MAX_PACKAGES:
        return _error(f"JSON-da «packages» siyahısı olmalıdır (1–{MAX_PACKAGES} paket)", 422)
    try:
        ids = [agent.validate_package_id(p.get("id") if isinstance(p, dict) else None) for p in packages]
    except ValueError as exc:
        return _error(str(exc), 422)
    if len(set(ids)) != len(ids):
        return _error("Paket id-ləri təkrarlanır", 422)
    try:
        result = await run_in_threadpool(agent.replace_packages, brand, packages)
    except agent.OfferAgentError as exc:
        return _agent_error(exc)
    log.info("offer_packages_replaced", brand=brand, count=len(packages), user=me.email)
    return JSONResponse(result)


@router.put("/api/offer-packages/{brand}/{package_id}")
async def packages_update(brand: str, package_id: str, request: Request, me: User = Depends(REQUIRE["admin"]),
                  _csrf: None = Depends(verify_csrf_header)):
    if (bad := _brand_or_error(brand)):
        return bad
    raw = await _body(request)
    try:
        agent.validate_package_id(package_id)
    except ValueError as exc:
        return _error(str(exc), 422)
    if not isinstance(raw, dict) or raw.get("id") != package_id:
        return _error("Paketin məlumatı yanlışdır", 422)
    # Menyu planı ilə: agentə gedən mətn menyusu plandan yaradılır, plan Neo-nun bazasında saxlanılır
    plan = None
    if "plan" in raw:
        with security.SessionLocal() as db:
            try:
                plan = package_menus.clean(raw.pop("plan"), db)
            except package_menus.PlanError as exc:
                return _error(str(exc), 422)
            raw["menu"] = package_menus.agent_menu(plan, package_menus.dish_map(db, plan))
    try:
        result = await run_in_threadpool(agent.update_package, brand, package_id, raw)
    except agent.OfferAgentError as exc:
        return _agent_error(exc)
    if plan is not None:
        with security.SessionLocal() as db:
            package_menus.save(db, brand, package_id, plan, me.id)
            db.commit()
    log.info("offer_package_saved", brand=brand, package=package_id, user=me.email, plan=plan is not None)
    return JSONResponse(result)


@router.delete("/api/offer-packages/{brand}/{package_id}")
def packages_delete(brand: str, package_id: str, me: User = Depends(REQUIRE["admin"]),
                  _csrf: None = Depends(verify_csrf_header)):
    if (bad := _brand_or_error(brand)):
        return bad
    try:
        agent.validate_package_id(package_id)
        agent.delete_package(brand, package_id)
    except ValueError as exc:
        return _error(str(exc), 422)
    except agent.OfferAgentError as exc:
        return _agent_error(exc)
    with security.SessionLocal() as db:
        package_menus.delete(db, brand, package_id)
        db.commit()
    log.info("offer_package_deleted", brand=brand, package=package_id, user=me.email)
    return JSONResponse({"ok": True})


# --- Menyu planı (paket redaktorunda) ------------------------------------------------


def _dish_json(d: Dish, has_image: bool) -> dict:
    return {"id": d.id, "name": d.name, "name_en": d.name_en or "", "name_ru": d.name_ru or "",
            "category": d.category or "",
            "img": f"/dishes/{d.id}/image?v={int(d.image_updated_at.timestamp()) if d.image_updated_at else 0}" if has_image else None}


@router.get("/api/dish-catalog")
def dish_catalog(me: User = Depends(REQUIRE["sales"])):
    """Menyu planının yemək seçicisi üçün kataloq (şəkil özü yox, yalnız ünvanı)."""
    with security.SessionLocal() as db:
        dishes.seed_starter_photos(db)
        with_image = set(db.scalars(select(Dish.id).where(Dish.image.is_not(None))))
        rows = db.scalars(select(Dish).order_by(Dish.name)).all()
        return JSONResponse({"dishes": [_dish_json(d, d.id in with_image) for d in rows],
                             "categories": dishes.CATEGORIES})


@router.post("/api/dish-catalog")
async def dish_catalog_add(request: Request, me: User = Depends(REQUIRE["sales"]),
                           _csrf: None = Depends(verify_csrf_header)):
    raw = await _body(request)
    if not isinstance(raw, dict):
        return _error("Sorğu yanlışdır", 422)
    with security.SessionLocal() as db:
        try:
            d = dishes.add_dish(db, str(raw.get("name") or ""), str(raw.get("category") or ""), user_id=me.id)
        except dishes.DishError as exc:
            return _error(str(exc), 422)
        db.commit()
        return JSONResponse({"dish": _dish_json(d, False)})


def _find_package(brand: str, package_id: str) -> dict | None:
    data = agent.list_packages(brand)
    items = data if isinstance(data, list) else (data.get("packages") if isinstance(data, dict) else None) or []
    return next((p for p in items if isinstance(p, dict) and p.get("id") == package_id), None)


@router.get("/api/offer-packages/{brand}/plans")
def package_plan_ids(brand: str, me: User = Depends(REQUIRE["admin"])):
    if (bad := _brand_or_error(brand)):
        return bad
    with security.SessionLocal() as db:
        return JSONResponse({"ids": sorted(package_menus.load_all(db, brand))})


@router.get("/api/offer-packages/{brand}/{package_id}/plan")
def package_plan(brand: str, package_id: str, source: str = "", me: User = Depends(REQUIRE["admin"])):
    """Saxlanmış plan; yoxdursa (və ya ?source=agent) — agentdəki mətn menyusundan çevrilmiş (hələ saxlanmayıb,
    `imported`). Saxlanmış plan agentdəki menyudan fərqlənirsə (məs. JSON ilə yüklənib), plan qaytarılır və
    `differs` — istifadəçi seçir: planı saxlasın və ya mətndən yenidən qursun."""
    if (bad := _brand_or_error(brand)):
        return bad
    with security.SessionLocal() as db:
        plan = package_menus.load(db, brand, package_id)
        menu = None
        if package_id != "_new":
            try:
                found = _find_package(brand, package_id)
            except agent.OfferAgentError as exc:
                if plan is None:
                    return _agent_error(exc)
                found = None          # agent cavab vermir — saxlanmış plan yenə də açılsın
            menu = (found or {}).get("menu")
        if plan is not None and source != "agent":
            differs = bool(menu) and not package_menus.same_menu(package_menus.agent_menu(plan, package_menus.dish_map(db, plan)), menu)
            return JSONResponse({"plan": plan, "imported": False, "differs": differs, "presets": package_menus.FIELD_PRESETS})
        plan = package_menus.from_agent_menu(db, menu) if menu else package_menus.default_plan()
        db.commit()   # çevirmədə kataloqa düşən yeni yeməklər
        return JSONResponse({"plan": plan, "imported": bool(menu), "presets": package_menus.FIELD_PRESETS})


def _menu_pdf(package: dict, plan: dict, brand: str, schedule: bool) -> bytes:
    with security.SessionLocal() as db:
        dishes.seed_starter_photos(db)
        dish_rows = package_menus.dish_map(db, plan)
        pkg = package_menus.pdf_package(plan, dish_rows, package)
        photos = dishes.photos_for(db, [i for b in pkg.blocks for g in b.groups for i in g.items])
    return offer_pdf.render_menu(pkg, photos, brand, plan.get("note") or "", schedule)


def _pdf_response(pdf: bytes, name: str) -> Response:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "menu"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{safe}.pdf"'})


@router.post("/api/offer-packages/{brand}/{package_id}/menu.pdf")
async def package_menu_preview(brand: str, package_id: str, request: Request, me: User = Depends(REQUIRE["admin"]),
                               _csrf: None = Depends(verify_csrf_header)):
    """Redaktordakı (hələ saxlanmamış) planın PDF-i — "PDF-ə bax"."""
    if (bad := _brand_or_error(brand)):
        return bad
    raw = await _body(request)
    if not isinstance(raw, dict) or not isinstance(raw.get("package"), dict):
        return _error("Sorğu yanlışdır", 422)
    with security.SessionLocal() as db:
        try:
            plan = package_menus.clean(raw.get("plan"), db)
        except package_menus.PlanError as exc:
            return _error(str(exc), 422)
    pdf = await run_in_threadpool(_menu_pdf, raw["package"], plan, brand, raw.get("schedule") is not False)
    return _pdf_response(pdf, f"{package_id}-menu")


@router.get("/tools/packages/{brand}/{package_id}/menu.pdf")
def package_menu_pdf(brand: str, package_id: str, schedule: str = "1", me: User = Depends(REQUIRE["sales"])):
    """Saxlanmış planın PDF-i (müştəriyə göndərmək üçün): üz qabığı, həftəlik menyu, yemək şəkilləri."""
    if (bad := _brand_or_error(brand)):
        return bad
    with security.SessionLocal() as db:
        plan = package_menus.load(db, brand, package_id)
    if plan is None:
        return _error("Bu paketin menyu planı yoxdur", 404)
    try:
        package = _find_package(brand, package_id) or {"id": package_id, "name": {"az": package_id}}
    except agent.OfferAgentError as exc:
        return _agent_error(exc)
    return _pdf_response(_menu_pdf(package, plan, brand, schedule != "0"), f"{package_id}-menu")
