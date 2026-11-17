"""Alt şirkətlər: şirkət analiz olunanda AI onun törəmə/qrup şirkətlərini araşdırır.

Mənbələr: OpenAI veb axtarışı (Responses API, `web_search` aləti) + ana şirkətin saytındakı "Şirkətlərimiz /
Group / Haqqımızda" kimi səhifələr. Yalnız əlaqəni açıq yazan mənbəsi (link) olan şirkətlər saxlanılır —
AI ad uydurmasın deyə mənbəsiz nəticə atılır. Tapılanlar şirkət bazasına ana şirkətə bağlı yazılır
(organizations.parent_id), sonra panel hər birini ayrıca analiz edir (app/company_routes.py).

Env: SUBSIDIARY_MODEL (defolt AI_ANALYSIS_MODEL / gpt-4.1-mini), SUBSIDIARY_SEARCH_TOOL (defolt web_search),
SUBSIDIARIES_MAX (defolt 8 — avtomatik analiz olunan alt şirkət sayı).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import structlog
from bs4 import BeautifulSoup
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import companies
from app.company_analysis import FIT_NAMES, _client, site_text
from app.contacts import WebsiteContacts, crawlable
from app.db.models import Organization
from app.outreach import OutreachError
from app.tenders import normalize_org

log = structlog.get_logger()

MAX_SUBSIDIARIES = int(os.environ.get("SUBSIDIARIES_MAX") or 8)
MAX_GROUP_PAGES = 3
MAX_GROUP_CHARS = 6000
TIMEOUT_S = 50  # Vercel funksiyası 60 saniyədir

# Saytda alt şirkətlərin siyahısı adətən bu səhifələrdə olur.
_GROUP_LINK_RE = re.compile(
    r"compan|group|subsidiar|holding|portfolio|brand|about|"
    r"şirkətlər|sirketler|qrup|holdinq|brendl|haqq|"
    r"компани|холдинг|групп|о-нас|о_нас|бренд", re.I)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subsidiaries"],
    "properties": {"subsidiaries": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "website", "relation", "source_url", "in_azerbaijan"],
        "properties": {
            "name": {"type": "string"},
            "website": {"type": "string"},
            "relation": {"type": "string"},
            "source_url": {"type": "string"},
            "in_azerbaijan": {"type": "boolean"},
        },
    }}},
}

PROMPT = """\
Research the subsidiaries of ONE company for an Azerbaijani catering company's sales team.

Company: {name}
Website: {site}

Find companies that this company owns or controls, or that belong to the same group/holding under it
(subsidiaries, daughter companies, group member companies). Use web search and the website text below.

Rules:
- Include a company ONLY if a source explicitly states the relationship; source_url must be that page.
- Do not include the company itself, its parent, partners, clients, suppliers, or mere product brands.
- name: the company's name as used in Azerbaijan (e.g. "Azersun Holding" → "Azərsun Logistika MMC").
- website: its own site if known, else "".
- relation: a short Azerbaijani phrase, e.g. "törəmə şirkət", "holdinqin üzvü", "100% payı ana şirkətdədir".
- in_azerbaijan: true if it operates in Azerbaijan.
- If you find none, return an empty list. Never invent companies. At most 12.

