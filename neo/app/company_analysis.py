"""Alətlər → Şirkət analizi: şirkətin adı (və istəyə görə saytı) → AI satış analizi.

Mənbələr: GPT veb axtarışı (OpenAI Responses API, `web_search` — rəsmi sayt, LinkedIn, xəbərlər, reyestrlər),
şirkət saytının ana səhifəsi (robots.txt-ə əməl edir), bazadakı təşkilat, həmin şirkətlə bağlı lead-lər və
fürsətlər. AI yalnız bu faktlara əsaslanır; tapılmayan "bilinmir"/boş qalır, internetdən gələn hər fakt üçün
mənbə linki saxlanılır. Veb axtarışı alınmasa (vaxt, xəta) analiz əvvəlki kimi sayt + baza ilə gedir.

Env: COMPANY_WEB_SEARCH (defolt 1), COMPANY_SEARCH_MODEL (defolt AI_ANALYSIS_MODEL / gpt-4.1-mini),
COMPANY_SEARCH_TOOL (defolt web_search).
"""
from __future__ import annotations

import json
import os
import time

import structlog
from bs4 import BeautifulSoup
from openai import OpenAI
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from app.ai_filter import OPENAI_MAX_RETRIES
from app.ai_scoring import DEFAULT_MODEL, chat_json
from app.contacts import WebsiteContacts, crawlable, extract_from_text
from app.db.models import LeadRecord, Opportunity, Organization
from app.outreach import OutreachError, company_description
from app.tenders import normalize_org

log = structlog.get_logger()

MAX_SITE_CHARS = 5000
MAX_LEADS = 10

_LIST = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "industry", "size", "size_note", "fit", "fit_reason", "needs", "approach",
                 "talking_points", "risks"],
    "properties": {
        "summary": {"type": "string"},
        "industry": {"type": "string"},
        "size": {"type": "string", "enum": ["small", "medium", "large", "unknown"]},
        "size_note": {"type": "string"},
        "fit": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
        "fit_reason": {"type": "string"},
        "needs": _LIST,
        "approach": _LIST,
        "talking_points": _LIST,
        "risks": _LIST,
    },
}

_PERSON = {"type": "object", "additionalProperties": False, "required": ["name", "role"],
           "properties": {"name": {"type": "string"}, "role": {"type": "string"}}}
_NEWS = {"type": "object", "additionalProperties": False, "required": ["title", "date", "url"],
         "properties": {"title": {"type": "string"}, "date": {"type": "string"}, "url": {"type": "string"}}}
# Veb axtarışlı analizin əlavə sahələri (tapılmayan — "" və ya [])
WEB_FIELDS = {
    "website": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"},
    "address": {"type": "string"}, "employees": {"type": "string"}, "founded": {"type": "string"},
    "locations": _LIST, "key_people": {"type": "array", "items": _PERSON}, "news": {"type": "array", "items": _NEWS},
    "sources": _LIST,
}
WEB_SCHEMA = {**SCHEMA, "required": SCHEMA["required"] + list(WEB_FIELDS),
              "properties": {**SCHEMA["properties"], **WEB_FIELDS}}
# Vercel funksiyası 60 saniyədir: sayt + veb axtarışı + (alınmasa) əvvəlki üsul bu büdcəyə sığmalıdır.
BUDGET_S = 54
FALLBACK_S = 12  # əvvəlki üsula (sayt + baza) lazım olan vaxt

WEB_PROMPT = """\
You are a B2B sales analyst for an Azerbaijani catering company. Research ONE company as a potential
client with web search (official website, LinkedIn, news sites, business registries, job boards) and the
facts given below, then answer in Azerbaijani (Latin script).

About us (what we sell):
{company}

Rules: only state what a source says. If something is not found, use "bilinmir" for text fields and ""
or [] for contact fields and lists — never invent people, numbers, phones or emails. Prefer sources about
the company in Azerbaijan.

Fields:
- summary: 3-4 sentences — who they are, what they do, where, how big.
- industry: one short phrase.
- size: small / medium / large / unknown (by staff size); size_note: the evidence, or "bilinmir".
- employees: staff count or range with its source (e.g. "≈450 (LinkedIn)"), or "".
- founded: year, or "".
- website, phone, email, address: the company's official contacts (head office in Azerbaijan), or "".
- locations: offices, plants, branches, sites in Azerbaijan (short, max 8).
- key_people: leaders and decision makers named publicly (CEO, director, HR, procurement), max 8.
- news: up to 5 recent news items about the company (title, date YYYY-MM-DD if known, url).
- sources: the URLs you used (max 10).
- fit: how likely they need our catering (staff meals, events, facilities); fit_reason: 1-2 sentences.
- needs: 2-4 concrete catering needs they probably have.
- approach: 3-5 concrete next steps for our salesperson (whom to contact, what to offer, when).
- talking_points: 2-4 short points to mention in the first conversation.
- risks: 0-3 risks or reasons it may not work (own kitchen, competitor, too small...).
"""

