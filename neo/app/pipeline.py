from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from app.ai_filter import AIFilter
from app.crm_client import CRMClient
from app.db.models import ScrapeRun
from app.db.repository import LeadRepository
from app.deadlines import extract_deadline
from app.normalizer import Normalizer
from app.scrapers.base import BaseScraper
from app.tenders import link_tender

log = structlog.get_logger()


@dataclass
class PipelineResult:
    """Bir scrape-in nəticəsi — admin panel mesajı və `scrape_runs` üçün."""

    scraped: int = 0          # scraper-in qaytardığı elan
    fresh: int = 0            # bazaya yeni yazılan lead (dublikatlar daxil)
    url_duplicates: int = 0   # eyni link artıq bazada idi — yazılmadı
    tender_duplicates: int = 0  # başqa mənbədən artıq tapılmış tender — `duplicate`
    pending: int = 0          # AI: validasiyaya
    rejected: int = 0         # AI: rədd

    def summary(self) -> str:
        """Məs. "3 yeni lead — 1 validasiyaya, 2 AI rədd etdi" (+ dublikatlar)."""
        text = f"{self.fresh} yeni lead"
        parts = []
        if self.fresh:
            parts += [f"{self.pending} validasiyaya", f"{self.rejected} AI rədd etdi"]
        if self.tender_duplicates:
            parts.append(f"{self.tender_duplicates} başqa mənbədə artıq var")
        if self.url_duplicates:
            parts.append(f"{self.url_duplicates} təkrar (artıq bazada)")
        return f"{text} — {', '.join(parts)}" if parts else text


def run_pipeline(
    scraper_cls: type[BaseScraper],
    session: Session,
    *,
    source_key: str | None = None,
    trigger: str = "manual",
) -> PipelineResult:
    """Bir mənbə üçün: scrape -> dedup -> son tarix -> tender/dublikat -> AI score.

    CRM-ə ötürmə burada BAŞ VERMİR — human-in-the-loop prinsipinə görə (bax
    sənəd §6, §11) `ai_score >= threshold` olan lead-lər `pending_review`
    statusu ilə admin paneldə gözləyir; insan orada Approve/Reject edir
    (bax app/web.py). Hər işə salınma `scrape_runs`-a yazılır (`source_key` —
    admin paneldəki mənbə açarı, məs. `fed_tenders`; scraper-in `source_name`-i
    bir neçə mənbədə eyni ola bilər).
    """
    source_key = source_key or scraper_cls.source_name
    run = ScrapeRun(source_key=source_key, trigger=trigger, started_at=datetime.utcnow(), status="running",
                    items_seen=0, leads_new=0, leads_duplicate=0, leads_pending=0)
    session.add(run)
    session.commit()

    try:
        result = _run(scraper_cls, session)
    except Exception as exc:
        session.rollback()
        run.status, run.finished_at, run.error = "failed", datetime.utcnow(), f"{type(exc).__name__}: {exc}"[:500]
        session.commit()
        raise

    run.status, run.finished_at = "ok", datetime.utcnow()
    run.items_seen, run.leads_new, run.leads_pending = result.scraped, result.fresh, result.pending
    run.leads_duplicate = result.url_duplicates + result.tender_duplicates
    session.commit()
    return result


def _run(scraper_cls: type[BaseScraper], session: Session) -> PipelineResult:
    repo = LeadRepository(session)
    normalizer = Normalizer(repo)
    ai_filter = AIFilter()
    result = PipelineResult()

    log.info("pipeline_started", source=scraper_cls.source_name)
    scraper = scraper_cls()
    raw_leads = scraper.run()
    for lead in raw_leads:
        lead.deadline_at = lead.deadline_at or extract_deadline(lead.raw_text)
    fresh_leads = normalizer.process(raw_leads)
    result.scraped, result.fresh = len(raw_leads), len(fresh_leads)
    result.url_duplicates = len(raw_leads) - len(fresh_leads)

    to_score = []
    for lead in fresh_leads:
        record = repo.get(lead.dedupe_key())
        if record is not None and link_tender(session, record):
            result.tender_duplicates += 1
        else:
            to_score.append(lead)
    session.commit()

    log.info(
        "dedup_done",
        source=scraper_cls.source_name,
        scraped=result.scraped,
        fresh=result.fresh,
        duplicates=result.url_duplicates,
        cross_source_duplicates=result.tender_duplicates,
    )

    if not fresh_leads:
        log.info("pipeline_no_new_leads", source=scraper_cls.source_name)
        return result

    for lead in ai_filter.score_batch(to_score):
        repo.update_after_scoring(lead.dedupe_key(), lead)
        if lead.status.value == "pending_review":
            result.pending += 1
        else:
            result.rejected += 1

    log.info(
        "pipeline_completed",
        source=scraper_cls.source_name,
        scraped=result.scraped,
        fresh=result.fresh,
        pending_review=result.pending,
        cross_source_duplicates=result.tender_duplicates,
    )
    return result


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