WEBSITE TEXT (group / about pages):
{pages}
"""


def group_pages(site: str, crawler: WebsiteContacts | None = None) -> str:
    """Ana şirkətin saytından alt şirkətlər siyahısı ola biləcək səhifələrin mətni (ən çox 3 səhifə)."""
    if not site or not crawlable(site):
        return ""
    crawler = crawler or WebsiteContacts()
    try:
        soup = BeautifulSoup(crawler.get(site, timeout=10.0).text, "lxml")
    except Exception as exc:
        log.info("subsidiary_site_failed", url=site, error=type(exc).__name__)
        return ""
    host = urlparse(site).netloc.lower().removeprefix("www.")
    links = []
    for a in soup.find_all("a", href=True):
        url = urljoin(site, a["href"]).split("#")[0]
        if urlparse(url).netloc.lower().removeprefix("www.") != host or url in links or url.rstrip("/") == site.rstrip("/"):
            continue
        if _GROUP_LINK_RE.search(url) or _GROUP_LINK_RE.search(a.get_text(" ", strip=True)):
            links.append(url)
    texts = []
    for url in links[:MAX_GROUP_PAGES]:
        text, _ = site_text(url, crawler)
        if text:
            texts.append(f"[{url}]\n{text}")
    return "\n\n".join(texts)[:MAX_GROUP_CHARS]


def _clean(found: list, parent_name: str, parent_site: str = "") -> list[dict]:
    """Mənbəsiz, xaricdəki, ana şirkətin özü olan və təkrar nəticələr atılır. Ana şirkətin domenindəki səhifə alt
    şirkətin saytı sayılmır — yoxsa şirkət bazası onu domenə görə ana şirkətin özü kimi tanıyardı."""
    out, seen = [], {normalize_org(parent_name)}
    parent_domain = companies.normalize_domain(parent_site)
    for f in found:
        name = " ".join(str(f.get("name") or "").split())[:300]
        source = str(f.get("source_url") or "").strip()
        key = normalize_org(name)
        if len(name) < 2 or not key or key in seen or not source.startswith(("http://", "https://")):
            continue
        if not f.get("in_azerbaijan"):
            continue
        seen.add(key)
        website = companies.site_url(str(f.get("website") or "").strip())
        domain = companies.normalize_domain(website)
        out.append({"name": name, "website": website if domain and domain != parent_domain else None,
                    "relation": " ".join(str(f.get("relation") or "").split())[:200] or None, "source": source[:500]})
    return out[:MAX_SUBSIDIARIES]


def research(name: str, site: str = "", *, client: OpenAI | None = None,
             crawler: WebsiteContacts | None = None) -> list[dict]:
    """AI + veb axtarışı → [{name, website, relation, source}] (mənbəsi olanlar)."""
    pages = group_pages(site, crawler) if site else ""
    model = (os.environ.get("SUBSIDIARY_MODEL") or os.environ.get("AI_ANALYSIS_MODEL") or "gpt-4.1-mini")
    prompt = PROMPT.format(name=name, site=site or "(unknown)", pages=pages or "(not available)")
    try:
        resp = _client(client).with_options(timeout=TIMEOUT_S).responses.create(
            model=model, input=prompt,
            tools=[{"type": os.environ.get("SUBSIDIARY_SEARCH_TOOL") or "web_search"}],
            text={"format": {"type": "json_schema", "name": "subsidiaries", "strict": True, "schema": SCHEMA}},
        )
        found = json.loads(resp.output_text).get("subsidiaries") or []
    except OutreachError:
        raise
    except Exception as exc:
        log.exception("subsidiary_research_failed", company=name)
        raise OutreachError(f"Alt şirkətlər araşdırılmadı: {type(exc).__name__}") from exc
    out = _clean(found if isinstance(found, list) else [], name, site)
    log.info("subsidiaries_found", company=name, raw=len(found), kept=len(out), group_pages=bool(pages))
    return out


def save(session: Session, parent: Organization, found: list[dict]) -> list[Organization]:
    """Tapılanları şirkət bazasına ana şirkətə bağlı yazır (dublikatsız); başqa ana şirkətə bağlı olanlara toxunmur."""
    for f in found:
        website = f.get("website") if companies.normalize_domain(f.get("website")) != parent.domain else None
        try:
            org = companies.company_for(session, f["name"], website)
        except companies.CompanyError:
            continue
        if org.id == parent.id or org.id == parent.parent_id or org.parent_id not in (None, parent.id):
            continue
        org.parent_id = parent.id
        org.parent_relation = f.get("relation")
        org.parent_source = f.get("source")
        companies.list_company(org)
    parent.subsidiaries_checked_at = datetime.utcnow()
    session.commit()
    return of(session, parent)


def of(session: Session, parent: Organization) -> list[Organization]:
    return list(session.scalars(select(Organization).where(Organization.parent_id == parent.id)
                                .order_by(Organization.name)))


def ai_ready() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def context(session: Session, org: Organization) -> dict:
    """Səhifələr üçün: alt şirkətlərin kartları, araşdırma vaxtı və ana şirkət (bu şirkət özü alt şirkətdirsə)."""
    return {"subs": [card(o) for o in of(session, org)], "subs_at": org.subsidiaries_checked_at,
            "parent_org": session.get(Organization, org.parent_id) if org.parent_id else None, "ai_ready": ai_ready()}


def card(org: Organization) -> dict:
    """templates/_subsidiaries.html üçün: ad, sayt, münasibət, mənbə, analiz vəziyyəti."""
    analysis = companies.analysis_of(org)
    ai = (analysis or {}).get("ai") or {}
    return {"id": org.id, "name": org.name, "website": org.website, "domain": org.domain,
            "relation": org.parent_relation, "source": org.parent_source, "analysed": analysis is not None,
            "fit": ai.get("fit"), "fit_name": FIT_NAMES.get(ai.get("fit") or "", ""), "summary": ai.get("summary") or "",
            "industry": org.industry}
