"""Göndərən şirkətin poçt qutuları və gələn cavablar.

- Məktub göndərilərkən "Kimdən" seçilir: `mailboxes` cədvəlindəki şirkət poçtu və ya .env-dəki defolt SMTP.
  Şifrə bazada saxlanmır: env `MAILBOX_<KEY>_PASSWORD` (məs. key=seaport → MAILBOX_SEAPORT_PASSWORD).
- Cavablar IMAP ilə oxunur (`poll`): bizim göndərdiyimiz məktubun Message-ID-sinə istinad edən
  (In-Reply-To / References) və ya "Re: <mövzu>" ilə alıcının özündən gələn məktub fürsətə bağlanır,
  AI onu müsbət / mənfi / avtomatik cavab kimi təsnif edir → `opportunities.reply_status`:
  müsbət → "potential" (Potensial müştəri), mənfi → "declined" (İmtina), cavab gələnə qədər "waiting".
  Avtomatik cavab (məzuniyyət, çatdırılma xətası) statusu dəyişmir.

Yoxlama: fürsətlər səhifəsində "Cavabları yoxla" düyməsi, `/api/cron/mail` (Bearer TRIGGER_TOKEN)
və Modal tick-i (modal_app.py::poll_mail).
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import getaddresses, parsedate_to_datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import InboundEmail, Mailbox, Opportunity, Outreach
from app.notify import notify

log = structlog.get_logger()

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,31}$")
REPLY_STATUS_NAMES = {"waiting": "Gözləmədə", "potential": "Potensial müştəri", "declined": "İmtina"}
SENTIMENT_NAMES = {"positive": "Müsbət", "negative": "Mənfi", "auto": "Avtomatik cavab"}
FIRST_POLL_DAYS = 14
MAX_MESSAGES_PER_POLL = 200
MAX_BODY = 20000


@dataclass
class Sender:
    """SMTP/IMAP parametrləri — ya `mailboxes` sətri, ya .env-dəki defolt poçt."""

    name: str
    email: str
    user: str
    password: str | None
    smtp_host: str
    smtp_port: int
    imap_host: str | None
    imap_port: int
    mailbox_id: int | None = None
    password_env: str | None = None  # xəta mətni üçün: hansı env dəyişəni təyin edilməlidir

    @property
    def can_send(self) -> bool:
        return bool(self.user and self.password)

    @property
    def can_receive(self) -> bool:
        return bool(self.can_send and self.imap_host)


def password_env(key: str) -> str:
    return f"MAILBOX_{re.sub(r'[^A-Za-z0-9]', '_', key).upper()}_PASSWORD"


def default_sender() -> Sender | None:
    from app.outreach import company

    user = os.environ.get("SMTP_USER")
    if not user:
        return None
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    imap_host = os.environ.get("IMAP_HOST") or ("imap.gmail.com" if smtp_host == "smtp.gmail.com" else None)
    return Sender(name=os.environ.get("MAIL_FROM_NAME") or company()["name"] or user, email=user, user=user,
                  password=os.environ.get("SMTP_PASSWORD"), smtp_host=smtp_host,
                  smtp_port=int(os.environ.get("SMTP_PORT", "587")), imap_host=imap_host,
                  imap_port=int(os.environ.get("IMAP_PORT", "993")))


def sender_for(mb: Mailbox) -> Sender:
    return Sender(name=mb.name, email=mb.email, user=mb.username or mb.email,
                  password=os.environ.get(password_env(mb.key)), smtp_host=mb.smtp_host or "smtp.gmail.com",
                  smtp_port=mb.smtp_port or 587, imap_host=mb.imap_host, imap_port=mb.imap_port or 993,
                  mailbox_id=mb.id, password_env=password_env(mb.key))


def active(session: Session) -> list[Mailbox]:
    return list(session.scalars(select(Mailbox).where(Mailbox.is_active.is_(True)).order_by(Mailbox.name)))


def resolve(session: Session, mailbox_id: str | int | None) -> Sender | None:
    """Formdan gələn seçim: boş → defolt, rəqəm → aktiv poçt qutusu (yoxdursa None)."""
    if mailbox_id in (None, "", "0", 0):
        return default_sender()
    try:
        mb = session.get(Mailbox, int(mailbox_id))
    except (TypeError, ValueError):
        return None
    return sender_for(mb) if mb is not None and mb.is_active else None


# --- Cavabların təsnifatı ---------------------------------------------------------

CLASSIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sentiment", "summary"],
    "properties": {"sentiment": {"type": "string", "enum": ["positive", "negative", "auto"]},
                   "summary": {"type": "string"}},
}
CLASSIFY_PROMPT = """\
You classify a reply that a potential client sent to an Azerbaijani catering company's first sales email.
- positive: any interest — wants an offer/prices/menu, a call or meeting, asks questions, forwards us
  to the right person, says "send details", or is open later.
