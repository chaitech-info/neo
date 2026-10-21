"""Seçilmiş şirkətin LinkedIn-dəki bütün işçiləri — Apify (app/leaders.py ilə eyni actor, lakin vəzifə
süzgəci olmadan). Pulludur (profil başına) — nəticə `employee_searches`-də saxlanılır və
EMPLOYEES_CACHE_DAYS ərzində eyni şirkət üçün yenidən ödənilmir ("Yenidən axtar" istisna).

Env: APIFY_TOKEN; LEADERS_APIFY_ACTOR (defolt harvestapi~linkedin-company-employees);
EMPLOYEES_MAX (defolt 100, ən çox 300). Vercel funksiyası 60 saniyədir — çox böyük şirkətdə
siyahı qismən ola bilər (Apify 45 saniyədə nə qaytarırsa).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EmployeeSearch
from app.leaders import APIFY_TIMEOUT_S, DEFAULT_ACTOR, LeadersError, _http, _item_to_found
from app.tenders import normalize_org

log = structlog.get_logger()

DEFAULT_MAX = 100
HARD_MAX = 300
CACHE_DAYS = int(os.environ.get("EMPLOYEES_CACHE_DAYS") or 30)


def company_key(name: str) -> str:
    return (normalize_org(name) or name.strip().lower())[:200]


def _location(item: dict) -> str | None:
    loc = item.get("location") or item.get("locationName") or item.get("geoLocationName")
    if isinstance(loc, dict):  # harvestapi: {"linkedinText": "Baku, Azerbaijan", "parsed": {...}}
        loc = loc.get("linkedinText") or (loc.get("parsed") or {}).get("text")
    return str(loc)[:120] if isinstance(loc, str) and loc else None


def search(company: str, limit: int, *, client: httpx.Client | None = None, token: str | None = None) -> list[dict]:
    token = token or os.environ.get("APIFY_TOKEN")
    if not token:
        raise LeadersError("APIFY_TOKEN təyin edilməyib — LinkedIn axtarışı işləmir")
    actor = os.environ.get("LEADERS_APIFY_ACTOR") or DEFAULT_ACTOR
    http = client or _http()
    try:
        resp = http.post(f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                         params={"token": token, "timeout": APIFY_TIMEOUT_S},
                         json={"companies": [company], "maxItems": limit}, timeout=APIFY_TIMEOUT_S + 8)
        resp.raise_for_status()
        items = resp.json()
    except httpx.TimeoutException as exc:
        raise LeadersError("LinkedIn axtarışı vaxtında bitmədi — sayı azaldıb yenidən cəhd et") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = {401: "APIFY_TOKEN yanlışdır", 402: "Apify balansı bitib", 403: "Apify-da bu actor-a icazə yoxdur",
                404: f"Apify actor tapılmadı ({actor})"}.get(status, "")
        log.warning("employees_search_failed", status=status)
        raise LeadersError(f"LinkedIn axtarışı alınmadı (Apify {status}{': ' + hint if hint else ''})") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise LeadersError(f"LinkedIn axtarışı alınmadı: {type(exc).__name__}") from exc
    finally:
        if client is None:
            http.close()

    out, seen = [], set()
    for item in items if isinstance(items, list) else []:
        f = _item_to_found(item) if isinstance(item, dict) else None
        if f is None or f.linkedin_url.rstrip("/").lower() in seen:
            continue
        seen.add(f.linkedin_url.rstrip("/").lower())
        out.append({"name": f.name, "title": f.title, "linkedin_url": f.linkedin_url, "location": _location(item)})
    log.info("employees_found", company=company, items=len(items) if isinstance(items, list) else 0, kept=len(out))
    return out[:limit]


def cached(session: Session, company: str) -> EmployeeSearch | None:
    return session.scalars(select(EmployeeSearch).where(EmployeeSearch.company_key == company_key(company))
                           .order_by(EmployeeSearch.created_at.desc())).first()


def find(session: Session, company: str, limit: int = DEFAULT_MAX, *, force: bool = False, user_id: int | None = None,
         client: httpx.Client | None = None) -> tuple[EmployeeSearch, bool]:
    """(axtarış, keşdən?) — keş təzədirsə Apify-a getmir; `force` ("Yenidən axtar") həmişə yenidən axtarır."""
    limit = max(1, min(limit, HARD_MAX))
    prev = cached(session, company)
    if not force and prev is not None and prev.created_at >= datetime.utcnow() - timedelta(days=CACHE_DAYS):
        return prev, True
    found = search(company, limit, client=client)
    row = EmployeeSearch(company=company[:300], company_key=company_key(company), items=json.dumps(found, ensure_ascii=False),
                         user_id=user_id, created_at=datetime.utcnow())
    session.add(row)
    session.commit()
    return row, False


def items_of(row: EmployeeSearch | None) -> list[dict]:
    if row is None:
        return []
    try:
        data = json.loads(row.items)
    except ValueError:
        return []
    return data if isinstance(data, list) else []
