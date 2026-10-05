from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy.orm import Session

from app.ai_filter import AIFilter
from app.ai_scoring import StructuredAIFilter
from app.db.models import ScrapeRun
from app.db.repository import LeadRepository
from app.deadlines import extract_deadline
from app.normalizer import Normalizer
from app.scrapers.base import BaseScraper
from app.story_dedup import mark_story_duplicate
from app.tenders import link_tender

log = structlog.get_logger()

# Bu müddətdən köhnə `running` run = yarımçıq (ən uzun mənbə etender ~5 dəq + AI).
INTERRUPTED_AFTER = timedelta(hours=2)
# Bu müddətdən köhnə `new` lead = əvvəlki run AI mərhələsində qırılıb.
UNSCORED_AFTER = timedelta(minutes=30)


@dataclass
class PipelineResult:
    """Bir scrape-in nəticəsi — admin panel mesajı və `scrape_runs` üçün."""

    scraped: int = 0          # scraper-in qaytardığı elan
    fresh: int = 0            # bazaya yeni yazılan lead (dublikatlar daxil)
    url_duplicates: int = 0   # eyni link artıq bazada idi — yazılmadı
    tender_duplicates: int = 0  # başqa mənbədən artıq tapılmış tender — `duplicate`
    pending: int = 0          # AI: validasiyaya
    rejected: int = 0         # AI: rədd
    error: str | None = None  # scraper xətası (məs. sayt 403 qaytardı) — run "failed" sayılır
    fetched: int = 0          # filtrdən ƏVVƏL saytdan gələn elan (0 = sayt/selector sınıb ola bilər)
    recovered: int = 0        # əvvəlki qırılmış run-dan skorsuz qalan lead-lər, indi skorlandı
    story_duplicates: int = 0  # eyni şirkət/hekayə artıq validasiyadadır — `duplicate` (app/story_dedup.py)

    def summary(self) -> str:
        """Məs. "3 yeni lead — 1 validasiyaya, 2 AI rədd etdi" (+ dublikatlar)."""
        text = f"{self.fresh} yeni lead"
        parts = []
        if self.fresh:
            parts += [f"{self.pending} validasiyaya", f"{self.rejected} AI rədd etdi"]
        if self.tender_duplicates:
            parts.append(f"{self.tender_duplicates} başqa mənbədə artıq var")
        if self.story_duplicates:
            parts.append(f"{self.story_duplicates} eyni şirkət artıq validasiyada")
        if self.url_duplicates:
            parts.append(f"{self.url_duplicates} təkrar (artıq bazada)")
        return f"{text} — {', '.join(parts)}" if parts else text


def make_scorer():
    """Defolt v2 — strukturlaşdırılmış qiymətləndirmə (app/ai_scoring.py). 2026-10-05 ölçüsü
    (scripts/eval_filter.py, test hissəsi): v1 P 0.82 / R 0.77 → v2 + gpt-4.1-mini P 0.90 / R 0.93-0.97.
    AI_SCORER=v1 köhnə qiymətləndirməyə qaytarır."""
    if os.environ.get("AI_SCORER", "v2") == "v1":
        return AIFilter()
    return StructuredAIFilter()


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
    started = datetime.utcnow()
    closed = LeadRepository(session).close_interrupted_runs(source_key, started - INTERRUPTED_AFTER)
    if closed:
        log.warning("interrupted_runs_closed", source=source_key, count=closed)
    run = ScrapeRun(source_key=source_key, trigger=trigger, started_at=started, status="running",
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

    # Scraper xətanı udur (bir mənbə digərlərini dayandırmasın), amma heç nə gətirməyibsə
    # run uğursuzdur — yoxsa bloklanmış mənbə dashboard-da "ok, 0 elan" kimi görünür.
    failed = bool(result.error) and not result.scraped
    run.status, run.finished_at = ("failed" if failed else "ok"), datetime.utcnow()
    run.error = result.error if failed else None
    run.items_seen, run.leads_new, run.leads_pending = result.scraped, result.fresh, result.pending
    run.items_fetched = result.fetched
    run.leads_duplicate = result.url_duplicates + result.tender_duplicates + result.story_duplicates
    session.commit()
    return result


def _run(scraper_cls: type[BaseScraper], session: Session) -> PipelineResult:
    repo = LeadRepository(session)
    normalizer = Normalizer(repo)
    ai_filter = make_scorer()
    result = PipelineResult()

    log.info("pipeline_started", source=scraper_cls.source_name)
    scraper = scraper_cls()
    started = datetime.utcnow()
    raw_leads = scraper.run()
    result.error = getattr(scraper, "error", None)
    fetched = getattr(scraper, "fetched", None)
    result.fetched = len(raw_leads) if fetched is None else fetched
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

    # Əvvəlki run AI mərhələsində qırılıbsa (Neon bağlantısı düşüb və s.) onun lead-ləri
    # `new` statusunda qalıb — heç vaxt skorlanmırdı. İndi bu run-la birlikdə skorlanır.
    stuck = repo.list_unscored(scraper_cls.source_name, started - UNSCORED_AFTER)
    if stuck:
        log.warning("unscored_leads_recovered", source=scraper_cls.source_name, count=len(stuck))
        result.recovered = len(stuck)
        to_score += [repo.to_raw_lead(record) for record in stuck]

    if not to_score:
        log.info("pipeline_no_new_leads", source=scraper_cls.source_name)
        return result

    for lead in ai_filter.score_batch(to_score):
        repo.update_after_scoring(lead.dedupe_key(), lead)
        if lead.status.value != "pending_review":
            result.rejected += 1
            continue
        record = repo.get(lead.dedupe_key())
        if record is not None and mark_story_duplicate(session, record):
            result.story_duplicates += 1
        else:
            result.pending += 1

    log.info(
        "pipeline_completed",
        source=scraper_cls.source_name,
        scraped=result.scraped,
        fresh=result.fresh,
        pending_review=result.pending,
        cross_source_duplicates=result.tender_duplicates,
    )
    return result


def restore_lead(dedupe_key: str, session: Session, reviewer_id: int | None = None) -> bool:
    """'AI rədd etdi' səhifəsində 'Validasiyaya qaytar' — AI-nin səhv rəddini insan düzəldir."""
    repo = LeadRepository(session)
    record = repo.get(dedupe_key)
    if record is None or record.status != "rejected":
        return False
    repo.set_status(dedupe_key, "pending_review", reviewer_id=reviewer_id)
    return True


def reject_lead(dedupe_key: str, session: Session, reviewer_id: int | None = None) -> bool:
    """Admin panelində 'Reject' düyməsi çağırır.

    Yalnız pending_review rədd edilə bilər — artıq CRM-ə göndərilmiş lead-in
    statusunu dəyişmək DB ilə CRM arasında uyğunsuzluq yaradardı.
    """
    repo = LeadRepository(session)
    record = repo.get(dedupe_key)
    if record is None or record.status != "pending_review":
        return False
    repo.set_status(dedupe_key, "rejected", reviewer_id=reviewer_id)
    return True
