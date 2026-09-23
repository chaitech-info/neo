from datetime import date

from app.scrapers import WorldBankScraper

PAYLOAD = {"procnotices": [
    {"id": "OP1", "notice_type": "Invitation for Bids", "bid_description": "Catering services for project trainings",
     "project_name": "Employment Support Project", "submission_date": "2026-10-10T00:00:00Z",
     "noticedate": "15-Sep-2026", "notice_text": "<p>Lunch and coffee breaks for 200 participants</p>"},
    {"id": "OP2", "notice_type": "Contract Award", "bid_description": "Catering services (awarded)",
     "submission_date": "2026-10-10T00:00:00Z", "noticedate": "15-Sep-2026"},
    {"id": "OP3", "notice_type": "Request for Expression of Interest", "bid_description": "Conference organization",
     "submission_date": "2026-08-18T00:00:00Z", "noticedate": "29-Jul-2026"},
    {"id": "OP4", "notice_type": "Invitation for Bids", "bid_description": "Server equipment",
     "submission_date": "2026-12-01T00:00:00Z", "noticedate": "20-Sep-2026"},
]}


def test_worldbank_keeps_only_open_relevant_notices():
    scraper = WorldBankScraper()
    scraper.today = lambda: date(2026, 9, 23)
    scraper.get = lambda url, **kw: type("R", (), {"json": lambda self: PAYLOAD})()

    leads = scraper.fetch_leads()

    # OP2: bağlanıb; OP3: son tarix keçib; OP4: açar söz yoxdur
    assert [lead.source_url for lead in leads] == [
        "https://projects.worldbank.org/en/projects-operations/procurement-detail/OP1"
    ]
    lead = leads[0]
    assert lead.posted_at.isoformat() == "2026-09-14T20:00:00"
    assert "Son tarix: 2026-10-10" in lead.raw_text
    assert "200 participants" in lead.raw_text
