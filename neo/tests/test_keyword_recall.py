"""Açar söz + AI triajı: heç bir fürsət buraxılmasın (bax 04-decisions.md)."""
import json

from app import ai_triage
from app.scrapers.base import NEWS_KEYWORDS, TENDER_KEYWORDS, BaseScraper, contains_keyword
from app.scrapers.etender import EtenderScraper


def test_keywords_match_suffixes_caps_and_other_languages():
    assert contains_keyword("Komandaqurma tədbirlərinin təşkili xidmətinin satın alınması", TENDER_KEYWORDS)
    assert contains_keyword("BAYRAM TƏDBİRLƏRİNİN TƏŞKİLİ", TENDER_KEYWORDS)
    assert contains_keyword("PROVISION OF OFFSHORE CATERING SERVICES", TENDER_KEYWORDS)
    assert contains_keyword("Əməkdaşların qidalanmasının təşkili", TENDER_KEYWORDS)
    assert contains_keyword("Услуги по организации питания сотрудников", TENDER_KEYWORDS)
    assert contains_keyword("Şirkət 300 nəfəri işə götürəcək", NEWS_KEYWORDS)
    assert contains_keyword("Yeni zavod istifadəyə verilib", NEWS_KEYWORDS)


def test_keywords_do_not_match_inside_other_words():
    assert not contains_keyword("Leak prevention services", TENDER_KEYWORDS)  # "event"
    assert not contains_keyword("Mədəniyyət nazirliyi yeni layihə təqdim edib", NEWS_KEYWORDS)  # "mədən"
    assert not contains_keyword("Yolların əsaslı təmiri işlərinin satın alınması", TENDER_KEYWORDS)


class _Dummy(BaseScraper):
    source_name = "dummy"

    def fetch_leads(self):
        return []


def test_select_relevant_is_keyword_or_ai(monkeypatch):
    titles = [
        "Bayram tədbirlərinin təşkili",           # açar söz
        "Qonaqların yerləşdirilməsi xidməti",     # yalnız AI
        "Printer kartricləri",                    # heç biri
    ]
    monkeypatch.setattr("app.ai_triage.triage", lambda t, kind="news", client=None: {1})

    assert _Dummy().select_relevant(titles, TENDER_KEYWORDS, kind="tender") == [True, True, False]


def test_select_relevant_falls_back_to_keywords_when_ai_is_down():
    # conftest: triage -> None (AI yoxdur)
    titles = ["Bayram tədbirlərinin təşkili", "Qonaqların yerləşdirilməsi xidməti"]
    assert _Dummy().select_relevant(titles, TENDER_KEYWORDS, kind="tender") == [True, False]


def _page(items, has_next):
    payload = {"items": items, "hasNextPage": has_next}
    return type("R", (), {"json": lambda self: payload})()


def _event(i, name, end, buyer="ŞABRAN RAYON İCRA HAKİMİYYƏTİ"):
    return {"eventId": i, "eventName": name, "buyerOrganizationName": buyer,
            "publishDate": "2026-09-20T10:00:00", "endDate": end}


def test_etender_reads_every_page_skips_expired_and_selects(monkeypatch):
    pages = [
        [_event(1, "Bayram tədbirlərinin təşkili", "2026-10-09T18:00:00"),
         _event(2, "Tədbir və digər proqramlarda hazır yemək (keytrinq)", "2026-02-05T18:00:00"),  # bitib
         _event(3, "Yolların əsaslı təmiri", "2026-10-22T18:00:00")],
        [_event(4, "Qonaqların yerləşdirilməsi və qonaqlanması", "2026-10-15T18:00:00"),
         _event(1, "Bayram tədbirlərinin təşkili", "2026-10-09T18:00:00")],  # dublikat
    ]
    calls = []
    scraper = EtenderScraper()
    scraper.polite_sleep = lambda: None
    scraper.now = lambda: __import__("datetime").datetime(2026, 9, 27, 12, 0)

    def get(url, params=None, **kw):
        calls.append(params["PageNumber"])
        i = params["PageNumber"] - 1
        return _page(pages[i], i + 1 < len(pages))

    scraper.get = get
    # AI "Qonaqların yerləşdirilməsi..."-ni seçir (açar sözü yoxdur).
    monkeypatch.setattr("app.ai_triage.triage", lambda t, kind="news", client=None: {
        i for i, x in enumerate(t) if "Qonaqların" in x})

    leads = scraper.fetch_leads()

    assert calls == [1, 2]
    assert [l.title for l in leads] == ["Bayram tədbirlərinin təşkili", "Qonaqların yerləşdirilməsi və qonaqlanması"]
    assert "Son tarix: 09.10.2026 18:00" in leads[0].raw_text
    assert leads[0].entity_name == "ŞABRAN RAYON İCRA HAKİMİYYƏTİ"
    assert leads[0].source_url == "https://etender.gov.az/main/competition/detail/1"


