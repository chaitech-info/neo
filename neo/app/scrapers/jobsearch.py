"""jobsearch.az — "işçilərinə nahar verən şirkət" siqnalı (Phase 2, 2026-10-05).

Qızıl dəstdə (scripts/eval_filter.py) relevant elementlərin ən böyük qrupu
işəgötürən siqnalıdır (133-dən 59-u) — indiyədək yalnız boss.az və LinkedIn-dən.
jobsearch.az-ın açıq JSON API-si var (robots.txt `Allow: /`):
- siyahı: /api-az/vacancies-az?page=1 (30 elan, `next` linki, ən yenilər əvvəl);
- detal: /api-az/vacancies-az/{slug} (`text` — HTML təsvir).
Gündə bir dəfə son LOOKBACK_DAYS günün elanları; artıq yoxlanmış siqnalsız elanın
detalı təkrar çəkilmir. Şirkət `company.id` ilə qruplaşdırılır (bir elanda korporativ
email, digərində yox — ad/domen açarı eyni şirkəti iki lead-ə bölürdü).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, parse_datetime
from app.scrapers.employers import EmployerCollector, clean_company, remember, seen_without_signal, usable_employer

log = structlog.get_logger()

LIST_URL = "https://jobsearch.az/api-az/vacancies-az?page=1"
DETAIL_URL = "https://jobsearch.az/api-az/vacancies-az/{slug}"
PUBLIC_URL = "https://jobsearch.az/vacancies/{slug}"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
LOOKBACK_DAYS = 3
MAX_PAGES = 15


class JobSearchScraper(BaseScraper):
    source_name = "jobsearch"
    base_url = "https://jobsearch.az"
    min_delay_s = 1.0
    max_delay_s = 2.0

    def fetch_leads(self):
        cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
        recent: list[dict] = []
        url, pages = LIST_URL, 0
        while url and pages < MAX_PAGES:
            if pages:
                self.polite_sleep()
            data = self.get(url, headers=HEADERS).json()
            pages += 1
            items = data.get("items") or []
            fresh = [i for i in items if (parse_datetime(i.get("created_at")) or datetime.utcnow()) >= cutoff]
            recent += fresh
            # VIP elanlar qarışıq gəlir — səhifədə heç bir təzə elan yoxdursa dayan.
            if items and not fresh:
                break
            url = data.get("next")
        self.fetched = len(recent)

        public = {i["slug"]: PUBLIC_URL.format(slug=i["slug"]) for i in recent}
        skip = seen_without_signal(self.source_name, list(public.values()))
        collector = EmployerCollector(self.source_name, "jobsearch.az")
        checked: dict[str, bool] = {}
        for item in recent:
            vacancy_url = public[item["slug"]]
            name = clean_company((item.get("company") or {}).get("title"))
            if vacancy_url in skip:
                continue
            if item.get("hide_company") or not usable_employer(name):
                checked[vacancy_url] = False
                continue
            self.polite_sleep()
            try:
                detail = self.get(DETAIL_URL.format(slug=item["slug"]), headers=HEADERS).json()
            except Exception:
                log.warning("jobsearch_detail_failed", slug=item.get("slug"))
                continue
            text = BeautifulSoup(detail.get("text") or "", "lxml").get_text(" ", strip=True)
            group = str((item.get("company") or {}).get("id") or name.lower())
            checked[vacancy_url] = collector.add(group, name, item.get("title") or "", vacancy_url, text,
                                                 phone=detail.get("phone"),
                                                 posted_at=parse_datetime(item.get("created_at")))
        remember(self.source_name, checked)
        log.info("jobsearch_scanned", pages=pages, recent=len(recent), skipped=len(skip),
                 employers=len(collector.employers))
        return collector.leads()
