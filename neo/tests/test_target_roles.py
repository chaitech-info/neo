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
