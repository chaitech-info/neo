from bs4 import BeautifulSoup

from app.scrapers import EmlakScraper, News1Scraper, OxuScraper


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def test_1news_takes_all_title_blocks_not_only_desc():
    html = """
    <div class="desc"><h3 class="title"><a href="/az/news/20260923100000000-Top">Top xəbər</a></h3></div>
    <div class="news"><h3 class="title"><a href="/az/news/20260923110000000-Lent">Lentdəki xəbər</a></h3></div>
    <h3 class="title"><a href="/az/category/xronika">Xronika</a></h3>
    """
    urls = [u for u, _ in News1Scraper().article_links(_soup(html))]

    assert urls == [
        "https://1news.az/az/news/20260923100000000-Top",
        "https://1news.az/az/news/20260923110000000-Lent",
    ]


def test_oxu_takes_feed_links_without_flash_class_and_skips_tags():
    html = """
    <a class="umami--click--flash" href="/cemiyyet/flash-bolmesindeki-xeber">Flash bölməsindəki xəbər başlığı</a>
    <a class="feed-slider-item-img" href="/iqtisadiyyat/lentdeki-korporativ-tedbir"></a>
    <a href="/iqtisadiyyat/lentdeki-korporativ-tedbir">Lentdəki korporativ tədbir başlığı</a>
    <a href="https://oxu.az/tag/bmt-bas-assambleyasi">BMT Baş Assambleyası etiketi</a>
    """
    links = OxuScraper().article_links(_soup(html))

    assert links == [
        ("https://oxu.az/cemiyyet/flash-bolmesindeki-xeber", "Flash bölməsindəki xəbər başlığı"),
        ("https://oxu.az/iqtisadiyyat/lentdeki-korporativ-tedbir", "Lentdəki korporativ tədbir başlığı"),
    ]


def test_emlak_resolves_relative_and_unicode_links():
    html = """
    <a href="https://emlak.gov.az/az/news/view/10524-ozellesdirme">İnvestisiya müsabiqəsi elanı</a>
    <a href="/az/news/view/10540-novbeti-herracda-6-neqliyyat">Növbəti hərracda 6 nəqliyyat vasitəsi satılıb</a>
    <a href="az/news/view/10202-Əmlak-Xidməti">Yeni kommersiya obyekti icarəyə verilir</a>
    <a href="/az/news/view/4856-Əlaqə">Əlaqə</a>
    """
    scraper = EmlakScraper()
    scraper.get = lambda url: FakeResponse(html)
    scraper.polite_sleep = lambda: None

    urls = sorted(lead.source_url for lead in scraper.fetch_leads())

    assert urls == [
        "https://emlak.gov.az/az/news/view/10202-Əmlak-Xidməti",
        "https://emlak.gov.az/az/news/view/10524-ozellesdirme",
        "https://emlak.gov.az/az/news/view/10540-novbeti-herracda-6-neqliyyat",
    ]


def test_keyword_filter_matches_azerbaijani_uppercase_titles():
    from app.scrapers.base import az_lower

    assert "tədbir" in az_lower("Restoranla bağlı TƏDBİR GÖRÜLƏCƏK")
    assert "açılış" in az_lower("Yeni ofisin AÇILIŞI oldu")
    assert "ictimai iaşə" in az_lower("İCTİMAİ İAŞƏ xidməti")


ARTICLE_HTML = """
<html><head>
<meta property="article:published_time" content="2026-09-23T20:56:00+04:00">
<meta property="og:description" content="Qısa təsvir">
</head><body>
<div class="sidebar"><p>Reklam</p></div>
<div class="post-detail-content-inner"><p>Birinci abzas.</p><p>200 nəfərlik banket.</p></div>
</body></html>
"""


def _scraper_with_page(html: str) -> OxuScraper:
    scraper = OxuScraper()
    scraper.get = lambda url: FakeResponse(html)
    scraper.polite_sleep = lambda: None
    return scraper


def test_fetch_article_extracts_body_and_utc_publish_time():
    text, posted_at = _scraper_with_page(ARTICLE_HTML).fetch_article("https://oxu.az/x/y")

    assert text == "Birinci abzas.\n200 nəfərlik banket."
    assert posted_at.isoformat() == "2026-09-23T16:56:00"


def test_fetch_article_falls_back_when_selector_missing():
    html = ARTICLE_HTML.replace("post-detail-content-inner", "renamed-by-site")
    text, _ = _scraper_with_page(html).fetch_article("https://oxu.az/x/y")

    assert "200 nəfərlik banket." in text


