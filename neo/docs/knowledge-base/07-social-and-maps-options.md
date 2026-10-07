# 07 — Sosial şəbəkə və xəritə mənbələri: variantlar (qərar sənədi)

> **Status:** Open — qərar gözləyir (2026-10-07). **A variantı icra olunub** (aşağıda "İcra statusu"); B, C, D, E gözləyir. Bu sənəd **kod yazmır**: Mənbə siyahısının Faza 7-sidir.
> Birbaşa scrape bu kanalların ToS-unu pozur, buna görə yalnız **rəsmi API** və ya **lisenziyalı provayder** yolları təhlil olunur.
> Hüquqi rəy deyil — ümumi ToS qaydalarına və aşağıdakı rəsmi sənədlərə əsaslanan mühəndis qiymətləndirməsidir.

## Nə istənilib (siyahıdan)

1. Şirkətlərin **LinkedIn / Instagram / Facebook** səhifələri (news, press, projects, careers, procurement postları)
2. **Tikinti obyektləri üzrə sosial media**
3. **Google Maps üzərindən yeni obyektlər**

Məqsəd: yemək/catering ehtiyacı yaradan siqnal — yeni zavod/ofis/tikinti sahəsi/düşərgə, tədbir, işçi qəbulu.

## Hazırkı vəziyyət

- **LinkedIn:** `linkedin` mənbəyi Apify actor (`harvestapi/linkedin-post-search`) ilə **açar sözlə post axtarışı** edir (qərar: `04-decisions.md` → "LinkedIn scraping yanaşması"). Müəyyən şirkət səhifəsini izləmir. Qiymət: ~$1.50–2 / 1000 post (repo sənədinə görə).
- Instagram, Facebook, Google Maps: **yoxdur**.

## Variantlar

### A. LinkedIn şirkət səhifələri (izləmə siyahısı)
| | |
|---|---|
| Necə | Eyni Apify provayderində şirkət-postları actor-u ilə **seçilmiş şirkət siyahısı** (SOCAR, bp, ACWA, AFEZ, iri podratçılar) üzrə son postlar |
| Xərc | Post başına ödəniş (indiki actor ilə eyni dərəcə, ~$1.5–2/1000 post); 30 şirkət × 10 post × həftəlik ≈ cüzi |
| Risk | **Orta** — LinkedIn ToS scraping-i qadağan edir; indiki yanaşma (lisenziyalı üçüncü tərəf) riski provayderə ötürür, tam aradan qaldırmır. Artıq qəbul edilmiş risk sinfidir |
| Siqnal | **Yüksək** — rəsmi şirkət elanları, layihə başlanğıcları, HR elanları |
| İş həcmi | Kiçik (yeni scraper `linkedin.py`-ə oxşar, `APIFY_TOKEN` mövcuddur) |

### B. Facebook səhifələri
| | |
|---|---|
| Rəsmi yol | Meta Graph API — **Page Public Content Access** (başqa səhifələrin ictimai postlarını oxumaq üçün) |
| Tələblər | Meta **App Review** + **business verification** (rəsmi sənəd: "requires successful completion of App Review… only available with business verification"). İnkişaf rejimində yalnız özünüzün admin olduğunuz səhifələr oxunur |
| Xərc | API pulsuzdur; **vaxt xərci** — şirkət doğrulaması və review həftələr çəkə bilər |
| Risk | **Aşağı** (rəsmi yol), amma review rədd oluna bilər: icazə verilən məqsəd "analyze/display posts and engagement on Pages"-dir, lead generation üçün istifadə izahı review-də çətinlik yarada bilər |
| Siqnal | **Orta** — Azərbaycan şirkətlərinin çoxu Facebook-da aktivdir, amma postların çoxu marketinqdir |
| Qeyri-rəsmi yol | Apify-tipli Facebook scraper-lər mövcuddur, amma Meta ToS-u pozur — **tövsiyə olunmur** |

### C. Instagram
| | |
|---|---|
| Rəsmi yol | Instagram Graph API: **hashtag search** və **business discovery** (Business/Creator hesab tələb edir) |
| Məhdudiyyətlər (rəsmi) | 7 günlük pəncərədə maksimum **30 unikal hashtag**; hashtag axtarışı yalnız ictimai media qaytarır; "recent" yalnız son **24 saat**, ən çox **250** post |
| Risk | Aşağı (rəsmi), amma işə yarayan siqnal az |
| Siqnal | **Aşağı** B2B üçün — Instagram şirkət postları əsasən brend məzmunudur; azərbaycanca hashtag-lar səs-küylüdür |
| Tövsiyə | **Etməmək** (xərc/fayda zəifdir). Yalnız çox spesifik izləmə siyahısı (5–10 hesab, business discovery) lazım olsa yenidən baxmaq |

### D. Google Maps — "yeni obyektlər"
| | |
|---|---|
| Əsas məhdudiyyət | Places API **yaradılma tarixi vermir**. "Yeni obyekt" yalnız **dövri snapshot fərqi** ilə aşkarlanır (həftəlik axtarışın nəticəsini əvvəlki ilə müqayisə: yeni `place_id`) |
| Rəsmi yol | Google Places API (New) — Text Search. Qiymət (2026, açıq mənbələrə görə): **Pro $32/1000 sorğu**, Enterprise (telefon, sayt, iş saatı) **$35/1000** (həcmə görə endirim). Sorğu başına ≤20 nəticə. Pulsuz aylıq limit ola bilər — Google Cloud konsolunda yoxlanmalıdır |
| Lisenziyalı provayder | Apify "Google Maps Scraper" (`compass/crawler-google-places`): "$1.50/1000 yer"-dən başlayır (digər mənbələr $4–7/1000 göstərir) — ToS baxımından Google Maps Platform qaydalarını yan keçir, **orta risk** |
| Təxmini xərc (rəsmi API) | Pilot: 2 rayon × 8 sorğu növü (zavod, fabrik, tikinti sahəsi, ofis binası, sənaye parkı, anbar, otel-düşərgə...) × 4 səhifə × həftəlik ≈ 64 sorğu/həftə ≈ **$2–3/həftə**. Bütün ölkə (100 şəbəkə hücrəsi) ≈ 3200 sorğu/həftə ≈ **$100+/həftə** |
| Siqnal | **Orta**: yeni zavod/ofis/restoran açılışı Maps-ə gec düşür (aylar), amma xəbərlərdə olmayan kiçik obyektləri tutur |
| Risk | Rəsmi API ilə **aşağı**; cache/saxlama qaydalarına əməl etmək lazımdır (yalnız `place_id` uzunmüddətli saxlanır) |

