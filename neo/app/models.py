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
    worldbank = "worldbank"
    bp_edp = "bp_edp"
    corporate = "corporate"  # şirkət/agentliklərin öz tender səhifələri (app/scrapers/corporate.py)
    report = "report"  # report.az/senaye — sənaye xəbərləri (app/scrapers/news.py)
    boss = "boss"  # boss.az vakansiyalarından işçilərinə nahar verən şirkətlər (app/scrapers/boss.py)


class LeadCategory(str, Enum):
    event = "event"
    corporate = "corporate"
    tender = "tender"
    real_estate = "real_estate"
    event_listing = "event_listing"  # sərgi/tədbir təqvimindən yaxınlaşan tədbir (app/scrapers/events.py)
    other = "other"


class LeadStatus(str, Enum):
    new = "new"
    pending_review = "pending_review"
    sent_to_crm = "sent_to_crm"  # köhnə: xarici CRM-ə göndərilmiş (yeni lead-lər bunu almır)
    converted = "converted"  # təsdiqlənib → fürsət yaradılıb (app/crm.py)
    rejected = "rejected"
    duplicate = "duplicate"


class ContactInfo(BaseModel):
    phone: str | None = None
    email: str | None = None
    website: str | None = None


class Author(BaseModel):
    """Postun müəllifi (LinkedIn) — kontakt şəxs kimi də saxlanılır."""

    name: str | None = None
    title: str | None = None  # LinkedIn headline
    url: str | None = None  # profil linki


class RawLead(BaseModel):
    """Bir scraper-in qaytardığı vahid format. Bax: sənəd §4 Data Model."""

    source: str  # mənbə açarı (sources.key; reyestr app/sources.py, bazada FK yoxlayır)
    source_url: str
    title: str
    raw_text: str

    entity_name: str | None = None
    contact_info: ContactInfo | None = None
    # Əsl post/elan linki, source_url-dan fərqlidirsə (məs. işəgötürən lead-i: source_url = şirkət saytı).
    origin_url: str | None = None
    author: Author | None = None
    category: LeadCategory = LeadCategory.other
    location: str | None = None

    posted_at: datetime | None = None
    deadline_at: datetime | None = None  # təklif son tarixi, naive UTC (app/deadlines.py)
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    ai_score: float | None = None
    ai_reason: str | None = None
    tier: str | None = None  # A | B | C (v2), rədd edilənlərdə None
    status: LeadStatus = LeadStatus.new
    # AI-nin mətndən çıxardığı kontakt şəxslər [{name, role, email, phone}] — bazada `contacts`
    # cədvəlinə yazılır (app/contacts.py), leads sütunu deyil.
    people: list[dict] = Field(default_factory=list)

    def dedupe_key(self) -> str:
        """source + source_url üzrə hash. Sənəd §4-dəki deduplication qaydası."""
        raw = f"{self.source}:{self.source_url}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
