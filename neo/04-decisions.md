# 04 — Decisions & Open Questions

## Qəbul edilmiş qərarlar

### Human-in-the-loop (AI heç vaxt birbaşa CRM-ə göndərmir)
- **Status:** Decided (2026-09-22)
- **Qərar:** AI skorlama `ai_score >= 0.6` olan lead-ləri `pending_review` statusuna qoyur, CRM-ə YOX. Yalnız admin paneldə insan "Təsdiqlə" düyməsini basanda CRM-ə göndərilir.
- **Niyə:** yanlış/qərəzli AI qərarının CRM-i "zibilləməsinin" qarşısını almaq üçün. Sənədin əsas mesajı ilə üst-üstə düşür: "AI analiz edir və xəbərdar edir, yekun qərar isə səlahiyyətli insan tərəfindən verilir."
- **Kodda:** `app/ai_filter.py`, `app/pipeline.py::send_lead_to_crm`

### AI xətası zamanı fail-safe davranış
- **Status:** Decided (2026-09-23)
- **Qərar:** OpenAI API əlçatan olmasa (API key yoxdur, şəbəkə xətası və s.), lead avtomatik `rejected`-ə düşmür — `pending_review`-a düşür, `ai_score=None`, `ai_reason="AI qiymətləndirməsi alınmadı — əl ilə yoxlanmalıdır"`.
- **Niyə:** ilk versiyada AI xətası = avtomatik rədd idi, bu isə API key olmadıqda BÜTÜN lead-lərin səssizcə itməsinə səbəb olurdu (data itkisi, sükutla). İndi insan hələ də görə bilir.
- **Kodda:** `app/ai_filter.py::AIFilter.score`

### AI Filter provideri: OpenAI (Anthropic əvəzinə)
- **Status:** Decided (2026-09-23)
- **Qərar:** `AIFilter` ilkin versiyada Anthropic (Claude) API istifadə edirdi, indi OpenAI API-yə (`gpt-4o-mini`, Chat Completions + `response_format=json_object`) keçirildi. Prompt, JSON format (`{"score", "reason"}`), threshold (0.6) və fail-safe davranış DƏYİŞMƏDİ — yalnız provider və `OPENAI_API_KEY` env dəyişəni.
- **Niyə:** komanda üzvü OpenAI açarı ilə davam etmək istədi.
- **Kodda:** `app/ai_filter.py`, `requirements.txt` (`anthropic` → `openai`)

### Development/test üçün SQLite, production üçün Postgres
- **Status:** Decided (2026-09-22)
- **Qərar:** `DATABASE_URL` təyin olunmayıbsa, `sqlite:///./neo_leads.db` işlədilir. Production-da `.env`-də Postgres URL-i verilməlidir.
- **Niyə:** development komandası admin paneli test edərkən Postgres qurmaq məcburiyyətində qalmasın.
- **Kodda:** `app/db/engine.py`

### Azerweb.az-da yalnız tender/announcement, vacancy yox
- **Status:** Decided (2026-09-23)
- **Qərar:** Azerweb API-də "catering" axtarışı iş elanlarını da qaytarır (Chef, Housekeeper və s.) — bunlar müştəri lead-i deyil. Yalnız `type in {tender, announcement}` saxlanılır.
- **Kodda:** `app/scrapers/azerweb.py::RELEVANT_TYPES`

### LinkedIn scraping yanaşması
- **Status:** Decided (2026-09-23)
- **Qərar:** Birbaşa HTML/cookie scraping YOX — Apify-də lisenziyalı actor `harvestapi/linkedin-post-search` (keyword üzrə post axtarışı, cookie/hesab lazım deyil) istifadə olunur. Apify-nin `run-sync-get-dataset-items` REST endpoint-i birbaşa `httpx` ilə çağırılır (ayrıca `apify-client` SDK-sı əlavə olunmadı — mövcud `BaseScraper.post()` kifayət etdi).
- **Niyə:** ToS pozuntusu/hesab bloklanma riskini aradan qaldırır — açıq sualda qeyd olunan "lisenziyalı 3-cü tərəf provider" yolu seçildi (Proxycurl/Phantombuster əvəzinə Apify, sərfəli qiymət və sadə REST API-yə görə).
- **Qeyd:** `APIFY_TOKEN` `.env`-də olmalıdır, yoxdursa scraper boş siyahı qaytarır və loglayır (digər fail-safe qərarlarla eyni naxış). Post başına ödəniş olduğu üçün scheduler-də gündə 1 dəfə işə salınır (bax `app/scheduler.py`), tez-tez YOX.
- **Kodda:** `app/scrapers/linkedin.py`, `app/scrapers/base.py::BaseScraper.post`

