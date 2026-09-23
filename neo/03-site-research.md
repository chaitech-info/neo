# 03 — Site Research

Hər mənbənin **real** strukturu — brauzerlə birbaşa yoxlanılıb (ilkin sənəd fərziyyələri bir neçə saytda səhv çıxdı, aşağıda düzəldilmiş vəziyyət var). Kod bu tapıntılara əsasən yazılıb: `app/scrapers/`.

> Saytlar öz strukturlarını istənilən vaxt dəyişə bilər — bu sənəd "son yoxlanılan tarixdə düzgün olan" məlumatdır, əbədi qarantiya deyil. Scraper xəta verməyə başlasa, ilk addım budur: saytı yenidən brauzerdə aç və bu cədvəli yenilə.

## Marja.az

- **Nə olduğu (real):** iqtisadi/biznes **xəbər saytı** (ilkin fərziyyə: elan/klassifayd saytı — SƏHV idi)
- **Yanaşma:** statik HTML, `httpx` + `BeautifulSoup`
- **Məqalə URL forması:** `https://marja.az/<rəqəm-id>/<slug>`
- **Seçici:** bütün `<a href>`-ləri reqex `^https://marja\.az/\d+/[a-z0-9-]+$` ilə süz, mətn üçün ən uzun dublikat variantını saxla (link həm şəkil, həm başlıq üçün təkrarlanır)
- **Son yoxlanılan nəticə:** 71 namizəd link tapıldı, keyword filtrindən sonra faktiki lead (məs. "Məşhur restoran şəbəkəsi Metroparka gəlir")

## Azerweb.az

- **Nə olduğu (real):** "İş elanları, Tenderlər və Təhsil" platforması (ilkin fərziyyə: ümumi elan saytı — SƏHV idi)
- **Yanaşma:** React SPA, amma **real JSON API** var — HTML scraping lazım DEYİL
- **API:** `GET https://azerweb.az/api/announcements/?page_size=30&q=<söz>` — server-side axtarış dəstəkləyir
- **Vacib:** `type` sahəsinə görə filtrlə (`tender`, `announcement` saxla, `vacancy` AT — "catering" sözü iş elanlarında da çıxır, məs. "Chef", "Housekeeper", bunlar lead deyil, iş axtaran adamdır)
- **Detay URL:** `https://azerweb.az/announcements/<id>`

## 1news.az

- **Nə olduğu:** xəbər portalı, RSS **YOXDUR** (ilkin fərziyyə RSS var idi — SƏHV)
- **Yanaşma:** statik HTML
- **Seçici:** `.desc h3.title a[href]` (kateqoriya səhifələrində, məs. `/az/xronika/`)
- **Vacib:** başlıq linki şəkil linkindən ayrıdır — `figure > a` şəkildir, `.desc h3.title > a` başlıqdır, yalnız ikincini istifadə et

## Oxu.az

- **Nə olduğu:** xəbər portalı, RSS **YOXDUR**
- **Yanaşma:** statik HTML
- **Seçici:** `a.umami--click--flash[href]` (analitika class-ı, amma etibarlı işarədir)
- **⚠️ Tələ:** `href` **NİSBİDİR** (`/kateqoriya/slug`) — mütləq URL-ə çevirmək lazımdır (`base_url + href`). İlk versiyada bu unudulmuşdu, DB-yə yarımçıq URL yazılırdı.

## Fed.az

- **Nə olduğu:** iqtisadi/biznes xəbər saytı
- **Yanaşma:** statik HTML
- **Seçici:** `href` nümunəsi `/az/<kateqoriya>/<slug>-<rəqəm-id>` (⚠️ **NİSBİ**, `https://fed.az` prefiksi əl ilə əlavə olunmalıdır)
- **Tələ #1:** ilkin regex mütləq URL gözləyirdi (`^https://fed\.az/...`), amma real `href` nisbi idi → 0 nəticə. Regex-i nisbi formaya çevirmək lazım oldu.
- **Tələ #2:** bəzi kartlarda başlıq mətninə vaxt prefiksi yapışır ("16:47Menyunu düzgün..."). `re.sub(r"^\d{1,2}:\d{2}", "", text)` ilə təmizlənir.

## Etender.gov.az

