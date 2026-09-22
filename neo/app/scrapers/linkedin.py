from __future__ import annotations

import structlog

from app.models import RawLead
from app.scrapers.base import BaseScraper

log = structlog.get_logger()


class LinkedInScraper(BaseScraper):
    """PLACEHOLDER — sənəd §5 və §7-dəki açıq sual həll olunmayana qədər deaktivdir.

    LinkedIn-in ToS-u açıq HTML scraping-i qadağan edir və hesab bloklanma
    riski yaradır. Bu sinif yalnız gələcəkdə rəsmi API və ya lisenziyalı
    data provider (məs. Proxycurl, Phantombuster) qoşulanda dolduruacaq.
    Scheduler-də (app/scheduler.py) bu mənbə hazırda commentlənib.
    """

    source_name = "linkedin"
    base_url = "https://www.linkedin.com"

    def fetch_leads(self) -> list[RawLead]:
        log.warning(
            "linkedin_scraper_not_implemented",
            reason="Hüquqi/texniki qərar gözlənilir — bax sənəd §7 açıq sual",
        )
        return []