PROMPT = """\
You are a B2B sales analyst for an Azerbaijani catering company. Analyse ONE company as a potential
client and answer in Azerbaijani (Latin script).

About us (what we sell):
{company}

Use ONLY the facts given (website text, our database records). If something is not in the facts,
say "bilinmir" — do not invent employee numbers, revenue, names or events.

Fields:
- summary: 2-3 sentences — who they are and what they do.
- industry: one short phrase.
- size: small / medium / large / unknown (by staff size); size_note: the evidence, or "bilinmir".
- fit: how likely they need our catering (staff meals, events, facilities); fit_reason: 1-2 sentences.
- needs: 2-4 concrete catering needs they probably have.
- approach: 3-5 concrete next steps for our salesperson (whom to contact, what to offer, when).
- talking_points: 2-4 short points to mention in the first conversation.
- risks: 0-3 risks or reasons it may not work (own kitchen, competitor, too small...).
"""

SIZE_NAMES = {"small": "Kiçik", "medium": "Orta", "large": "Böyük", "unknown": "Bilinmir"}
FIT_NAMES = {"high": "Yüksək", "medium": "Orta", "low": "Aşağı", "unknown": "Bilinmir"}


def _client(client: OpenAI | None) -> OpenAI:
    if client is not None:
        return client
    if not os.environ.get("OPENAI_API_KEY"):
        raise OutreachError("OPENAI_API_KEY təyin edilməyib — AI analiz edə bilmir")
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=OPENAI_MAX_RETRIES)


def normalize_site(site: str) -> str:
    site = (site or "").strip()
    if site and not site.startswith(("http://", "https://")):
        site = "https://" + site
    return site


def site_text(url: str, crawler: WebsiteContacts | None = None) -> tuple[str, list]:
    """(saytın mətni, tapılan email/telefonlar). Oxunmasa ("", [])."""
    if not crawlable(url):
        return "", []
    crawler = crawler or WebsiteContacts()
    try:
        soup = BeautifulSoup(crawler.get(url, timeout=10.0).text, "lxml")
    except Exception as exc:
        log.info("company_site_failed", url=url, error=type(exc).__name__)
        return "", []
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return text[:MAX_SITE_CHARS], extract_from_text(text)[:6]


def db_facts(session: Session, name: str) -> dict:
    """Bazada bu şirkət haqqında nə var: təşkilat, lead-lər, fürsətlər."""
    key = normalize_org(name)  # "Sahil MMC" → "sahil", bazadakı name_normalized kimi
    orgs = list(session.scalars(select(Organization).where(Organization.name_normalized.contains(key)).limit(3))) if key else []
    needle = name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    lead_q = select(LeadRecord).where(or_(
        LeadRecord.entity_name.ilike(f"%{needle}%", escape="\\"),
        LeadRecord.title.ilike(f"%{needle}%", escape="\\"),
        LeadRecord.organization_id.in_([o.id for o in orgs]) if orgs else false(),
    )).order_by(LeadRecord.scraped_at.desc()).limit(MAX_LEADS)
    leads = list(session.scalars(lead_q))
    opps = session.scalar(select(func.count()).select_from(Opportunity).where(
        Opportunity.organization_id.in_([o.id for o in orgs]))) if orgs else 0
    return {"orgs": orgs, "leads": leads, "opportunities": opps or 0}


def _url(value) -> str:
    value = str(value or "").strip()
    return value[:500] if value.startswith(("http://", "https://")) else ""


def _clean_web(out: dict) -> dict:
    """Linkləri yoxlayır, siyahıları qısaldır (AI cavabı səhifəyə və bazaya düşməzdən əvvəl)."""
    out["sources"] = [u for u in (_url(x) for x in out.get("sources") or []) if u][:10]
    out["news"] = [{"title": str(n.get("title") or "")[:200], "date": str(n.get("date") or "")[:20], "url": _url(n.get("url"))}
                   for n in out.get("news") or [] if isinstance(n, dict) and n.get("title")][:5]
    out["key_people"] = [{"name": str(p.get("name") or "")[:120], "role": str(p.get("role") or "")[:120]}
                         for p in out.get("key_people") or [] if isinstance(p, dict) and p.get("name")][:8]
    out["locations"] = [str(x)[:160] for x in out.get("locations") or [] if x][:8]
    out["website"] = _url(out.get("website")) or (("https://" + out["website"].strip()) if "." in str(out.get("website") or "")
                                                   and " " not in str(out.get("website")).strip() else "")
    for k in ("phone", "email", "address", "employees", "founded"):
        v = str(out.get(k) or "").strip()
        out[k] = "" if v.lower() == "bilinmir" else v[:200]
    return out