- **Nə olduğu:** dövlət satınalma portalı (React SPA)
- **Yanaşma:** real JSON API tapıldı — HTML scraping lazım deyil
- **API:** `GET /api/events?EventType=2&PageSize=20&PageNumber=1&EventStatus=1&Keyword=<söz>&...` (digər boş parametrlər `BASE_PARAMS`-da, bax `app/scrapers/etender.py`)
- **Detay URL:** `https://etender.gov.az/main/competition/detail/<eventId>`
- **Nümunə uğurlu nəticə:** "Tədbir və digər proqramlarda hazır yemək (keytrinq) xidmətlərinin satın alınması"

## Emlak.gov.az

- **Nə olduğu (real):** "Əmlak Məsələləri Dövlət Xidməti" — xəbər + hərrac saytı (ilkin fərziyyə: "yeni obyekt qeydiyyatı reyestri" — bu MÖVCUD DEYİL, SƏHV fərziyyə idi)
- **Yanaşma:** statik HTML
- **URL forması:** həm xəbərlər, həm hərraclar eyni pattern-dədir: `https://emlak.gov.az/az/news/view/<id>-<slug>`
- **Qərar:** birbaşa "yeni obyekt" siqnalı olmadığı üçün, hərrac/müsabiqə/obyekt/restoran/kommersiya açar sözləri ilə aşağı-prioritetli, dolayı siqnal kimi istifadə olunur (bax §5 development sənədində)

## LinkedIn

- **Yanaşma:** birbaşa scraping YOX — Apify actor `harvestapi/linkedin-post-search` (bax `04-decisions.md` → "LinkedIn scraping yanaşması")
- **API:** `POST https://api.apify.com/v2/acts/harvestapi~linkedin-post-search/run-sync-get-dataset-items?token=<APIFY_TOKEN>`, body: `{"searchQueries": [...], "postedLimit": "week", "sortBy": "date", "maxPosts": N}`
- **Cavab formatı:** dataset item-ləri `type` sahəsinə görə filtrlə (`"post"` saxla, `"comment"`/`"reaction"` AT — `scrapeComments`/`scrapeReactions` deaktivdir, defolt yalnız post gəlir)
- **Sahə uyğunluğu:** `linkedinUrl` → `source_url`, `content` (ilk sətir) → `title`, `content` → `raw_text`, `author.name` → `entity_name`, `postedAt.date` (ISO 8601) → `posted_at`
- **Qiymət:** ~$1.50-2 / 1000 post — buna görə scheduler-də gündə 1 dəfə (bax `app/scheduler.py::beat_schedule`), xəbər saytları kimi saatlıq YOX
- **⚠️ Tələ #1 (canlı test):** nəticələrin çoxu iş elanı (AzVak, ASG Aviation — şirkət təsvirində "catering" keçir) və rəqib reklamıdır (Plan&Eat, Lunchbox.az). Filtrlər: `app/scrapers/linkedin.py::rejection_reason`.
- **⚠️ Tələ #2:** uzun sorğular (4+ söz) 0 nəticə qaytarır — yalnız 2-3 sözlük sorğular.
- **⚠️ Tələ #3:** türkcə "bakış" içində "bakı" var; `"İ".lower()` birləşdirici nöqtə qaytarır. Hər ikisi kodda həll olunub — bax `04-decisions.md` → "LinkedIn filtrləri — hədəf müştəri profili".
- **Ən güclü siqnal:** vakansiya elanlarında "Nahar şirkət tərəfindən təmin olunur" — işəgötürən özü lead-dir. Əksər elanlarda HR email-i var, domeni şirkəti müəyyən edir. Bax `04-decisions.md` → "İşçilərinə nahar verən şirkətlər".
- **Kodda:** `app/scrapers/linkedin.py`

## Ümumi dərs

İlk sənəd versiyası (development spec-in ilk qaralaması) hər saytın funksiyasını **görmədən, adından fərziyyə ilə** təsvir etmişdi (məs. "Marja.az = elan saytı", "Emlak.gov.az = qeydiyyat reyestri"). Bunların əksəriyyəti səhv çıxdı. **Nəticə:** yeni mənbə əlavə edərkən HƏMİŞƏ əvvəlcə brauzerdə real saytı aç, strukturu/API-ni tap, SONRA kodu yaz — adından güman etmə.
