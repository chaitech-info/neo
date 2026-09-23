"""AI skoru olmayan pending_review lead-lərini yenidən qiymətləndirir.

AI əlçatan olmayanda (məs. API key yox idi) lead-lər skorsuz pending_review-a
düşür (sənəd §6). Key əlavə edildikdən sonra bu skript onları emal edir:
yalnız başlığı olan HTML-mənbə lead-lərinin əvvəlcə məqalə mətni çəkilir.

    python -m scripts.rescore_pending            # skorla
    python -m scripts.rescore_pending --dry-run  # yalnız nəyin emal olunacağını göstər
"""
from __future__ import annotations

import sys

from sqlalchemy.orm import sessionmaker

from app.ai_filter import AIFilter
from app.db.engine import engine
from app.db.repository import LeadRepository
from app.scrapers import ALL_SCRAPERS

SCRAPERS_BY_SOURCE = {cls.source_name: cls for cls in ALL_SCRAPERS}
# JSON API mənbələrində ayrıca məqalə səhifəsi yoxdur — raw_text olduğu kimi qalır.
ENRICHABLE_SOURCES = {"marja", "1news", "oxu", "fed", "emlak"}


def main(dry_run: bool = False) -> None:
    session = sessionmaker(bind=engine)()
    repo = LeadRepository(session)
    records = [r for r in repo.list_by_status("pending_review") if r.ai_score is None]
    print(f"Skorsuz pending_review lead: {len(records)}")

    ai_filter = AIFilter()
    counts = {"pending_review": 0, "rejected": 0, "failed": 0}
    for record in records:
        lead = repo.to_raw_lead(record)

        if lead.source.value in ENRICHABLE_SOURCES and lead.raw_text == lead.title and not dry_run:
            scraper = SCRAPERS_BY_SOURCE[lead.source.value]()
            lead = scraper.enrich(lead)
            record.raw_text = lead.raw_text
            record.posted_at = lead.posted_at

        if dry_run:
            print(f"  [{lead.source.value}] {lead.title[:80]}")
            continue

        lead = ai_filter.score(lead)
        repo.update_after_scoring(record.dedupe_key, lead)  # raw_text/posted_at da commit olunur
        outcome = "failed" if lead.ai_score is None else lead.status.value
        counts[outcome] += 1
        score = "—" if lead.ai_score is None else f"{lead.ai_score:.2f}"
        print(f"  {score:>4} {lead.status.value:<14} [{lead.source.value}] {lead.title[:60]}")
        print(f"       {lead.ai_reason}")

    if not dry_run:
        print(f"\nNəticə: {counts}")
    session.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