def test_enrich_keeps_title_when_article_fetch_fails():
    from app.models import RawLead

    scraper = OxuScraper()
    scraper.polite_sleep = lambda: None

    def boom(url):
        raise RuntimeError("down")

    scraper.get = boom
    lead = RawLead(source="oxu", source_url="https://oxu.az/x/y", title="Başlıq", raw_text="Başlıq")

    assert scraper.enrich(lead).raw_text == "Başlıq"
    assert lead.posted_at is None


FED_TENDERS_HTML = """
<div class="news"><a href="/az/tenderler/dovlet-qurumu-keytring-xidmeti-alir-tender-elani-264949">
  <div class="news-detail"><span class="category-name">Tenderlər</span><span class="time date">21 Sen 2026</span></div>
  <div class="heading"><h5>Dövlət qurumu keytring xidməti alır – TENDER ELANI</h5></div></a></div>
<div class="news"><a href="/az/tenderler/44-mekteb-tikilecek-25-milyonluq-tender-264950">
  <div class="heading"><h5>44 məktəb tikiləcək – 25 MİLYONLUQ TENDER</h5></div></a></div>
<div class="news"><a href="/az/maliyye/bank-xeberi-265000"><div class="heading"><h5>Bank tədbiri</h5></div></a></div>
<a href="az/tenderler/2">2</a>
"""


def test_fed_tenders_reads_cards_filters_keywords_and_paginates():
    from app.models import LeadCategory
    from app.scrapers import FedTenderScraper

    scraper = FedTenderScraper()
    requested = []
    scraper.get = lambda url: requested.append(url) or FakeResponse(FED_TENDERS_HTML)
    scraper.polite_sleep = lambda: None

    leads = scraper.fetch_leads()

    assert requested[:2] == ["https://fed.az/az/tenderler", "https://fed.az/az/tenderler/2"]
    assert [lead.title for lead in leads] == ["Dövlət qurumu keytring xidməti alır – TENDER ELANI"]
    assert leads[0].source == "fed"
    assert leads[0].category == LeadCategory.tender


def test_azerweb_skips_expired_tenders():
    from datetime import date

    from app.scrapers import AzerwebScraper

    payload = {"results": [
        {"id": 1, "type": "tender", "title": "Catering 2019", "company": "GIZ", "deadline": "2019-11-15", "posted_date": "2019-11-07"},
        {"id": 2, "type": "tender", "title": "Catering açıq", "company": "FAO", "deadline": "2026-10-01", "posted_date": "2026-09-20"},
        {"id": 3, "type": "tender", "title": "Deadline-sız", "company": "X", "deadline": None, "posted_date": None},
    ]}
    scraper = AzerwebScraper()
    scraper.today = lambda: date(2026, 9, 23)
    scraper.get = lambda url, **kw: type("R", (), {"json": lambda self: payload})()
    scraper.polite_sleep = lambda: None

    leads = scraper.fetch_leads()

    assert [lead.title for lead in leads] == ["Catering açıq", "Deadline-sız"]
    assert leads[0].posted_at.isoformat() == "2026-09-19T20:00:00"


def test_fed_keeps_clean_title_when_card_text_has_category_and_date():
    from bs4 import BeautifulSoup

    from app.scrapers import FedScraper

    href = "/az/senaye/azersun-zavodu-boyudur-265418"
    html = f"""
    <a href="{href}">Sənaye26 Sen 2026“Azərsun” zavodunu böyüdür</a>
    <a href="{href}">“Azərsun” zavodunu böyüdür</a>
    <a href="{href}">“Azərsun” zavodunu böyüdür19:3626 Sen 2026</a>
    """
    links = FedScraper().article_links(BeautifulSoup(html, "lxml"))

    assert links == [(f"https://fed.az{href}", "“Azərsun” zavodunu böyüdür")]


def test_fed_strips_glued_category_date_and_time_date():
    from bs4 import BeautifulSoup

    from app.scrapers import FedScraper

    html = """
    <a href="/az/senaye/mingecevir-tekstil-265403">Sənaye26 Sen 2026Mingəçevirdə tekstil layihəsinin təməli qoyuldu</a>
    <a href="/az/senaye/mingecevir-tekstil-265403">Mingəçevirdə tekstil layihəsinin təməli qoyuldu13:0126 Sen 2026</a>
    """
    links = FedScraper().article_links(BeautifulSoup(html, "lxml"))

    assert links == [("https://fed.az/az/senaye/mingecevir-tekstil-265403",
                      "Mingəçevirdə tekstil layihəsinin təməli qoyuldu")]