### E. "Tikinti obyektləri üzrə sosial media"
Konkret kanal göstərilməyib. Real alternativlər:
- Podratçı/developer **LinkedIn + Facebook səhifələri** → A və B variantları ilə əhatə olunur.
- **Telegram ictimai kanalları** (siyahıda yox): rəsmi Telegram API ilə ictimai kanalların oxunması mümkündür, Azərbaycan tikinti/vakansiya kanalları aktivdir. Yoxlanmayıb — istəsəniz ayrıca araşdırma.

## Tövsiyə (sıra ilə)

| # | Addım | Niyə | Xərc | Risk |
|---|---|---|---|---|
| 1 | **A (LinkedIn izləmə siyahısı)** | Ən yüksək siqnal, mövcud provayder və token, kiçik iş | <$10/ay (təxmin: 30 şirkət × 10 post × 4 həftə ≈ 1200 post) | Artıq qəbul edilmiş |
| 2 | **D pilot (rəsmi Places API, 1–2 rayon, büdcə limiti ilə)** | "Yeni obyekt" ehtiyacını yeganə birbaşa ödəyən yol; kiçik xərclə səmərəni ölçmək olur | ~$10/ay (pilot) | Aşağı |
| 3 | **B (Facebook rəsmi API)** — yalnız şirkətin business verification-ı varsa | Rəsmi, amma təsdiq vaxtı uzundur; paralel başlamaq olar | $0 + vaxt | Aşağı |
| 4 | **C (Instagram)** | Etmək | — | — |
| — | Qeyri-rəsmi scraper (FB/IG/Maps) | **Etmək** — ToS pozuntusu, blok riski, biznes üçün risk dəyərindən çoxdur | — | Yüksək |

## Sizdən lazım olan qərarlar

1. **A** üçün izləniləcək şirkət siyahısı (ilkin təklif: SOCAR, bp, ACWA, AFEZ + 10–15 iri podratçı/sənaye şirkəti) və aylıq Apify büdcəsi təsdiqlənirmi?
2. **D** pilotu üçün Google Cloud hesabı və **aylıq büdcə limiti** (məs. $20) təyin oluna bilərmi? Hansı 1–2 rayonla başlayaq (Sumqayıt, Abşeron, Gəncə)?
3. Şirkətin Meta **business verification**-ı mövcuddurmu (B üçün)?
4. Telegram ictimai kanalları araşdırılsınmı?

Qərarlardan sonra hər addım ayrıca plan və PR ilə icra olunur (mövcud `LinkListSite/MediaSite` yox, ayrıca scraper sinfi: `app/scrapers/` altında, `APIFY_TOKEN`/`GOOGLE_PLACES_API_KEY` `.env`-də).

## Mənbələr
- Meta, *Page Public Content Access* və *Features Reference*: developers.facebook.com/docs/features-reference/page-public-content-access
- Meta, *Instagram API — Hashtag Search*: developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/hashtag-search
- Google Maps Platform qiymətləri: developers.google.com/maps/billing-and-pricing/pricing (rəqəmlər üçüncü tərəf icmallarından alınıb, qərardan əvvəl rəsmi səhifədə təsdiqlənməlidir)
- Apify, *Google Maps Scraper*: apify.com/compass/crawler-google-places

## İcra statusu

- **A — LinkedIn izləmə siyahısı: kodlaşdırılıb (2026-10-07).** `app/scrapers/linkedin_companies.py`, mənbə açarı `linkedin_companies`
  (`app/sources.py`, gündə 1, `APIFY_TOKEN` lazımdır — yoxdursa heç nə çağırılmır və xərc olmur). Actor: `harvestapi/linkedin-company-posts`
  (giriş `targetUrls`/`maxPosts`/`postedLimit`, çıxış post-search actor-u ilə eyni sahələr). Siyahı kodda `WATCHLIST`-dədir (SOCAR, bp, ACWA Power,
  TotalEnergies, Masdar, AZPROMO, AFEZ); qlobal şirkətlər `az_only=True` — yalnız Azərbaycanla bağlı postlar keçir və birbaşa AI skoruna gedir.
  - ⚠️ **Slug-lar canlı təsdiqlənməyib** (token olmadan yoxlamaq mümkün olmadı). Yanlış slug 0 post qaytarır və `linkedin_companies_empty` loglanır — ilk
    işə salmadan sonra loglara baxıb `WATCHLIST`-i düzəldin; siyahını 10–15 iri podratçı/sənaye şirkəti ilə genişləndirmək üçün adlarınız lazımdır.
  - Təxmini xərc: 7 şirkət × 10 post × gündə 1 işə salma ≈ 2100 post/ay ≈ $3/ay (postedLimit=week olduğundan təkrar postlar da sayılır; URL ilə dedup olunur).
- **D — Google Places pilotu:** Google Cloud API açarı və aylıq büdcə limiti gözlənilir.
- **B, C, E:** qərar gözlənilir.
