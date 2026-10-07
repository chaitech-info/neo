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

## Boss.az (2026-09-27 əlavə olunub)

- **Nə olduğu:** Azərbaycanın ən böyük iş elanı saytı (~660 aktiv vakansiya)
- **robots.txt:** `Allow: /`, `/search/*`, `/vacancies/*`; qadağan yalnız admin/login/signup/profile/bookmarks
- **Yanaşma:** Next.js SPA, məzmun `POST https://boss.az/graphql` ilə gəlir (`operationName: "Vacancies"`, `variables: {filter: {}, first: 24, after: <cursor>, sortBy: "RECOMMENDED"}`) — sorğu brauzerdə `fetch` tutularaq tapılıb
- **⚠️ Tələ #1:** `?search[keyword]=nahar` URL-i və `filter.searchTerm` təsvirdə AXTARMIR — "nahar" 1 nəticə verir; səhifədəki "660 nəticə" sadəcə bütün aktiv elanların sayıdır. Ona görə bütün səhifələr (~28) oxunur, filtr lokal.
- **⚠️ Tələ #2:** `createdAt`/`updatedAt` ictimai API-də `null` gəlir — tarix üçün `bumpedAt` (son yenilənmə)
- **Sahələr:** `name` (şirkət adı — regex lazım deyil), `positionName`, `description`/`jobDescription`/`requirements`/`responsibilities` (HTML), `email`, `phones`, `location`
- **Canlı nəticə:** 660 elan → 49-da nahar qeyd olunub → 39 unikal işəgötürən → filtrdən sonra 31 → AI-dan 29 `pending_review`
- **Kodda:** `app/scrapers/boss.py`, ortaq məntiq `app/scrapers/employer_signal.py`

## Report.az — Sənaye bölməsi (2026-09-27 əlavə olunub)

- **Nə olduğu:** ən böyük xəbər agentliyinin `/senaye/` bölməsi (zavod, sənaye zonası, mədən xəbərləri)
- **robots.txt:** yalnız `/admin/`, `/search/` və izləmə parametrləri qadağandır
- **Seçici:** `a.news__item[href^='/senaye/'] > h2.news__title` (kateqoriya/tarix ayrı elementlərdədir — başlığa yapışmır). Dərc vaxtı `article:published_time` meta-da.
- **Qeyd:** `/biznes/` bölməsi 404-dür.
- **Kodda:** `app/scrapers/news.py::ReportSenayeScraper`

## Phase 2 mənbələri (2026-10-05, Mac və Modal-dan yoxlanılıb)

**RSS (app/scrapers/rss.py):** trend (`az.trend.az/rss` — `www.trend.az/rss` ingilis/Mərkəzi Asiya lentidir; gövdə `div.article-content`), apa (`/rss`), azertag (`/rss`), banker (`/feed/`), minval (`/feed/`, rus; gövdə `div.content-inner`), caliber (`/rss.xml`, rus, Yandex "turbo" — `<title>` yoxdur, başlıq `turbo:content` içindəki `<h1>`; Modal-a 403 → Mac), president.az (`/az/rss`; gövdə bir neçə `div.news_paragraph-block`), bizim.media (`/feed`), ask.org.az (`/feed/`).

**Siyahı səhifəsi + URL nümunəsi (app/scrapers/listing.py):** portofbaku satınalma (`/az/elanlar/\d+`, başlıqda "04 AVQUST / 2026 / 21:00" prefiksi; Modal-a 403 → Mac), economy.gov.az xəbərlər (`/az/post/\d+`), azpromo (`/az/posts/\d+`), afez.az (`/media/news/...`, ingilis), minenergy (`/az/xeberler-arxivi/\d+`).

**jobsearch.az (app/scrapers/jobsearch.py):** açıq JSON API — `/api-az/vacancies-az?page=1` (30 elan, `next`), detal `/api-az/vacancies-az/{slug}` (`text`). İlk run: 109 təzə elandan 14 işəgötürən (Baku Steel, Main Steel, Azersun, Cronos Construction...). Şirkət `company.id` ilə qruplaşdırılır (bir elanda korporativ email, digərində yox — əks halda iki lead).

