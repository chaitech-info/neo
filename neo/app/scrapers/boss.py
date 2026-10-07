from __future__ import annotations

import re

import structlog
from bs4 import BeautifulSoup

from app.models import ContactInfo, LeadCategory, RawLead
from app.scrapers.base import BaseScraper, parse_datetime
from app.scrapers.employer_signal import (
    EMAIL_RE,
    SIGNAL_PREFIX,
    corporate_domain,
    employer_url,
    fold,
    has_own_kitchen,
    is_competitor,
    mentions_company_meal,
)
from app.scrapers.jobsearch import collect_role_employer, role_lead

log = structlog.get_logger()

# boss.az — Azərbaycanın ən böyük iş elanı saytı (Next.js + GraphQL, 2026-09-27
# brauzerlə yoxlanıb; robots.txt `Allow: /`, qadağan yalnız admin/login/profile).
# Açar sözlə axtarış (searchTerm) yalnız vəzifə adında işləyir — "nahar" 1 nəticə
# verir. Ona görə bütün aktiv elanlar (~660, 24-lük səhifələr) oxunur və təsvirdə
# işçilərə nahar verildiyi yazılanların işəgötürəni lead olur.
GRAPHQL_URL = "https://boss.az/graphql"
VACANCY_URL = "https://boss.az/vacancies/{id}"
PAGE_SIZE = 24

QUERY = """
query Vacancies($filter: AdFilter, $after: String, $first: Int, $sortBy: AdSortByEnum) {
  vacancies(filter: $filter, after: $after, first: $first, sortBy: $sortBy) {
    nodes {
      id name positionName description jobDescription requirements responsibilities
      email phones location createdAt bumpedAt
    }
    pageInfo { endCursor hasNextPage }
  }
}
"""

# İşəgötürən adı gizlədilib — kimə zəng edəcəyimiz bəlli deyil.
ANONYMOUS_NAMES = {"company", "şirkət", "sirket", "gizli", "anonim", "məxfi", "mexfi"}

SAMPLE_CHARS = 2500


def _plain(node: dict) -> str:
    html = " ".join(
        node.get(k) or "" for k in ("description", "jobDescription", "requirements", "responsibilities")
    )
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True) if html.strip() else ""


def _first_phone(phones) -> str | None:
    if isinstance(phones, list):
        return str(phones[0]) if phones else None
    return str(phones) if phones else None


class BossAzScraper(BaseScraper):
    """boss.az vakansiyalarından "işçilərinə nahar verən şirkət" lead-ləri.

    Hər şirkət bir lead-dir (açar: email domeni və ya ad, LinkedIn ilə eyni —
    bax employer_signal.employer_url); nahar qeyd olunan bütün vakansiyaları
    lead-in mətnində sadalanır — vəzifələrin sayı və növü (fəhlə, operator,
    anbar) şirkətin ölçüsü haqqında AI-yə siqnal verir.
    """

    source_name = "boss"
    base_url = "https://boss.az"
    min_delay_s = 1.5
    max_delay_s = 3.0
    max_pages = 45

    def fetch_leads(self) -> list[RawLead]:
        employers: dict[str, dict] = {}
        role_employers: dict[str, dict] = {}  # hədəf vəzifəyə (Facility/Camp Manager...) işçi axtaranlar
        after = None
        scanned = 0
        for page in range(self.max_pages):
            if page:
                self.polite_sleep()
            resp = self.post(
                GRAPHQL_URL,
                json={
                    "operationName": "Vacancies",
                    "query": QUERY,
                    "variables": {"filter": {}, "first": PAGE_SIZE, "after": after, "sortBy": "RECOMMENDED"},
                },
            )
            vacancies = resp.json()["data"]["vacancies"]
            for node in vacancies["nodes"]:
                scanned += 1
                self._collect(node, employers)
                collect_role_employer(
                    VACANCY_URL.format(id=node["id"]), node.get("positionName") or "", node.get("name") or "",
                    role_employers,
                )
            if not vacancies["pageInfo"]["hasNextPage"]:
                break
            after = vacancies["pageInfo"]["endCursor"]

        leads = [self._to_lead(e) for e in employers.values()]
        # Nahar siqnalı olan şirkət artıq güclü lead-dir (eyni URL = eyni dedup açarı) — təkrar yox.
        role_leads = [role_lead(e, source="boss", board="boss.az")
                      for url, e in role_employers.items() if url not in employers]
        log.info("boss_scanned", scanned=scanned, employers=len(leads), role_employers=len(role_leads))
        return leads + role_leads

    @staticmethod
    def _collect(node: dict, employers: dict[str, dict]) -> None:
        name = re.sub(r"[\"“”«»„]", "", node.get("name") or "").strip()
        text = _plain(node)
        if not name or fold(name) in ANONYMOUS_NAMES or not mentions_company_meal(text):
            return
        if is_competitor(name, text) or has_own_kitchen(name):
            return

        contact_text = " ".join(filter(None, [node.get("email"), text]))
        domain = corporate_domain(contact_text)
        url = employer_url(domain, name)
        emails = [m.group(0) for m in EMAIL_RE.finditer(contact_text)]
        email = next((e for e in emails if domain and e.lower().endswith("@" + domain)), None)

        entry = employers.setdefault(url, {
            "name": name, "url": url, "domain": domain, "positions": [], "vacancy_urls": [],
            "email": None, "phone": None, "location": None, "posted_at": None, "sample": text,
        })
        entry["positions"].append(node.get("positionName") or "")
        entry["vacancy_urls"].append(VACANCY_URL.format(id=node["id"]))
        entry["email"] = entry["email"] or email or (emails[0] if emails else None)
        entry["phone"] = entry["phone"] or _first_phone(node.get("phones"))
        entry["location"] = entry["location"] or node.get("location")
        # createdAt ictimai API-də null gəlir; bumpedAt = elanın son yenilənmə vaxtı.
        posted = parse_datetime(node.get("createdAt") or node.get("bumpedAt"))
        if posted and (entry["posted_at"] is None or posted > entry["posted_at"]):
            entry["posted_at"] = posted

    @staticmethod
    def _to_lead(e: dict) -> RawLead:
        positions = sorted({p for p in e["positions"] if p})
        raw_text = (
            f"{SIGNAL_PREFIX}\n"
            f"Şirkət: {e['name']}\n"
            f"boss.az-da nahar qeyd olunan aktiv vakansiyalar ({len(e['vacancy_urls'])}): "
            f"{', '.join(positions)}\n"
            f"Yer: {e['location'] or 'göstərilməyib'}\n"
            f"Elan: {e['vacancy_urls'][0]}\n\n{e['sample'][:SAMPLE_CHARS]}"
        )
        return RawLead(
            source="boss",
            source_url=e["url"],
            title=f"{e['name']} — işçilərinə nahar verir ({len(e['vacancy_urls'])} vakansiya, boss.az)",
            raw_text=raw_text,
            entity_name=e["name"],
            contact_info=ContactInfo(
                email=e["email"],
                phone=e["phone"],
                website=f"https://{e['domain']}" if e["domain"] else None,
            ),
            category=LeadCategory.corporate,
            location=(e["location"] or "Azərbaycan")[:128],
            posted_at=e["posted_at"],
        )
