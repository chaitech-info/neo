from app.scrapers import BossAzScraper


def _node(id_, name, position, description, email=None, phones=None, created="2026-09-27T10:00:00+04:00"):
    return {
        "id": id_, "name": name, "positionName": position, "description": description,
        "jobDescription": None, "requirements": None, "responsibilities": None,
        "email": email, "phones": phones, "location": "Bakı", "createdAt": None, "bumpedAt": created,
    }


PAGE_1 = [
    _node(1, "\"Unimetal\" MMC", "Laborant", "<p>Nahar şirkət tərəfindən təmin olunur</p>",
          email="hr@unimetal.az", phones=["+994 50 111 22 33"]),
    _node(2, "Company", "Sürücü", "Nahar yeməyi ilə təmin olunur", email="x@gmail.com"),
    _node(3, "Sağlam Catering MMC", "Xadimə", "Naharla təmin olunur", email="office@saglamcatering.az"),
    _node(4, "SUPREME HOTEL", "Aşpaz", "Naharla təmin olunur", email="hr@supremehotel.com"),
]
PAGE_2 = [
    _node(5, "\"Unimetal\" MMC", "Fəhlə", "NAHAR ŞİRKƏT TƏRƏFİNDƏN", email="hr@unimetal.az",
          created="2026-09-26T09:00:00+04:00"),
    _node(6, "Radius MMC", "Satıcı", "Maaş 800 AZN, rəsmi əmək müqaviləsi", email="hr@radius.az"),
    _node(7, "AzProtein Foods Group MMC", "Fəhlə", "Nahar şirkət tərəfindən", email="azprotein@gmail.com"),
]


def _scraper(pages):
    scraper = BossAzScraper()
    scraper.polite_sleep = lambda: None
    calls = []

    def post(url, **kw):
        calls.append(kw["json"]["variables"]["after"])
        i = len(calls) - 1
        payload = {"data": {"vacancies": {
            "nodes": pages[i],
            "pageInfo": {"endCursor": f"c{i}", "hasNextPage": i + 1 < len(pages)},
        }}}
        return type("R", (), {"json": lambda self: payload})()

    scraper.post = post
    return scraper, calls


def test_one_lead_per_employer_with_all_positions_and_contacts():
    scraper, calls = _scraper([PAGE_1, PAGE_2])

    leads = {lead.entity_name: lead for lead in scraper.fetch_leads()}

    assert calls == [None, "c0"]  # cursor pagination
    assert set(leads) == {"Unimetal MMC", "AzProtein Foods Group MMC"}
    unimetal = leads["Unimetal MMC"]
    assert unimetal.source == "boss"
    assert unimetal.source_url == "https://unimetal.az"
    assert unimetal.contact_info.email == "hr@unimetal.az"
    assert unimetal.contact_info.phone == "+994 50 111 22 33"
    assert "(2)" in unimetal.raw_text and "Fəhlə" in unimetal.raw_text and "Laborant" in unimetal.raw_text
    assert unimetal.raw_text.startswith("[SİQNAL")
    assert unimetal.posted_at.isoformat() == "2026-09-27T06:00:00"  # newest ad, UTC


def test_skips_anonymous_competitor_own_kitchen_and_no_meal():
    scraper, _ = _scraper([PAGE_1, PAGE_2])

    names = {lead.entity_name for lead in scraper.fetch_leads()}

    assert not any(n in names for n in ("Company", "Sağlam Catering MMC", "SUPREME HOTEL", "Radius MMC"))


def test_free_email_employer_falls_back_to_name_url():
    scraper, _ = _scraper([PAGE_2])

    lead = next(l for l in scraper.fetch_leads() if l.entity_name == "AzProtein Foods Group MMC")

    assert "linkedin.com/search/results/companies" in lead.source_url
    assert lead.contact_info.email == "azprotein@gmail.com"
    assert lead.contact_info.website is None