**İş elanı saytları (HTML, app/scrapers/jobboards.py):** position.az (`/az?page=N`, şirkət `.vacancy-company-header-title`, mətn `.vacancy-text`; detal səhifəsi yavaşdır ~4 san), hellojob.az (`/vakansiyalar?page=N`, şirkət `.company__row a.vacancies__category`, mətn `.company__text`). Artıq yoxlanmış siqnalsız elanın detalı təkrar çəkilmir (`triage_cache`, növ `jobad:<mənbə>`). İlk run: position 9, hellojob 5 işəgötürən.

**Sərgi təqvimi (app/scrapers/events.py):** Baku Expo Center təqvimi — bakuexpocenter.az → `pashaproperty.az/properties/baku-expo-centre` (server HTML): "14 - 16 Okt BakuBuild 31-ci ..." + sərginin saytı; il göstərilmir (növbəti buraxılış). 19 sərgi. `iteca.events` — ITECA **Qazaxıstan**dır (səhv); iteca.az illik səhifələri JS, "təqvimi yüklə" linki ölüdür.

**Qoşulmayıb:**
| Sayt | Səbəb |
| --- | --- |
| metro.gov.az satınalma | robots.txt 500 qaytarır → RFC 9309: hamısı qadağan say |
| socar.az satınalma, tabib.gov.az elanlar, azercosmos, airport.az | siyahı JavaScript ilə yüklənir / link yoxdur |
| ady.az, asco.az, azal.az | Mac-dan da 403 (bot qoruması) — brauzer lazımdır |
| ecepp.ebrd.com | `/feed/` WordPress bloqudur, satınalma elanları JS portalındadır |
| ungm.org | axtarış POST-u anti-forgery token tələb edir (400) |
| btime.az, abc.az, interfax.az | JS / timeout / domen işləmir |
| cabmin, aic, abif, ezda, araz-valley, aay | domen tapılmadı (düzgün ünvan lazımdır) |
| caspianenergy.net | domen qumar saytına yönləndirir |

## Yoxlanılıb, hələ qoşulmayıb

| Sayt | robots.txt | Niyə hələ yox |
| --- | --- | --- |
| hellojob.az (www) | `Allow: /` | Server HTML, siyahıda 80 elan/səhifə, amma nahar qeydi yalnız detal səhifəsində → hər elana ayrı sorğu (80 × 2-5 san ≈ 5 dəq/səhifə). boss.az-dan sonra növbəti namizəd. |
| jobsearch.az | `Allow: /` | Strukturu hələ yoxlanmayıb |
| position.az | hər şeyə icazə | Strukturu hələ yoxlanmayıb |
| busy.az | `/api/` və `?`-li URL-lər qadağan | Filtrli siyahı `?` tələb edir — məhdud |
| smartjob.az | `/api/` qadağan | Yalnız HTML |

## Mənbə siyahısı auditi (2026-10-07)

"Neo AI — Mənbə siyahısı" (7 qrup) kodla tutuşdurulub. Hər sayt canlı yoxlanılıb (əlçatanlıq, robots.txt, HTML/API).

**Yeni qoşulanlar** (robots.txt-ə uyğun, testlə örtülüb; `main`-dəki Phase 2 reyestri `app/sources.py` ilə birləşdirilib):

| Mənbə açarı | Nə oxuyur | Fayl |
|---|---|---|
| `media` | BusinessTime, TƏBİB, TÜİB, Caspian Oil & Gas, BakuBuild, TransLogistica, Rebuild Karabakh (APA, Trend, AZƏRTAC, Banker, AFEZ, İqtisadiyyat Nazirliyi, AZPROMO, ASK artıq `sources.py`-də RSS/listing kimi var) | `app/scrapers/media.py` (`MEDIA_SITES`) |
| `intl_tender` | UNGM (ölkə=Azərbaycan, ID 2306) + EBRD ECEPP ("Azerbaijan:" açıq bildirişlər) | `app/scrapers/intl.py` |
| `corporate` (+SOCAR) | SOCAR satınalma elanları cədvəli | `app/scrapers/corporate.py` |
| `boss` (+rol siqnalı) | boss.az-da hədəf vəzifələrə (Facility/Camp/Canteen/Procurement/HR/HSE/Site Manager, Chef...) işçi axtaran şirkətlər | `app/scrapers/boss.py`, `employers.py` |

