"""PR 2-dən əvvəl yazılmış lead-ləri yeni sahələrlə doldurur (idempotent).

- `deadline_at`: mətndəki "Son tarix: ..." sətrindən (app/deadlines.py);
- tender lead-ləri `tenders`-ə bağlanır, ən köhnədən başlayaraq — hər tenderin
  "ilk" lead-i ən əvvəl tapılanı olur, sonrakı mənbələrinki `duplicate`.
Artıq doldurulmuş sətirlərə toxunmur; server hər başlayanda çağırılır.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeadRecord
from app.deadlines import extract_deadline
from app.tenders import link_tender

log = structlog.get_logger()


def backfill(session: Session) -> dict[str, int]:
    deadlines = linked = duplicates = 0

    for record in session.execute(select(LeadRecord).where(LeadRecord.deadline_at.is_(None))).scalars():
        deadline = extract_deadline(record.raw_text)
        if deadline:
            record.deadline_at = deadline
            deadlines += 1
    session.flush()

    pending = select(LeadRecord).where(LeadRecord.category == "tender", LeadRecord.tender_id.is_(None))
    for record in session.execute(pending.order_by(LeadRecord.scraped_at)).scalars().all():
        if link_tender(session, record):
            duplicates += 1
        linked += 1
        session.flush()

    session.commit()
    if deadlines or linked:
        log.info("backfill_done", deadlines=deadlines, tenders_linked=linked, duplicates=duplicates)
    return {"deadlines": deadlines, "tenders_linked": linked, "duplicates": duplicates}
