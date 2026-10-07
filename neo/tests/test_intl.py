from datetime import date

from app.scrapers.intl import IntlTenderScraper

UNGM_PAGE = '<input name="__RequestVerificationToken" type="hidden" value="tok123" />'


def _ungm_row(id_, title, deadline, published, agency, kind):
    return f"""
<div role="row" class="tableRow dataRow notice-table">
  <div class="tableCell resultOptions"><a href="javascript:void(0);">Save</a></div>
  <div class="tableCell resultTitle"><div><span class="ungm-title ungm-title--small">{title}</span>
    <a href="/Public/Notice/{id_}"><span>Open in a new window</span></a></div></div>
  <div class="tableCell resultInfo1 deadline">{deadline}
     (GMT 2.00) 9.1482</div>
  <div class="tableCell">{published}</div>
  <div class="tableCell resultAgency">{agency}</div>
  <div class="tableCell">{kind}</div>
  <div class="tableCell resultInfo1">REF-{id_}</div>
  <div class="tableCell">Azerbaijan</div>
</div>"""


UNGM_RESULTS = "".join([
    _ungm_row(1, "Arrangement of Teambuilding Workshop", "16-Oct-2026 11:00", "06-Oct-2026", "FAO", "Request for quotation"),
    _ungm_row(2, "Catering for conference", "01-Oct-2026 11:00", "20-Sep-2026", "UNDP", "Invitation to bid"),  # bitib
    _ungm_row(3, "Supply of laboratory equipment", "20-Oct-2026 11:00", "06-Oct-2026", "ITC", "Invitation to bid"),
])


def _ebrd_row(title, kind, closing, state, published="19/08/2026 14:42 UK Time", project="[Ganja Street Lighting, 52419, Azerbaijan, ]"):
    return (f'<tr><td>{title}</td><td>{kind}</td><td>x</td><td>{published}</td><td>{closing}</td><td>{state}</td>'
            f'<td>19/08/2026</td><td>1</td><td>2</td><td>{project}</td>'
            f'<td><a href="viewNotice.html?displayNoticeId={abs(hash(title)) % 1000}">v</a></td></tr>')


EBRD_PAGE = "<table>" + "".join([
    _ebrd_row("Azerbaijan: Catering for stakeholder event", "Invitation For Tenders Single", "23/10/2026 11:00 UK Time", "Open"),
    _ebrd_row("Azerbaijan: Old catering tender", "Invitation For Tenders Single", "01/09/2026 11:00 UK Time", "Closed"),
    _ebrd_row("Azerbaijan: Award for catering", "Contract  Award  Notice", "N/A", "Information Only"),
    _ebrd_row("Moldova: Catering network", "Invitation For Tenders Single", "23/10/2026 11:00 UK Time", "Open"),
]) + "</table>"


class FakeResponse:
    def __init__(self, text):
        self.text = text


def _scraper(ungm_ok=True, ebrd_ok=True):
    scraper = IntlTenderScraper()
    scraper.today = lambda: date(2026, 10, 7)
    scraper.polite_sleep = lambda: None
    posts = []

    def get(url, **kw):
        if "ungm" in url:
            if not ungm_ok:
                raise RuntimeError("ungm down")
            return FakeResponse(UNGM_PAGE)
        if not ebrd_ok:
            raise RuntimeError("ebrd down")
        return FakeResponse(EBRD_PAGE)

    def post(url, **kw):
        posts.append(kw)
        return FakeResponse(UNGM_RESULTS)

    scraper.get, scraper.post, scraper.posts = get, post, posts
    return scraper


def test_ungm_search_uses_token_azerbaijan_filter_and_skips_expired():
    scraper = _scraper()
    notices = scraper.fetch_ungm()

    assert scraper.posts[0]["headers"]["RequestVerificationToken"] == "tok123"
    assert scraper.posts[0]["json"]["Countries"] == [2306]
    assert [n["url"] for n in notices] == [
        "https://www.ungm.org/Public/Notice/1", "https://www.ungm.org/Public/Notice/3"]
    first = notices[0]
    assert first["title"] == "Arrangement of Teambuilding Workshop"  # "Open in a new window" yox
    assert first["org"] == "FAO" and first["deadline"] == date(2026, 10, 16)


def test_ebrd_keeps_only_open_azerbaijan_notices():
    notices = _scraper().fetch_ebrd()
    assert [n["title"] for n in notices] == ["Catering for stakeholder event"]
    assert notices[0]["deadline"] == date(2026, 10, 23)
    assert notices[0]["org"] == "Ganja Street Lighting"


def test_fetch_leads_keyword_filtered_with_expected_fields():
    leads = _scraper().fetch_leads()
    titles = {lead.title for lead in leads}
    assert titles == {"Arrangement of Teambuilding Workshop", "Catering for stakeholder event"}
    lead = next(l for l in leads if l.entity_name == "FAO")
    assert lead.source.value == "intl_tender" and lead.category.value == "tender"
    assert "Son tarix: 16.10.2026" in lead.raw_text and "UNGM" in lead.raw_text


def test_one_portal_failing_does_not_lose_the_other():
    leads = _scraper(ungm_ok=False).fetch_leads()
    assert [l.title for l in leads] == ["Catering for stakeholder event"]
    leads = _scraper(ebrd_ok=False).fetch_leads()
    assert [l.title for l in leads] == ["Arrangement of Teambuilding Workshop"]
