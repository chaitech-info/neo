"""Köhnə (v1) skorlu lead-ləri v2 ilə yenidən qiymətləndirir (2026-10-05, Phase 1).

Toxunmur: insanın baxdığı (reviewed_by), fürsətə çevrilən, dublikat lead-lərə, artıq
v2 ilə skorlananlara (ai_model "v2:" ilə başlayır). AI çağırışları paralel, bazaya
yazma ardıcıl. Sonra keçən lead-lərə mənbələr arası dublikat yoxlaması tətbiq olunur.

    python -m scripts.rescore_v2 --dry-run   # nə dəyişəcəyini göstər, yazma
    python -m scripts.rescore_v2
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai_scoring import StructuredAIFilter
from app.db.engine import engine
from app.db.models import LeadRecord
from app.db.repository import LeadRepository
from app.pipeline import scorer_label
from app.story_dedup import mark_story_duplicate


def main(dry_run: bool) -> None:
    with Session(engine) as session:
        records = session.scalars(
            select(LeadRecord).where(
                LeadRecord.status.in_(("pending_review", "rejected")),
                LeadRecord.reviewed_by_id.is_(None),
                or_(LeadRecord.ai_model.is_(None), LeadRecord.ai_model.not_like("v2:%")),
            ).order_by(LeadRecord.scraped_at)
        ).all()
        print(f"Yenidən qiymətləndiriləcək: {len(records)}")
        snapshot = [(r.dedupe_key, r.status, r.ai_score, r.title, LeadRepository.to_raw_lead(r)) for r in records]

    def score(row):
        lead = StructuredAIFilter().score(row[4])
        return row, lead

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(score, snapshot))

    changes = {"→ validasiya": [], "→ rədd": [], "dəyişmədi": 0, "AI xətası": 0, "dublikat": 0}
    label = scorer_label(StructuredAIFilter())
    with Session(engine) as session:
        repo = LeadRepository(session)
        for (key, old_status, old_score, title, _), lead in results:
            if lead.ai_score is None:
                changes["AI xətası"] += 1   # köhnə skor qalır
                continue
            new_status = lead.status.value
            if new_status != old_status:
                bucket = "→ validasiya" if new_status == "pending_review" else "→ rədd"
                changes[bucket].append(f"{old_score} → {lead.ai_score}  {title[:70]}")
            else:
                changes["dəyişmədi"] += 1
            if not dry_run:
                repo.update_after_scoring(key, lead, ai_model=label)
        if not dry_run:
            for (key, *_), lead in results:
                record = repo.get(key)
                if record is not None and record.status == "pending_review" and mark_story_duplicate(session, record):
                    changes["dublikat"] += 1

    for bucket in ("→ validasiya", "→ rədd"):
        print(f"\n{bucket}: {len(changes[bucket])}")
        for line in changes[bucket]:
            print("  " + line)
    print(f"\ndəyişmədi: {changes['dəyişmədi']} · AI xətası: {changes['AI xətası']} · "
          f"dublikat kimi birləşdi: {changes['dublikat']}" + ("  (DRY RUN — heç nə yazılmadı)" if dry_run else ""))


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
