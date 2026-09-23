from datetime import date

from app.scrapers import BpEdpScraper

HTML = """
<table><tbody id="table-body-tender-list">
<tr><td>1</td><td class="td-unique-bid">EDP-5-10/15/2026</td><td><b>Provision of Offshore Catering Services</b></td>
    <td>Meals and housekeeping for platform crews</td><td>15/10/2026</td></tr>
<tr><td>2</td><td class="td-unique-bid">EDP-4-9/30/2026</td><td><b>Provision of Waste Transportation Services</b></td>
    <td>Collection and transportation of waste</td><td>30/09/2026</td></tr>
<tr><td>3</td><td class="td-unique-bid">EDP-3-6/1/2026</td><td><b>Leak prevention services</b></td>
    <td>Event-driven inspection of catering-area pipes</td><td>01/06/2026</td></tr>
<tr><td>4</td><td class="td-unique-bid">EDP-2-10/1/2026</td><td><b>Leak prevention services</b></td>
    <td>Inspection of pipelines</td><td>01/10/2026</td></tr>
</tbody></table>
"""


def test_bp_edp_filters_deadline_and_keywords_in_title_or_scope():
    scraper = BpEdpScraper()
    scraper.today = lambda: date(2026, 9, 23)
    sent = {}
    scraper.post = lambda url, **kw: sent.update(kw) or type("R", (), {"text": HTML})()

    leads = scraper.fetch_leads()

    # 2: açar söz yoxdur; 3: son tarix keçib; 4: "prevention" ≠ "event"
    assert [lead.source_url for lead in leads] == ["https://edp.az/Tender/Announcements#EDP-5-10/15/2026"]
    assert "Son tarix: 15.10.2026" in leads[0].raw_text
    assert "platform crews" in leads[0].raw_text
    assert sent["data"] == {"count": 20, "page": 1}
