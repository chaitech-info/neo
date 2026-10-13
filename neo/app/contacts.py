"""Kontakt toplama (pulsuz mənbələr): lead mətni, AI-nin çıxardığı şəxslər, LinkedIn
müəllifi, şirkət saytının əlaqə səhifəsi. Satış komandası kimə yazacağını/zəng
edəcəyini bilsin — HR, direktor, satınalma, postu yazan şəxs.

Pullu zənginləşdirmə (LinkedIn işçi axtarışı və s.) sonrakı mərhələdir — `source="enrichment"`.
Pipeline yalnız validasiyaya düşən lead-lər üçün çağırır (app/pipeline.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import structlog
from bs4 import BeautifulSoup
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Contact, LeadRecord
from app.scrapers.base import BaseScraper, fold
from app.scrapers.employer_signal import is_job_board

log = structlog.get_logger()

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.I)
# Azərbaycan nömrələri: +994 / 994 / 0 + operator/şəhər kodu + 7 rəqəm, aralarında boşluq, tire, mötərizə.
PHONE_RE = re.compile(
    r"(?<![\d+])(?:\+?\s?994|0)[\s\-.(]*(10|12|18|2\d|36|40|44|50|51|55|60|70|77|99)[\s\-.)]*"
    r"(\d{3})[\s\-.]*(\d{2})[\s\-.]*(\d{2})(?!\d)"
)
# Email-in özü deyil, şəkil/fayl adı ("logo@2x.png") və saytların texniki ünvanları.
_JUNK_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp)$|^(no-?reply|noreply|example|test|wordpress)@|@(sentry|example)\.", re.I)
# Kontakt kimi saxlanmayan domenlər: sosial şəbəkələr, iş elanı saytları — onların ümumi poçtu satış üçün deyil.
_SKIP_SITES = ("linkedin.com", "facebook.com", "instagram.com", "t.me", "youtube.com", "twitter.com", "x.com",
               "google.com", "wa.me", "boss.az", "jobsearch.az", "hellojob.az", "position.az", "etender.gov.az")
# Əlaqə səhifəsinin linki (mətn və ya URL): az/ru/en.
_CONTACT_LINK = re.compile(r"contact|kontakt|elaqe|əlaqə|elaqə|haqqimizda|haqqımızda|about|контакт|о-нас|o-nas", re.I)
MAX_CONTACT_PAGES = 3


@dataclass
class Found:
    name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    source: str = "text"

    def key(self) -> str | None:
        """Dublikat açarı: email > telefon > LinkedIn > ad."""
        return ((self.email or "").lower() or self.phone or (self.linkedin_url or "").rstrip("/").lower()
                or (fold(self.name) if self.name else "")) or None


def normalize_phone(raw: str) -> str | None:
    m = PHONE_RE.search(raw or "")
    if not m:
        return None
    return "+994" + "".join(m.groups())


def _email_ok(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return not _JUNK_EMAIL.search(email) and not is_job_board(domain)


def extract_from_text(text: str, source: str = "text") -> list[Found]:
    """Mətndəki email və telefonlar (hər biri ayrı kontakt — kimə aid olduğu bilinmir)."""
    out: list[Found] = []
    seen: set[str] = set()
    for m in EMAIL_RE.finditer(text or ""):
        email = m.group(0).strip(".").lower()
        if email not in seen and _email_ok(email):
            seen.add(email)
            out.append(Found(email=email, source=source))
    for m in PHONE_RE.finditer(text or ""):
        phone = "+994" + "".join(m.groups())
        if phone not in seen:
            seen.add(phone)
            out.append(Found(phone=phone, source=source))
    return out


def from_ai(people: list[dict] | None) -> list[Found]:
    """AI qiymətləndirməsinin `contacts` sahəsi (app/ai_scoring.py) — yalnız mətndə adı çəkilənlər."""
    out = []
    for p in people or []:
        email = (p.get("email") or "").strip().lower() or None
        if email and not _email_ok(email):
            email = None
        found = Found(name=(p.get("name") or "").strip() or None, title=(p.get("role") or "").strip() or None,
                      email=email, phone=normalize_phone(p.get("phone") or ""), source="ai")
        if found.name or found.email or found.phone:
            out.append(found)
    return out


def crawlable(url: str | None) -> bool:
    if not url or not url.startswith("http"):
        return False
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and not any(host == s or host.endswith("." + s) for s in _SKIP_SITES)


class WebsiteContacts(BaseScraper):
    """Şirkət saytının ana səhifəsi + ən çox 3 əlaqə/haqqımızda səhifəsi. robots.txt-ə əməl edir."""

    source_name = "contacts"
    min_delay_s = 0.3
    max_delay_s = 0.8

    def fetch_leads(self):  # BaseScraper interfeysi — burada lead yoxdur
        return []

    def crawl(self, url: str) -> list[Found]:
        try:
            home = self.get(url, timeout=10.0)
        except Exception as exc:
            log.info("contact_crawl_failed", url=url, error=type(exc).__name__)
            return []
        soup = BeautifulSoup(home.text, "lxml")
        pages, found = [], self._page(soup)
        host = urlparse(str(home.url)).hostname
        for a in soup.find_all("a", href=True):
            href = urljoin(str(home.url), a["href"])
            if urlparse(href).hostname == host and href not in pages and (
                    _CONTACT_LINK.search(href) or _CONTACT_LINK.search(a.get_text(" ", strip=True))):
                pages.append(href)
            if len(pages) >= MAX_CONTACT_PAGES:
                break
        for page in pages:
            self.polite_sleep()
            try:
                found += self._page(BeautifulSoup(self.get(page, timeout=10.0).text, "lxml"))
            except Exception:
                continue
        return found

    @staticmethod
    def _page(soup: BeautifulSoup) -> list[Found]:
        links = " ".join(a["href"].replace("mailto:", " ").replace("tel:", " ")
                         for a in soup.find_all("a", href=True) if a["href"].startswith(("mailto:", "tel:")))
        return extract_from_text(soup.get_text(" ", strip=True) + " " + links, source="website")


def collect(record: LeadRecord, ai_people: list[dict] | None = None, crawler: WebsiteContacts | None = None) -> list[Found]:
    """Bir lead üçün bütün pulsuz mənbələr. `crawler` verilməyibsə sayt oxunmur."""
    found: list[Found] = []
    if record.author_name or record.author_url:
        found.append(Found(name=record.author_name, title=record.author_title,
                           linkedin_url=record.author_url, source="author"))
    found += from_ai(ai_people)
    found += extract_from_text(record.raw_text)
    site = record.contact_website if crawlable(record.contact_website) else None
    if crawler is not None and site:
        found += crawler.crawl(site)
    return found


def save_contacts(session: Session, record: LeadRecord, found: list[Found]) -> int:
    """Yeni kontaktları yazır (lead və təşkilat üzrə dublikatsız); lead-in köhnə tək
    email/telefon sahələri boşdursa ilk tapılanla doldurulur. Commit çağıranın işidir."""
    scope = [Contact.lead_key == record.dedupe_key]
    if record.organization_id:
        scope.append(Contact.organization_id == record.organization_id)
    existing = {Found(name=c.name, email=c.email, phone=c.phone, linkedin_url=c.linkedin_url).key()
                for c in session.execute(select(Contact).where(or_(*scope))).scalars()}
    added = 0
    for f in found:
        key = f.key()
        if not key or key in existing:
            continue
        existing.add(key)
        session.add(Contact(lead_key=record.dedupe_key, organization_id=record.organization_id,
                            name=(f.name or None) and f.name[:200], title=(f.title or None) and f.title[:200],
                            email=f.email, phone=f.phone, linkedin_url=f.linkedin_url, source=f.source))
        added += 1
    record.contact_email = record.contact_email or next((f.email for f in found if f.email), None)
    record.contact_phone = record.contact_phone or next((f.phone for f in found if f.phone), None)
    return added


def contacts_for(session: Session, lead_key: str | None, organization_id: int | None) -> list[Contact]:
    """Fürsət səhifəsi: lead-in və təşkilatın kontaktları — adı/vəzifəsi olan şəxslər əvvəl."""
    scope = []
    if lead_key:
        scope.append(Contact.lead_key == lead_key)
    if organization_id:
        scope.append(Contact.organization_id == organization_id)
    if not scope:
        return []
    rows = session.execute(select(Contact).where(or_(*scope)).order_by(Contact.id)).scalars().all()
    return sorted(rows, key=lambda c: (c.name is None, c.email is None))