### LinkedIn axtarış sözləri (canlı test edilib)
- **Status:** Decided (2026-09-23), Apify MCP vasitəsilə canlı test edildikdən sonra
- **Test:** 2 dəfə real actor run (26 post cəmi) — sadə açar sözlərlə (`"catering Bakı"`, `"corporate catering Baku"`, `"looking for catering Baku"` və s.) axtarış demək olar TAMAMİLƏ iş elanı (AzVak, ASG Aviation Services Group — "Baş Aşpaz tələb olunur", "Catering Loader", hətta əlaqəsi olmayan "Lead Accountant" vakansiyaları, çünki şirkət təsvirində "catering" sözü keçir) və catering şirkətlərinin öz reklamı/HR məzmunu ilə doldu. 26 postdan cəmi 1-i (bax aşağı) real müştəri siqnalı idi.
- **Nə işlədi:** niyyət bildirən ifadələr (`"catering axtarırıq"`) — nümunə: Azeri Steel MMC-nin İR rəhbəri əməkdaşların gündəlik qidalanması üçün catering şirkəti ilə əməkdaşlıq axtardığını yazmışdı (dəqiq bizim istədiyimiz lead tipi).
- **Qərar:** `SEARCH_QUERIES` niyyət-ifadələrinə keçirildi (`"catering axtarırıq"`, `"catering şirkəti ilə əməkdaşlıq axtarırıq"`, `"iaşə təminatçısı axtarılır"` və s.), bare keyword-lər çıxarıldı. Əlavə olaraq `EXCLUDE_KEYWORDS` filtri əlavə olundu (`"tələb olunur"`, `"əməkhaqqı"`, `"cv göndər"`, `"hr@"` və s.) — çünki "axtarırıq" sözü iş elanlarında da "əməkdaş axtarırıq" mənasında çıxır, sadəcə niyyət-ifadəsi kifayət deyil.
- **Qeyd:** filtr mükəmməl deyil (məs. marketinq postları hələ keçə bilər) — qalan səs-küyü AI Filter (`ai_score`) və insan review tutmalıdır, sənədin əsas prinsipinə uyğun (bax "Human-in-the-loop").
- **Yeniləmə (2026-09-23, ikinci test dövrəsi):** bax aşağı "LinkedIn filtrləri — hədəf müştəri profili".

### LinkedIn filtrləri — hədəf müştəri profili
- **Status:** Decided (2026-09-23), 3 əlavə canlı test + real kodla dry-run
- **Hədəf müştəri:** Azərbaycanda əməkdaşları/işçiləri üçün gündəlik paketlənmiş/porsiyalı yemək (lunch box, toplu çatdırılma) alan təşkilatlar — ofis, zavod, tikinti, anbar, sənaye parkı. Korporativ tədbir ikinci dərəcəlidir.
- **Tapıntılar:**
  - LinkedIn axtarışı uzun ifadələrə (4+ söz, məs. `"əməkdaşların qidalanması üçün"`, `"yemək təchizatı tender"`) **0 nəticə** qaytarır — yalnız qısa (2-3 söz) sorğular işləyir.
  - `"korporativ qidalanma"` alıcı yox, **satıcı** dilidir — nəticələrin çoxu rəqib reklamı idi: **Plan&Eat** (ən aktiv), **Lunchbox.az** (eyni məhsul), Doydum Catering, TAIS İKF, Catering 145 Group.
  - Türkcə "bakış açısı" içində "bakı" var → sadə substring yoxlaması türk postlarını Azərbaycan kimi qəbul edirdi. Tam söz regex-i ilə düzəldildi.
  - Python-da `"İ".lower()` → `"i̇"` (birləşdirici nöqtə ilə) — `"VAKANSİYA:"` heç vaxt `"vakansiya:"` ilə uyğun gəlmirdi. `_norm()` ilə düzəldildi.
  - "tələb olunur" filtrdən çıxarıldı — alıcı da "nahar xidməti tələb olunur" yaza bilər.
