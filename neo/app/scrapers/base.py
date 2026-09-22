from __future__ import annotations

import abc
import random
import time

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.models import RawLead

log = structlog.get_logger()

DEFAULT_USER_AGENT = "NeoLeadBot/1.0 (+contact: dev@company.az)"


class BaseScraper(abc.ABC):
    """Hər mənbə üçün ortaq skelet. Bax: sənəd §3 Architecture, §7 Scheduling.

    Alt-siniflər `source_name` və `fetch_leads()` təyin edir. Bir scraper
    xəta versə də scheduler digərlərini dayandırmır (§3: "biri sınarsa
    digərləri davam edir").
    """

    source_name: str = "unknown"
    base_url: str = ""
    min_delay_s: float = 2.0
    max_delay_s: float = 5.0

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=20.0,
            follow_redirects=True,
        )

    def polite_sleep(self) -> None:
        """Sorğular arası jitter. Sənəd §7: "2-5 saniyə random jitter"."""
        time.sleep(random.uniform(self.min_delay_s, self.max_delay_s))

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    def get(self, url: str, **kwargs) -> httpx.Response:
        resp = self._client.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    @abc.abstractmethod
    def fetch_leads(self) -> list[RawLead]:
        """Bu mənbədən tapılan bütün RawLead-ləri qaytarır."""
        raise NotImplementedError

    def run(self) -> list[RawLead]:
        """Scheduler-in çağırdığı giriş nöqtəsi — xətanı udur, loglayır."""
        try:
            leads = self.fetch_leads()
            log.info("scraper_completed", source=self.source_name, count=len(leads))
            return leads
        except Exception:
            log.exception("scraper_failed", source=self.source_name)
            return []
        finally:
            self._client.close()
