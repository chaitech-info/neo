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
import re
from datetime import datetime, timedelta

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EmployeeSearch
from app.leaders import (APIFY_API, BUYER_RE, RUN_TIMEOUT_S, _DONE_BAD, _RUN_ID_RE, LeadersError, _actor, _call,
                         _item_to_found, _token)
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
    """Saxlanmış işçilər vəzifə səviyyəsinə görə sıralı (ranked)."""
    if row is None:
        return []
    try:
        data = json.loads(row.items)
    except ValueError:
        return []
    return ranked(data) if isinstance(data, list) else []


# --- Vəzifə səviyyəsi -----------------------------------------------------------------------------------------
# Vəzifə mətni əvvəl sadələşdirilir (kiçik hərf, ə→e, ı/İ→i, ş→s …), ona görə nümunələr ASCII yazılıb;
# kiril sözləri kiçik hərflə olduğu kimi qalır.
_FOLD = str.maketrans({"ə": "e", "ı": "i", "ö": "o", "ü": "u", "ğ": "g", "ş": "s", "ç": "c", "̇": None})


def _fold(text: str | None) -> str:
    return (text or "").replace("İ", "i").replace("I", "i").lower().translate(_FOLD)


def _words(pattern: str) -> re.Pattern:
    return re.compile(rf"\b({pattern})\b")


_JUNIOR = _words(r"intern|internship|trainee|assistant|asst|junior|jr|tecrubeci|komekci|"
                 r"стаж[её]р\w*|ассистент\w*|помощник\w*|младш\w*")
_DEPUTY = _words(r"deputy|muavin\w*|заместител\w*")
_VICE = _words(r"vice[- ]president|вице-президент\w*")
_TOP = _words(r"ceo|chief executive( officer)?|founder|co-?founder|owner|president|chairman|chairwoman|chairperson|"
              r"managing director|managing partner|general manager|general director|bas direktor|bas icraci direktor|"
              r"tesisci|sedr\w*|генеральн\w* директор\w*|основател\w*|владел\w*|председател\w*|президент\w*")
_CLEVEL = _words(r"cfo|coo|cto|cmo|chro|cio|cdo|cpo|cco|cso|chief \w+|vp|svp|evp")
_DIRECTOR = _words(r"director|head|direktor\w*|rehber\w*|mudir\w*|директор\w*|руководител\w*|начальник\w*")
_MANAGER = _words(r"manager|lead|leader|supervisor|coordinator|menecer\w*|koordinator\w*|менеджер\w*|координатор\w*")
# "Deputy Director of Sales" — departament direktorunun müavini; qualifiersiz "Direktor müavini" şirkət rəhbərinin müavinidir.
_DEPT = _words(r"of|sobe\w*|department|departament\w*|по|отдел\w*|департамент\w*")

RANK_NAMES = {1: ("Rəhbərlik", "rəhbər"), 2: ("C-level və VP", "C-level/VP"), 3: ("Direktorlar", "direktor"),
              4: ("Menecerlər", "menecer"), 5: ("Mütəxəssislər", "mütəxəssis"), 6: ("Junior", "junior"),
              7: ("Digər", "vəzifəsi bilinməyən")}
OPEN_RANKS = (1, 2)  # rəhbərlik həmişə tam açıq; aşağı qruplar COLLAPSE_AFTER-dən çoxdursa qatlanır
COLLAPSE_AFTER = 10


def _base_rank(f: str) -> int:
    if _VICE.search(f):
        return 2
    if _TOP.search(f):
        return 1
    if _CLEVEL.search(f):
        return 2
    if _DIRECTOR.search(f):
        return 3
    if _MANAGER.search(f):
        return 4
    return 5


def seniority_rank(title: str | None) -> int:
    """1 rəhbərlik … 6 junior/köməkçi, 7 vəzifə yoxdur. Köməkçilər ("Assistant to CEO") rəhbər sayılmır;
    müavin (deputy, müavin, заместитель) əsas vəzifədən bir pillə aşağı düşür."""
    f = _fold(title).strip()
    if not f:
        return 7
    if _JUNIOR.search(f):
        return 6
    if not _DEPUTY.search(f):
        return _base_rank(f)
    rest = _DEPUTY.sub(" ", f)
    base = _base_rank(rest)
    if base == 5 and not rest.strip():
        base = 1  # təkcə "Заместитель" / "Müavin" — şirkət rəhbərinin müavini
    elif base == 3 and not _DEPT.search(rest):
        base = 1  # "Direktor müavini", "Deputy Director"
    return min(base + 1, 5)


def contact_fit(title: str | None, rank: int | None = None) -> bool:
    """Catering satışı üçün qərar verən (HR, satınalma, inzibati/ofis menecer) — köməkçilər istisna."""
    return bool(title and BUYER_RE.search(title)) and (rank or seniority_rank(title)) != 6


def _initials(name: str | None) -> str:
    parts = [p for p in (name or "").replace(".", " ").split() if p[:1].isalpha()]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def ranked(items: list) -> list[dict]:
    """Səviyyə, sonra ad üzrə sıralı; hər işçiyə rank, fit, initials əlavə olunur (JSON və Jinja eyni sıranı görür)."""
    out = []
    for p in items:
        if not isinstance(p, dict):
            continue
        rank = seniority_rank(p.get("title"))
        out.append({**p, "rank": rank, "fit": contact_fit(p.get("title"), rank), "initials": _initials(p.get("name"))})
    return sorted(out, key=lambda p: (p["rank"], _fold(p.get("name"))))


def view(items: list) -> dict:
    """Qruplar (boşlar göstərilmir) və xülasə — templates/_employees_list.html üçün."""
    people_ = items if all(isinstance(p, dict) and "rank" in p for p in items) else ranked(items)
    groups = []
    for rank, (label, short) in RANK_NAMES.items():
        members = [p for p in people_ if p["rank"] == rank]
        if not members:
            continue
        cut = len(members) if rank in OPEN_RANKS or len(members) <= COLLAPSE_AFTER else COLLAPSE_AFTER
        groups.append({"rank": rank, "label": label, "short": short, "shown": members[:cut], "more": members[cut:]})
    fit = sum(1 for p in people_ if p["fit"])
    summary = [f"Cəmi {len(people_)}"] + [f"{len(g['shown']) + len(g['more'])} {g['short']}" for g in groups]
    if fit:
        summary.append(f"{fit} əlaqə üçün uyğun")
    return {"total": len(people_), "groups": groups, "summary": " · ".join(summary)}