- **Qərar — iki qatlı filtr:**
  1. Scraper (ucuz, deterministik, `rejection_reason()`): Azərbaycan olmayan → at; vakansiya işarələri və ya iş elanı saytı müəllifi (AzVak, Jobsearch.az, Vakansiya.biz) → at; `KNOWN_COMPETITORS` → at.
  2. AI Filter (semantik): prompt hədəf müştəri profili ilə yenidən yazıldı — rəqib reklamı, fikir yazısı, avadanlıq satışı → 0.0-0.2.
- **Dry-run nəticəsi (3 aylıq pəncərə):** 80 post → scraper 66-nı atdı (41 vakansiya, 12 rəqib, 13 xarici) → 14 AI-yə → **1 `pending_review`** (Azeri Steel, 0.9), 13 `rejected`. Yanlış müsbət: 0.
- **Real gözlənti:** LinkedIn-də Azərbaycanda açıq "catering axtarırıq" postu nadirdir (3 ayda ~1). LinkedIn köməkçi mənbədir; bu müştəri profili üçün `etender` (iaşə tenderləri) çox güman ki daha güclüdür.
- **Hər lead-də:** müəllifin LinkedIn profili `contact_website`-a, postdakı email `contact_email`-ə yazılır, müəllifin vəzifəsi `raw_text`-in sonuna əlavə olunur (satış komandası kimlə danışacağını bilsin).
- **Kodda:** `app/scrapers/linkedin.py` (`SEARCH_QUERIES`, `AZ_MARKER_RE`, `VACANCY_MARKERS`, `JOB_BOARDS`, `KNOWN_COMPETITORS`, `rejection_reason`), `app/ai_filter.py::SYSTEM_PROMPT`

### İşçilərinə nahar verən şirkətlər (vakansiya elanlarından lead)
- **Status:** Decided (2026-09-23), canlı test + real kodla dry-run
- **Problem:** açıq alıcı postu çox nadirdir (3 ayda ~1). LinkedIn-in həcmi azdır.
- **Fikir:** Azərbaycan vakansiya elanlarının çoxu "Nahar şirkət tərəfindən təmin olunur" yazır — bu, işəgötürənin artıq hər gün işçilərini yedizdirdiyini (yəni yemək aldığını/təşkil etdiyini) göstərir. Bu, təchizatçı dəyişməyə potensial müştəridir. Əvvəl bu elanlar "vakansiya" kimi atılırdı.
- **Test edilib, rədd edilib:** genişlənmə sorğuları (`"yeni zavod"`, `"sənaye parkı"`, `"istehsal müəssisəsi"`) — 29 postun demək olar hamısı fikir yazısı, logistika reklamı, universitet xəbəri idi. Belə xəbərləri artıq marja/fed/oxu scraper-ləri tutur.
- **Qərar:** `"nahar şirkət tərəfindən"`, `"yemək şirkət tərəfindən"` sorğuları əlavə olundu. Vakansiya + `MEAL_PROVIDED_RE` → post rədd edilmir, **işəgötürən şirkət** lead olur:
  - İşəgötürən `employer_identity()` ilə tapılır: əvvəl korporativ email domeni (`hr@vumes.az` → `vumes.az`), sonra `"X" MMC` adı. Pulsuz email (gmail, mail.ru) və iş elanı saytı/agentlik domenləri (`JOB_BOARDS`, məs. `humanpro.az` — gizli müştəri üçün elan verir) sayılmır. Canlı testdə 32 elandan 29-da işəgötürən tapıldı (27 domen, 2 ad).
  - `source_url` = şirkətin saytı (post URL-i YOX) — dedup şirkət üzrə olur: eyni şirkətin 5 elanı 1 lead, sonrakı günlərin elanları yeni lead yaratmır. Post linki `raw_text`-dədir.
  - `raw_text` `[SİQNAL: ...]` ilə başlayır; AI prompt bunu iş elanı kimi yox, işəgötürən lead-i kimi 0.6-0.8 qiymətləndirir. İşəgötürən özü catering şirkətidirsə → rəqib (ATS Food bu testdə tapılıb, `KNOWN_COMPETITORS`-a əlavə olundu; rəqib yoxlaması indi vakansiyadan ƏVVƏL gedir).