Vəzifə siyahısı `employer_signal.TARGET_ROLES`-dadır; "x manager" ifadələri bir aralıq sözə icazə verir. Rəqib, öz mətbəxi olan, anonim və iş saytı işəgötürənləri atılır; lead açarı `employer_url` ilə LinkedIn/boss ilə eynidir. Rol siqnalı hələlik yalnız boss.az-dadır — jobsearch.az/position/hellojob scraper-ləri (`EmployerCollector`) yalnız nahar siqnalı axtarır; rol siqnalını oraya da əlavə etmək mümkündür (`collect_role_employer`).

**Kod olmadan əhatə olunanlar:** `etender.gov.az`, World Bank, bp EDP, LinkedIn (Apify), boss.az, Report.az, Marja, Fed, Oxu, 1news, Emlak.gov.az — əvvəldən var idi.

**Qoşula bilməyənlər və səbəbi** (kodla keçməyə cəhd edilmir):

| Mənbə | Səbəb |
|---|---|
| ADY, ASCO, AZAL, bp.com, adb.org | Bot qoruması — HTTP 403 |
| metro.gov.az | (main-də də qeyd olunub) `robots.txt` HTTP 500 qaytarır → `robots.py` "qadağan" sayır. Düzələndə `a[href*='/az/procurement/']`, başlıq `img[alt]` ilə qoşulur (bax `corporate.py` şərhi) |
| abc.az, bakuseaport.az, aic.az, cabmin.gov.az, energetika.gov.az, mdt.gov.az, sdh.gov.az, caspianenergyclub.com, totalenergies.az | Yoxlama zamanı cavab vermədi (timeout/DNS). Sonra yenidən yoxlanmalıdır |
| EU Funding & Tenders | Qrant çağırışlarıdır (Horizon və s.), təchizat tenderi deyil — catering/tədbir fürsəti çıxmır |
| caliber.az | Geosiyasi analitika, biznes siqnalı yoxdur |
| Offer.az | Cloudflare 526 (sertifikat xətası) |
| LinkedIn/Instagram/Facebook səhifələri, Google Maps, "tikinti obyektləri üzrə sosial media" | ToS/robots icazə vermir; rəsmi API və ya lisenziyalı provayder (Apify tipli) lazımdır — ayrıca qərar. LinkedIn artıq Apify ilə var |
| Şirkət saytlarının news/careers/annual-report bölmələri (BP, TotalEnergies, Masdar, ACWA...) | Hər saytın strukturu fərqlidir; hələ `MEDIA_SITES`-ə əlavə edilməyib. Struktur yoxlandıqca `MediaSite` ilə əlavə olunur (BP/Total bot qoruması/cavabsızlıq) |
| Sənaye parkları (Sumqayıt, Ağdam, Hacıqabul, Araz Vadisi, Pirallahı, Mingəçevir, Balaxanı, Neftçala, Masallı, Sabirabad) | **Yoxlanılmayıb** — ayrı sayt ünvanları təsdiqlənməyib. AFEZ (Alat) və İqtisadiyyat Nazirliyi qoşuldu; Pirallahı kimi park xəbərləri APA/AZƏRTAC üzərindən gəlir |
| AmCham, AHK, ASK, TÜİB, Türkiyə/İtaliya/Çin/Britaniya ticarət qurumları, Caspian Energy Club | ASK və TÜİB qoşuldu. AmCham `/en/news` səhifəsində xəbər lenti yoxdur (yalnız tədbir təqvimi linki — ayrıca baxılmalı); AHK, xarici ticarət palataları və Caspian Energy Club **yoxlanılmayıb/cavab vermədi** |

### Faza 2 yoxlaması (2026-10-07, `scripts/probe_sources.py`)

`python -m scripts.probe_sources <ünvanlar>` — yalnız oxuyur: status, robots.txt, RSS, əsas linklər. Nəticələr:

