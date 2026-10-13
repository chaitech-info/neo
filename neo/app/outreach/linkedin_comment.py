"""LinkedIn postuna avtomatik qısa şərh — "bu işi görürük, bizimlə əlaqə saxlayın".

Nə vaxt: LinkedIn **alıcı postu** (kimsə catering axtardığını yazıb) validasiyada təsdiqlənib
fürsətə çevriləndə (app/web.py::approve_lead). Şərhin mətni yoxlanmır (istifadəçi qərarı,
2026-10-13), amma lead-i insan təsdiqləyir — human-in-the-loop qorunur.
Vakansiya elanından gələn işəgötürən lead-lərinə ("[SİQNAL: ...]") şərh yazılmır — iş
elanının altında catering reklamı spam kimi görünür; onlara email/zəng.

Göndərmə: Unipile API (şirkətin bir satış LinkedIn hesabı qoşulub). LinkedIn-in öz API-si
başqasının postuna şərhə icazə vermir; Unipile LinkedIn qaydalarına zidd olsa da riski
azaltmaq üçün: gündəlik limit, bir posta bir şərh, söndürmə açarı.
Env: LINKEDIN_AUTOCOMMENT=on, LINKEDIN_COMMENT_DAILY_CAP (10), UNIPILE_DSN, UNIPILE_API_KEY,
UNIPILE_ACCOUNT_ID; mətndə COMPANY_NAME, COMPANY_PHONE.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import httpx
import structlog
from openai import OpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_filter import OPENAI_MAX_RETRIES
from app.ai_scoring import DEFAULT_MODEL, chat_json
from app.db.models import LeadRecord, Opportunity, Outreach
from app.db.repository import record_event
from app.outreach import LANGUAGE_NAMES_EN, OutreachError, company, company_description, detect_language
from app.scrapers.employer_signal import SIGNAL_PREFIX

log = structlog.get_logger()

MAX_CHARS = 300
_ACTIVITY = re.compile(r"(?:activity|ugcPost|share)[-:](\d{10,25})")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["comment"],
    "properties": {"comment": {"type": "string"}},
}

PROMPT = """\
You write a SHORT public LinkedIn comment from an Azerbaijani catering company under
a post where someone looks for exactly what we offer. Write in {language}.

About us:
{company}