- **Dry-run nəticəsi (1 aylıq pəncərə):** **22 işəgötürən lead-i**, hamısı 0.7 → `pending_review` (Azersun, Sirab, Gilan-Knauf, Avrora, Sea Breeze, Rossmann, VUMART, Chinar Park və s.) + əvvəlki alıcı-post axını. Əvvəl: ayda ~0.3 lead.
- **Qiymət:** 7 sorğu × 30 post (gündəlik, 1 həftəlik pəncərə) = gündə ≤210 post ≈ ≤$0.4/gün (Apify). OpenAI (`gpt-4o-mini`) xərci cüzidir.
- **Məhdudiyyət:** AI şirkət ölçüsünü bilmir (elanda yazılmır) — demək olar hamısına 0.7 verir. Sıralama üçün satış komandası özü baxmalıdır.
- **Kodda:** `app/scrapers/linkedin.py` (`MEAL_PROVIDED_RE`, `employer_identity`, `_employer_lead`), `app/ai_filter.py::SYSTEM_PROMPT`

## Açıq suallar (hələ qərar verilməyib)

### CRM real endpoint-i
- **Status:** Open
- **Sual:** `/api/opportunities` endpoint-inin dəqiq yolu, request/response formatı, autentifikasiya üsulu (API key vs OAuth) nədir?
- **Niyə açıqdır:** hazırkı `CRM_BASE_URL` (`.env`) placeholder-dir, real CRM komandası ilə hələ dəqiqləşdirilməyib.
- **Kim qərar verməlidir:** CRM development komandası
- **Hazırkı vəziyyət:** "Təsdiqlə → CRM" düyməsi işləyir, amma real endpoint olmadığı üçün aydın xəta mesajı ilə uğursuz olur (səssiz uğur GÖSTƏRMİR — bu bilərəkdən belədir)

### `ai_score` eşik dəyəri (0.6)
- **Status:** Open (default qiymətdir, sınaqdan keçirilməyib)
- **Sual:** 0.6 düzgün həddir, yoxsa real istifadədən sonra tənzimlənməlidir?
- **Kim qərar verməlidir:** satış komandası, real lead axını gördükdən sonra
- **Kodda:** `app/ai_filter.py::SCORE_THRESHOLD`

### Scraping tezliyi (§7)
- **Status:** Decided default, amma sınaqdan keçirilməyib
- **Hazırkı dəyərlər:** xəbər saytları 1-2 saat, dövlət portalları gündə 1 dəfə (bax `app/scheduler.py`)
- **Sual:** real yükə görə bu tezliklər dəyişməlidirmi?

### Hər saytın ToS/robots.txt hüquqi təsdiqi
- **Status:** Open — HƏLƏ EDİLMƏYİB
- **Qeyd:** development başlamazdan (yəni production-a keçmədən) əvvəl 7 birbaşa scrape olunan mənbə üçün hüquq şöbəsindən yazılı təsdiq alınmalıdır. LinkedIn bura daxil deyil (Apify — lisenziyalı 3-cü tərəf, öz ToS-u üzərindən işləyir), amma Apify-nin öz istifadə şərtlərinin qəbul edilməsi də hüquq şöbəsi ilə yoxlanmalıdır. Bu, kodun yazılmasını əngəlləmədi, amma **production deploy-u əngəlləməlidir**.
