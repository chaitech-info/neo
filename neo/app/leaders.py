"""Fürsət yaranan şirkətin rəhbər işçilərinin LinkedIn profilləri — Apify (lisenziyalı provider,
birbaşa LinkedIn scraping yox; bax 04-decisions.md → "LinkedIn scraping yanaşması").

Fürsət səhifəsi ilk dəfə açılanda bir dəfə çağırılır (pulludur): tapılanlar `contacts`-a
`source="enrichment"` kimi yazılır, `opportunities.leaders_fetched_at` qeyd olunur.

Env: APIFY_TOKEN; LEADERS_APIFY_ACTOR (defolt harvestapi~linkedin-company-employees),
LEADERS_MAX (defolt 8). Vercel funksiyası 60 saniyədir — Apify-a 45 saniyə verilir.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import httpx
import structlog
from sqlalchemy.orm import Session

from app.contacts import Found, save_contacts
from app.db.models import LeadRecord, Opportunity, Organization

log = structlog.get_logger()

DEFAULT_ACTOR = "harvestapi~linkedin-company-employees"
APIFY_TIMEOUT_S = 45
DEFAULT_MAX = 8

# Rəhbər / qərar verən vəzifələr (az/ru/en) — catering satışında kimə yazmaq lazımdır.
SENIOR_RE = re.compile(
    r"\b(ceo|cfo|coo|cto|chief|founder|co-founder|owner|president|partner|managing|general manager|"
    r"director|head of|head|vp|vice president|"
    r"direktor|rəhbər|rehber|təsisçi|tesisci|sədr|sedr|müdir|mudir|baş menecer|"
    r"директор|руководитель|основатель|генеральный|начальник|"
    r"hr|human resources|people|procurement|purchasing|satınalma|satinalma|admin(istrative)? manager)\b",
    re.I,
)
SEARCH_TITLES = ["CEO", "Director", "Founder", "Owner", "General Manager", "Head", "HR", "Procurement",
                 "Direktor", "Директор"]


def _http() -> httpx.Client:
    return httpx.Client()


class LeadersError(Exception):
    """İstifadəçiyə göstərilən xəta."""


def enabled() -> bool:
    return bool(os.environ.get("APIFY_TOKEN"))


def company_name(lead: LeadRecord | None, org: Organization | None) -> str | None:
    """Axtarış üçün şirkət adı. LinkedIn postunda entity_name müəllifin öz adıdır — şirkət deyil."""
    if org is not None and org.name:
        return org.name
    if lead is None or not lead.entity_name:
        return None
    if lead.entity_name == lead.author_name:
        return None
    return lead.entity_name


def _item_to_found(item: dict) -> Found | None:
    name = (item.get("fullName") or item.get("name")
            or " ".join(x for x in (item.get("firstName"), item.get("lastName")) if x) or "").strip()
    title = (item.get("headline") or item.get("position") or item.get("title") or item.get("jobTitle") or "").strip()
    current = item.get("currentPosition") or item.get("currentPositions") or []
    if not title and isinstance(current, list) and current and isinstance(current[0], dict):
        title = (current[0].get("position") or current[0].get("title") or "").strip()
    url = (item.get("linkedinUrl") or item.get("profileUrl") or item.get("url") or "").strip()
    if not url.startswith("https://") or "linkedin.com/" not in url or not name:
        return None
    return Found(name=name[:200], title=title[:200] or None, linkedin_url=url, source="enrichment")


def search(company: str, *, client: httpx.Client | None = None, token: str | None = None) -> list[Found]:
    token = token or os.environ.get("APIFY_TOKEN")
    if not token:
        raise LeadersError("APIFY_TOKEN təyin edilməyib — LinkedIn profilləri axtarıla bilmir")
    actor = os.environ.get("LEADERS_APIFY_ACTOR") or DEFAULT_ACTOR
    limit = int(os.environ.get("LEADERS_MAX") or DEFAULT_MAX)
    # Yalnız əsas sahələr: opsional sahələrin (profileScraperMode, locations) dəqiq dəyərləri actor-dan asılıdır —
    # səhv dəyər bütün sorğunu 400 edir. Vəzifə süzgəci onsuz da aşağıda SENIOR_RE ilə edilir.
    payload = {"companies": [company], "jobTitles": SEARCH_TITLES, "maxItems": limit * 3}
    http = client or _http()
    try:
        resp = http.post(f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                         params={"token": token, "timeout": APIFY_TIMEOUT_S}, json=payload,
                         timeout=APIFY_TIMEOUT_S + 8)
        resp.raise_for_status()
        items = resp.json()
    except httpx.TimeoutException as exc:
        raise LeadersError("LinkedIn axtarışı vaxtında bitmədi — bir az sonra yenidən cəhd et") from exc
    except httpx.HTTPStatusError as exc:
        # Apify xətanı JSON-da izah edir ({"error": {"type", "message"}}) — səbəb paneldə görünsün.
        try:
            detail = (exc.response.json().get("error") or {}).get("message") or ""
        except ValueError:
            detail = ""
        status = exc.response.status_code
        log.warning("leaders_search_failed", actor=actor, status=status, detail=detail[:300])
        hint = {401: "APIFY_TOKEN yanlışdır", 402: "Apify balansı bitib", 403: "Apify-da bu actor-a icazə yoxdur",
                404: f"Apify actor tapılmadı ({actor})"}.get(status, "")
        raise LeadersError(f"LinkedIn axtarışı alınmadı (Apify {status}"
                           + (f": {hint}" if hint else "") + (f" — {detail[:200]}" if detail else "") + ")") from exc
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("leaders_search_failed", actor=actor, error=type(exc).__name__)
        raise LeadersError(f"LinkedIn axtarışı alınmadı: {type(exc).__name__}") from exc
    finally:
        if client is None:
            http.close()

    found, seen = [], set()
    for item in items if isinstance(items, list) else []:
        f = _item_to_found(item) if isinstance(item, dict) else None
        if f is None or f.linkedin_url.rstrip("/").lower() in seen:
            continue
        if f.title and not SENIOR_RE.search(f.title):
            continue  # rəhbər / qərar verən deyil
        seen.add(f.linkedin_url.rstrip("/").lower())
        found.append(f)
    log.info("leaders_found", company=company, items=len(items) if isinstance(items, list) else 0, kept=len(found))
    return found[:limit]


def fetch_for(session: Session, opp: Opportunity, *, client: httpx.Client | None = None) -> int:
    """Fürsət üçün rəhbərləri tapıb kontakt kimi yazır, əlavə olunanların sayını qaytarır.
    Uğurlu axtarışdan sonra (0 nəticə də) bir daha avtomatik çağırılmır."""
    lead = session.get(LeadRecord, opp.lead_key) if opp.lead_key else None
    org = session.get(Organization, opp.organization_id) if opp.organization_id else None
    name = company_name(lead, org)
    if lead is None or not name:
        raise LeadersError("Şirkətin adı bilinmir — rəhbərləri axtarmaq üçün təşkilatı və ya kontaktı əl ilə əlavə et")
    found = search(name, client=client)
    added = save_contacts(session, lead, found)
    opp.leaders_fetched_at = datetime.utcnow()
    session.commit()
    return added
