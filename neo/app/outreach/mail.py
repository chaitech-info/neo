"""Fürsətdən AI ilə ilk məktub: model ~10 cümləlik qaralama yazır, satış əməkdaşı onu
paneldə düzəldir və "Göndər" basır — məktub şirkətin Google Workspace poçtundan SMTP ilə
birbaşa gedir (poçt proqramı açılmır). Cavab (Reply-To) göndərən əməkdaşın öz ünvanına gəlir.

Env: SMTP_HOST (defolt smtp.gmail.com), SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD
(Google "app password"), MAIL_FROM_NAME; imza üçün COMPANY_NAME/PHONE/SIGNATURE.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape

import structlog
from openai import OpenAI

from app.ai_filter import OPENAI_MAX_RETRIES
from app.ai_scoring import DEFAULT_MODEL, chat_json
from app.outreach import LANGUAGE_NAMES_EN, OutreachError, company, company_description

log = structlog.get_logger()

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject", "body"],
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
}

PROMPT = """\
You write the FIRST outreach email from an Azerbaijani catering company's sales
team to a potential client. Write in {language}.

About us (what we sell):
{company}

Rules:
- About 10 sentences in the body, plain text, short paragraphs. No markdown, no emojis.
- Polite business tone; address the contact by name if given, otherwise neutrally.
- Sentence 1-2: who we are. Then refer CONCRETELY to the client's situation from the
  source below (their tender, event, new facility, staff meals, post) — show we read it.
- Explain how we can help (staff lunches / event catering as fits), 2-3 concrete
  benefits, NO invented numbers, prices, certificates, clients or guarantees.
- Close with a clear next step (short call or meeting, or sending a tailored offer).
- Do NOT add a signature or contact block — it is appended automatically.
- If the source is a tender, mention we will prepare an offer for the tender and ask
  whom to contact for documents; do not claim we already applied.
