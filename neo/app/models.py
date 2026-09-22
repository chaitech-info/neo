from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class LeadSource(str, Enum):
    linkedin = "linkedin"
    marja = "marja"
    news_1 = "1news"
    oxu = "oxu"
    azerweb = "azerweb"
    fed = "fed"
    etender = "etender"
    emlak = "emlak"


class LeadCategory(str, Enum):
    event = "event"
    corporate = "corporate"
    tender = "tender"
    real_estate = "real_estate"
    other = "other"


class LeadStatus(str, Enum):
    new = "new"
    pending_review = "pending_review"
    sent_to_crm = "sent_to_crm"
    rejected = "rejected"
    duplicate = "duplicate"


class ContactInfo(BaseModel):
    phone: str | None = None
    email: str | None = None
    website: str | None = None


class RawLead(BaseModel):
    """Bir scraper-in qaytardığı vahid format. Bax: sənəd §4 Data Model."""

    source: LeadSource
    source_url: str
    title: str
    raw_text: str

    entity_name: str | None = None
    contact_info: ContactInfo | None = None
    category: LeadCategory = LeadCategory.other
    location: str | None = None

    posted_at: datetime | None = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    ai_score: float | None = None
    ai_reason: str | None = None
    status: LeadStatus = LeadStatus.new

    def dedupe_key(self) -> str:
        """source + source_url üzrə hash. Sənəd §4-dəki deduplication qaydası."""
        raw = f"{self.source.value}:{self.source_url}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
