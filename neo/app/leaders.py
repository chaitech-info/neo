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


# LinkedIn şirkət adları qısadır ("OBA", "Elba Chem") — rəsmi/təsviri əlavələrlə axtarış 0 nəticə verirdi
# (2026-11-02: "OBA marketlər şəbəkəsi", "Elba Chem QSC", "... PUBLİK HÜQUQİ ŞƏXSİ" → 0; "Sosial Xidmətlər Agentliyi" → 8).
_LEGAL_RE = re.compile(
    r"\b(mmc|asc|qsc|ascs|phş|phs|llc|ltd|limited|inc|corp|ojsc|cjsc|jsc|llp|gmbh|ooo|оао|зао|ооо|пао|"
    r"publik hüquqi şəxs[iə]?|açıq səhmdar cəmiyyəti|qapalı səhmdar cəmiyyəti|məhdud məsuliyyətli cəmiyyət[i]?)\b\.?",
    re.I,
)
_PREFIX_RE = re.compile(r"^(azərbaycan respublikasının|azerbaijan republic(?: of)?)\s+", re.I)
_TAIL_RE = re.compile(r"\s+(marketlər(?:i)? şəbəkəsi|mağazalar(?:ı)? şəbəkəsi|şəbəkəsi|şirkətlər qrupu|"
                      r"şirkəti|şirkətləri|holdinqi|group of companies)$", re.I)


_AZ_LOWER = str.maketrans({"İ": "i", "I": "ı"})  # str.lower() "İ"-ni "i̇" (nöqtəli) edir
_SMALL_WORDS = {"və", "ve", "of", "and", "и"}


def search_names(name: str) -> list[str]:
    """Axtarış variantları: təmizlənmiş ad, lazım olsa onun əsası (ilk söz). Dublikatsız, sıra ilə."""
    clean = re.sub(r"[\"“”«»'„]", " ", name or "")
    clean = _LEGAL_RE.sub(" ", clean)
    clean = " ".join(clean.split()).strip(" ,.-—")
    clean = _PREFIX_RE.sub("", clean)
    clean = _TAIL_RE.sub("", clean).strip(" ,.-—")
    if clean.isupper() and len(clean.split()) > 1:  # "DÖVLƏT TURİZM AGENTLİYİ" → "Dövlət Turizm Agentliyi"; "SOCAR" qalır
        clean = " ".join(w.translate(_AZ_LOWER).lower() if w.lower() in _SMALL_WORDS
                         else w[0] + w[1:].translate(_AZ_LOWER).lower() for w in clean.split())
    names = [clean] if clean else []
    first = clean.split()[0] if clean else ""
    if len(clean.split()) > 1 and len(first) >= 3 and first.isupper():  # "OBA Market" → "OBA" (brend qısaltması)
        names.append(first)
    return list(dict.fromkeys(n for n in names if len(n) >= 2))


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


# --- Apify asinxron run: başlat → yoxla (Vercel 60 san limiti; sinxron run 45 san-da TIMED-OUT olurdu) ---
APIFY_API = "https://api.apify.com/v2"
RUN_TIMEOUT_S = 300  # Apify tərəfində run-ın ən çox müddəti
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
_DONE_BAD = {"FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"}


def _token() -> str:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise LeadersError("APIFY_TOKEN təyin edilməyib — LinkedIn profilləri axtarıla bilmir")
    return token


def _actor() -> str:
    return os.environ.get("LEADERS_APIFY_ACTOR") or DEFAULT_ACTOR


def _limit() -> int:
    return int(os.environ.get("LEADERS_MAX") or DEFAULT_MAX)


def _apify_error(exc: httpx.HTTPStatusError) -> LeadersError:
    # Apify xətanı JSON-da izah edir ({"error": {"type", "message"}}) — səbəb paneldə görünsün.
    try:
        detail = (exc.response.json().get("error") or {}).get("message") or ""
    except ValueError:
        detail = ""
    status = exc.response.status_code
    log.warning("leaders_apify_failed", actor=_actor(), status=status, detail=detail[:300])
    hint = {401: "APIFY_TOKEN yanlışdır", 402: "Apify balansı bitib", 403: "Apify-da bu actor-a icazə yoxdur",
            404: f"Apify actor tapılmadı ({_actor()})"}.get(status, "")
    return LeadersError(f"LinkedIn axtarışı alınmadı (Apify {status}" + (f": {hint}" if hint else "")
                        + (f" — {detail[:200]}" if detail else "") + ")")


def _call(method: str, url: str, *, client: httpx.Client | None = None, **kw) -> dict | list:
    http = client or _http()
    try:
        resp = http.request(method, url, timeout=20, **kw)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise _apify_error(exc) from exc
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("leaders_apify_failed", error=type(exc).__name__)
        raise LeadersError(f"LinkedIn axtarışı alınmadı: {type(exc).__name__}") from exc
    finally:
        if client is None:
            http.close()