- subject: short, specific, in the same language.
"""


def _client(client: OpenAI | None) -> OpenAI:
    if client is not None:
        return client
    if not os.environ.get("OPENAI_API_KEY"):
        raise OutreachError("OPENAI_API_KEY təyin edilməyib — AI məktub yaza bilmir")
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=OPENAI_MAX_RETRIES)


TEMPLATE_RULES = """
The user uploaded THEIR OWN LETTER TEMPLATE (below, between <template> tags). Follow it closely:
keep its structure, paragraph order, tone, approximate length and fixed phrases; replace its
placeholders ([...], {...}, <...>, "X", blanks) with real facts from the source; drop template parts
that clearly do not fit this client. If it is in another language, write it in the language above.
Skip any signature/contact block in the template — the signature is appended automatically.
The general rules above still apply where the template is silent.
"""
REVISION_RULES = """
This is a REVISION. The previous draft is below (between <previous> tags) and the user asked for
changes (between <changes> tags). Apply exactly the requested changes / additions and keep the rest
of the draft as it is, unless the change requires otherwise. Do not invent facts.
"""
MAX_TEMPLATE = 6000
MAX_INSTRUCTIONS = 2000


def without_signature(body: str) -> str:
    """Panel imzanı "\\n--\\n" ilə əlavə edir — yenidən yazdırmazdan əvvəl çıxarılır ki, ikiqat olmasın."""
    head, sep, _ = (body or "").rpartition("\n--\n")
    return head.rstrip() if sep else (body or "").strip()


def draft_email(*, lead_title: str, lead_text: str, org_name: str | None, contact_name: str | None,
                contact_title: str | None, sender_name: str, language: str,
                template: str | None = None, previous: dict | None = None, instructions: str | None = None,
                company_name: str | None = None, client: OpenAI | None = None) -> dict[str, str]:
    """{subject, body} — body imzasızdır (imza `with_signature` ilə əlavə olunur).
    `template` — istifadəçinin şablonu; `previous` + `instructions` — "Yenidən yarat" (istifadəçinin düzəlişi);
    `company_name` — seçilmiş göndərən şirkət (yoxdursa .env-dəki COMPANY_NAME)."""
    system = (PROMPT.replace("{language}", LANGUAGE_NAMES_EN.get(language, "Azerbaijani"))
              .replace("{company}", company_description()))
    if template:
        system += TEMPLATE_RULES
    if previous:
        system += REVISION_RULES
    me = company()
    user = (f"OUR COMPANY NAME: {company_name or me['name'] or '(not set)'}\nSENDER: {sender_name}\n"
            f"CLIENT ORGANIZATION: {org_name or '(unknown)'}\n"
            f"CONTACT: {contact_name or '(unknown)'}{', ' + contact_title if contact_title else ''}\n\n"
            f"SOURCE TITLE: {lead_title}\n\nSOURCE TEXT:\n{(lead_text or '')[:3500]}")
    if template:
        user += f"\n\n<template>\n{template[:MAX_TEMPLATE]}\n</template>"
    if previous:
        user += (f"\n\n<previous>\nSUBJECT: {previous.get('subject', '')}\n\n"
                 f"{without_signature(previous.get('body', ''))[:6000]}\n</previous>"
                 f"\n\n<changes>\n{(instructions or '').strip()[:MAX_INSTRUCTIONS]}\n</changes>")
    model = os.environ.get("AI_EMAIL_MODEL") or os.environ.get("AI_MODEL_V2") or DEFAULT_MODEL
    try:
        out, _ = chat_json(_client(client), model, system, user, SCHEMA, "email", max_tokens=1500)
    except OutreachError:
        raise
    except Exception as exc:
        log.exception("email_draft_failed")
        raise OutreachError(f"AI məktub yaza bilmədi: {type(exc).__name__}") from exc
    return {"subject": out["subject"].strip()[:200], "body": out["body"].strip()}


def with_signature(body: str, sender_name: str) -> str:
    me = company()
    lines = [body.rstrip(), "", "--", sender_name]
    if me["signature"]:
        lines.append(me["signature"])
    else:
        lines += [v for v in (me["name"], me["phone"]) if v]
    return "\n".join(lines)


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def send_email(*, to: str, subject: str, body: str, reply_to: str | None,
               attachments: list[tuple[str, bytes, str]] = (), smtp_factory=smtplib.SMTP, sender=None) -> str:
    """Göndərir, Message-ID qaytarır. Xəta → OutreachError (mətn istifadəçiyə göstərilir).
    `attachments` — (fayl adı, məzmun, MIME tipi); yoxlama (ölçü, növ) çağıranın işidir.
    `sender` — seçilmiş şirkət poçtu (app/mailboxes.Sender); None → .env-dəki defolt SMTP."""
    if sender is not None:
        user, password, address, from_name = sender.user, sender.password, sender.email, sender.name
        host, port = sender.smtp_host, sender.smtp_port
        if not password:
            raise OutreachError(f"{sender.name} poçtunun şifrəsi təyin edilməyib"
                                + (f" ({sender.password_env})" if sender.password_env else ""))
    else:
        user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
        address, from_name = user, os.environ.get("MAIL_FROM_NAME") or company()["name"] or user
        host, port = os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "587"))
    if not (user and password):
        raise OutreachError("Poçt qoşulmayıb: SMTP_USER və SMTP_PASSWORD təyin edilməyib")
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, address))
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to and reply_to.lower() not in (user.lower(), (address or "").lower()):
        msg["Reply-To"] = reply_to
    msg["Message-ID"] = make_msgid(domain=(address or user).rsplit("@", 1)[-1])
    msg.set_content(body)
    paragraphs = "".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in body.split("\n\n"))
    msg.add_alternative(f"<html><body style=\"font-family:Arial,sans-serif;font-size:14px\">{paragraphs}</body></html>",
                        subtype="html")
    for name, data, mime in attachments:
        maintype, _, subtype = (mime or "").partition("/")
        if not (maintype and subtype):
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    # 465 — birbaşa TLS (SMTP_SSL), digər portlar — STARTTLS.
    implicit_tls = port == 465 and smtp_factory is smtplib.SMTP
    factory = (lambda h, p, timeout: smtplib.SMTP_SSL(h, p, timeout=timeout, context=ssl.create_default_context())) \
        if implicit_tls else smtp_factory
    try:
        with factory(host, port, timeout=20) as smtp:
            if not implicit_tls:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise OutreachError(f"Poçt girişi alınmadı ({address}) — istifadəçi adını / app password-u yoxla") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise OutreachError(f"Alıcı ünvanı qəbul edilmədi: {to}") from exc
    except (smtplib.SMTPException, OSError) as exc:
        log.exception("email_send_failed", host=host)
        raise OutreachError(f"Məktub göndərilmədi: {type(exc).__name__}") from exc
    log.info("email_sent", to_domain=to.rsplit("@", 1)[-1], attachments=len(attachments))
    return msg["Message-ID"]
