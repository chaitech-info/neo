from __future__ import annotations

import re

import structlog
from bs4 import BeautifulSoup

from app.models import LeadCategory, RawLead
from app.scrapers.base import BaseScraper
from app.scrapers.employer_signal import (
    ROLE_SIGNAL_PREFIX,
    employer_url,
    fold,
    has_own_kitchen,
    is_competitor,
    is_job_board,
    matches_target_role,
)

log = structlog.get_logger()

# jobsearch.az — ikinci ən böyük iş elanı saytı (2026-10-07 yoxlanıb; robots.txt
# `Allow: /`). Siyahı SSR-dir: `a.list__item__text` > h3.list__item__title
# (vəzifə) + şirkət adı. Elan detalı Nuxt SPA-dır (təsvir ayrıca API-dən gəlir),
# ona görə yalnız VƏZİFƏ ADI əsasında işləyirik: hədəf vəzifələrə (Facility/Camp/
# Canteen/Procurement/HSE/Site Manager, Chef...) işçi axtaran şirkət lead-dir.
# "Nahar verilir" siqnalı boss.az və LinkedIn-də tutulur — burada detal yoxdur.
#
# ⚠️ Səhifələmə YOXDUR: `?page=N` SSR-də həmişə eyni ilk 30 elanı qaytarır, "Daha
# ətraflı" düyməsi /vacancies/undefined-ə gedir, siyahı API-si ictimai deyil
# (2026-10-07 brauzerlə yoxlanıb). Ona görə hər çalışmada yalnız ən yeni ~30 elan
# görünür — scheduler gün ərzində bir neçə dəfə çağırır ki, heç nə keçməsin.
LISTING_URL = "https://jobsearch.az/vacancies"
BASE_URL = "https://jobsearch.az"
ANONYMOUS_NAMES = {"company", "şirkət", "sirket", "gizli", "anonim", "məxfi", "mexfi"}


def parse_listing(html: str) -> list[tuple[str, str, str]]:
    """[(vakansiya URL-i, vəzifə, şirkət)]"""
    soup = BeautifulSoup(html, "lxml")
    results = []
    for a in soup.select("a.list__item__text[href*='/vacancies/']"):
        title_el = a.select_one(".list__item__title")
        if title_el is None:
            continue
        position = title_el.get_text(" ", strip=True)
        company = re.sub(r"\s+", " ", a.get_text(" ", strip=True).replace(position, "", 1)).strip()
        href = a["href"]
        results.append((href if href.startswith("http") else f"{BASE_URL}{href}", position, company))
    return results


def collect_role_employer(vacancy_url: str, position: str, company: str, employers: dict[str, dict]) -> None:
    """Hədəf vəzifəyə uyğun elanın işəgötürənini `employers`-ə əlavə edir (boss.az da istifadə edir)."""
    name = re.sub(r"[\"“”«»„]", "", company or "").strip()
    if not matches_target_role(position) or not name or fold(name) in ANONYMOUS_NAMES:
        return
    if is_job_board(name) or is_competitor(name) or has_own_kitchen(name):
        return
    entry = employers.setdefault(employer_url(None, name), {"name": name, "positions": [], "urls": []})
    entry["positions"].append(position)
    entry["urls"].append(vacancy_url)


def role_lead(e: dict, source: str, board: str) -> RawLead:
    positions = sorted(set(e["positions"]))
    raw_text = (
        f"{ROLE_SIGNAL_PREFIX}\n"
        f"Şirkət: {e['name']}\n"
        f"{board}-da aktiv vakansiyalar ({len(e['urls'])}): {', '.join(positions)}\n"
        f"Elan: {e['urls'][0]}"
    )
    return RawLead(
        source=source,
        source_url=employer_url(None, e["name"]),
        title=f"{e['name']} — {', '.join(positions[:3])} axtarır ({board})",
        raw_text=raw_text,
        entity_name=e["name"],
        category=LeadCategory.corporate,
        location="Azərbaycan",
    )


class JobSearchScraper(BaseScraper):
    """jobsearch.az-da hədəf vəzifələrə işçi axtaran şirkətlər (hər şirkət bir lead)."""

    source_name = "jobsearch"
    base_url = BASE_URL
    min_delay_s = 1.5
    max_delay_s = 3.0
    max_pages = 1  # bax yuxarıdakı qeyd: SSR yalnız ilk səhifəni verir

    def fetch_leads(self) -> list[RawLead]:
        employers: dict[str, dict] = {}
        scanned = 0
        for page in range(1, self.max_pages + 1):
            if page > 1:
                self.polite_sleep()
            items = parse_listing(self.get(LISTING_URL, params={"page": page} if page > 1 else None).text)
            if not items:
                break
            for vacancy_url, position, company in items:
                scanned += 1
                self._collect(vacancy_url, position, company, employers)
        leads = [self._to_lead(e) for e in employers.values()]
        log.info("jobsearch_scanned", scanned=scanned, employers=len(leads))
        return leads

    @staticmethod
    def _collect(vacancy_url: str, position: str, company: str, employers: dict[str, dict]) -> None:
        collect_role_employer(vacancy_url, position, company, employers)

    @staticmethod
    def _to_lead(e: dict) -> RawLead:
        return role_lead(e, source="jobsearch", board="jobsearch.az")