- negative: not interested, no need, already has a supplier, asks to stop writing, rejects.
- auto: automatic message — out of office, vacation, delivery failure, ticket auto-acknowledgement.
summary: one short sentence in Azerbaijani about what the client said.
"""
_AUTO_RE = re.compile(r"out of office|automatic reply|auto.?reply|autoreply|undeliver|delivery status|"
                      r"avtomatik cavab|məzuniyyət|автоответ|в отпуске", re.I)
_NEGATIVE_RE = re.compile(r"maraql[ıi] deyil|maraqlanm[ıi]r|ehtiyac[ıi]m[ıi]z yoxdur|ehtiyac yoxdur|"
                          r"not interested|no need|no thank|не интерес|не нужн|отказ", re.I)


def classify(subject: str, body: str, client=None) -> tuple[str | None, str | None]:
    """(sentiment, qısa xülasə). OpenAI yoxdursa sadə qayda; anlaşılmırsa (None, None)."""
    if client is not None or os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI

            from app.ai_filter import OPENAI_MAX_RETRIES
            from app.ai_scoring import DEFAULT_MODEL, chat_json

            client = client or OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=OPENAI_MAX_RETRIES)
            model = os.environ.get("AI_EMAIL_MODEL") or os.environ.get("AI_MODEL_V2") or DEFAULT_MODEL
            out, _ = chat_json(client, model, CLASSIFY_PROMPT, f"SUBJECT: {subject}\n\n{body[:4000]}",
                               CLASSIFY_SCHEMA, "reply", max_tokens=200)
            return out["sentiment"], out["summary"].strip()[:500] or None
        except Exception:
            log.warning("reply_classify_failed", exc_info=True)
    text = f"{subject}\n{body}"
    if _AUTO_RE.search(text):
        return "auto", None
    if _NEGATIVE_RE.search(text):
        return "negative", None
    return None, None


def apply_reply_status(opp: Opportunity, sentiment: str | None) -> None:
    if sentiment == "positive":
        opp.reply_status = "potential"
    elif sentiment == "negative":
        opp.reply_status = "declined"
    opp.updated_at = datetime.utcnow()


# --- IMAP ---------------------------------------------------------------------------

_QUOTE_HEAD = re.compile(r"^(On .+wrote:|.+ yazdı:|.+ написал\(а\):|-{2,} ?Original Message|From: .+)$", re.I)


def strip_quoted(text: str) -> str:
    """Cavabın yalnız yeni hissəsi: sitat (">") və "On ... wrote:"-dan sonrası atılır."""
    out = []
    for line in (text or "").splitlines():
        if _QUOTE_HEAD.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _text_of(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        from bs4 import BeautifulSoup

        content = BeautifulSoup(content, "lxml").get_text("\n")
    return content


def _ids(value: str | None) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value or "")


def _address(value: str | None) -> str:
    pairs = getaddresses([value or ""])
    return (pairs[0][1] if pairs else "").strip().lower()


def _norm_subject(subject: str | None) -> str:
    return re.sub(r"^((re|fw|fwd|aw|ответ)\s*:\s*)+", "", (subject or "").strip(), flags=re.I).lower()


def match_outreach(session: Session, msg: email.message.EmailMessage, since: datetime) -> Outreach | None:
    refs = _ids(msg.get("In-Reply-To")) + _ids(msg.get("References"))
    if refs:
        row = session.scalars(select(Outreach).where(Outreach.kind == "email", Outreach.external_id.in_(refs))
                              .order_by(Outreach.sent_at.desc())).first()
        if row is not None:
            return row
    # In-Reply-To-suz cavab: eyni alıcıdan, "Re: <bizim mövzu>".
    sender, subject = _address(msg.get("From")), _norm_subject(msg.get("Subject"))
    if not sender or not subject:
        return None
    for row in session.scalars(select(Outreach).where(Outreach.kind == "email", Outreach.status == "sent",
                                                      Outreach.to_address == sender, Outreach.sent_at >= since)
                               .order_by(Outreach.sent_at.desc())):
        if _norm_subject(row.subject) == subject:
            return row
    return None


def _store(session: Session, msg: email.message.EmailMessage, outreach: Outreach, mailbox_id: int | None,
           classifier) -> InboundEmail | None:
    message_id = (_ids(msg.get("Message-ID")) or [None])[0]
    if not message_id or session.scalar(select(InboundEmail.id).where(InboundEmail.message_id == message_id)):
        return None
    subject = " ".join(str(msg.get("Subject") or "").split())[:300]
    body = strip_quoted(_text_of(msg))[:MAX_BODY] or "(boş məktub)"
    try:
        received = parsedate_to_datetime(msg.get("Date"))
        if received.tzinfo is not None:  # bazada naive UTC
            received = received.astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        received = datetime.utcnow()
    sentiment, summary = classifier(subject, body)
    row = InboundEmail(message_id=message_id[:255], mailbox_id=mailbox_id, outreach_id=outreach.id,
                       opportunity_id=outreach.opportunity_id, from_address=_address(msg.get("From"))[:254],
                       subject=subject, body=body, sentiment=sentiment, summary=summary, received_at=received)
    session.add(row)
    opp = session.get(Opportunity, outreach.opportunity_id) if outreach.opportunity_id else None
    if opp is not None:
        apply_reply_status(opp, sentiment)
        status = REPLY_STATUS_NAMES.get(opp.reply_status or "", "")
        text = f"Məktuba cavab gəldi: {row.from_address} — {opp.title[:80]}" + (f" ({status})" if status else "")
        for uid in {opp.owner_id, outreach.user_id} - {None}:
            notify(session, uid, text, f"/opportunities/{opp.id}#replies", commit=False)
    try:
        session.commit()
    except IntegrityError:  # eyni məktubu paralel yoxlama artıq yazıb
        session.rollback()
        return None
    return row


def poll(session: Session, sender: Sender, *, imap_factory=imaplib.IMAP4_SSL, classifier=classify,
         since: datetime | None = None) -> int:
    """Bir poçt qutusunun INBOX-unu oxuyur (məktublar "oxunmuş" olmur), yeni cavabların sayı."""
    if not sender.can_receive:
        return 0
    mb = session.get(Mailbox, sender.mailbox_id) if sender.mailbox_id else None
    since = since or (mb.last_polled_at - timedelta(days=1) if mb and mb.last_polled_at
                      else datetime.utcnow() - timedelta(days=FIRST_POLL_DAYS))
    added = 0
    try:
        imap = imap_factory(sender.imap_host, sender.imap_port)
        try:
            imap.login(sender.user, sender.password)
            imap.select("INBOX", readonly=True)
            _, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"))
            ids = (data[0] or b"").split()[-MAX_MESSAGES_PER_POLL:]
            for num in ids:
                _, parts = imap.fetch(num, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw, policy=policy.default)
                if _address(msg.get("From")) == sender.email.lower():
                    continue  # öz göndərdiyimiz
                outreach = match_outreach(session, msg, since - timedelta(days=60))
                if outreach is not None and _store(session, msg, outreach, sender.mailbox_id, classifier):
                    added += 1
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except (imaplib.IMAP4.error, OSError) as exc:
        log.warning("mail_poll_failed", mailbox=sender.email, error=type(exc).__name__)
        raise MailboxError(f"{sender.email}: poçt oxunmadı ({type(exc).__name__})") from exc
    if mb is not None:
        mb.last_polled_at = datetime.utcnow()
        session.commit()
    log.info("mail_polled", mailbox=sender.email, added=added)
    return added


class MailboxError(Exception):
    """İstifadəçiyə göstərilən xəta."""


def poll_all(session: Session, **kw) -> tuple[int, list[str]]:
    """Bütün aktiv poçt qutuları + defolt. (yeni cavab sayı, xəta mətnləri)."""
    senders = [sender_for(mb) for mb in active(session)]
    default = default_sender()
    if default is not None and all(s.email.lower() != default.email.lower() for s in senders):
        senders.append(default)
    added, errors = 0, []
    for s in senders:
        try:
            added += poll(session, s, **kw)
        except MailboxError as exc:
            errors.append(str(exc))
    return added, errors