Rules: 1-2 sentences, under 250 characters. Friendly and specific to the post (refer
to what they need), say we do this and invite them to contact us{phone}. No links, no
hashtags, no emojis, no prices or invented claims. Do not sound like an ad blast.
Sign with the company name only if given: {name}.
"""


def enabled() -> bool:
    return os.environ.get("LINKEDIN_AUTOCOMMENT", "off").lower() == "on"


def daily_cap() -> int:
    try:
        return int(os.environ.get("LINKEDIN_COMMENT_DAILY_CAP", "10"))
    except ValueError:
        return 10


def is_buyer_post(lead: LeadRecord) -> bool:
    """Yalnız açar sözlə tapılan LinkedIn postu. İşəgötürən lead-inin source_url-u şirkət saytı və ya
    LinkedIn şirkət axtarışıdır (post deyil) — buna görə yoxlama source_url-un özünün post olmasıdır;
    "[SİQNAL" prefiksi əlavə qoruyucudur (SIGNAL_PREFIX, ROLE_SIGNAL_PREFIX)."""
    url = lead.source_url or ""
    return (lead.source == "linkedin" and "linkedin.com/" in url and post_id(url) is not None
            and not (lead.raw_text or "").lstrip().startswith(SIGNAL_PREFIX[:8]))


def post_id(url: str) -> str | None:
    m = _ACTIVITY.search(url or "")
    return m.group(1) if m else None


def write_comment(lead: LeadRecord, client: OpenAI | None = None) -> tuple[str, str]:
    """(şərh, dil)."""
    language = detect_language(lead.raw_text)
    me = company()
    system = (PROMPT.replace("{language}", LANGUAGE_NAMES_EN[language])
              .replace("{company}", company_description())
              .replace("{phone}", f" (phone {me['phone']})" if me["phone"] else " (by direct message)")
              .replace("{name}", me["name"] or "(none)"))
    if client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise OutreachError("OPENAI_API_KEY təyin edilməyib")
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=OPENAI_MAX_RETRIES)
    model = os.environ.get("AI_EMAIL_MODEL") or os.environ.get("AI_MODEL_V2") or DEFAULT_MODEL
    out, _ = chat_json(client, model, system, f"POST:\n{(lead.raw_text or '')[:2500]}", SCHEMA, "comment")
    text = " ".join(out["comment"].split())
    if not text:
        raise OutreachError("AI boş şərh qaytardı")
    return text[:MAX_CHARS], language


def post_comment(url: str, text: str, http: httpx.Client | None = None) -> str:
    """Unipile ilə şərh. Əvvəl postun `social_id`-si alınır (URL-dəki activity nömrəsindən),
    sonra şərh yazılır. Qaytarır: şərhin id-si (və ya social_id)."""
    dsn, key, account = (os.environ.get(n, "").strip() for n in ("UNIPILE_DSN", "UNIPILE_API_KEY", "UNIPILE_ACCOUNT_ID"))
    if not (dsn and key and account):
        raise OutreachError("LinkedIn hesabı qoşulmayıb: UNIPILE_DSN / UNIPILE_API_KEY / UNIPILE_ACCOUNT_ID")
    activity = post_id(url)
    if not activity:
        raise OutreachError("Post linkindən LinkedIn post nömrəsi tapılmadı")
    base = dsn if dsn.startswith("http") else f"https://{dsn}"
    headers = {"X-API-KEY": key, "accept": "application/json"}
    own = http is None
    http = http or httpx.Client(timeout=20.0)
    try:
        post = http.get(f"{base}/api/v1/posts/{activity}", params={"account_id": account}, headers=headers)
        post.raise_for_status()
        social_id = post.json().get("social_id") or f"urn:li:activity:{activity}"
        resp = http.post(f"{base}/api/v1/posts/{social_id}/comments", headers=headers,
                         json={"account_id": account, "text": text})
        resp.raise_for_status()
        body = resp.json() if resp.content else {}
    except httpx.HTTPStatusError as exc:
        raise OutreachError(f"Unipile xətası {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except httpx.HTTPError as exc:
        raise OutreachError(f"Unipile-a çatmaq olmadı: {type(exc).__name__}") from exc
    finally:
        if own:
            http.close()
    return str(body.get("comment_id") or body.get("id") or social_id)


def auto_comment(session: Session, opp: Opportunity, *, user_id: int | None = None, retry: bool = False,
                 client: OpenAI | None = None, http: httpx.Client | None = None) -> Outreach | None:
    """Şərti ödəyirsə şərh yazır və `outreach`-a yazır; ödəmirsə None. Xəta fürsəti bloklamır —
    `failed` sətri yazılır, səhifədə "Yenidən cəhd et". `retry` — istifadəçi düyməsi (söndürmə açarı yenə də keçərlidir)."""
    lead = session.get(LeadRecord, opp.lead_key) if opp.lead_key else None
    if lead is None or not is_buyer_post(lead) or not enabled():
        return None
    url = lead.origin_url or lead.source_url
    sent = session.scalar(select(func.count()).select_from(Outreach).where(
        Outreach.kind == "linkedin_comment", Outreach.to_address == url, Outreach.status == "sent"))
    if sent:
        return None  # bir posta bir şərh
    if not retry and session.scalar(select(func.count()).select_from(Outreach).where(
            Outreach.kind == "linkedin_comment", Outreach.to_address == url)):
        return None
    since = datetime.utcnow() - timedelta(days=1)
    today = session.scalar(select(func.count()).select_from(Outreach).where(
        Outreach.kind == "linkedin_comment", Outreach.status == "sent", Outreach.sent_at >= since))
    now = datetime.utcnow()
    row = Outreach(kind="linkedin_comment", lead_key=lead.dedupe_key, opportunity_id=opp.id, to_address=url,
                   body="", status="failed", user_id=user_id, created_at=now)
    if today >= daily_cap():
        row.status, row.error = "skipped", f"Gündəlik limit ({daily_cap()}) dolub — sabah yenidən cəhd et"
        row.body = "(yazılmadı)"
    else:
        try:
            row.body, row.language = write_comment(lead, client)
            row.external_id = post_comment(url, row.body, http)
            row.status, row.sent_at = "sent", datetime.utcnow()
            record_event(session, lead, "comment", lead.status, user_id, note=row.body[:500])
        except OutreachError as exc:
            row.error = str(exc)[:500]
            row.body = row.body or "(yazılmadı)"
        except Exception as exc:  # AI və s. — fürsət yaradılması pozulmasın
            log.exception("linkedin_comment_failed", url=url)
            row.error = f"{type(exc).__name__}"
            row.body = row.body or "(yazılmadı)"
    session.add(row)
    session.commit()
    log.info("linkedin_comment", status=row.status, url=url, error=row.error)
    return row