| Ünvan | Nəticə |
|---|---|
| metro.gov.az | robots.txt 500 → qoşulmaz (dəyişməyib) |
| abc.az, cabmin.gov.az, energetika.gov.az, mdt.gov.az, sdh.gov.az, aic.az, bakuseaport.az, caspianenergyclub.com, totalenergies.az, ezda.gov.az, azeravtoyol.*, dsak.gov.az | Bu maşından `ConnectError/Timeout`. Domen səhv və ya xarici IP bloku ola bilər — **skripti Azərbaycan IP-li Mac worker-də də işlədib müqayisə etmək lazımdır** |
| offer.az | HTTP 526 (sertifikat) |
| **abif.az** | **Azərbaycan Bilyard Federasiyasıdır** (investisiya qurumu deyil) — siyahıdakı "ABİF" üçün səhv fərziyyə idi; qoşulmur. RSS də 500 verir |
| mincom.gov.az (Rəqəmsal İnkişaf və Nəqliyyat Nazirliyi) | Açılır, robots OK. `/az/media/elanlar` = **kadr müsabiqəsi elanları** (satınalma deyil) → qoşulmur; xəbərləri yemək fürsəti vermir |
| dtsea.gov.az (İcbari Tibbi Sığorta), sehiyye.gov.az | `/media/elanlar` = vətəndaş qəbulu/inzibati elanlar, satınalma yox → qoşulmur |
| taxes.gov.az | "Tenderlər" linki etender.gov.az-a yönləndirir (artıq qoşulub) |
| Sənaye parkları (Sumqayıt, Balaxanı, Pirallahı, Mingəçevir, Qaradağ, Ağdam, Araz Vadisi) | Axtarışla **ayrı rəsmi sayt tapılmadı**; park xəbərləri economy.gov.az, president.az (RSS) və APA/AZƏRTAC-dan gəlir |

### Faza 3 – dövlət/bank satınalmaları (2026-10-07)

**Qoşuldu** (`app/sources.py`, `LinkListSite`, tender): Unibank (`/az/news/category/649`), DOST Agentliyi (`/announcements?id=N`, `local_only`), AFB Bank (`/tender-elanlari`). Hamısı robots-a uyğundur. Real tenderlərin əksəriyyəti IT/mebel/avadanlıqdır — yemək/tədbir açar sözünə nadir düşür, ona görə lead sayı azdır (AFB: 28 tender → 0, Unibank: 30 → 1, DOST: 12 → 1). Banklar canteen tenderi də keçirir (Yapı Kredi Bank Azərbaycan "yeməkxana xidməti" tenderi axtarışda çıxdı), buna görə saxlanılır.

**Sınanıb, qoşulmadı:**

| Mənbə | Səbəb |
|---|---|
| justice.gov.az/az/announcements, mfa.gov.az/category/elanlar | 12 elandan 9-u kadr qəbulu/vətəndaş qəbulu ("qəbul" açar sözünə düşür); satınalma elanları 2021-dən qalıb |
| customs.gov.az (Satınalmalar → Tender) | Səhifə hazırda boşdur (elan yoxdur); yenidən yoxlanmalıdır |
| abb-bank.az (müsabiqələrin elanı) | Next.js RSC axını, adi HTML-də siyahı yoxdur (corporate.py-dəki qeyd) |
| kapitalbank.az, azersun.com, bakcell.com, azertelecom.az | 403 bot qoruması |
| yapikredi.com.az, yelo.az | Xəbər siyahısı JS ilə yüklənir; tender elanları xəbər məqalələridir (siyahı SSR-də yoxdur) |
| cbar.az (Mərkəzi Bank) | "Müsabiqələr" səhifəsində tender elanlarını ayırmaq üçün sabit element tapılmadı |
| sosial.gov.az, dtsea.gov.az, sehiyye.gov.az, mincom.gov.az | "Elanlar" kadr/vətəndaş elanlarıdır, satınalma deyil |
| procure.az, azexport.az | procure.az SaaS tender platformasıdır (müştərilərin tenderləri girişlə); azexport.az-da statik elan siyahısı yoxdur — aqreqator kimi istifadə olunmadı |
| azerigaz, azersu, azerkimya, socar-polymer, azercosmos, aeroport saytları, bsu/qu.edu.az | Bu maşından açılmır — Mac worker-də `probe_sources` ilə yoxlanmalıdır |
| Dövlət universitetləri (unec, ada, aztu, adau, atu, bhos, azmiu) | Açılır, amma satınalma bölməsi yoxdur (yalnız ümumi elanlar/xəbərlər); satınalmalar etender.gov.az üzərindən gedir |
| Dövlət xəstəxanaları | TƏBİB tabeliyindəki müəssisələrin ayrı satınalma səhifəsi tapılmadı; satınalmalar etender-dədir |

