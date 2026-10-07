from app.scrapers.linkedin_companies import APIFY_RUN_SYNC_URL, Company, LinkedInCompaniesScraper

SOCAR = Company("SOCAR", "https://www.linkedin.com/company/socar")
BP = Company("bp", "https://www.linkedin.com/company/bp", az_only=True)
GHOST = Company("Yanlış slug", "https://www.linkedin.com/company/does-not-exist")


def _post(url, content, company_url, posted="2026-10-05T08:00:00.000Z"):
    return {"type": "post", "linkedinUrl": url, "content": content,
            "author": {"name": "X", "linkedinUrl": company_url}, "postedAt": {"date": posted}}


ITEMS = [
    _post("https://li/p/1", "SOCAR Sumqayıtda yeni zavod açır, 300 iş yeri yaradılacaq", SOCAR.url),
    _post("https://li/p/2", "Bayram təbrikləri bütün əməkdaşlarımıza", SOCAR.url),
    _post("https://li/p/3", "bp opens a new factory in Houston with 500 jobs", BP.url + "/"),       # qlobal, Azərbaycan yox
    _post("https://li/p/4", "bp starts new factory plant at Baku terminal, 400 jobs", BP.url),
    {"type": "comment", "linkedinUrl": "https://li/c/1", "content": "zavod", "author": {}},
    _post("https://li/p/5", "", SOCAR.url),
]


def _scraper(token="tok"):
    scraper = LinkedInCompaniesScraper(api_token=token)
    scraper.watchlist = [SOCAR, BP, GHOST]
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        return type("R", (), {"json": lambda self: ITEMS})()

    scraper.post = post
    scraper.calls = calls
    return scraper


def test_posts_are_filtered_by_keywords_and_azerbaijan_gate_for_global_companies():
    scraper = _scraper()
    leads = scraper.fetch_leads()

    assert [l.source_url for l in leads] == ["https://li/p/1", "https://li/p/4"]   # bayram: açar söz yox; Houston: Azərbaycan yox
    assert leads[0].entity_name == "SOCAR" and leads[0].source == "linkedin_companies"
    assert leads[0].posted_at.year == 2026 and "Şirkət səhifəsi: SOCAR" in leads[0].raw_text
    url, kw = scraper.calls[0]
    assert url == APIFY_RUN_SYNC_URL and kw["params"] == {"token": "tok"}
    assert kw["json"]["targetUrls"] == [SOCAR.url, BP.url, GHOST.url] and kw["json"]["maxPosts"] == 10


def test_without_token_nothing_is_called_or_spent():
    scraper = _scraper(token=None)
    scraper.api_token = None
    assert scraper.fetch_leads() == []
    assert scraper.calls == []
