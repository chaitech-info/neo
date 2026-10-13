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
    assert lead.origin_url == "https://www.linkedin.com/posts/buyer-activity-1"
    assert lead.author.name == "Shabnam Mammadli"
    assert lead.author.url == "https://www.linkedin.com/in/shabnam"
    assert "İnsan Resursları" in lead.author.title


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


def _vacancy(url, content, author="AzVak"):
    return {
        "type": "post",
        "linkedinUrl": url,
        "content": content,
        "author": {"name": author, "info": "Azərbaycanda Vakansiyalar"},
    }


VUMART_1 = "Təndirçi tələb olunur\nNahar Şirkət tərəfindən təmin olunur\nCV-lərinizi hr@vumes.az ünvanına göndərin"
VUMART_2 = "Mühasib tələb olunur\nNahar Şirkət tərəfindən təmin olunur\nCV-lərinizi hr@vumes.az ünvanına göndərin"


def test_vacancy_with_company_lunch_becomes_one_employer_lead():
    items = [_vacancy("https://li/p/1", VUMART_1), _vacancy("https://li/p/2", VUMART_2)]
    scraper = LinkedInScraper(client=FakeClient(items), api_token="fake-token")

    leads = scraper.fetch_leads()

    assert len(leads) == 1
    lead = leads[0]
    assert lead.source_url == "https://vumes.az"
    assert lead.entity_name == "vumes.az"
    assert lead.contact_info.email == "hr@vumes.az"
    assert lead.raw_text.startswith("[SİQNAL")
    assert "https://li/p/1" in lead.raw_text
    assert lead.origin_url == "https://li/p/1"  # paneldə "LinkedIn-də aç" — source_url şirkət saytıdır
    assert lead.author.name == "AzVak"


def test_vacancy_without_meal_signal_is_still_rejected():
    text = "Kassir tələb olunur, Bakı\nƏməkhaqqı: 600 AZN\nhr@market.az"
    scraper = LinkedInScraper(client=FakeClient([_vacancy("https://li/p/3", text)]), api_token="t")
    assert scraper.fetch_leads() == []


def test_meal_vacancy_with_only_free_email_and_no_name_is_rejected():
    for email in ("someone@gmail.com", "hr.jobs@usa.com"):
        text = f"Gözətçi tələb olunur, Bakı\nNahar şirkət tərəfindən verilir\nCV: {email}"
        scraper = LinkedInScraper(client=FakeClient([_vacancy("https://li/p/4", text)]), api_token="t")
        assert scraper.fetch_leads() == []


def test_employer_name_fallback_when_no_corporate_email():
    text = "“Royale Sea Breeze Operation” MMC hüquqşünas axtarır\nNahar şirkət tərəfindən\nnamizədlər zəng etsin"
    scraper = LinkedInScraper(client=FakeClient([_vacancy("https://li/p/5", text)]), api_token="t")

    leads = scraper.fetch_leads()

    assert len(leads) == 1
    assert leads[0].entity_name == "Royale Sea Breeze Operation"
    assert "linkedin.com/search/results/companies" in leads[0].source_url


def test_competitor_own_vacancy_is_not_employer_lead():
    text = "Mühəndis-mexanik tələb olunur, Bakı\nNahar şirkət tərəfindən təmin olunur\nhr@atsfood.az"
    scraper = LinkedInScraper(client=FakeClient([_vacancy("https://li/p/6", text)]), api_token="t")
    assert scraper.fetch_leads() == []


def test_job_board_or_agency_email_is_not_the_employer():
    for email in ("info@vakansiya.biz", "cv@humanpro.az"):
        text = f"Anbardar tələb olunur, Bakı\nNahar şirkət tərəfindən\n{email}"
        scraper = LinkedInScraper(client=FakeClient([_vacancy("https://li/p/7", text)]), api_token="t")
        assert scraper.fetch_leads() == []


def test_keeps_buyer_writing_teleb_olunur():
    text = "Zavodumuzun 200 işçisi üçün gündəlik nahar xidməti tələb olunur, Sumqayıt"
    assert rejection_reason(text, "Elvin", "Operations Manager") is None
