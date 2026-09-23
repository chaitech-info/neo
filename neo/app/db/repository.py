from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeadRecord
from app.models import ContactInfo, RawLead


class LeadRepository:
    """`leads` cədvəli üzərində CRUD + dedup yoxlaması."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def exists(self, dedupe_key: str) -> bool:
        stmt = select(LeadRecord.dedupe_key).where(LeadRecord.dedupe_key == dedupe_key)
        return self.session.execute(stmt).scalar_one_or_none() is not None

    def save(self, dedupe_key: str, lead: RawLead) -> LeadRecord:
        record = LeadRecord(
            dedupe_key=dedupe_key,
            source=lead.source.value,
            source_url=lead.source_url,
            title=lead.title,
            raw_text=lead.raw_text,
            entity_name=lead.entity_name,
            contact_phone=lead.contact_info.phone if lead.contact_info else None,
            contact_email=lead.contact_info.email if lead.contact_info else None,
            contact_website=lead.contact_info.website if lead.contact_info else None,
            category=lead.category.value,
            location=lead.location,
            posted_at=lead.posted_at,
            scraped_at=lead.scraped_at,
            ai_score=lead.ai_score,
            ai_reason=lead.ai_reason,
            status=lead.status.value,
        )
        self.session.add(record)
        self.session.commit()
        return record

    def update_after_scoring(self, dedupe_key: str, lead: RawLead) -> None:
        record = self.session.get(LeadRecord, dedupe_key)
        if record is None:
            return
        record.ai_score = lead.ai_score
        record.ai_reason = lead.ai_reason
        record.status = lead.status.value
        self.session.commit()

    def mark_sent_to_crm(self, dedupe_key: str, crm_opportunity_id: str) -> None:
        record = self.session.get(LeadRecord, dedupe_key)
        if record is None:
            return
        record.status = "sent_to_crm"
        record.crm_opportunity_id = crm_opportunity_id
        self.session.commit()

    def get(self, dedupe_key: str) -> LeadRecord | None:
        return self.session.get(LeadRecord, dedupe_key)

    def set_status(self, dedupe_key: str, status: str) -> None:
        record = self.session.get(LeadRecord, dedupe_key)
        if record is None:
            return
        record.status = status
        self.session.commit()

    def list_by_status(self, status: str) -> list[LeadRecord]:
        stmt = (
            select(LeadRecord)
            .where(LeadRecord.status == status)
            .order_by(LeadRecord.ai_score.desc().nullslast())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_ai_rejected(self, threshold: float) -> list[LeadRecord]:
        """AI-nin eşikdən aşağı skorla rədd etdikləri (insanın rədd etdikləri yox —
        insan yalnız pending_review-u, yəni skoru eşikdən yuxarı və ya boş olanı rədd edir).
        Eşiyə ən yaxın olanlar əvvəldə — səhv rədd ehtimalı ən yüksək olanlar."""
        stmt = (
            select(LeadRecord)
            .where(
                LeadRecord.status == "rejected",
                LeadRecord.ai_score.is_not(None),
                LeadRecord.ai_score < threshold,
            )
            .order_by(LeadRecord.ai_score.desc(), LeadRecord.scraped_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_all(self, limit: int = 200) -> list[LeadRecord]:
        stmt = select(LeadRecord).order_by(LeadRecord.scraped_at.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    @staticmethod
    def to_raw_lead(record: LeadRecord) -> RawLead:
        """DB sətrini geri `RawLead`-ə çevirir (CRM-ə göndərmək üçün, §9)."""
        contact = None
        if record.contact_phone or record.contact_email or record.contact_website:
            contact = ContactInfo(
                phone=record.contact_phone,
                email=record.contact_email,
                website=record.contact_website,
            )
        return RawLead(
            source=record.source,
            source_url=record.source_url,
            title=record.title,
            raw_text=record.raw_text,
            entity_name=record.entity_name,
            contact_info=contact,
            category=record.category,
            location=record.location,
            posted_at=record.posted_at,
            scraped_at=record.scraped_at,
            ai_score=record.ai_score,
            ai_reason=record.ai_reason,
            status=record.status,
        )
