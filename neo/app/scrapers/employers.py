"""İş elanı saytlarının ortaq hissəsi: vakansiyaları işəgötürənə görə qruplaşdırıb
"işçilərinə nahar verir" lead-i qurmaq + artıq yoxlanmış elanların yaddaşı.

boss.az (GraphQL) öz qruplaşdırmasını saxlayır; jobsearch.az, position.az,
hellojob.az bunu işlədir (Phase 2, 2026-10-05).
"""
from __future__ import annotations

import re
from datetime import datetime

from app.models import ContactInfo, LeadCategory, RawLead
from app.scrapers.employer_signal import (
    EMAIL_RE,
    SIGNAL_PREFIX,
    corporate_domain,
    employer_url,
    fold,
    has_own_kitchen,
    is_competitor,
    mentions_company_meal,
)

ANONYMOUS_NAMES = {"company", "company-", "şirkət", "sirket", "gizli", "anonim", "məxfi", "mexfi", ""}
SAMPLE_CHARS = 2500


def clean_company(name: str | None) -> str:
    return re.sub(r"[\"“”«»„]", "", name or "").strip(" -–")


def usable_employer(name: str) -> bool:
    """Adı gizli deyil və öz mətbəxi yoxdur (restoran/otel/catering)."""
    return bool(name) and fold(name) not in ANONYMOUS_NAMES and not has_own_kitchen(name)


class EmployerCollector:
    """Nahar qeyd olunan vakansiyaları şirkət üzrə yığır, hər şirkətə bir lead."""

    def __init__(self, source: str, site_label: str) -> None:
        self.source, self.site_label = source, site_label
        self.employers: dict[str, dict] = {}

    def add(self, group: str, name: str, position: str, vacancy_url: str, text: str,
            phone: str | None = None, posted_at: datetime | None = None) -> bool:
        """Nahar siqnalı varsa əlavə edir (True). `group` — saytın şirkət ID-si və ya adı."""
        if not mentions_company_meal(text) or is_competitor(name, text):
            return False
        emails = [m.group(0) for m in EMAIL_RE.finditer(text)]
        domain = corporate_domain(text)
        entry = self.employers.setdefault(group, {
            "name": name, "domain": None, "positions": [], "vacancy_urls": [],
            "email": None, "phone": phone, "posted_at": None, "sample": text,
        })
        entry["domain"] = entry["domain"] or domain
        entry["email"] = entry["email"] or next(
            (e for e in emails if domain and e.lower().endswith("@" + domain)), None) or (emails[0] if emails else None)
        entry["phone"] = entry["phone"] or phone
        entry["positions"].append(position or "")
        entry["vacancy_urls"].append(vacancy_url)
        if posted_at and (entry["posted_at"] is None or posted_at > entry["posted_at"]):
            entry["posted_at"] = posted_at
        return True

    def leads(self) -> list[RawLead]:
        return [self._lead(e) for e in self.employers.values()]

    def _lead(self, e: dict) -> RawLead:
        positions = sorted({p for p in e["positions"] if p})
        n = len(e["vacancy_urls"])
        raw_text = (
            f"{SIGNAL_PREFIX}\n"
            f"Şirkət: {e['name']}\n"
            f"{self.site_label}-da nahar qeyd olunan yeni vakansiyalar ({n}): {', '.join(positions)}\n"
            f"Elan: {e['vacancy_urls'][0]}\n\n{e['sample'][:SAMPLE_CHARS]}"
        )
        return RawLead(
            source=self.source,
            source_url=employer_url(e["domain"], e["name"]),
            title=f"{e['name']} — işçilərinə nahar verir ({n} vakansiya, {self.site_label})",
            raw_text=raw_text,
            entity_name=e["name"],
            contact_info=ContactInfo(email=e["email"], phone=e["phone"],
                                     website=f"https://{e['domain']}" if e["domain"] else None),
            category=LeadCategory.corporate,
            location="Azərbaycan",
            posted_at=e["posted_at"],
        )


# --- Artıq yoxlanmış elanlar (triage_cache cədvəli, növ "jobad:<mənbə>") ----------------------
# Gündəlik run eyni elanların detal səhifəsini təkrar çəkməsin: yalnız yeni elanlar.
# Siqnalı olanlar yadda saxlanmır — onların şirkəti hər run-da lead siyahısında qalmalıdır
# (dedup onsuz da URL ilə tutur), yalnız siqnalsızlar atlanır.

def seen_without_signal(source: str, urls: list[str]) -> set[str]:
    from app.ai_triage import _db_load

    return {u for u, fit in _db_load(f"jobad:{source}", "1", urls).items() if not fit}


def remember(source: str, results: dict[str, bool]) -> None:
    from app.ai_triage import _db_save

    _db_save(f"jobad:{source}", "1", results)
