from __future__ import annotations

import structlog

from app.db.repository import LeadRepository
from app.models import RawLead

log = structlog.get_logger()


class Normalizer:
    """Ham RawLead-ləri qəbul edir, dublikatları ayıqlayır. Sənəd §4."""

    def __init__(self, repo: LeadRepository) -> None:
        self.repo = repo

    def process(self, leads: list[RawLead]) -> list[RawLead]:
        fresh: list[RawLead] = []
        for lead in leads:
            key = lead.dedupe_key()
            # URL yoxlaması mənbədən asılı deyil: işəgötürən lead-lərinin URL-i
            # şirkətin saytıdır — eyni şirkət LinkedIn-dən də, boss.az-dan da gələ bilər.
            if self.repo.exists(key) or self.repo.url_exists(lead.source_url):
                log.debug("duplicate_skipped", source=lead.source, url=lead.source_url)
                continue
            self.repo.save(key, lead)
            fresh.append(lead)
        return fresh
