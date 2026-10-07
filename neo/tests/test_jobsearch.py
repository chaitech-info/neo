from app.scrapers import BossAzScraper, JobSearchScraper
from app.scrapers.employer_signal import matches_target_role
from app.scrapers.jobsearch import parse_listing


def _item(href, position, company):
    return (f'<div class="list__item"><a class="list__item__text d-block" href="{href}">'
            f'<h3 class="list__item__title">\n  {position}\n </h3>\n  {company}\n </a></div>')


LISTING = "".join([
    _item("/vacancies/a-camp-manager-1", "Camp Manager", "Caspian Drilling"),
    _item("/vacancies/b-facility-2", "Facility Manager", "Caspian Drilling"),
    _item("/vacancies/c-hse-3", "HSE Manager", "Gilan Holding"),
    _item("/vacancies/d-sales-4", "Satış meneceri", "Radius MMC"),
    _item("/vacancies/e-chef-5", "Chef", "Pizza Mizza"),               # öz mətbəxi var
    _item("/vacancies/f-chef-6", "Aşpaz", "Sağlam Catering MMC"),      # rəqib
    _item("/vacancies/g-site-7", "Site Manager", "Company"),            # anonim
    _item("/vacancies/h-proc-8", "Procurement Manager", "Azvak"),       # iş saytı
])


def test_matches_target_role():
    assert matches_target_role("Camp Manager") == "camp manager"
    assert matches_target_role("Senior HSE Manager (Baku)") == "hse manager"
    assert matches_target_role("Sous Chef") == "chef"
    assert matches_target_role("Satınalma meneceri") == "satinalma meneceri"
    assert matches_target_role("Facility Operations Manager") == "facility operations manager"
    assert matches_target_role("Mechef Assistant") is None
    assert matches_target_role("Facility Coordinator") is None
    assert matches_target_role("Satış meneceri") is None


def test_parse_listing_splits_position_and_company():
    items = parse_listing(LISTING)
    assert items[0] == ("https://jobsearch.az/vacancies/a-camp-manager-1", "Camp Manager", "Caspian Drilling")
    assert len(items) == 8


def test_jobsearch_groups_by_employer_and_drops_noise():
    scraper = JobSearchScraper()
    scraper.polite_sleep = lambda: None
    scraper.get = lambda url, **kw: type("R", (), {"text": LISTING})()
    leads = scraper.fetch_leads()

    assert [l.entity_name for l in leads] == ["Caspian Drilling", "Gilan Holding"]
    drilling = leads[0]
    assert drilling.source.value == "jobsearch"
    assert drilling.source_url.startswith("https://www.linkedin.com/search/results/companies/")  # boss/LinkedIn ilə eyni dedup açarı
    assert "Camp Manager" in drilling.title and "Facility Manager" in drilling.title
    assert "(2)" in drilling.raw_text


def test_boss_adds_role_leads_but_not_for_employers_already_flagged_by_meal_signal():
    def node(id_, name, position, description):
        return {"id": id_, "name": name, "positionName": position, "description": description,
                "jobDescription": None, "requirements": None, "responsibilities": None,
                "email": None, "phones": None, "location": "Bakı", "createdAt": None, "bumpedAt": None}

    nodes = [
        node(1, "Unimetal MMC", "Camp Manager", "Nahar şirkət tərəfindən təmin olunur"),
        node(2, "Gilan Holding", "HSE Manager", "Tələblər: təcrübə"),
        node(3, "Radius MMC", "Satıcı", "Maaş 800 AZN"),
    ]
    scraper = BossAzScraper()
    scraper.polite_sleep = lambda: None
    payload = {"data": {"vacancies": {"nodes": nodes, "pageInfo": {"endCursor": "c", "hasNextPage": False}}}}
    scraper.post = lambda url, **kw: type("R", (), {"json": lambda self: payload})()

    leads = scraper.fetch_leads()

    assert [l.entity_name for l in leads] == ["Unimetal MMC", "Gilan Holding"]
    assert "nahar verir" in leads[0].title          # nahar siqnalı, təkrar rol lead-i yox
    assert "HSE Manager axtarır" in leads[1].title
