"""Alətlər → Şirkət analizi: şirkətin adı (və istəyə görə saytı) → AI satış analizi.

Mənbələr: şirkət saytının ana səhifəsi (robots.txt-ə əməl edir), bazadakı təşkilat, həmin şirkətlə
bağlı lead-lər və fürsətlər. AI yalnız bu faktlara əsaslanır; bilinməyən "bilinmir" yazılır.
"""
from __future__ import annotations

import os

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


def analyse(session: Session, name: str, website: str = "", *, client: OpenAI | None = None,
            crawler: WebsiteContacts | None = None) -> dict:
    """Nəticə: {"ai": {...}, "facts": {...}, "site": url, "site_ok": bool, "found": [...]}."""
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

    system = PROMPT.replace("{company}", company_description())
    model = os.environ.get("AI_ANALYSIS_MODEL") or os.environ.get("AI_MODEL_V2") or DEFAULT_MODEL
    try:
        # Azərbaycanca 10 sahəli cavab 700 tokendən (chat_json defoltu) uzun ola bilər.
        out, _ = chat_json(_client(client), model, system, "\n".join(lines), SCHEMA, "company", max_tokens=1500)
    except OutreachError:
        raise
    except Exception as exc:
        log.exception("company_analysis_failed")
        raise OutreachError(f"AI analiz edə bilmədi: {type(exc).__name__}") from exc
    log.info("company_analysed", has_site=bool(text), leads=len(facts["leads"]))
    return {"name": name, "ai": out, "facts": facts, "site": site, "site_ok": bool(text), "found": found}
