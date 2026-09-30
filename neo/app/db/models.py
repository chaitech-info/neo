"""Baza modelləri. Sxem layihəsi: CRM baza sxemi (Claude artifact, 28.09.2026).

Portativlik (qərar 4 — hazırda SQLite, sonra PostgreSQL): yalnız hər iki bazada
işləyən tiplər. Enum-lar VARCHAR + CHECK (`native_enum=False`), tarixlər naive
UTC `DateTime` (mövcud `leads` sütunları kimi), JSON adi `JSON`. Sxem dəyişikliyi
yalnız Alembic miqrasiyası ilə (neo/migrations) — `create_all()` testlər üçündür.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Adlandırılmış constraint-lər: Alembic SQLite batch rejimində və Postgres-də
# eyni adları görsün (adsız FK/CHECK-i sonradan dəyişmək/silmək olmur).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enum(name: str, *values: str) -> Enum:
    """Portativ enum: VARCHAR + CHECK, Postgres-də də native ENUM tipi yaratmır."""
    return Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


# --- Dəyər siyahıları — miqrasiya 0002 bunların nüsxəsini saxlayır; ikisinin
# uyğunluğunu tests/test_migrations.py yoxlayır. -------------------------------

ROLE_CODES = ("admin", "manager", "sales", "viewer")
SOURCE_KINDS = ("tender_portal", "news", "jobs", "corporate", "intl_api", "social")
RUN_TRIGGERS = ("schedule", "manual", "scrape_all")
RUN_STATUSES = ("running", "ok", "failed", "blocked_robots")
KEYWORD_LANGUAGES = ("az", "en", "ru")
KEYWORD_MATCHES = ("prefix", "word", "phrase")
KEYWORD_SCOPES = ("all", "news", "tender", "jobs")
ORG_KINDS = ("state", "state_company", "private", "intl_org")
TENDER_STATUSES = ("open", "closed", "awarded", "cancelled")
OPPORTUNITY_STAGES = ("new", "contacted", "qualified", "proposal", "negotiation", "won", "lost")
LOST_REASONS = ("price", "competitor", "no_response", "not_eligible", "deadline_missed", "other")
TASK_STATUSES = ("open", "in_progress", "done", "cancelled")
TASK_PRIORITIES = ("low", "normal", "high")


# --- Toplama qatı -----------------------------------------------------------


class Source(Base):
    """Mənbə. Açar `leads.source`-dakı mövcud dəyərlərlə eynidir."""

    __tablename__ = "sources"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(_enum("source_kind", *SOURCE_KINDS))
    base_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schedule_cron: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    respect_robots: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(ForeignKey("sources.key"), index=True)
    trigger: Mapped[str] = mapped_column(_enum("run_trigger", *RUN_TRIGGERS))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(_enum("run_status", *RUN_STATUSES), default="running")
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    leads_new: Mapped[int] = mapped_column(Integer, default=0)
    leads_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    leads_pending: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class InterestArea(Base):
    """Şirkətin maraq sahəsi; `ai_description` AI promptuna gedir."""

    __tablename__ = "interest_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(Text)
    ai_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(SmallInteger, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Keyword(Base):
    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("interest_area_id", "term", "applies_to"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interest_area_id: Mapped[int] = mapped_column(ForeignKey("interest_areas.id"), index=True)
    term: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(_enum("keyword_language", *KEYWORD_LANGUAGES), default="az")
    match: Mapped[str] = mapped_column(_enum("keyword_match", *KEYWORD_MATCHES), default="prefix")
    applies_to: Mapped[str] = mapped_column(_enum("keyword_scope", *KEYWORD_SCOPES), default="all")
    is_negative: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- Lead qatı ---------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    name_normalized: Mapped[str] = mapped_column(Text, index=True)
    voen: Mapped[Optional[str]] = mapped_column(String(10), unique=True, nullable=True)
    kind: Mapped[Optional[str]] = mapped_column(_enum("org_kind", *ORG_KINDS), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Tender(Base):
    """Bir satınalma — onu tapan bütün mənbələrin lead-ləri buna bağlanır."""

    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    reference_no: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    budget_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(_enum("tender_status", *TENDER_STATUSES), default="open")
    awarded_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LeadRecord(Base):
    """Scrape olunmuş lead. Mövcud sütunlar dəyişmir; CRM sütunları 0002-də əlavə olunub."""

    __tablename__ = "leads"

    dedupe_key: Mapped[str] = mapped_column(String(64), primary_key=True)

    source: Mapped[str] = mapped_column(ForeignKey("sources.key"))
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
    # Sərbəst sətir: new/pending_review/rejected/duplicate/converted (+ köhnə sent_to_crm).
    # `converted` — "Təsdiqlə" lead-i fürsətə çevirəndə (app/crm.py).
    status: Mapped[str] = mapped_column(String(16), default="new")

    crm_opportunity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # CRM sütunları (0002) — hamısı nullable, mövcud kod onları yazmır.
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    organization_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    tender_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tenders.id"), index=True, nullable=True)
    duplicate_of: Mapped[Optional[str]] = mapped_column(ForeignKey("leads.dedupe_key"), nullable=True)
    ai_model: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Dublikatın ilk lead-i (admin paneldə "ilk dəfə: <mənbə>"). joined — şablon
    # sessiya bağlandıqdan sonra render olunur, lazy yükləmə orada işləməzdi.
    original: Mapped[Optional["LeadRecord"]] = relationship(
        remote_side=[dedupe_key], foreign_keys=[duplicate_of], lazy="joined", join_depth=1
    )


# --- CRM qatı ----------------------------------------------------------------


class Role(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(_enum("role_code", *ROLE_CODES), primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kiçik hərflə saxlanılır (citext əvəzinə — SQLite-da da işləsin).
    email: Mapped[str] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role_code: Mapped[str] = mapped_column(String(32), ForeignKey("roles.code"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    lead_key: Mapped[Optional[str]] = mapped_column(ForeignKey("leads.dedupe_key"), unique=True, nullable=True)
    organization_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=True)
    tender_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tenders.id"), nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    stage: Mapped[str] = mapped_column(_enum("opportunity_stage", *OPPORTUNITY_STAGES), default="new")
    expected_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="AZN")
    expected_close_on: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    lost_reason_code: Mapped[Optional[str]] = mapped_column(_enum("lost_reason", *LOST_REASONS), nullable=True)
    lost_reason_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # boş = sistem
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(_enum("task_status", *TASK_STATUSES), default="open")
    priority: Mapped[str] = mapped_column(_enum("task_priority", *TASK_PRIORITIES), default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
