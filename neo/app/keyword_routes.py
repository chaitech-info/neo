"""Açar sözlər və maraq sahələri — paneldən redaktə (menecer və admin).

Dəyişiklik dərhal keşi təmizləyir (app/keywords.py::invalidate) — növbəti scrape
və AI qiymətləndirməsi yeni siyahını işlədir; Celery worker ən gec 1 dəqiqəyə görür.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import keywords
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.db.models import KEYWORD_LANGUAGES, KEYWORD_MATCHES, KEYWORD_SCOPES, InterestArea, Keyword, User, UserKeyword

router = APIRouter()

MAX_TERM_LENGTH = 100
SCOPE_NAMES = {"all": "Hamısı", "news": "Xəbər", "tender": "Tender", "jobs": "Vakansiya"}
MATCH_NAMES = {"prefix": "Söz başı (şəkilçili)", "word": "Tam söz", "phrase": "İfadə"}
LANGUAGE_NAMES = {"az": "az", "en": "en", "ru": "ru"}
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def _go(msg: str | None = None, err: str | None = None, **params) -> RedirectResponse:
    query = {k: v for k, v in params.items() if v}
    if msg:
        query["msg"] = msg
    if err:
        query["err"] = err
    url = "/settings/keywords" + (f"?{urlencode(query, quote_via=quote)}" if query else "")
    return RedirectResponse(url=url, status_code=303)


def _language(term: str) -> str:
    if re.search(r"[а-яё]", term.lower()):
        return "ru"
    if re.search(r"[əğıöüşç]", term.lower()):
        return "az"
    return "en" if re.fullmatch(r"[a-z \-]+", term.lower()) else "az"


@router.get("/settings/keywords")
def keywords_page(request: Request, area: str = "", scope: str = "", q: str = "",
                  me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        areas = list(db.scalars(select(InterestArea).order_by(InterestArea.priority, InterestArea.id)))
        counts = dict(db.execute(select(Keyword.interest_area_id, func.count()).group_by(Keyword.interest_area_id)).all())
        query = select(Keyword).order_by(Keyword.interest_area_id, Keyword.is_negative, Keyword.term)
        if area.isdigit():
            query = query.where(Keyword.interest_area_id == int(area))
        if scope in KEYWORD_SCOPES:
            query = query.where(Keyword.applies_to == scope)
        if q.strip():
            needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(Keyword.term.ilike(f"%{needle}%", escape="\\"))
        rows = list(db.scalars(query))
    return request.app.state.templates.TemplateResponse(request, "keywords.html", {
        "request": request, "active": "keywords", "areas": areas, "area_by_id": {a.id: a for a in areas},
        "counts": counts, "rows": rows, "area": area, "scope": scope, "q": q,
        "scope_names": SCOPE_NAMES, "match_names": MATCH_NAMES, "languages": KEYWORD_LANGUAGES,
    })


@router.post("/settings/keywords", dependencies=[Depends(verify_csrf)])
def keywords_add(terms: str = Form(...), interest_area_id: int = Form(...), applies_to: str = Form("all"),
                 match: str = Form("prefix"), language: str = Form("auto"), is_negative: str = Form(""),
                 me: User = Depends(REQUIRE["manager"])):
    if applies_to not in KEYWORD_SCOPES or match not in KEYWORD_MATCHES:
        return _go(err="Yanlış seçim")
    if language != "auto" and language not in KEYWORD_LANGUAGES:
        return _go(err="Yanlış dil")
    # Bir neçə söz: hər sətirdə biri (və ya vergüllə).
    wanted = [t.strip() for t in re.split(r"[\n,]", terms) if t.strip()]
    if not wanted:
        return _go(err="Açar söz boş ola bilməz")
    too_long = [t for t in wanted if len(t) > MAX_TERM_LENGTH]
    if too_long:
        return _go(err=f"Açar söz çox uzundur (maks. {MAX_TERM_LENGTH} simvol): {too_long[0][:40]}…")
    negative = is_negative == "1"

    added, skipped, opposite = 0, 0, []
    seen: set[str] = set()
    with security.SessionLocal() as db:
        if db.get(InterestArea, interest_area_id) is None:
            return _go(err="Maraq sahəsi tapılmadı")
        for term in wanted:
            if term.lower() in seen:
                skipped += 1
                continue
            seen.add(term.lower())
            # Bazada unikal açar (sahə, söz, kontekst) polaritetsizdir — eyni söz həm müsbət, həm mənfi ola bilməz.
            existing = db.scalars(select(Keyword.is_negative).where(
                func.lower(Keyword.term) == term.lower(), Keyword.interest_area_id == interest_area_id,
                Keyword.applies_to == applies_to)).all()
            if existing:
                if negative in existing:
                    skipped += 1
                else:
                    opposite.append(term)
                continue
            db.add(Keyword(interest_area_id=interest_area_id, term=term,
                           language=_language(term) if language == "auto" else language,
                           match="phrase" if match == "prefix" and " " in term else match,
                           applies_to=applies_to, is_negative=negative, is_active=True))
            added += 1
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return _go(err="Açar sözlər əlavə olunmadı: bəziləri artıq mövcuddur", area=str(interest_area_id))
    keywords.invalidate()
    note = f" ({skipped} artıq var idi)" if skipped else ""
    if opposite:
        kind = "müsbət" if negative else "mənfi"
        return _go(err=f"{added} açar söz əlavə olundu{note}. Bu sözlər artıq {kind} kimi var, əvvəlcə onu silin: "
                       + ", ".join(opposite[:5]), area=str(interest_area_id))
    return _go(msg=f"{added} açar söz əlavə olundu{note}", area=str(interest_area_id))


@router.post("/settings/keywords/{kw_id}/toggle", dependencies=[Depends(verify_csrf)])
def keywords_toggle(kw_id: int, me: User = Depends(REQUIRE["manager"])):
    with security.SessionLocal() as db:
        kw = db.get(Keyword, kw_id)
        if kw is None:
            return _go(err="Açar söz tapılmadı")
        kw.is_active = not kw.is_active
        term, state = kw.term, "aktiv" if kw.is_active else "deaktiv"
        db.commit()
    keywords.invalidate()
    return _go(msg=f"«{term}» {state}")


@router.post("/settings/keywords/{kw_id}/delete", dependencies=[Depends(verify_csrf)])
def keywords_delete(kw_id: int, me: User = Depends(REQUIRE["manager"])):
    with security.SessionLocal() as db:
        kw = db.get(Keyword, kw_id)
        if kw is None:
            return _go(err="Açar söz tapılmadı")
        term = kw.term
        db.delete(kw)
        db.commit()
    keywords.invalidate()
    return _go(msg=f"«{term}» silindi")


# --- Mənim açar sözlərim (hər istifadəçinin özü, viewer da) ---------------------------

MAX_USER_KEYWORDS = 100


def _go_mine(msg: str | None = None, err: str | None = None) -> RedirectResponse:
    query = {"msg": msg} if msg else ({"err": err} if err else {})
    return RedirectResponse(url="/me/keywords" + (f"?{urlencode(query, quote_via=quote)}" if query else ""),
                            status_code=303)


@router.get("/me/keywords")
def my_keywords_page(request: Request, me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        rows = list(db.scalars(select(UserKeyword).where(UserKeyword.user_id == me.id).order_by(UserKeyword.term)))
    return request.app.state.templates.TemplateResponse(request, "my_keywords.html", {
        "request": request, "active": "my_keywords", "rows": rows, "match_names": MATCH_NAMES,
        "limit": MAX_USER_KEYWORDS,
    })


@router.post("/me/keywords", dependencies=[Depends(verify_csrf)])
def my_keywords_add(terms: str = Form(...), match: str = Form("prefix"), me: User = Depends(REQUIRE["viewer"])):
    if match not in KEYWORD_MATCHES:
        return _go_mine(err="Yanlış seçim")
    wanted = [t.strip() for t in re.split(r"[\n,]", terms) if t.strip()]
    if not wanted:
        return _go_mine(err="Açar söz boş ola bilməz")
    too_long = [t for t in wanted if len(t) > MAX_TERM_LENGTH]
    if too_long:
        return _go_mine(err=f"Açar söz çox uzundur (maks. {MAX_TERM_LENGTH} simvol): {too_long[0][:40]}…")
    with security.SessionLocal() as db:
        have = {t.lower() for t in db.scalars(select(UserKeyword.term).where(UserKeyword.user_id == me.id))}
        added = 0
        for term in wanted:
            if term.lower() in have:
                continue
            if len(have) >= MAX_USER_KEYWORDS:
                db.commit()
                keywords.invalidate()
                return _go_mine(err=f"{added} söz əlavə olundu; ən çox {MAX_USER_KEYWORDS} söz ola bilər")
            have.add(term.lower())
            db.add(UserKeyword(user_id=me.id, term=term,
                               match="phrase" if match == "prefix" and " " in term else match))
            added += 1
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return _go_mine(err="Bəzi sözlər artıq var idi — yenidən cəhd et")
    keywords.invalidate()  # scrape də bu sözləri görsün
    skipped = len(wanted) - added
    return _go_mine(msg=f"{added} açar söz əlavə olundu" + (f" ({skipped} artıq var idi)" if skipped else ""))


@router.post("/me/keywords/{kw_id}/delete", dependencies=[Depends(verify_csrf)])
def my_keywords_delete(kw_id: int, me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        kw = db.get(UserKeyword, kw_id)
        if kw is None or kw.user_id != me.id:
            return _go_mine(err="Açar söz tapılmadı")
        term = kw.term
        db.delete(kw)
        db.commit()
    keywords.invalidate()
    return _go_mine(msg=f"«{term}» silindi")


@router.post("/settings/areas", dependencies=[Depends(verify_csrf)])
def areas_create(code: str = Form(...), name: str = Form(...), ai_description: str = Form(""),
                 priority: int = Form(2), me: User = Depends(REQUIRE["manager"])):
    code = code.strip().lower()
    if not _CODE_RE.match(code):
        return _go(err="Kod: kiçik latın hərfləri, rəqəm və _ (2-32 simvol), məs. hospitals")
    if not name.strip():
        return _go(err="Ad boş ola bilməz")
    with security.SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(InterestArea).where(InterestArea.code == code)):
            return _go(err=f"«{code}» kodu artıq var")
        area = InterestArea(code=code, name=name.strip(), ai_description=ai_description.strip() or None,
                            priority=min(max(priority, 1), 3), is_active=True)
        db.add(area)
        db.commit()
        area_id = area.id
    keywords.invalidate()
    return _go(msg=f"Maraq sahəsi əlavə olundu: {name.strip()}", area=str(area_id))


@router.post("/settings/areas/{area_id}", dependencies=[Depends(verify_csrf)])
def areas_update(area_id: int, name: str = Form(...), ai_description: str = Form(""), priority: int = Form(2),
                 is_active: str = Form(""), me: User = Depends(REQUIRE["manager"])):
    if not name.strip():
        return _go(err="Ad boş ola bilməz")
    with security.SessionLocal() as db:
        area = db.get(InterestArea, area_id)
        if area is None:
            return _go(err="Maraq sahəsi tapılmadı")
        area.name = name.strip()
        area.ai_description = ai_description.strip() or None
        area.priority = min(max(priority, 1), 3)
        area.is_active = is_active == "1"
        db.commit()
    keywords.invalidate()
    return _go(msg=f"Yadda saxlandı: {name.strip()}", area=str(area_id))
