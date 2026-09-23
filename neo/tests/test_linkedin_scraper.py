from app.models import LeadCategory
from app.scrapers.linkedin import LinkedInScraper, rejection_reason


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.last_call = None

    def post(self, url, **kwargs):
        self.last_call = (url, kwargs)
        return FakeResponse(self._payload)

    def close(self):
        pass


# Real post from live test (Azeri Steel HR) — the exact lead type we want.
# Note the #Vakansiya hashtag: must NOT be treated as a job ad.
BUYER_POST = (
    "Catering şirkəti ilə əməkdaşlıq axtarırıq 🍽️\n"
    "Şirkətimiz əməkdaşlarının qidalanmasının təşkili ilə bağlı peşəkar catering "
    "şirkəti ilə uzunmüddətli əməkdaşlıq etməyi planlaşdırır. Təkliflərinizi "
    "procurement@example.az ünvanına göndərin.\n#Catering #Vakansiya #Azərbaycan"
)

SAMPLE_ITEMS = [
    {
        "type": "post",
        "linkedinUrl": "https://www.linkedin.com/posts/buyer-activity-1",
        "content": BUYER_POST,
        "author": {
            "name": "Shabnam Mammadli",
            "info": "Azeri Steel MMC / İnsan Resursları Departamentinin Rəhbəri",
            "linkedinUrl": "https://www.linkedin.com/in/shabnam",
        },
        "postedAt": {"date": "2026-08-17T11:37:17.147Z"},
    },
    {
        "type": "comment",
        "linkedinUrl": "https://www.linkedin.com/x",
        "content": "irrelevant",
    },
    {
        "type": "post",
        "linkedinUrl": "https://www.linkedin.com/posts/employer_bas-aspaz-vacancy",
        "content": "BAŞ AŞPAZ TƏLƏB OLUNUR\nƏməkhaqqı: 1500-2000 AZN\nCV-nizi cv@company.az ünvanına göndərin",
        "author": {"name": "AzVak", "info": "24,582 followers"},
    },
]


def test_fetch_leads_keeps_buyer_post_with_contact_info():
    scraper = LinkedInScraper(client=FakeClient(SAMPLE_ITEMS), api_token="fake-token")

    leads = scraper.fetch_leads()

    assert len(leads) == 1
    lead = leads[0]
    assert lead.source == "linkedin"
    assert lead.source_url == "https://www.linkedin.com/posts/buyer-activity-1"
    assert lead.title == "Catering şirkəti ilə əməkdaşlıq axtarırıq 🍽️"
    assert lead.entity_name == "Shabnam Mammadli"
    assert lead.category == LeadCategory.corporate
    assert lead.posted_at is not None
    assert lead.contact_info.website == "https://www.linkedin.com/in/shabnam"
    assert lead.contact_info.email == "procurement@example.az"
    assert "Azeri Steel" in lead.raw_text


def test_fetch_leads_skips_when_no_api_token(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    client = FakeClient(SAMPLE_ITEMS)
    scraper = LinkedInScraper(client=client, api_token=None)

    assert scraper.fetch_leads() == []
    assert client.last_call is None


def test_rejects_vacancy():
    text = "VAKANSİYA: Logistika üzrə mütəxəssis\nNamizədlər CV göndərsin. Bakı"
    assert rejection_reason(text, "Vakansiya.biz", "") == "vacancy"


def test_rejects_non_azerbaijan_post():
    text = "Catering Günlükleri – staj dönemi notları, toplu yemek sektörü"
    assert rejection_reason(text, "Aydan", "Yemek365 Kurucu Ortak") == "not_azerbaijan"


def test_rejects_known_competitor_by_content_or_author():
    promo = "Plan & Eat ilə əməkdaşlarınıza sağlam korporativ nahar təqdim edin!"
    assert rejection_reason(promo, "Anar", "Director of Sales") == "competitor"
    assert rejection_reason("Baku ID forumu uğurla keçdi", "Xelil", "Sales at Plan&Eat") == "competitor"


def test_turkish_bakis_is_not_baku():
    text = "Catering sektöründe yöneticilerin bakış açısı çok önemli"
    assert rejection_reason(text, "Ibrahim", "--") == "not_azerbaijan"


def test_english_baku_post_passes_geo_check():
    text = "We are opening a new plant in Baku and need a staff lunch supplier"
    assert rejection_reason(text, "John", "COO") is None


def test_uppercase_azerbaijani_vacancy_header():
    text = "VAKANSİYA: Aşpaz köməkçisi, Bakı"
    assert rejection_reason(text, "Someone", "") == "vacancy"


def test_rejects_job_board_digest_by_author():
    text = "Bu günə olan catering sahəsində təkliflər, Bakı"
    assert rejection_reason(text, "Samira", "AzVak - Azərbaycanda Vakansiyalar") == "vacancy"


def test_rejects_uppercase_competitor_name():
    text = "TAİS İKF – Biznesinizin görünməyən gücü, catering xidmətlərimiz"
    assert rejection_reason(text, "Amir", "Founder") == "competitor"


def test_keeps_buyer_writing_teleb_olunur():
    text = "Zavodumuzun 200 işçisi üçün gündəlik nahar xidməti tələb olunur, Sumqayıt"
    assert rejection_reason(text, "Elvin", "Operations Manager") is None
