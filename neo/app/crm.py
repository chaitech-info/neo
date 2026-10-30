"""Öz CRM-imiz: fürsətlər (opportunities) və tapşırıqlar (tasks).

"Təsdiqlə" düyməsi lead-i fürsətə çevirir və ilk tapşırığı yaradır (əvvəl
xarici CRM-ə göndərirdi, endpoint olmadığı üçün həmişə xəta verirdi — bax
docs/knowledge-base/04-decisions.md → "CRM baza sxemi", qərar 1).

Mərhələlər (qərar 3): new → contacted → qualified → proposal → negotiation →
won | lost; tender üçün new → qualified → proposal → won | lost. `lost`
səbəbsiz bağlana bilməz.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import LOST_REASONS, Contact, LeadRecord, Opportunity, Outreach, Task
from app.db.repository import record_event
from app.tenders import extract_voen, organization_for

STAGE_NAMES = {
    "new": "Yeni",
    "contacted": "Əlaqə saxlanılıb",
    "qualified": "Uyğundur",
    "proposal": "Təklif göndərilib",
    "negotiation": "Danışıqlar",
    "won": "Qazanıldı",
    "lost": "Uduzuldu",
}
PIPELINE = ["new", "contacted", "qualified", "proposal", "negotiation", "won", "lost"]
TENDER_PIPELINE = ["new", "qualified", "proposal", "won", "lost"]
CLOSED_STAGES = {"won", "lost"}

LOST_REASON_NAMES = {
    "price": "Qiymət",
    "competitor": "Rəqib qazandı",
    "no_response": "Cavab yoxdur",
    "not_eligible": "Uyğun deyilik",
    "deadline_missed": "Son tarix keçdi",
    "other": "Digər",
}
TASK_STATUS_NAMES = {"open": "Açıq", "in_progress": "İcrada", "done": "Bitib", "cancelled": "Ləğv"}
PRIORITY_NAMES = {"low": "Aşağı", "normal": "Normal", "high": "Yüksək"}

# Tapşırıqlarda saat yoxdur — yalnız Bakı tarixi (Task.due_on, 0013).
# İlk tapşırığın son tarixi: tender üçün son tarixdən 2 gün əvvəl (sənəd
# hazırlamağa vaxt), amma bu gündən tez yox; digər lead-lər üçün sabah.
BAKU_OFFSET = timedelta(hours=4)
TENDER_PREP_BUFFER = timedelta(days=2)
DEFAULT_FIRST_CONTACT = timedelta(days=1)
URGENT_WITHIN = timedelta(days=3)


class CrmError(ValueError):
    """İstifadəçiyə göstərilən biznes qaydası xətası (Azərbaycan dilində)."""


@dataclass
class Conversion:
    opportunity: Opportunity
    task: Task


def is_tender(opp: Opportunity) -> bool:
    return opp.tender_id is not None


def pipeline_for(opp: Opportunity) -> list[str]:
    return TENDER_PIPELINE if is_tender(opp) else PIPELINE


def _now() -> datetime:
    return datetime.utcnow()


def baku_date(value: datetime) -> date:
    """Naive UTC (bazadakı format) → Bakı tarixi."""
    return (value + BAKU_OFFSET).date()


def today() -> date:
    return baku_date(_now())


def _first_task(lead: LeadRecord, org_name: str | None, now: datetime) -> tuple[str, str, date, str]:
    """(başlıq, təsvir, son tarix, prioritet) — lead növünə görə."""
    who = org_name or lead.entity_name or "müştəri"
    day = baku_date(now)
    if lead.category == "tender" or lead.tender_id:
        due = day + DEFAULT_FIRST_CONTACT
        if lead.deadline_at:
            deadline = baku_date(lead.deadline_at)
            due = min(max(deadline - TENDER_PREP_BUFFER, day), deadline)
        urgent = lead.deadline_at is not None and lead.deadline_at - now <= URGENT_WITHIN
        return (
            f"Tender sənədlərini yoxla və təklif hazırla — {who}",
            "Tender şərtlərini oxu, iştirak tələblərini və son tarixi yoxla, qiymət təklifini hazırla.",
            due,
            "high" if urgent else "normal",
        )
    contact = ", ".join(x for x in (lead.contact_phone, lead.contact_email, lead.contact_website) if x)
    return (
        f"İlk əlaqə: {who}",
        "Zəng et və ya yaz, işçi qidalanması / tədbir ehtiyacını dəqiqləşdir, görüş təyin et."
        + (f"\nƏlaqə: {contact}" if contact else ""),
        day + DEFAULT_FIRST_CONTACT,
        "high" if (lead.ai_score or 0) >= 0.8 else "normal",
    )


def convert_lead(session: Session, dedupe_key: str, user_id: int | None) -> Conversion:
    """pending_review lead → fürsət + ilk tapşırıq; lead `converted` olur."""
    lead = session.get(LeadRecord, dedupe_key)
    if lead is None or lead.status != "pending_review":
        raise CrmError("Lead tapılmadı və ya artıq emal olunub (yalnız validasiyadakı lead təsdiqlənə bilər)")
    existing = session.execute(select(Opportunity).where(Opportunity.lead_key == dedupe_key)).scalar_one_or_none()
    if existing is not None:
        raise CrmError(f"Bu lead üçün artıq fürsət var (#{existing.id})")

    org = None
    if lead.organization_id is None:
        org = organization_for(session, lead.entity_name, extract_voen(lead.raw_text))
        if org is not None:
            lead.organization_id = org.id
            _fill_contacts(org, lead)
    now = _now()

    opp = Opportunity(
        title=lead.title[:500],
        lead_key=lead.dedupe_key,
        organization_id=lead.organization_id,
        tender_id=lead.tender_id,
        owner_id=user_id,
        stage="new",
        currency="AZN",
        expected_close_on=lead.deadline_at.date() if lead.deadline_at else None,
        created_at=now,
        updated_at=now,
    )
    session.add(opp)
    session.flush()

    title, description, due, priority = _first_task(lead, org.name if org else lead.entity_name, now)
    task = Task(
        opportunity_id=opp.id,
        title=title,
        description=description,
        assignee_id=user_id,
        created_by_id=None,  # sistem yaradıb
        due_on=due,
        status="open",
        priority=priority,
        created_at=now,
    )
    session.add(task)

    if lead.organization_id:
        # Validasiyada toplanmış kontaktlar təşkilata bağlanır — eyni şirkətin növbəti fürsətində də görünsün.
        session.execute(update(Contact).where(Contact.lead_key == lead.dedupe_key, Contact.organization_id.is_(None))
                        .values(organization_id=lead.organization_id))
    record_event(session, lead, "approve", "converted", user_id, note=f"Fürsət #{opp.id}")
    lead.status = "converted"
    lead.crm_opportunity_id = str(opp.id)
    lead.reviewed_by_id = user_id
    lead.reviewed_at = now
    session.commit()
    return Conversion(opp, task)


def _fill_contacts(org, lead: LeadRecord) -> None:
    org.phone = org.phone or lead.contact_phone
    org.email = org.email or lead.contact_email
    org.website = org.website or lead.contact_website


def change_stage(session: Session, opp: Opportunity, stage: str,
                 lost_reason: str | None = None, lost_note: str | None = None) -> None:
    if stage not in pipeline_for(opp):
        raise CrmError("Bu fürsət növü üçün yanlış mərhələ")
    if opp.stage in CLOSED_STAGES and stage not in CLOSED_STAGES:
        opp.closed_at = None  # yenidən açılır
    if stage == "lost":
        if lost_reason not in LOST_REASONS:
            raise CrmError("Uduzma səbəbini seç")
        opp.lost_reason_code = lost_reason
        opp.lost_reason_note = (lost_note or "").strip() or None
    else:
        opp.lost_reason_code = opp.lost_reason_note = None
    if stage in CLOSED_STAGES and opp.stage not in CLOSED_STAGES:
        opp.closed_at = _now()
        # Bağlanan fürsətin açıq tapşırıqları artıq aktual deyil.
        for task in opp_tasks(session, opp.id):
            if task.status in ("open", "in_progress"):
                task.status = "cancelled"
    opp.stage = stage
    opp.updated_at = _now()
    session.commit()


EMAIL_FOLLOW_UP = timedelta(days=3)


def email_sent(session: Session, opp: Opportunity, *, to: str, subject: str, body: str, language: str | None,
               contact_id: int | None, user_id: int | None, message_id: str | None,
               row: Outreach | None = None) -> Outreach:
    """Göndərilmiş məktubun izi: outreach + lead tarixçəsi, 3 gün sonra "cavabı yoxla" tapşırığı,
    fürsət hələ "Yeni"-dirsə → "Əlaqə saxlanılıb" (tender fürsətində bu mərhələ yoxdur), cavab statusu "Gözləmədə".
    `row` — göndərməzdən əvvəl rezerv edilmiş sətir (send_key ilə), o "sent" olur."""
    now = _now()
    if row is None:
        row = Outreach(kind="email", lead_key=opp.lead_key, opportunity_id=opp.id, contact_id=contact_id,
                       to_address=to, subject=subject, body=body, language=language, user_id=user_id, created_at=now)
        session.add(row)
    row.status, row.error, row.external_id, row.sent_at = "sent", None, message_id, now
    # Cavab gələnə qədər "Gözləmədə"; artıq potensial müştəridirsə, yeni məktub bunu dəyişmir.
    if opp.reply_status != "potential":
        opp.reply_status = "waiting"
    lead = session.get(LeadRecord, opp.lead_key) if opp.lead_key else None
    if lead is not None:
        record_event(session, lead, "email", lead.status, user_id, note=f"{to}: {subject}"[:500])
    session.add(Task(opportunity_id=opp.id, title=f"Məktuba cavabı yoxla: {to}", description=subject,
                     due_on=baku_date(now) + EMAIL_FOLLOW_UP, assignee_id=opp.owner_id or user_id, created_by_id=None,
                     priority="normal", status="open", created_at=now))
    if opp.stage == "new" and "contacted" in pipeline_for(opp):
        opp.stage = "contacted"
    opp.updated_at = now
    session.commit()
    return row


def parse_due(value: str | None) -> date | None:
    """<input type="date"> → tarix; saat qəbul edilmir (tapşırıqlarda yalnız tarix var)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if len(value) != 10:
            raise ValueError(value)
        return date.fromisoformat(value)
    except ValueError:
        raise CrmError("Tarix formatı yanlışdır (gün.ay.il, saatsız)")


def add_task(session: Session, opp: Opportunity, title: str, due_on: date | None,
             assignee_id: int | None, created_by_id: int | None, priority: str = "normal",
             description: str | None = None) -> Task:
    title = (title or "").strip()
    if not title:
        raise CrmError("Tapşırığın adı boş ola bilməz")
    if priority not in PRIORITY_NAMES:
        raise CrmError("Yanlış prioritet")
    if isinstance(due_on, datetime):  # saat atılır
        due_on = due_on.date()
    task = Task(opportunity_id=opp.id, title=title[:500], description=(description or "").strip() or None,
                due_on=due_on, assignee_id=assignee_id, created_by_id=created_by_id,
                priority=priority, status="open", created_at=_now())
    session.add(task)
    opp.updated_at = _now()
    session.commit()
    return task


def set_task_status(session: Session, task: Task, status: str) -> None:
    if status not in TASK_STATUS_NAMES:
        raise CrmError("Yanlış tapşırıq statusu")
    task.status = status
    task.completed_at = _now() if status == "done" else None
    session.commit()


def opp_tasks(session: Session, opp_id: int) -> list[Task]:
    return list(session.scalars(select(Task).where(Task.opportunity_id == opp_id).order_by(Task.created_at, Task.id)))
