from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.ai_filter import AIFilter
from app.crm_client import CRMClient
from app.db.repository import LeadRepository
from app.normalizer import Normalizer
from app.scrapers.base import BaseScraper

log = structlog.get_logger()


def run_pipeline(scraper_cls: type[BaseScraper], session: Session) -> int:
    """Bir mənbə üçün: scrape -> normalize -> AI score.

    CRM-ə ötürmə burada BAŞ VERMİR — human-in-the-loop prinsipinə görə (bax
    sənəd §6, §11) `ai_score >= threshold` olan lead-lər `pending_review`
    statusu ilə admin paneldə gözləyir; insan orada Approve/Reject edir
    (bax app/web.py). Sənəd §3 Architecture diaqramının kod qarşılığı.
    Celery task-ları (app/scheduler.py) bunu hər scraper üçün çağırır.
    """
    repo = LeadRepository(session)
    normalizer = Normalizer(repo)
    ai_filter = AIFilter()

    log.info("pipeline_started", source=scraper_cls.source_name)
    scraper = scraper_cls()
    raw_leads = scraper.run()
    fresh_leads = normalizer.process(raw_leads)
    log.info(
        "dedup_done",
        source=scraper_cls.source_name,
        scraped=len(raw_leads),
        fresh=len(fresh_leads),
        duplicates=len(raw_leads) - len(fresh_leads),
    )

    if not fresh_leads:
        log.info("pipeline_no_new_leads", source=scraper_cls.source_name)
        return 0

    scored_leads = ai_filter.score_batch(fresh_leads)

    pending_count = 0
    for lead in scored_leads:
        key = lead.dedupe_key()
        repo.update_after_scoring(key, lead)
        if lead.status.value == "pending_review":
            pending_count += 1

    log.info(
        "pipeline_completed",
        source=scraper_cls.source_name,
        scraped=len(raw_leads),
        fresh=len(fresh_leads),
        pending_review=pending_count,
    )
    return pending_count


def send_lead_to_crm(dedupe_key: str, session: Session) -> str:
    """Admin panelində 'Approve' düyməsi çağırır. Sənəd §9 CRM inteqrasiyası.

    Qaytarır: "ok" | "not_found" | "crm_failed". "crm_failed" — CRM_BASE_URL
    hələ real endpoint-ə işarə etmirsə (sənəd §9 açıq sual) gözlənilən haldır.
    """
    repo = LeadRepository(session)
    record = repo.get(dedupe_key)
    if record is None or record.status != "pending_review":
        return "not_found"

    crm = CRMClient()
    lead = repo.to_raw_lead(record)
    opportunity_id = crm.create_opportunity(lead)
    if not opportunity_id:
        return "crm_failed"

    repo.mark_sent_to_crm(dedupe_key, opportunity_id)
    return "ok"


def restore_lead(dedupe_key: str, session: Session) -> bool:
    """'AI rədd etdi' səhifəsində 'Validasiyaya qaytar' — AI-nin səhv rəddini insan düzəldir."""
    repo = LeadRepository(session)
    record = repo.get(dedupe_key)
    if record is None or record.status != "rejected":
        return False
    repo.set_status(dedupe_key, "pending_review")
    return True


def reject_lead(dedupe_key: str, session: Session) -> bool:
    """Admin panelində 'Reject' düyməsi çağırır.

    Yalnız pending_review rədd edilə bilər — artıq CRM-ə göndərilmiş lead-in
    statusunu dəyişmək DB ilə CRM arasında uyğunsuzluq yaradardı.
    """
    repo = LeadRepository(session)
    record = repo.get(dedupe_key)
    if record is None or record.status != "pending_review":
        return False
    repo.set_status(dedupe_key, "rejected")
    return True
