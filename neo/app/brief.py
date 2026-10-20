"""Xəbərin qısa səsli izahı: AI 4-6 cümləlik izah yazır (nə baş verib, niyə bizim üçün fürsətdir,
nə etmək lazımdır), OpenAI TTS onu mp3-ə çevirir. Nəticə `lead_briefs`-də saxlanılır —
eyni lead üçün ikinci basış pulsuz və dərhal olur.

Env: OPENAI_API_KEY; AI_BRIEF_MODEL (mətn, defolt AI_MODEL_V2 / DEFAULT_MODEL),
AI_TTS_MODEL (defolt gpt-4o-mini-tts), AI_TTS_VOICE (defolt alloy).
"""
from __future__ import annotations

import os
from datetime import datetime

import structlog
from openai import OpenAI
from sqlalchemy.orm import Session

from app.ai_filter import OPENAI_MAX_RETRIES
from app.ai_scoring import DEFAULT_MODEL, chat_json
from app.db.models import LeadBrief, LeadRecord
from app.outreach import OutreachError, company_description

log = structlog.get_logger()

DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "alloy"
MAX_SCRIPT_CHARS = 1200  # ~1 dəqiqəlik səs

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["script"],
    "properties": {"script": {"type": "string"}},
}

PROMPT = """\
You brief a salesperson of an Azerbaijani catering company OUT LOUD about one news item / post /
tender that our system flagged as a sales opportunity. The text will be read by a text-to-speech
voice, so write natural spoken Azerbaijani (Latin script).

About us (what we sell):
{company}

Rules:
- 4-6 short sentences, about 30-45 seconds when spoken. Plain sentences only: no lists,
  markdown, emojis, URLs, abbreviations that are hard to pronounce.
- 1) What happened, in one or two sentences (who, what, where, when if known).
- 2) Why this is an opportunity for us, concretely.
- 3) The suggested next step (whom to contact, what to offer, deadline if there is one).
- Use ONLY facts from the source. Do not invent numbers, names or dates.
"""


def _client(client: OpenAI | None) -> OpenAI:
    if client is not None:
        return client
    if not os.environ.get("OPENAI_API_KEY"):
        raise OutreachError("OPENAI_API_KEY təyin edilməyib — AI izah yaza bilmir")
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=OPENAI_MAX_RETRIES)


def write_script(lead: LeadRecord, client: OpenAI | None = None) -> str:
    system = PROMPT.replace("{company}", company_description())
    user = (f"SOURCE: {lead.source}\nTITLE: {lead.title}\n"
            f"OUR AI'S NOTE: {lead.ai_reason or '-'}\n"
            f"DEADLINE: {lead.deadline_at.date() if lead.deadline_at else '-'}\n\n"
            f"TEXT:\n{(lead.raw_text or '')[:3500]}")
    model = os.environ.get("AI_BRIEF_MODEL") or os.environ.get("AI_MODEL_V2") or DEFAULT_MODEL
    out, _ = chat_json(_client(client), model, system, user, SCHEMA, "brief")
    return out["script"].strip()[:MAX_SCRIPT_CHARS]


def speak(script: str, client: OpenAI | None = None) -> tuple[bytes, str]:
    """(mp3, səs adı)."""
    model = os.environ.get("AI_TTS_MODEL") or DEFAULT_TTS_MODEL
    voice = os.environ.get("AI_TTS_VOICE") or DEFAULT_VOICE
    kwargs = {"model": model, "voice": voice, "input": script, "response_format": "mp3"}
    if model.startswith("gpt-4o"):
        # Yalnız gpt-4o-*-tts tonu/dili təlimatla idarə edir; tts-1 bu parametri qəbul etmir.
        kwargs["instructions"] = ("Speak Azerbaijani with a natural Baku accent, calm and clear, "
                                  "like a colleague giving a short briefing.")
    resp = _client(client).audio.speech.create(**kwargs)
    return resp.content, voice


def get_or_create(session: Session, lead: LeadRecord, client: OpenAI | None = None) -> LeadBrief:
    """Saxlanmış izah varsa onu, yoxdursa yaradıb saxlayır. Xəta → OutreachError (mətn istifadəçiyə)."""
    brief = session.get(LeadBrief, lead.dedupe_key)
    if brief is not None and brief.audio:
        return brief
    try:
        script = brief.script if brief is not None else write_script(lead, client)
        audio, voice = speak(script, client)
    except OutreachError:
        raise
    except Exception as exc:
        log.exception("brief_failed", lead=lead.dedupe_key)
        raise OutreachError(f"Səsli izah hazırlanmadı: {type(exc).__name__}") from exc
    if brief is None:
        brief = LeadBrief(lead_key=lead.dedupe_key, script=script, created_at=datetime.utcnow())
        session.add(brief)
    brief.audio, brief.voice = audio, voice
    session.commit()
    log.info("brief_created", lead=lead.dedupe_key, chars=len(script), bytes=len(audio))
    return brief