**Nəticə:** dövlət qurumlarının satınalmalarının böyük hissəsi artıq `etender.gov.az` (qanun üzrə məcburi portal) vasitəsilə tutulur; qurumların öz saytları əsasən kadr/vətəndaş elanlarıdır. Əsas əlavə dəyər özəl sektor və bank/korporativ tenderlərdədir.

### Faza 4 – böyük investor/şirkət monitorinqi (2026-10-07)

**Qoşuldu** (`media.py`): **SOCAR press-relizlər** (`socar.az/az/page/press-relizler`, `/az/post/<slug>`, SSR — investisiya, layihə, tərəfdaş görüşləri) və **ACWA Power** (`acwapower.com/en/media-center/latest-news/`). ACWA xəbərləri dünya miqyaslıdır, ona görə `MediaSite.must_match` (Azərbaycan/Bakı/Xəzər/Abşeron... başlıq və ya URL-də) süzgəci var — uyğun gəlməyən xəbər AI triajına getmir. Canlıda ACWA-nın 13 xəbərindən 0-ı keçdi (hazırda Azərbaycanla bağlı xəbər yoxdur) — gözlənilən davranış.

**Sınanıb, qoşulmadı:**

| Mənbə | Səbəb |
|---|---|
| bp.com (Azerbaijan news) | 403 bot qoruması |
| socar.az/az/news | WAF "Request Rejected" (bot qoruması) — keçməyə cəhd edilmir; press-relizlər ayrı ünvandadır |
| Masdar | Newsroom JS ilə yüklənir, SSR-də 3 link (biri "Media Inquiries") |
| TotalEnergies | `/news` qlobal köhnə məqalələr verir; Azərbaycan səhifəsi (`/countries/azerbaijan`) 404 |
| PASHA Holding (pasha-holding.az) | Xəbər lenti 2025-in iyununda dayanıb, əsasən CSR |
| Neft Fondu (oilfund.az), AzPetrol | Xəbərlər maliyyə/yanacaq stansiyası — yemək fürsəti siqnalı zəifdir |
| SOCAR "Pre-kvalifikasiya"/Vendor Registration | Statik məlumat səhifəsi (FAQ), elan lenti deyil — izlənmir |
| Annual/Sustainability Reports | PDF, siqnal aşağı — izlənmir |
| Şirkətlərin LinkedIn/Instagram/Facebook səhifələri | ToS — Faza 7 qərarı |

### Faza 5 – sektor mediası və assosiasiyalar (2026-10-07)

**Qoşuldu:**
- `media.py`: **AmCham** (`amcham.az/en/event-calendar` → `/en/event/<id>`), **AHK Azerbaijan** (`aserbaidschan.ahk.de/en/events/<slug>`), **ATİA** — Tikinti İstehsalçıları Assosiasiyası (`atia-az.az/news/...`). Tədbir saytları (`category=event`) artıq tədbir açar sözləri ilə (forum, konfrans, event, reception...) süzülür; "Register now" düymə-linklərinin başlığı kartın `<h3>`-dən götürülür.
- Yeni mənbələr `report_energy` (neft-qaz/enerji) və `report_infra` (yol, tikinti, nəqliyyat): report.az `/energetika/` və `/infrastruktur/` bölmələri `/senaye/` ilə eyni markup-dadır (`local_only`, ABŞ IP-dən 403).

**Sektor mediası:** neft-qaz, tikinti və logistika üzrə ayrıca *ixtisaslaşmış* Azərbaycan xəbər saytı tapılmadı; bu sektorlar Trend/APA RSS-i, Report.az bölmələri, ATİA və sərgi saytları (Caspian Oil & Gas, BakuBuild, TransLogistica) ilə əhatə olunur.

