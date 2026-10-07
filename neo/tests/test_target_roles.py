from app.scrapers import BossAzScraper
from app.scrapers.employer_signal import matches_target_role


def test_matches_target_role():
    assert matches_target_role("Camp Manager") == "camp manager"
    assert matches_target_role("Senior HSE Manager (Baku)") == "hse manager"
    assert matches_target_role("Sous Chef") == "chef"
    assert matches_target_role("Satınalma meneceri") == "satinalma meneceri"
    assert matches_target_role("Facility Operations Manager") == "facility operations manager"
    assert matches_target_role("Mechef Assistant") is None
    assert matches_target_role("Facility Coordinator") is None
    assert matches_target_role("Satış meneceri") is None


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


def test_role_collector_excludes_meal_employers_by_name_even_when_url_differs():
    from app.scrapers.employers import RoleCollector

    roles = RoleCollector("jobsearch", "jobsearch.az")
    roles.add("https://j/1", "Camp Manager", "Unimetal MMC")
    roles.add("https://j/2", "HSE Manager", "Gilan Holding")
    roles.add("https://j/3", "Operator", "Radius MMC")            # hədəf vəzifə deyil
    roles.add("https://j/4", "Chef", "Pizza Mizza")               # öz mətbəxi
    roles.add("https://j/5", "Site Manager", "Azvak")             # iş saytı

    # nahar lead-i domen URL-i ilə gəlib (https://unimetal.az) — URL ilə yox, ad ilə çıxarılmalıdır
    leads = roles.leads(exclude_names=['"Unimetal" MMC'], exclude_urls=["https://unimetal.az"])
    assert [l.entity_name for l in leads] == ["Gilan Holding"]
    assert leads[0].source == "jobsearch" and "HSE Manager axtarır (jobsearch.az)" in leads[0].title


def test_jobsearch_adds_role_leads_without_fetching_details(monkeypatch):
    from datetime import datetime

    from app.scrapers.jobsearch import JobSearchScraper

    today = datetime.utcnow().strftime("%Y-%m-%dT00:00:00+04:00")
    listing = {"items": [
        {"slug": "a-1", "title": "Camp Manager", "created_at": today, "company": {"title": "Caspian Drilling"}},
        {"slug": "a-2", "title": "Facility Manager", "created_at": today, "company": {"title": "Caspian Drilling"}},
        {"slug": "b-3", "title": "Operator", "created_at": today, "company": {"title": "Azersun Holding"}},
        {"slug": "c-4", "title": "HSE Manager", "created_at": today, "company": {"title": "X"}, "hide_company": True},
        {"slug": "d-5", "title": "Site Manager", "created_at": today, "company": {"title": "Azersun Holding"}},
    ], "next": None}

    class _R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake_get(self, url, **kw):
        slug = url.rsplit("/", 1)[-1]
        return _R(listing if "page=" in url else {"text": "Nahar şirkət tərəfindən təmin olunur." if slug == "b-3" else "", "phone": None})

    monkeypatch.setattr(JobSearchScraper, "get", fake_get)
    monkeypatch.setattr(JobSearchScraper, "polite_sleep", lambda self: None)
    monkeypatch.setattr("app.scrapers.jobsearch.seen_without_signal", lambda *a: set())
    monkeypatch.setattr("app.scrapers.jobsearch.remember", lambda *a: None)
    leads = JobSearchScraper().fetch_leads()

    # Azersun: nahar siqnalı (operator elanı) → bir lead; onun "Site Manager" rolu təkrar lead yaratmır.
    # Caspian Drilling: yalnız rol siqnalı. Gizli şirkət (hide_company) atılır.
    assert sorted(l.entity_name for l in leads) == ["Azersun Holding", "Caspian Drilling"]
    role = next(l for l in leads if l.entity_name == "Caspian Drilling")
    assert "Camp Manager" in role.title and "Facility Manager" in role.title
    assert "nahar verir" in next(l for l in leads if l.entity_name == "Azersun Holding").title


def test_jobboard_adds_role_lead_for_employer_without_meal_signal(monkeypatch):
    import dataclasses

    from app.scrapers import jobboards

    site = dataclasses.replace(jobboards.HELLOJOB, pages=1, key="hellojob_roles")
    listing = '<a href="/vakansiya/camp-11">x</a><a href="/vakansiya/op-12">y</a>'
    pages = {"camp-11": ("Gilan Holding", "Camp Manager", "Tələblər: təcrübə."),
             "op-12": ("Ofis MMC", "Operator", "Nahar fasiləsi 13:00.")}

    class _R:
        def __init__(self, text):
            self.text = text

    def fake_get(self, url, **kw):
        if "page=" in url:
            return _R(listing)
        name, title, text = pages[url.rsplit("/", 1)[-1]]
        return _R(f'<div class="company__row"><a class="vacancies__category">{name}</a>'
                  f'<h2 class="vacancies__subTitle">{title}</h2></div><div class="company__text">{text}</div>')

    monkeypatch.setattr("app.scrapers.jobboards.seen_without_signal", lambda *a: set())
    monkeypatch.setattr("app.scrapers.jobboards.remember", lambda *a: None)
    S = jobboards.jobboard_scraper(site)
    monkeypatch.setattr(S, "get", fake_get)
    monkeypatch.setattr(S, "polite_sleep", lambda self: None)
    leads = S().fetch_leads()

    assert [(l.entity_name, l.source) for l in leads] == [("Gilan Holding", "hellojob_roles")]
    assert "Camp Manager axtarır (hellojob.az)" in leads[0].title


def test_hotel_brands_without_hotel_word_have_own_kitchen():
    from app.scrapers.employer_signal import has_own_kitchen

    assert has_own_kitchen("Fairmont Baku")
    assert has_own_kitchen("Hilton Baku")
    assert not has_own_kitchen("Gilan Holding")
