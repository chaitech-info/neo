import httpx
import pytest

from app.scrapers import robots
from app.scrapers.news import FedScraper
from app.scrapers.robots import RobotsDisallowed, check_allowed, parse_robots

FED = """
User-agent: *
Allow: /*.css
Disallow: /*?
Disallow: /az/axtar
"""


def test_wildcard_rules_like_fed_az():
    rules = parse_robots(FED)

    assert rules.allows("/az/tenderler/2")
    assert not rules.allows("/az/tenderler?page=2")
    assert not rules.allows("/az/axtar/abc")
    assert rules.allows("/style.css")


def test_specific_agent_group_overrides_star_and_longest_match_wins():
    text = """
User-agent: *
Disallow: /

User-agent: NeoLeadBot
Disallow: /private
Allow: /private/tenders
"""
    rules = parse_robots(text)

    assert rules.allows("/news")
    assert not rules.allows("/private/x")
    assert rules.allows("/private/tenders/1")


def test_disallow_all_and_empty_disallow_and_dollar_anchor():
    assert not parse_robots("User-agent: *\nDisallow: /").allows("/tender-elanlari/")
    assert parse_robots("User-agent: *\nDisallow:").allows("/anything")
    rules = parse_robots("User-agent: *\nDisallow: /*.pdf$")
    assert not rules.allows("/docs/a.pdf")
    assert rules.allows("/docs/a.pdf?x=1")


def _client(status: int, body: str = "", calls=None) -> httpx.Client:
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(status, text=body)
        return httpx.Response(200, text="page")
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def clear_cache():
    robots._cache.clear()
    yield
    robots._cache.clear()


def test_missing_robots_allows_but_server_error_blocks_without_caching():
    check_allowed("https://a.test/x", _client(404))  # icazə var

    with pytest.raises(RobotsDisallowed):
        check_allowed("https://b.test/x", _client(503))
    assert "https://b.test" not in robots._cache  # 5xx keşlənmir


def test_scraper_get_does_not_request_disallowed_url():
    calls = []
    scraper = FedScraper(client=_client(200, FED, calls))

    assert scraper.get("https://fed.az/az/tenderler/2").text == "page"
    with pytest.raises(RobotsDisallowed):
        scraper.get("https://fed.az/az/tenderler", params={"page": 2})

    assert calls == ["https://fed.az/robots.txt", "https://fed.az/az/tenderler/2"]  # robots 1 dəfə, keşdən
