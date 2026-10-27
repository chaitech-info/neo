"""Baza modelləri. Sxem layihəsi: CRM baza sxemi (Claude artifact, 28.09.2026).

Portativlik (qərar 4 — hazırda SQLite, sonra PostgreSQL): yalnız hər iki bazada
işləyən tiplər. Enum-lar VARCHAR + CHECK (`native_enum=False`), tarixlər naive
UTC `DateTime` (mövcud `leads` sütunları kimi), JSON adi `JSON`. Sxem dəyişikliyi
yalnız Alembic miqrasiyası ilə (neo/migrations) — `create_all()` testlər üçündür.
"""
from datetime import date, datetime
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
    LargeBinary,
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

ROLE_CODES = ("admin", "approver", "manager", "sales", "viewer")  # approver — 0012
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
# 0008 — miqrasiya öz nüsxəsini saxlayır.
LEAD_ACTIONS = ("approve", "reject", "restore", "comment", "email")
CONTACT_SOURCES = ("text", "ai", "author", "website", "enrichment", "manual")
OUTREACH_KINDS = ("linkedin_comment", "email")
OUTREACH_STATUSES = ("sent", "failed", "skipped")
# 0012 — sərbəst sətir kimi saxlanılır (CHECK yoxdur), dəyərlər kodda yoxlanılır.
REPLY_STATUSES = ("waiting", "potential", "declined")
APPROVAL_STATUSES = ("pending", "approved", "rejected")
AGREEMENT_STATUSES = ("draft", "active", "expired", "terminated")  # 0013: client_contracts.status


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
    # Filtrdən əvvəl saytdan gələn elan (0003). items_seen = filtrdən keçən.
    items_fetched: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    leads_new: Mapped[int] = mapped_column(Integer, default=0)
    leads_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    leads_pending: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class TriageCache(Base):
    """Başlıq süzgəcinin qərarı (0006) — app/ai_triage.py; açar sha1(növ|prompt versiyası|başlıq)."""

    __tablename__ = "triage_cache"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    fit: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


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