def test_report_senaye_reads_title_element_only():
    from bs4 import BeautifulSoup

    from app.scrapers import ReportSenayeScraper

    html = """
    <a class="news__item" href="/senaye/azergold-yeni-meden">
      <span class="news__category">Sənaye</span>
      <h2 class="news__title">"AzerGold" yeni qızıl mədənində istehsala başlayır</h2>
      <ul class="news__date"><li>25 sentyabr, 2026</li><li>19:05</li></ul>
    </a>
    <a class="news__item" href="/iqtisadiyyat/other">Başqa bölmə</a>
    """
    links = ReportSenayeScraper().article_links(BeautifulSoup(html, "lxml"))

    assert links == [("https://report.az/senaye/azergold-yeni-meden",
                      '"AzerGold" yeni qızıl mədənində istehsala başlayır')]


def test_news_keywords_skip_state_events_and_match_expansion():
    from app.scrapers.base import NEWS_KEYWORDS, contains_keyword

    assert not contains_keyword("II Azərbaycan Beynəlxalq İnvestisiya Forumunda sənədlər imzalandı", NEWS_KEYWORDS)
    assert not contains_keyword("Mədəniyyət nazirliyi yeni layihə təqdim edib", NEWS_KEYWORDS)
    assert contains_keyword("“Azərsun” mövcud zavodunu iki dəfə böyütməyi planlaşdırır", NEWS_KEYWORDS)
    assert contains_keyword("Filizçay polimetal yatağında ilk istehsalın başlayacağı vaxt açıqlanıb", NEWS_KEYWORDS)


def test_jobsearch_groups_meal_employers_and_skips_kitchens(monkeypatch):
    from datetime import datetime

    from app.scrapers.jobsearch import JobSearchScraper

    today = datetime.utcnow().strftime("%Y-%m-%dT00:00:00+04:00")
    listing = {"items": [
        {"slug": "azersun-operator-1", "title": "Operator", "created_at": today, "company": {"title": "Azersun Holding"}},
        {"slug": "azersun-anbardar-2", "title": "Anbardar", "created_at": today, "company": {"title": "Azersun Holding"}},
        {"slug": "pizza-kuryer-3", "title": "Kuryer", "created_at": today, "company": {"title": "Pizza Mizza"}},
        {"slug": "gizli-4", "title": "Mühasib", "created_at": today, "company": {"title": "X"}, "hide_company": True},
        {"slug": "ofis-5", "title": "Mühasib", "created_at": today, "company": {"title": "Ofis MMC"}},
    ], "next": None}
    details = {
        "azersun-operator-1": "İş qrafiki 5/2. Nahar şirkət tərəfindən təmin olunur. hr@azersun.com",
        "azersun-anbardar-2": "Pulsuz nahar və servis.",
        "ofis-5": "Nahar fasiləsi 13:00-14:00.",
    }

    class _R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake_get(self, url, **kw):
        slug = url.rsplit("/", 1)[-1]
        return _R(listing if "page=" in url else {"text": details.get(slug, ""), "phone": "+994"})

    monkeypatch.setattr(JobSearchScraper, "get", fake_get)
    monkeypatch.setattr(JobSearchScraper, "polite_sleep", lambda self: None)
    s = JobSearchScraper()
    leads = s.fetch_leads()
    assert s.fetched == 5
    assert [l.title for l in leads] == ["Azersun Holding — işçilərinə nahar verir (2 vakansiya, jobsearch.az)"]
    assert leads[0].contact_info.email == "hr@azersun.com" and leads[0].source == "jobsearch"


