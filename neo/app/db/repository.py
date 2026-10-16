from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Contact, LeadEvent, LeadRecord, ScrapeRun
from app.models import Author, ContactInfo, RawLead

# Status keçidi → lead_events.action
_ACTIONS = {"rejected": "reject", "pending_review": "restore", "converted": "approve"}


def record_event(session: Session, record: LeadRecord, action: str, to_status: str | None,
                 user_id: int | None, note: str | None = None) -> None:
    """lead_events sətri (commit çağıranın işidir)."""
    session.add(LeadEvent(lead_key=record.dedupe_key, user_id=user_id, action=action,
                          from_status=record.status, to_status=to_status, note=note,
                          created_at=datetime.utcnow()))


class LeadRepository:
    """`leads` cədvəli üzərində CRUD + dedup yoxlaması."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def exists(self, dedupe_key: str) -> bool:
        stmt = select(LeadRecord.dedupe_key).where(LeadRecord.dedupe_key == dedupe_key)
        return self.session.execute(stmt).scalar_one_or_none() is not None

    def url_exists(self, url: str) -> bool:
        stmt = select(LeadRecord.dedupe_key).where(LeadRecord.source_url == url).limit(1)
        return self.session.execute(stmt).scalar_one_or_none() is not None

    def save(self, dedupe_key: str, lead: RawLead) -> LeadRecord:
        record = LeadRecord(
            dedupe_key=dedupe_key,
            source=lead.source,
            source_url=lead.source_url,
            title=lead.title,
            raw_text=lead.raw_text,
            entity_name=lead.entity_name,
            origin_url=lead.origin_url,
            author_name=lead.author.name if lead.author else None,
            author_title=lead.author.title if lead.author else None,
            author_url=lead.author.url if lead.author else None,
            contact_phone=lead.contact_info.phone if lead.contact_info else None,
            contact_email=lead.contact_info.email if lead.contact_info else None,
            contact_website=lead.contact_info.website if lead.contact_info else None,
            category=lead.category.value,
            location=lead.location,
            posted_at=lead.posted_at,
            deadline_at=lead.deadline_at,
            scraped_at=lead.scraped_at,
            ai_score=lead.ai_score,
            ai_reason=lead.ai_reason,
            tier=lead.tier,
            status=lead.status.value,
        )
        self.session.add(record)
        self.session.commit()
        return record

    def update_after_scoring(self, dedupe_key: str, lead: RawLead, ai_model: str | None = None) -> None:
        record = self.session.get(LeadRecord, dedupe_key)
        if record is None:
            return
        record.ai_score = lead.ai_score
        record.ai_reason = lead.ai_reason
        record.status = lead.status.value
        record.rejected_by = "ai" if record.status == "rejected" else None
        record.tier = lead.tier
        if lead.entity_name and not record.entity_name:
            record.entity_name = lead.entity_name  # v2 təşkilat adını çıxarır — dublikat yoxlaması üçün
        if ai_model:
            record.ai_model = ai_model[:32]
        self.session.commit()

    def get(self, dedupe_key: str) -> LeadRecord | None:
        return self.session.get(LeadRecord, dedupe_key)

    def set_status(self, dedupe_key: str, status: str, reviewer_id: int | None = None) -> None:
        """İnsan qərarı: status + kim/nə vaxt + lead_events tarixçəsi."""
        record = self.session.get(LeadRecord, dedupe_key)
        if record is None:
            return
        if status in _ACTIONS:
            record_event(self.session, record, _ACTIONS[status], status, reviewer_id)
        record.status = status
        record.rejected_by = "human" if status == "rejected" else None
        if reviewer_id is not None:
            record.reviewed_by_id, record.reviewed_at = reviewer_id, datetime.utcnow()
        self.session.commit()

    def mark_reviewed(self, dedupe_key: str, reviewer_id: int | None) -> None:
        record = self.session.get(LeadRecord, dedupe_key)
        if record is not None and reviewer_id is not None:
            record.reviewed_by_id, record.reviewed_at = reviewer_id, datetime.utcnow()
            self.session.commit()

    def list_by_status(self, status: str) -> list[LeadRecord]:
        stmt = (
            select(LeadRecord)
            .where(LeadRecord.status == status)
            .order_by(LeadRecord.ai_score.desc().nullslast())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_rejected(self, by: str) -> list[LeadRecord]:
        """Rədd edilənlər: `human` — son rədd edən insandır (ən təzə qərar əvvəldə, səhvən
        basılanı tez tapmaq üçün); `ai` — eşiyə ən yaxın skor əvvəldə (səhv rədd ehtimalı
        ən yüksək olanlar)."""
        who = LeadRecord.rejected_by == "human"
        stmt = select(LeadRecord).where(LeadRecord.status == "rejected", who if by == "human" else ~who | LeadRecord.rejected_by.is_(None))
        if by == "human":
            stmt = stmt.order_by(LeadRecord.reviewed_at.desc().nullslast())
        else:
            stmt = stmt.order_by(LeadRecord.ai_score.desc().nullslast(), LeadRecord.scraped_at.desc())
        return list(self.session.execute(stmt).scalars().all())

    def contact_counts(self, keys: list[str]) -> dict[str, int]:
        if not keys:
            return {}
        rows = self.session.execute(select(Contact.lead_key, func.count()).where(Contact.lead_key.in_(keys))
                                    .group_by(Contact.lead_key)).all()
        return dict(rows)

    def count_rejected(self) -> dict[str, int]:
        rows = self.session.execute(
            select(LeadRecord.rejected_by, func.count()).where(LeadRecord.status == "rejected")
            .group_by(LeadRecord.rejected_by)).all()
        counts = {"ai": 0, "human": 0}
        for by, n in rows:
            counts["human" if by == "human" else "ai"] += n
        return counts

    def latest_runs(self) -> dict[str, ScrapeRun]:
        """Hər mənbənin ən son `scrape_runs` sətri."""
        newest = (
            select(ScrapeRun.source_key, func.max(ScrapeRun.id).label("id"))
            .group_by(ScrapeRun.source_key)
            .subquery()
        )
        stmt = select(ScrapeRun).join(newest, ScrapeRun.id == newest.c.id)
        return {run.source_key: run for run in self.session.execute(stmt).scalars()}

    def list_unscored(self, source: str, scraped_before: datetime) -> list[LeadRecord]:
        """Yazılıb, amma AI skoru almayan lead-lər (status `new`) — run AI mərhələsində
        qırılıb (məs. Neon bağlantısı düşüb). `scraped_before`: indiki run-ın öz
        lead-lərinə və paralel run-a toxunmamaq üçün."""
        stmt = (
            select(LeadRecord)
            .where(LeadRecord.source == source, LeadRecord.status == "new",
                   LeadRecord.scraped_at < scraped_before)
            .order_by(LeadRecord.scraped_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def close_interrupted_runs(self, source_key: str, started_before: datetime) -> int:
        """Proses və ya baza bağlantısı ölüb, `running` qalmış köhnə run-lar → failed."""
        runs = self.session.execute(
            select(ScrapeRun).where(ScrapeRun.source_key == source_key, ScrapeRun.status == "running",
                                    ScrapeRun.started_at < started_before)
        ).scalars().all()
        for run in runs:
            run.status, run.finished_at = "failed", run.finished_at or datetime.utcnow()
            run.error = run.error or "Yarımçıq qaldı: proses və ya baza bağlantısı run bitməmiş kəsildi"
        if runs:
            self.session.commit()
        return len(runs)

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
            origin_url=record.origin_url,
            author=Author(name=record.author_name, title=record.author_title, url=record.author_url)
            if record.author_name or record.author_url else None,
            category=record.category,
            location=record.location,
            posted_at=record.posted_at,
            deadline_at=record.deadline_at,
            scraped_at=record.scraped_at,
            ai_score=record.ai_score,
            ai_reason=record.ai_reason,
            status=record.status,
        )