class UserKeyword(Base):
    """İstifadəçinin öz açar sözü (0011): Validasiyada "Mənim sözlərim" filtri; scrape-də
    də müsbət söz kimi işləyir (qlobal mənfi sözlər yenə bağlayır)."""

    __tablename__ = "user_keywords"
    __table_args__ = (UniqueConstraint("user_id", "term"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    term: Mapped[str] = mapped_column(Text)
    match: Mapped[str] = mapped_column(String(8), default="prefix")  # KEYWORD_MATCHES
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    # 0013: Şirkət bazası — sayt domeni (dublikat yoxlaması) və son uğurlu analiz (app/companies.py)
    domain: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    analysis_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    analysed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, nullable=True)
    analysed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    listed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, nullable=True)  # şirkət bazasında
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # əlavə qeydlər (sərbəst mətn)
    notes_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes_updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


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
    # A/B/C — kommersiya kateqoriyası (0007, v2 qiymətləndirmə); None = v1 və ya rədd.
    tier: Mapped[Optional[str]] = mapped_column(String(1), nullable=True, index=True)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 0008: əsl elan/post linki (LinkedIn işəgötürən lead-ində source_url şirkət saytıdır),
    # LinkedIn müəllifi və kim rədd edib (ai/human — "Rədd edilənlər" səhifəsinin tabları).
    origin_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejected_by: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

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
    # 0012: Validasiyada A/B/C üçün öz süzgəci — JSON {"A": ["tender", "news"], ...}; boş = hamısı.
    lead_filters: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class LoginFailure(Base):
    """Səhv giriş cəhdi — bloklama bazada saxlanılır ki, bütün (serverless) instansiyalar eyni sayğacı görsün."""

    __tablename__ = "login_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


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
    # 0011: rəhbərlərin LinkedIn profilləri axtarılıb (Apify pulludur — hər fürsət üçün bir dəfə).
    leaders_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 0012: məktuba cavab vəziyyəti (REPLY_STATUSES) — boş = məktub göndərilməyib.
    reply_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # boş = sistem
    due_on: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)  # 0013: yalnız tarix (Bakı)
    status: Mapped[str] = mapped_column(_enum("task_status", *TASK_STATUSES), default="open")
    priority: Mapped[str] = mapped_column(_enum("task_priority", *TASK_PRIORITIES), default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class OfferRecord(Base):
    """Xarici AI agentin yaratdığı qiymət təklifinin metadata-sı (0009). PDF saxlanmır —
    agent `/v1/offers/{id}/pdf` ilə yenidən yaradır."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offer_id: Mapped[str] = mapped_column(String(64), unique=True)
    brand: Mapped[str] = mapped_column(String(16))
    customer_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)  # ƏDV daxil; miqdar bilinmirsə boş
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    lead_id: Mapped[Optional[str]] = mapped_column(ForeignKey("leads.dedupe_key"), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# --- Tarixçə, kontaktlar, ünsiyyət (0008) -------------------------------------


class LeadEvent(Base):
    """Lead üzərində insan/sistem hərəkəti — kim təsdiqlədi, rədd etdi, geri qaytardı."""

    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_key: Mapped[str] = mapped_column(ForeignKey("leads.dedupe_key"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # boş = sistem
    action: Mapped[str] = mapped_column(_enum("lead_action", *LEAD_ACTIONS))
    from_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    to_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Contact(Base):
    """Əlaqə saxlanılacaq şəxs və ya ümumi kanal (HR, direktor, post müəllifi, şirkət telefonu)."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=True)
    lead_key: Mapped[Optional[str]] = mapped_column(ForeignKey("leads.dedupe_key"), index=True, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(_enum("contact_source", *CONTACT_SOURCES))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Outreach(Base):
    """Göndərilmiş (və ya alınmamış) LinkedIn şərhi / email."""

    __tablename__ = "outreach"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(_enum("outreach_kind", *OUTREACH_KINDS))
    lead_key: Mapped[Optional[str]] = mapped_column(ForeignKey("leads.dedupe_key"), index=True, nullable=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("opportunities.id"), index=True, nullable=True)
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    to_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # email və ya post URL
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    language: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    status: Mapped[str] = mapped_column(_enum("outreach_status", *OUTREACH_STATUSES))
    external_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # boş = sistem
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Qaralama formunun birdəfəlik açarı — "Göndər" iki dəfə basılsa, ikinci sorğu unikal açara dəyir.
    send_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)
    # 0011: əlavə olunmuş faylların adları (sətirlə); faylın özü saxlanmır — yalnız məktubla gedir.
    attachments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 0012: hansı şirkət poçtundan göndərilib (boş = .env-dəki defolt SMTP).
    mailbox_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mailboxes.id"), nullable=True)


class LeadBrief(Base):
    """Xəbərin AI ilə qısa səsli izahı (0011) — mətn və mp3 bir dəfə yaradılır, sonra bazadan verilir."""

    __tablename__ = "lead_briefs"

    lead_key: Mapped[str] = mapped_column(ForeignKey("leads.dedupe_key"), primary_key=True)
    script: Mapped[str] = mapped_column(Text)
    audio: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    voice: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# --- İstifadəçi alətləri (0012) -------------------------------------------------


class UserCategory(Base):
    """İstifadəçinin öz kateqoriyası — Validasiyada ayrıca tab: seçilmiş A/B/C, lead növləri, açar sözlər."""

    __tablename__ = "user_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    tiers: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # "AC"; boş = hamısı
    kinds: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # "tender,news"; boş = hamısı
    terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # sətirlə; boş = sözsüz
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LetterTemplate(Base):
    """İstifadəçinin yüklədiyi məktub şablonu — AI qaralamanı onun quruluşu və tonu ilə yazır."""

    __tablename__ = "letter_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Mailbox(Base):
    """Göndərən şirkətin poçt qutusu (SMTP + cavablar üçün IMAP). Şifrə bazada saxlanmır —
    env-də `MAILBOX_<KEY>_PASSWORD` (app/mailboxes.py)."""

    __tablename__ = "mailboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120))  # şirkətin adı — "Kimdən" sahəsində görünür
    email: Mapped[str] = mapped_column(String(255))
    smtp_host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    imap_host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # boş = email
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_polled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InboundEmail(Base):
    """Göndərdiyimiz məktuba gələn cavab (IMAP-dan) — fürsət səhifəsində görünür."""

    __tablename__ = "inbound_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True)
    mailbox_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mailboxes.id"), nullable=True)
    outreach_id: Mapped[Optional[int]] = mapped_column(ForeignKey("outreach.id"), index=True, nullable=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("opportunities.id"), index=True, nullable=True)
    from_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # positive/negative/auto
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime)


class Reminder(Base):
    """İstifadəçinin öz qeydi/xatırlatması: `due_at`-dan `remind_before_min` dəqiqə əvvəl bildiriş."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    remind_before_min: Mapped[int] = mapped_column(Integer, default=0)
    remind_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    contract_id: Mapped[Optional[int]] = mapped_column(ForeignKey("client_contracts.id"), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Notification(Base):
    """Saytdaxili bildiriş (xatırlatma, məktubun təsdiqi/rəddi, məktuba cavab)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class LetterApproval(Base):
    """"Təsdiqə göndər": satışın məktub qaralaması təsdiqləyicinin növbəsinə düşür."""

    __tablename__ = "letter_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    to_address: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    language: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    mailbox_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mailboxes.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # APPROVAL_STATUSES
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ClientContract(Base):
    """Şirkətlə əməkdaşlıq razılaşması (müqavilə). Bir şirkətin bir neçə razılaşması ola bilər;
    `note` — təsvir, `contract_end` boşdursa müddətsizdir."""

    __tablename__ = "client_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_name: Mapped[str] = mapped_column(Text)
    organization_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")  # AGREEMENT_STATUSES
    starts_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    contract_end: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    contact: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class EmployeeSearch(Base):
    """Şirkətin LinkedIn-dəki bütün işçiləri (Apify, pulludur) — nəticə saxlanılır, təkrar ödənilməsin."""

    __tablename__ = "employee_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company: Mapped[str] = mapped_column(Text)
    company_key: Mapped[str] = mapped_column(String(200), index=True)
    items: Mapped[str] = mapped_column(Text)  # JSON siyahı: [{name, title, linkedin_url, location}]
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
