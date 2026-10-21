"""Saytdaxili bildirişlər və şəxsi xatırlatmalar.

Xatırlatmaların cron-u yoxdur (panel Vercel-də serverless-dir): vaxtı çatan xatırlatma istifadəçi
səhifə açanda və ya brauzer `/api/notifications`-ı yoxlayanda (hər dəqiqə) bildirişə çevrilir.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import Notification, Reminder

# Xatırlatma vaxtı: "X dəqiqə / saat / gün əvvəl".
UNIT_MINUTES = {"min": 1, "hour": 60, "day": 24 * 60}
UNIT_NAMES = {"min": "dəqiqə", "hour": "saat", "day": "gün"}
MAX_BEFORE_MIN = 365 * 24 * 60
BAKU_OFFSET = timedelta(hours=4)  # bazada naive UTC, paneldə Bakı vaxtı


class ReminderError(ValueError):
    """İstifadəçiyə göstərilən xəta."""


def notify(session: Session, user_id: int, text: str, link: str | None = None, *, commit: bool = True) -> Notification:
    row = Notification(user_id=user_id, text=text[:500], link=link, created_at=datetime.utcnow())
    session.add(row)
    if commit:
        session.commit()
    return row


def before_minutes(amount: str | int, unit: str) -> int:
    if unit not in UNIT_MINUTES:
        raise ReminderError("Xatırlatma vahidi yanlışdır")
    try:
        n = int(amount)
    except (TypeError, ValueError):
        raise ReminderError("Xatırlatma vaxtı rəqəm olmalıdır")
    minutes = n * UNIT_MINUTES[unit]
    if n < 0 or minutes > MAX_BEFORE_MIN:
        raise ReminderError("Xatırlatma vaxtı 0 ilə 365 gün arasında olmalıdır")
    return minutes


def describe_before(minutes: int) -> str:
    if minutes == 0:
        return "vaxtında"
    for unit in ("day", "hour", "min"):
        size = UNIT_MINUTES[unit]
        if minutes % size == 0:
            return f"{minutes // size} {UNIT_NAMES[unit]} əvvəl"
    return f"{minutes} dəqiqə əvvəl"


def add_reminder(session: Session, user_id: int, title: str, due_at: datetime, before_min: int,
                 note: str | None = None, contract_id: int | None = None) -> Reminder:
    title = (title or "").strip()
    if not title:
        raise ReminderError("Nəyi xatırladaq? Başlıq boş ola bilməz")
    now = datetime.utcnow()
    row = Reminder(user_id=user_id, title=title[:300], note=(note or "").strip()[:2000] or None, due_at=due_at,
                   remind_before_min=before_min, remind_at=due_at - timedelta(minutes=before_min),
                   contract_id=contract_id, created_at=now)
    session.add(row)
    session.commit()
    return row


def fire_due(session: Session, user_id: int, now: datetime | None = None) -> int:
    """Vaxtı çatmış xatırlatmaları bildirişə çevirir (bir dəfə). Yaranan bildiriş sayı."""
    now = now or datetime.utcnow()
    due = list(session.scalars(select(Reminder).where(
        Reminder.user_id == user_id, Reminder.remind_at <= now,
        Reminder.notified_at.is_(None), Reminder.done_at.is_(None))))
    for r in due:
        # Başqa sorğu eyni anda bildirişi yaradıbsa, ikinci dəfə yaratmırıq.
        claimed = session.execute(update(Reminder).where(Reminder.id == r.id, Reminder.notified_at.is_(None))
                                  .values(notified_at=now)).rowcount
        if claimed:
            link = f"/tools/contracts#c{r.contract_id}" if r.contract_id else "/me/reminders"
            when = (r.due_at + BAKU_OFFSET).strftime("%d.%m.%Y %H:%M")
            notify(session, user_id, f"Xatırlatma: {r.title} — {when}", link, commit=False)
    if due:
        session.commit()
    return len(due)


def unread_count(session: Session, user_id: int) -> int:
    return session.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None))) or 0


def unread(session: Session, user_id: int, after_id: int = 0, limit: int = 20) -> list[Notification]:
    return list(session.scalars(select(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None), Notification.id > after_id)
        .order_by(Notification.id.desc()).limit(limit)))


def mark_read(session: Session, user_id: int, notification_id: int | None = None) -> None:
    q = update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if notification_id is not None:
        q = q.where(Notification.id == notification_id)
    session.execute(q.values(read_at=datetime.utcnow()))
    session.commit()