**Sınanıb, qoşulmadı:** caspiannews.com (403), interfax.az və cci.az/eurochamber.az/itca.az (cavab yox), azernews.az (bölmə ünvanı 404), neftegaz.ru (468 bloku), bcca.az (British Chamber — saytda xəbər/tədbir lenti yoxdur, yalnız statik səhifələr), DEİK (Türkiyə-Azərbaycan Biznes Şurası — xəbər lenti SSR-də yoxdur), Caspian Energy Club (cavab yox), İtaliya və Çin ticarət qurumları (rəsmi sayt tapılmadı). report.az/en/energy qoşulmadı: ingilis xəbərləri əsasən neft qiyməti/proqnozdur, yemək siqnalı yoxdur.

### Faza 6 – tikinti/infrastruktur qatı (2026-10-07)

**Məntiq:** böyük tikinti obyektində çoxlu fəhlə olur — podratçıya düşərgə/obyekt yeməyi lazımdır. Tikinti sektorunu üç yerdən tuturuq: tender (hazırlıq mərhələsi), xəbər/elan (layihə başlanır), vakansiya (Facility/Camp/Site Manager — Faza 1).

**Qoşuldu:**
- **`etender_construction`** (yeni mənbə, `EtenderConstructionScraper`): eyni etender API-si, `CONSTRUCTION_KEYWORDS` (tikinti, inşa, yenidənqurma, əsaslı təmir, zavod, yol, körpü, tunel, sənaye parkı, yataqxana, düşərgə, ru/en) ilə. Açar söz **yalnız tender adında** axtarılır (sifarişçi adında deyil — "Dəmir Yolları" "dəmir yol"-a düşürdü); `CONSTRUCTION_EXCLUDE` kağız işi/avadanlıq/material/cari təmiri atır. Canlıda 351 aktiv tikinti-əlaqəli tenderdən ~70-i qalır. Eyni tender `etender` ilə də tutulsa `tenders.reference_for` ikisini bir tender sayır (dublikat).
- `media.py`: **Dövlət Şəhərsalma və Arxitektura Komitəsi** (`arxkom.gov.az`, xəbərlər + elanlar — layihə/satınalma bildirişləri) və **Azfen** (SOCAR-ın EPC podratçısı).

**Sınanıb, qoşulmadı:**

| Mənbə | Səbəb |
|---|---|
| HECOTEC (hecotec.az) | Açılır, amma xəbər linklərində başlıq mətni yoxdur (yalnız ikon); satınalma bölməsi statikdir |
| North West Construction, A+A Group, nwc.az, aagroup.az | Bu maşından açılmır |
| Rigzone | Baku filtri işləyir, amma yalnız 2 vakansiya (hər ikisi agentlik/xidmət şirkəti) — siqnal zəif |
| EnergyJobline | Ölkə süzgəci işləmir (qlobal say göstərir) |
| Orion (orionjobs.com) | Qeyri-şəffaf recruiter, işəgötürən adı gizlidir |
| Tikinti icazələri / tikintilərin dövlət reyestri | İcazə sistemi elektrondur (Şəhərsalma Komitəsi), amma **ictimai siyahı yoxdur** — yalnız ərizəçi girişi ilə; qoşulmur |
| General contractor / EPC / developer saytlarının geniş siyahısı | Mərkəzləşdirilmiş reyestr yoxdur; hər biri ayrı-ayrılıqda kəşf tələb edir (tövsiyə: etender-də qalib podratçı adlarından siyahı çıxarıb saytlarını yoxlamaq) |

## Ümumi dərs

İlk sənəd versiyası (development spec-in ilk qaralaması) hər saytın funksiyasını **görmədən, adından fərziyyə ilə** təsvir etmişdi (məs. "Marja.az = elan saytı", "Emlak.gov.az = qeydiyyat reyestri"). Bunların əksəriyyəti səhv çıxdı. **Nəticə:** yeni mənbə əlavə edərkən HƏMİŞƏ əvvəlcə brauzerdə real saytı aç, strukturu/API-ni tap, SONRA kodu yaz — adından güman etmə.