def start_run(company: str, *, client: httpx.Client | None = None) -> str:
    """Rəhbər axtarışını Apify-da başladır (gözləmir), run id qaytarır."""
    # Yalnız əsas sahələr: opsional sahələrin (profileScraperMode, locations) dəqiq dəyərləri actor-dan asılıdır —
    # səhv dəyər bütün sorğunu 400 edir. Vəzifə süzgəci onsuz da aşağıda SENIOR_RE ilə edilir.
    payload = {"companies": [company], "jobTitles": SEARCH_TITLES, "maxItems": _limit() * 3}
    data = _call("POST", f"{APIFY_API}/acts/{_actor()}/runs", client=client,
                 params={"token": _token(), "timeout": RUN_TIMEOUT_S}, json=payload)
    run_id = ((data or {}).get("data") or {}).get("id") if isinstance(data, dict) else None
    if not run_id or not _RUN_ID_RE.match(run_id):
        raise LeadersError("LinkedIn axtarışı başlamadı (Apify cavabında run id yoxdur)")
    log.info("leaders_run_started", company=company, run=run_id)
    return run_id


def run_result(run_id: str, *, company: str = "", original: str | None = None,
               client: httpx.Client | None = None) -> list[Found] | None:
    """None — hələ işləyir; siyahı — bitdi (rəhbərlər süzülüb). Uğursuz run → LeadersError."""
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
    return pick_leaders(items, company, original)


_AZ_PLACE_RE = re.compile(r"azerbaijan|azərbaycan|azerbaycan|baku|bakı|baki|sumqayıt|sumgait|gəncə|ganja|азербайджан|баку")
_STOP = {"və", "ve", "and", "of", "the", "group", "company", "mmc", "asc", "qsc"}


def _place(item: dict) -> str:
    loc = item.get("location") or item.get("locationName") or item.get("geoLocationName") or ""
    if isinstance(loc, dict):  # harvestapi: {"linkedinText": "Baku, Azerbaijan", "parsed": {...}}
        loc = loc.get("linkedinText") or (loc.get("parsed") or {}).get("text") or ""
    return str(loc)


def _company_words(name: str) -> set[str]:
    """Şirkət adının ayırd edici sözləri (folded, ≥3 hərf): "OBA marketlər şəbəkəsi" → {oba, marketlər, şəbəkəsi}."""
    from app.scrapers.base import fold

    return {w for w in re.findall(r"\w+", fold(name)) if len(w) >= 3 and w not in _STOP}


def relevant(item: dict, f: Found, company: str, original: str | None = None) -> bool:
    """Bu şirkətin işçisidirmi? Qısa ad ("OBA") başqa şirkətlərə də uyğun gəlir (OBA Makarnacılık, OBA Couture —
    retest 2026-11-02). Qəbul: yeri Azərbaycandadır, və ya başlığında adın ən azı iki sözü / tam qısa adı var."""
    from app.scrapers.base import fold

    if _AZ_PLACE_RE.search(fold(_place(item))):
        return True
    head = fold(f.title or "")
    words = _company_words(original or company)
    hits = sum(1 for w in words if re.search(rf"(?<!\w){re.escape(w[:6])}", head))  # kök: "marketlər" ~ "market"
    return hits >= min(2, len(words)) and bool(words)


def pick_leaders(items, company: str = "", original: str | None = None) -> list[Found]:
    found, seen = [], set()
    for item in items if isinstance(items, list) else []:
        f = _item_to_found(item) if isinstance(item, dict) else None
        if f is None or f.linkedin_url.rstrip("/").lower() in seen:
            continue
        if f.title and not SENIOR_RE.search(f.title):
            continue  # rəhbər / qərar verən deyil
        if company and not relevant(item, f, company, original):
            continue  # eyni adlı başqa şirkət / başqa ölkə
        seen.add(f.linkedin_url.rstrip("/").lower())
        found.append(f)
    log.info("leaders_found", items=len(items) if isinstance(items, list) else 0, kept=len(found))
    return found[:_limit()]


def target(session: Session, opp: Opportunity) -> tuple[LeadRecord, list[str], str]:
    """Fürsətin lead-i, axtarış variantları (search_names) və orijinal ad."""
    lead = session.get(LeadRecord, opp.lead_key) if opp.lead_key else None
    org = session.get(Organization, opp.organization_id) if opp.organization_id else None
    name = company_name(lead, org)
    if lead is None or not name:
        raise LeadersError("Şirkətin adı bilinmir — rəhbərləri axtarmaq üçün təşkilatı və ya kontaktı əl ilə əlavə et")
    return lead, search_names(name) or [name], name


def finish(session: Session, opp: Opportunity, lead: LeadRecord, found: list[Found]) -> int:
    """Tapılanları kontakt kimi yazır; uğurlu axtarışdan sonra (0 nəticə də) bir daha avtomatik çağırılmır."""
    added = save_contacts(session, lead, found)
    opp.leaders_fetched_at = datetime.utcnow()
    session.commit()
    return added
