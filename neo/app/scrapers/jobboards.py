"""HTML iş elanı saytları — siyahı səhifələri + detal səhifəsi (Phase 2, 2026-10-05).

Bir sayt = bir `JobBoardSite`. Siyahıdan elan linkləri (`link_pattern`), hər yeni
elanın detal səhifəsindən şirkət, vəzifə və mətn (selector-lar) oxunur; nahar
qeyd olunanların işəgötürəni lead olur (app/scrapers/employers.py). Artıq
yoxlanmış siqnalsız elanların detalı təkrar çəkilmir.

Yoxlanılıb: position.az (`/az?page=N`, robots: hər şey icazəli), hellojob.az
(`/vakansiyalar?page=N`, robots `Allow: /`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper
from app.scrapers.employers import (
    EmployerCollector, RoleCollector, clean_company, remember, seen_without_signal, usable_employer,
)

log = structlog.get_logger()


@dataclass(frozen=True)
class JobBoardSite:
    key: str
    label: str                 # lead başlığında: "(N vakansiya, position.az)"
    page_url: str              # "{page}" ilə
    link_pattern: str
    company_selector: str
    title_selector: str
    text_selector: str
    pages: int = 3             # ən yeni elanlar ilk səhifələrdədir
    max_details: int = 150


class JobBoardScraper(BaseScraper):
    site: JobBoardSite
    min_delay_s = 1.0
    max_delay_s = 2.0

    def fetch_leads(self):
        site = self.site
        pattern = re.compile(site.link_pattern)
        urls: list[str] = []
        for page in range(1, site.pages + 1):
            if page > 1:
                self.polite_sleep()
            listing = site.page_url.format(page=page)
            soup = BeautifulSoup(self.get(listing).text, "lxml")
            for a in soup.select("a[href]"):
                url = urljoin(listing, a["href"]).split("?")[0].split("#")[0]
                if pattern.search(url) and url not in urls:
                    urls.append(url)
        self.fetched = len(urls)

        skip = seen_without_signal(self.source_name, urls)
        todo = [u for u in urls if u not in skip][:site.max_details]
        collector = EmployerCollector(self.source_name, site.label)
        roles = RoleCollector(self.source_name, site.label)
        checked: dict[str, bool] = {}
        for url in todo:
            self.polite_sleep()
            try:
                page = BeautifulSoup(self.get(url).text, "lxml")
            except Exception:
                log.warning("jobboard_detail_failed", source=self.source_name, url=url)
                continue
            name = clean_company(_text(page, site.company_selector))
            text = _text(page, site.text_selector)
            if not usable_employer(name):
                checked[url] = False
                continue
            title = _text(page, site.title_selector)
            roles.add(url, title, name)
            checked[url] = collector.add(name.lower(), name, title, url, text)
        remember(self.source_name, checked)
        log.info("jobboard_scanned", source=self.source_name, links=len(urls), skipped=len(skip),
                 checked=len(checked), employers=len(collector.employers))
        meal_leads = collector.leads()
        role_leads = roles.leads(exclude_names=[e["name"] for e in collector.employers.values()],
                                 exclude_urls=[lead.source_url for lead in meal_leads])
        return meal_leads + role_leads


def _text(page: BeautifulSoup, selector: str) -> str:
    el = page.select_one(selector)
    return el.get_text(" ", strip=True) if el is not None else ""


def jobboard_scraper(site: JobBoardSite) -> type[JobBoardScraper]:
    return type(f"Jobs_{site.key}", (JobBoardScraper,), {"site": site, "source_name": site.key})


POSITION = JobBoardSite(
    key="position", label="position.az", page_url="https://position.az/az?page={page}",
    link_pattern=r"position\.az/az/vacancy/[\w-]+-\d+$",
    company_selector=".vacancy-company-header-title", title_selector=".vacancy-title, h1, h2",
    text_selector=".vacancy-text.vacancy-az, .vacancy-text",
)
HELLOJOB = JobBoardSite(
    key="hellojob", label="hellojob.az", page_url="https://www.hellojob.az/vakansiyalar?page={page}",
    link_pattern=r"hellojob\.az/vakansiya/[\w-]+-\d+$",
    company_selector=".company__row a.vacancies__category", title_selector="h2.vacancies__subTitle",
    text_selector=".company__text",
)
