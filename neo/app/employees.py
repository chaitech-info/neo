"""Seçilmiş şirkətin LinkedIn-dəki bütün işçiləri — Apify (app/leaders.py ilə eyni actor, lakin vəzifə
süzgəci olmadan). Pulludur (profil başına) — nəticə `employee_searches`-də saxlanılır və
EMPLOYEES_CACHE_DAYS ərzində eyni şirkət üçün yenidən ödənilmir ("Yenidən axtar" istisna).

Env: APIFY_TOKEN; LEADERS_APIFY_ACTOR (defolt harvestapi~linkedin-company-employees);
EMPLOYEES_MAX (defolt 100, ən çox 300). Vercel funksiyası 60 saniyədir, ona görə axtarış asinxrondur
(app/leaders.py kimi): `start` run-ı başladır, panel `poll`-u bitənə qədər bir neçə saniyədən bir çağırır.
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
from app.leaders import (APIFY_API, RUN_TIMEOUT_S, _DONE_BAD, _RUN_ID_RE, LeadersError, _actor, _call, _item_to_found,
                         _token)
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


def start(company: str, limit: int, *, client: httpx.Client | None = None) -> str:
    """İşçi axtarışını Apify-da başladır (gözləmir), run id qaytarır — nəticə `poll` ilə gəlir."""
    payload = {"companies": [company], "maxItems": max(1, min(limit, HARD_MAX))}
    data = _call("POST", f"{APIFY_API}/acts/{_actor()}/runs", client=client,
                 params={"token": _token(), "timeout": RUN_TIMEOUT_S}, json=payload)
    run_id = ((data or {}).get("data") or {}).get("id") if isinstance(data, dict) else None
    if not run_id or not _RUN_ID_RE.match(run_id):
        raise LeadersError("LinkedIn axtarışı başlamadı (Apify cavabında run id yoxdur)")
    log.info("employees_run_started", company=company, run=run_id)
    return run_id


def run_items(run_id: str, *, client: httpx.Client | None = None) -> list | None:
    """None — hələ işləyir; siyahı — bitdi (xam Apify nəticələri). Uğursuz run → LeadersError."""
    if not _RUN_ID_RE.match(run_id or ""):
        raise LeadersError("Yanlış axtarış id-si")
    token = _token()
    run = (_call("GET", f"{APIFY_API}/actor-runs/{run_id}", client=client, params={"token": token}) or {}).get("data") or {}
    status = (run.get("status") or "").upper()
    if status in _DONE_BAD:
        raise LeadersError(f"LinkedIn axtarışı alınmadı (Apify run: {status})")
    if status != "SUCCEEDED":
        return None
    items = _call("GET", f"{APIFY_API}/datasets/{run.get('defaultDatasetId')}/items", client=client,
                  params={"token": token, "clean": "true"})
    return items if isinstance(items, list) else []


def people(items: list, limit: int = HARD_MAX) -> list[dict]:
    """Yalnız adı və LinkedIn profili olan, təkrarsız nəticələr."""
    out, seen = [], set()
    for item in items:
        f = _item_to_found(item) if isinstance(item, dict) else None
        if f is None or f.linkedin_url.rstrip("/").lower() in seen:
            continue
        seen.add(f.linkedin_url.rstrip("/").lower())
        out.append({"name": f.name, "title": f.title, "linkedin_url": f.linkedin_url, "location": _location(item)})
    return out[:limit]


def cached(session: Session, company: str) -> EmployeeSearch | None:
    return session.scalars(select(EmployeeSearch).where(EmployeeSearch.company_key == company_key(company))
                           .order_by(EmployeeSearch.created_at.desc())).first()


def fresh(session: Session, company: str) -> EmployeeSearch | None:
    """Keşdəki nəticə hələ təzədirsə (EMPLOYEES_CACHE_DAYS) — Apify-a getmədən göstərilir."""
    prev = cached(session, company)
    if prev is not None and prev.created_at >= datetime.utcnow() - timedelta(days=CACHE_DAYS):
        return prev
    return None


def poll(session: Session, run_id: str, company: str, limit: int = HARD_MAX, *, user_id: int | None = None,
         client: httpx.Client | None = None) -> EmployeeSearch | None:
    """Run bitibsə nəticəni saxlayır və qaytarır; hələ işləyirsə None."""
    items = run_items(run_id, client=client)
    if items is None:
        return None
    found = people(items, max(1, min(limit, HARD_MAX)))
    log.info("employees_found", company=company, items=len(items), kept=len(found))
    row = EmployeeSearch(company=company[:300], company_key=company_key(company), items=json.dumps(found, ensure_ascii=False),
                         user_id=user_id, created_at=datetime.utcnow())
    session.add(row)
    session.commit()
    return row


def items_of(row: EmployeeSearch | None) -> list[dict]:
    if row is None:
        return []
    try:
        data = json.loads(row.items)
    except ValueError:
        return []
    return data if isinstance(data, list) else []