class _FakeOpenAI:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kw):
        self.calls += 1
        content = json.dumps({"r": self.answers.pop(0)})
        msg = type("M", (), {"content": content})()
        return type("R", (), {"choices": [type("Ch", (), {"message": msg})()]})()


def _real_triage():
    # conftest-in əvəzləməsindən yan keç — modulun orijinal funksiyası
    import importlib

    return importlib.reload(ai_triage).triage


def test_triage_bad_batch_includes_everything_recall_first():
    triage = _real_triage()
    client = _FakeOpenAI([[1], [1, 0]])  # 3 başlıq, iki dəfə pozuq cavab

    assert triage(["a1", "b1", "c1"], kind="tender", client=client) == {0, 1, 2}
    assert client.calls == 2  # əvvəl bir dəfə təkrar soruşur


def test_triage_retries_once_on_count_mismatch():
    triage = _real_triage()
    client = _FakeOpenAI([[1], [0, 1, 0]])  # birinci pozuq, ikinci düzgün

    assert triage(["a3", "b3", "c3"], kind="tender", client=client) == {1}


def test_triage_uses_cache_and_reports_failure_as_none():
    triage = _real_triage()
    client = _FakeOpenAI([[1, 0]])
    assert triage(["x2", "y2"], kind="news", client=client) == {0}
    assert triage(["x2", "y2"], kind="news", client=client) == {0}
    assert client.calls == 1  # ikinci dəfə keşdən

    class Broken:
        chat = type("C", (), {"completions": type("X", (), {"create": staticmethod(lambda **kw: 1 / 0)})()})()

    assert triage(["new title"], kind="news", client=Broken()) is None


def test_deadline_is_computed_in_code_not_by_ai():
    from datetime import date

    from app.ai_filter import annotate_deadline

    today = date(2026, 9, 27)
    assert annotate_deadline("X\nSon tarix: 06.10.2026 18:00", today).endswith("[SON TARİX: AKTİV — 9 gün qalıb]")
    assert annotate_deadline("X\nSon tarix: 27.09.2026", today).endswith("[SON TARİX: AKTİV — 0 gün qalıb]")
    assert annotate_deadline("X\nSon tarix: 05.02.2026", today).endswith("[SON TARİX: BİTİB — 234 gün əvvəl]")
    assert annotate_deadline("Son tarixi yoxdur", today) == "Son tarixi yoxdur"


def test_meal_benefit_phrasings_from_real_boss_ads():
    from app.scrapers.employer_signal import mentions_company_meal

    found = [
        "şirkət tərəfindən pulsuz nahar və servis xidməti olunur",       # tərs sıra
        "səhər və günorta yeməyi şirkət tərəfindən qarşılanır",          # "yeməyi"
        "nahar və çay şirkət hesabına qarşılanır",
        "Növbəli iş rejimi, nahar verilir",
        "bonus sistemi, qidalanma kartı, yol xərci",
        "Əlavə imkanlar: nahar",
        "şirkət tərəfindən əlavə 300 AZN yemək pulu ödənilir",
        "Yemək: işəgötürən tərəfindən verilir",
        "обед за счёт компании",
        "питание на территории Sea Breeze за счет компании",
        "NAHAR ŞİRKƏT TƏRƏFİNDƏN TƏMİN OLUNUR",
    ]
    not_found = [
        "nahar və axşam yeməyi şirkət tərəfindən verilmir",              # inkar (LOADSPHERE)
        "İş saatı 09:00-18:00, nahar fasiləsi 13:00-14:00",
        "Nahar: 13:00-dan 14:00-dək",
        "nahar vaxtı saat 13:00",
        "menyuya uyğun yeməklərin hazırlanmasını təmin etmək",           # aşpaz vəzifəsi
    ]
    assert [t for t in found if not mentions_company_meal(t)] == []
    assert [t for t in not_found if mentions_company_meal(t)] == []


def test_own_kitchen_employer_names():
    from app.scrapers.employer_signal import has_own_kitchen

    assert has_own_kitchen("concept.rest")
    assert has_own_kitchen("SUPREME HOTEL")
    assert has_own_kitchen("Beast Coffee")
    assert not has_own_kitchen("Sahil Transport MMC")
    assert not has_own_kitchen("restart.az")  # ".rest" yalnız domen sonluğu kimi