def web_research(client: OpenAI, system: str, user: str, timeout: float = BUDGET_S - FALLBACK_S) -> dict | None:
    """GPT veb axtarışı ilə analiz (WEB_SCHEMA); alınmasa None — çağıran əvvəlki üsula keçir."""
    if (os.environ.get("COMPANY_WEB_SEARCH") or "1") == "0" or timeout < 10:
        return None
    model = (os.environ.get("COMPANY_SEARCH_MODEL") or os.environ.get("AI_ANALYSIS_MODEL") or "gpt-4.1-mini")
    try:
        resp = client.with_options(timeout=timeout, max_retries=0).responses.create(
            model=model, instructions=system, input=user,
            tools=[{"type": os.environ.get("COMPANY_SEARCH_TOOL") or "web_search"}],
            text={"format": {"type": "json_schema", "name": "company", "strict": True, "schema": WEB_SCHEMA}},
        )
        out = json.loads(resp.output_text)
    except Exception as exc:
        log.warning("company_web_research_failed", error=type(exc).__name__)
        return None
    return _clean_web(out) if isinstance(out, dict) else None


def analyse(session: Session, name: str, website: str = "", *, client: OpenAI | None = None,
            crawler: WebsiteContacts | None = None) -> dict:
    """Nəticə: {"ai": {...}, "facts": {...}, "site": url, "site_ok": bool, "found": [...]}."""
    started = time.monotonic()
    name = " ".join((name or "").split())[:200]
    if len(name) < 2:
        raise OutreachError("Şirkətin adını yaz")
    facts = db_facts(session, name)
    site = normalize_site(website) or next((o.website for o in facts["orgs"] if o.website), "") or ""
    text, found = site_text(site, crawler) if site else ("", [])

    lines = [f"COMPANY: {name}", f"WEBSITE: {site or '(unknown)'}"]
    for o in facts["orgs"]:
        lines.append(f"OUR DB ORGANIZATION: {o.name}; VÖEN {o.voen or '-'}; kind {o.kind or '-'}; "
                     f"phone {o.phone or '-'}; email {o.email or '-'}")
    lines.append(f"OUR OPEN/CLOSED OPPORTUNITIES WITH THEM: {facts['opportunities']}")
    for lead in facts["leads"]:
        lines.append(f"- LEAD ({lead.source}, {lead.status}, {lead.scraped_at:%Y-%m-%d}): {lead.title[:200]}"
                     + (f" | AI note: {lead.ai_reason[:200]}" if lead.ai_reason else ""))
    lines.append(f"\nWEBSITE TEXT:\n{text or '(could not read)'}")

    ai = _client(client)
    left = BUDGET_S - (time.monotonic() - started)
    out = web_research(ai, WEB_PROMPT.replace("{company}", company_description()), "\n".join(lines), left - FALLBACK_S)
    if out is None:
        if BUDGET_S - (time.monotonic() - started) < FALLBACK_S - 4:
            raise OutreachError("Araşdırma çox uzun çəkdi — bir az sonra yenidən cəhd et")
        system = PROMPT.replace("{company}", company_description())
        model = os.environ.get("AI_ANALYSIS_MODEL") or os.environ.get("AI_MODEL_V2") or DEFAULT_MODEL
        try:
            # Azərbaycanca 10 sahəli cavab 700 tokendən (chat_json defoltu) uzun ola bilər.
            out, _ = chat_json(ai, model, system, "\n".join(lines), SCHEMA, "company", max_tokens=1500)
        except OutreachError:
            raise
        except Exception as exc:
            log.exception("company_analysis_failed")
            raise OutreachError(f"AI analiz edə bilmədi: {type(exc).__name__}") from exc
    web = "sources" in out
    if web and not site and out.get("website"):
        site = out["website"]
    log.info("company_analysed", has_site=bool(text), leads=len(facts["leads"]), web=web,
             sources=len(out.get("sources") or []))
    return {"name": name, "ai": out, "facts": facts, "site": site, "site_ok": bool(text), "found": found, "web": web}
