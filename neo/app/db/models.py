from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LeadRecord(Base):
    """PostgreSQL-də saxlanan lead. Sənəd §4 RawLead + §8 Tech stack."""

    __tablename__ = "leads"

    dedupe_key: Mapped[str] = mapped_column(String(64), primary_key=True)

    source: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)

    entity_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    contact_website: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    category: Mapped[str] = mapped_column(String(32))
    location: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime)

    ai_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ai_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="new")

    crm_opportunity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