def test_rss_and_listing_scrapers_parse_and_clean():
    from app.scrapers.listing import LinkListSite, clean_title, listing_scraper
    from app.scrapers.rss import RssSite, rss_scraper

    assert clean_title("04 AVQUST / 2026 / 21:00 Bakı Limanı ehtiyat hissələri") == "Bakı Limanı ehtiyat hissələri"
    assert clean_title("Satınalma və hərraclar Mal-material alışı", "Satınalma və hərraclar") == "Mal-material alışı"

    rss = """<?xml version='1.0'?><rss><channel>
      <item><title>Yeni zavod tikilir</title><link>https://x.az/1</link><description>&lt;p&gt;300 iş yeri&lt;/p&gt;</description>
        <pubDate>Mon, 05 Oct 2026 10:00:00 +0400</pubDate></item>
      <item><title>Hava</title><link>https://x.az/2</link></item></channel></rss>""".encode()

    class _R:
        content = rss
        text = rss.decode()

    S = rss_scraper(RssSite("trend", "https://x.az/rss", fetch_body=False))
    S.get = lambda self, url, **kw: _R()
    S.select_relevant = lambda self, texts, fallback, kind, extra=(): [t.startswith("Yeni") for t in texts]
    leads = S().fetch_leads()
    assert [(l.source, l.title) for l in leads] == [("trend", "Yeni zavod tikilir")]
    assert "300 iş yeri" in leads[0].raw_text and leads[0].posted_at.hour == 6   # UTC

    html = """<a href="/az/post/1/x">12.09.2026 Sumqayıtda yeni zavod istifadəyə verildi</a>
              <a href="/az/page/about">Haqqımızda səhifəsi uzun mətn</a>"""

    class _H:
        text = html

    L = listing_scraper(LinkListSite("economy", "https://economy.gov.az/az/page/media/news", r"/az/post/\d+"))
    L.get = lambda self, url, **kw: _H()
    L.select_relevant = lambda self, texts, fallback, kind, extra=(): [True] * len(texts)
    L.fetch_article = lambda self, url: ("Mətn", None)
    leads = L().fetch_leads()
    assert [(l.source, l.title, l.source_url) for l in leads] == [
        ("economy", "Sumqayıtda yeni zavod istifadəyə verildi", "https://economy.gov.az/az/post/1/x")]


def test_jobboard_reads_details_and_skips_seen_ads(monkeypatch):
    import dataclasses

    from app.scrapers import jobboards

    site = dataclasses.replace(jobboards.HELLOJOB, pages=1, key="hellojob_test")
    listing = """<a href="/vakansiya/operator-11">Operator</a><a href="/vakansiya/kassir-12">Kassir</a>
                 <a href="/vakansiya/sef-13">Şef</a><a href="/sirketler">Şirkətlər</a>"""
    pages = {
        "operator-11": ("Binə Aqro QSC", "Operator", "Nahar şirkət tərəfindən təmin olunur."),
        "kassir-12": ("Ofis MMC", "Kassir", "Nahar fasiləsi 13:00."),
        "sef-13": ("Dadlı Restoran", "Şef", "Pulsuz nahar."),
    }

    def page_html(name, title, text):
        return (f'<div class="company__row"><a class="vacancies__category">{name}</a>'
                f'<h2 class="vacancies__subTitle">{title}</h2></div><div class="company__text">{text}</div>')

    requested = []

    class _R:
        def __init__(self, text):
            self.text = text

    def fake_get(self, url, **kw):
        requested.append(url)
        slug = url.rsplit("/", 1)[-1]
        return _R(listing if "page=" in url else page_html(*pages[slug]))

    S = jobboards.jobboard_scraper(site)
    monkeypatch.setattr(S, "get", fake_get)
    monkeypatch.setattr(S, "polite_sleep", lambda self: None)
    leads = S().fetch_leads()
    assert [l.title for l in leads] == ["Binə Aqro QSC — işçilərinə nahar verir (1 vakansiya, hellojob.az)"]
    assert leads[0].source == "hellojob_test"
    detail_calls = [u for u in requested if "page=" not in u]
    assert len(detail_calls) == 3          # restoran: detal oxunur, sonra öz mətbəxi səbəbilə atılır

    requested.clear()
    S().fetch_leads()                       # ikinci run: siqnalsız elanlar (kassir, restoran) atlanır
    assert [u.rsplit("/", 1)[-1] for u in requested if "page=" not in u] == ["operator-11"]


def test_expo_calendar_parses_dates_and_infers_year():
    from datetime import date

    from app.scrapers.events import parse_event

    today = date(2026, 10, 5)
    assert parse_event("14 - 16 Okt BakuBuild 31-ci İnşaat Sərgisi", today) == (
        date(2026, 10, 14), date(2026, 10, 16), "BakuBuild 31-ci İnşaat Sərgisi")
    assert parse_event("05 - 08 May Caspian Agro Week", today)[0] == date(2027, 5, 5)   # bu il keçib
    assert parse_event("01 - 03 Iyn Caspian Oil & Gas", today)[0] == date(2027, 6, 1)
    assert parse_event("Haqqımızda", today) is None
