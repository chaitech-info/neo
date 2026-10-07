from app.scrapers.corporate import CorporateTenderScraper, SiteConfig, parse_listing_date


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def test_parse_listing_date_formats_to_utc():
    assert parse_listing_date("18.09.2026").isoformat() == "2026-09-17T20:00:00"
    assert parse_listing_date("Dərc: 17 Sentyabr 2026").isoformat() == "2026-09-16T20:00:00"
    assert parse_listing_date("23 September, 2026").isoformat() == "2026-09-22T20:00:00"
    assert parse_listing_date("tarix yoxdur") is None


TABLE_HTML = """
<table>
<tr><th>Tenderin adı</th><th>Başlama</th><th>Bitmə</th></tr>
<tr class="clickable" href="/docs/sus.pdf"><td>Bağlanış MƏRASİMİNİN təşkili</td><td>15.09.2026</td><td>07.10.2026</td></tr>
<tr class="clickable" href="/docs/konf.pdf"><td>Konfransın təşkili</td><td>21.08.2026</td><td>14.09.2026</td></tr>
<tr class="clickable" href="/docs/today.pdf"><td>Festival üçün bufet</td><td>01.09.2026</td><td>23.09.2026</td></tr>
<tr class="clickable" href="/docs/fw.pdf"><td>Firewall avadanlığı</td><td>28.08.2026</td><td>21.09.2026</td></tr>
</table>
<div class="m" data-id="186"><a href="javascript:void(0)"><p>Konfrans üçün iaşə xidməti</p></a></div>
"""


def _scraper(sites):
    from datetime import date

    scraper = CorporateTenderScraper()
    scraper.today = lambda: date(2026, 9, 23)
    scraper.sites = sites
    scraper.get = lambda url: FakeResponse(TABLE_HTML)
    scraper.polite_sleep = lambda: None
    return scraper


def test_table_site_filters_keywords_expired_and_reads_dates():
    site = SiteConfig(
        key="t", org="Agentlik", listing_url="https://x.gov.az/tenders",
        item_selector="tr.clickable", title_selector="td:nth-of-type(1)",
        date_selector="td:nth-of-type(2)", deadline_selector="td:nth-of-type(3)",
    )
    leads = _scraper([site]).fetch_leads()

    # Firewall: açar söz yoxdur; Konfrans: son tarix 14.09 keçib; Festival: son gün bu gündür
    assert [lead.source_url for lead in leads] == ["https://x.gov.az/docs/sus.pdf", "https://x.gov.az/docs/today.pdf"]
    lead = leads[0]
    assert lead.source_url == "https://x.gov.az/docs/sus.pdf"
    assert lead.entity_name == "Agentlik"
    assert lead.posted_at.isoformat() == "2026-09-14T20:00:00"
    assert "Son tarix: 07.10.2026" in lead.raw_text


def test_items_without_link_get_stable_unique_url_and_failing_site_is_isolated():
    ok = SiteConfig(key="m", org="Şirkət", listing_url="https://y.az/elanlar",
                    item_selector="div.m", title_selector="p", link_selector="a", id_attr="data-id")
    broken = SiteConfig(key="b", org="X", listing_url="https://broken.az", item_selector="div")
    scraper = _scraper([broken, ok])
    real_get = scraper.get
    scraper.get = lambda url: (_ for _ in ()).throw(RuntimeError("down")) if "broken" in url else real_get(url)

    leads = scraper.fetch_leads()

    assert [lead.source_url for lead in leads] == ["https://y.az/elanlar#186"]


def test_title_from_attribute_and_slash_separated_date():
    from datetime import date

    html = """
<a href="/az/procurement/341"><div><img alt="Sərfiyyat materiallarının satın alınması" src="x.jpg">
<div>Satınalma və hərraclar Sərfiyyat materiallarının satın alınması 5 OKTYABR / 2026 / 11:04</div></div></a>
<a href="/az/procurement/340"><div><img alt="Banket xidmətinin satın alınması" src="y.jpg">
<div>Satınalma və hərraclar Banket xidmətinin satın alınması 1 OKTYABR / 2026 / 10:00</div></div></a>
"""
    site = SiteConfig(key="m", org="Metro", listing_url="https://m.gov.az/az/procurement",
                      item_selector="a[href*='/az/procurement/']", title_selector="img", title_attr="alt")
    scraper = _scraper([site])
    scraper.get = lambda url: FakeResponse(html)
    leads = scraper.fetch_leads()

    assert [l.title for l in leads] == ["Banket xidmətinin satın alınması"]
    assert leads[0].source_url == "https://m.gov.az/az/procurement/340"
    assert parse_listing_date("5 OKTYABR / 2026 / 11:04").isoformat() == "2026-10-04T20:00:00"
