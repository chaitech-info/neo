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
- **Yeniləmə (2026-09-27):** məntiq `app/scrapers/employer_signal.py`-a köçürüldü və boss.az-da da işlədilir — bax aşağı.

### Mənbə keyfiyyəti auditi və boss.az (2026-09-27)
- **Status:** Decided, canlı audit (hər mənbə real çəkilib + AI skorlanıb, DB-yə yazılmadan)
- **Audit nəticəsi (əvvəl):** 10 mənbə → 19 lead, cəmi 2-si ≥0.6 (fed.az və etender-də catering tenderləri). Xəbər açar sözləri (`forum`, `sərgi`, `tədbir`, `açılış`, `restoran`) 16 başlıqdan 15-ni dövlət tədbiri səs-küyü ilə doldurdu (İnvestisiya Forumu, F1 açılışı) — hamısı 0.0.
- **Qərarlar:**
  1. **boss.az** yeni mənbə (`app/scrapers/boss.py`): bütün aktiv elanlar GraphQL ilə oxunur, nahar qeyd olunanların işəgötürəni lead olur (şirkət başına bir lead, vəzifələr siyahısı ölçü siqnalıdır). Filtr: anonim ad ("Company"), rəqib, öz mətbəxi olan (otel/restoran/kafe/catering — adda). **Nəticə: 660 elan → 29 `pending_review` işəgötürən** (Siyəzən-Broyler, Zeytun Pharma, Unimetal, Avromak, CHOCO-ARS, Kral ət, Perfect Logistics, Aramex və s.).
  2. **Ortaq modul** `employer_signal.py` (LinkedIn + boss.az): `fold()` — `"İ".lower()` nöqtə problemi və `az_lower()`-un "CATERING" → "caterıng" problemi, hər ikisi həll.
  3. **Mənbələrarası dedup:** `Normalizer` indi `source_url`-u mənbədən asılı olmayaraq yoxlayır (`repo.url_exists`) — işəgötürənin URL-i onun saytıdır, eyni şirkət LinkedIn və boss.az-dan 1 lead olur.
  4. **Xəbər açar sözləri** `base.NEWS_KEYWORDS`-da birləşdirildi (marja/1news/oxu/fed): dövlət tədbiri sözləri çıxarıldı; genişlənmə (zavod, sənaye parkı, "təməli qoyul", "yeni iş yeri") və mədən ("qızıl mədən", "filiz", "istehsala başla") siqnalları əlavə olundu. Canlı başlıqlarda: köhnə 16 uyğunluq/1 faydalı → yeni 3/3 (Azərsun zavodu 2 dəfə böyüdür, Mingəçevir tekstil zavodu).
  5. **report.az/senaye** yeni xəbər mənbəyi (`ReportSenayeScraper`).
  6. **AI prompt:** işəgötürən ölçüsü (fəhlə/operator/anbar və ya bir neçə vakansiya → 0.75-0.85, kiçik ofis → 0.6); hazır yemək bişirən işəgötürən → 0.1-0.2 (qida istehsalı zavodları istisna); konkret şirkətli zavod/genişlənmə xəbəri → 0.6-0.7 (əvvəl 0.5 alıb eşikdən aşağı qalırdı); `reason` qaydaların mətnini təkrarlamasın (əvvəl "PAŞA Holding memorandumu" üçün "rəqib reklam edir" yazırdı).
  7. **fed.az başlıqları:** kart mətninə yapışan "Sənaye26 Sen 2026" / "13:0126 Sen 2026" təmizlənir.
- **Qəbul edilmiş məhdudiyyət:** gpt-4o-mini ölçü qaydalarını zəif tətbiq edir (boss.az-da demək olar hamısı 0.75). Hamısı real işəgötürəndir, sıralama satış komandasındadır.
- **Kodda:** `app/scrapers/boss.py`, `app/scrapers/employer_signal.py`, `app/normalizer.py`, `app/db/repository.py::url_exists`, `app/scrapers/base.py::NEWS_KEYWORDS`, `app/scrapers/news.py`, `app/ai_filter.py`

### Açar söz + AI triaj — "heç bir fürsət buraxılmasın" (2026-09-27)
- **Status:** Decided, etender və boss.az-da canlı ölçülüb
- **Problem:** hər mənbə sabit açar söz siyahısı ilə süzürdü; siyahıda olmayan söz = buraxılmış fürsət. Etender-də 5 sözlə SERVER axtarışı edilirdi.
- **Ölçü (etender, 1707 "aktiv" tender):** 486-sının son tarixi hələ keçməyib (EventStatus=1 bitmişləri də qaytarır — scraper 2024-cü il tenderlərini də ötürürdü). Köhnə 5 sözlük axtarış: 4 nəticə, **0 real fürsət** (avadanlıq, təmir, fevralda bitmiş). Buraxılanlar: tədbir təşkili tenderləri — Şabran bayram tədbirləri, SOFAZ komandaqurma, Şuşa 2026 bağlanış mərasimi, İsmayıllı 95 illik, İdman Festivalı (təşkilatçı yemək alır).
- **Qərar:**
  1. **Açar sözlər genişləndirildi** (`base.py`: `MEAL_KEYWORDS`, `EVENT_KEYWORDS`, `EXPANSION_KEYWORDS`): az kök sözləri + rus + ingilis. Uyğunluq `fold()` ilə sözün əvvəlində (böyük hərf, "İ"/"ı", şəkilçi problemləri həll). "müsabiqə" yoxdur — tenderin öz adıdır.
  2. **AI başlıq triajı** (`app/ai_triage.py`): siyahıdakı BÜTÜN başlıqlar 40-lıq partiyalarla AI-yə, hər başlıq üçün 1/0 (nömrə siyahısı istəyəndə model həm artıq seçir, həm buraxırdı). "Şübhədə daxil et". Tender və xəbər üçün ayrı qaydalar (tenderdə tikinti/təmir = 0: alıcı yemək almır). Pozuq cavab → partiyanın hamısı daxil. AI işləmirsə → yalnız açar sözlər (heç nə itmir).
  3. **Seçim = açar söz VƏ YA AI** (`BaseScraper.select_relevant`) — bütün siyahı mənbələrində (xəbərlər, fed tenderləri, şirkət saytları, emlak, Dünya Bankı, bp EDP, etender). Logda `candidates_selected`: keyword/ai/ai_only sayları.
  4. **Etender:** server axtarışı yox — bütün aktiv tenderlər (100-lük səhifə, ~18 sorğu), son tarixi keçənlər kodda atılır, alıcı + son tarix `raw_text`-də.
  5. **AI skorlama düzəlişləri:** tədbir təşkili tenderi → 0.6-0.7 (yemək adı çəkilməsə də); müəssisə qidalanması → 0.8-1.0; tikinti/təmir tenderi → 0.1-0.2 (əvvəl su anbarı tikintisi 0.7 alırdı). **Son tarix kodda hesablanır** (`annotate_deadline` → "[SON TARİX: AKTİV — N gün qalıb]") — gpt-4o-mini 06.10.2026-nı 27.09.2026-da "keçib" sayıb açıq tenderi rədd etmişdi.
  6. **OpenAI 429:** paralel skorlama 200k TPM limitinə çatırdı — `max_retries=8` (eksponensial gözləmə).
- **Nəticə (etender, 486 açıq tender, tam AI skoru = həqiqət):** köhnə axtarış 0/5, yeni açar sözlər 5/5, AI triaj 4/5, birlikdə 5/5 real fürsət.
- **boss.az yemək imtiyazı naxışı** (`employer_signal.mentions_company_meal`): köhnə tək regex 660 elandan 49-u tuturdu; tərs sıra ("şirkət tərəfindən pulsuz nahar"), "günorta yeməyi", "nahar verilir", "şirkət hesabına", "qidalanma kartı", "əlavə imkanlar: nahar", rusca "обед за счёт компании" buraxılırdı. Yeni: **69 elan** (+20 real). İnkar ("... verilmir") və iş qrafiki ("nahar fasiləsi", "nahar: 13:00-14:00") istisna — köhnə regex LOADSPHERE-in "nahar ... şirkət tərəfindən verilmir" elanını səhvən lead etmişdi.
- **Qalan risk:** xəbərlərdə yalnız başlıq yoxlanır (məqalə mətni yox). AI triaj başlıqda semantik uyğunluğu tutur, amma fürsət yalnız mətndədirsə buraxıla bilər. "AI rədd etdi" səhifəsi (`/leads/ai_rejected`) AI-nin rədd etdiklərini əl ilə yoxlamaq üçündür.
- **Kodda:** `app/ai_triage.py`, `app/scrapers/base.py` (`fold`, keyword siyahıları, `select_relevant`), `app/scrapers/etender.py`, `app/scrapers/employer_signal.py`, `app/ai_filter.py` (`annotate_deadline`, prompt), testlər `tests/test_keyword_recall.py`

### CRM baza sxemi (2026-09-28)
- **Status:** Decided — icra PR-larla (`feature/crm-schema`)
- **Qərar:** tək `leads` cədvəlinin üzərinə 10 cədvəl: toplama (`sources`, `scrape_runs`, `interest_areas`, `keywords`), lead (`organizations`, `tenders`, `leads`-ə yeni sütunlar), CRM (`roles`, `users`, `opportunities`, `tasks`). `leads`-dən heç nə silinmir. Sxem layihəsi Claude artifact "Neo CRM Sxemi"-dədir.
- **Alt-qərarlar:**
  1. **Öz CRM-imiz** — xarici CRM-ə göndərmə (`crm_client.py`) istifadədən çıxır; fürsətlər `opportunities`-də.
  2. **Bir istifadəçi = bir rol** (`admin · manager · sales · viewer`).
  3. **Fürsət mərhələləri:** `new → contacted → qualified → proposal → negotiation → won | lost`; tender mərhələləri atlayır (`new → qualified → proposal → won | lost`). `lost` səbəbi seçimdən (`price · competitor · no_response · not_eligible · deadline_missed · other`) — AI əks-əlaqəsi üçün.
  4. **Hazırda SQLite, sonra PostgreSQL** — portativ tiplər (VARCHAR+CHECK enum, UTC `DateTime`, `JSON`), Alembic batch rejimi.
  5. **Açar sözlər maraq sahəsi + mənbə növü** (`applies_to`: all/news/tender/jobs) səviyyəsində, hər mənbəyə ayrıca siyahı yox. İlkin data koddakı siyahıların dəqiq nüsxəsidir (`MEAL` → all, `EVENT` → tender, `EXPANSION` → news).
- **Mərhələlər:** PR 1 baza (Alembic, cədvəllər, ilkin data, `create_user`); PR 2 deadline + mənbələr arası dublikat; PR 3 giriş/rollar; PR 4 CRM ekranları və "Təsdiqlə → fürsət" (`sent_to_crm` → `converted` də onda).
- **Kodda:** `app/db/models.py`, `migrations/`, `app/db/migrate.py`, `app/auth/`, `scripts/create_user.py`, testlər `tests/test_migrations.py`, `tests/test_auth.py`
- **PR 2 (deadline + dublikat, 2026-09-28):**
  - `deadline_at` scraper-lərin mətnə yazdığı "Son tarix: …" sətrindən oxunur (`app/deadlines.py`) — scraper-lərə toxunulmayıb. Saatsız son tarix = Bakı vaxtı 23:59; saxlama UTC.
  - Mənbələr arası dublikat `tenders` ilə (`app/tenders.py`): eyni istinad nömrəsi (etender `eventId`, bp EDP nömrəsi, Dünya Bankı ID) və ya eyni barmaq izi (normal təşkilat + normal başlıq) + eyni son tarix (±1 gün). Dublikat silinmir: `duplicate` + `duplicate_of`, AI-yə göndərilmir.
  - **Bilinən hədd:** fed.az məqaləsi ↔ etender elanı tutulmur — başlıqlar tam fərqlidir, etender siyahı API-si sifarişçi VÖEN-ini vermir. fed.az mətnindəki VÖEN `organizations`-a yazılır (CRM üçün). AI müqayisəsi Faza 2.
  - Hər scrape `scrape_runs`-a yazılır; dashboard "Son nəticə" server yenidən başlayanda bazadan oxunur. Mesaj: "N yeni lead — X validasiyaya, Y AI rədd etdi, Z başqa mənbədə artıq var, W təkrar".
  - Köhnə lead-lər server başlayanda idempotent doldurulur (`app/db/backfill.py`).
- **PR 3 (giriş və rollar, 2026-09-28):**
  - Bütün səhifələr giriş tələb edir; ilk admin brauzerdən `/setup` ilə (yalnız istifadəçi yoxdursa), qalanları admin "İstifadəçilər" səhifəsindən.
  - Rollar iyerarxikdir: viewer < sales (validasiya) < manager (scrape) < admin (istifadəçilər). Düymələr rola görə gizlənir, route-lar ayrıca qorunur (`app/auth/security.py`).
  - Sessiya imzalı cookie (Starlette `SessionMiddleware`, `itsdangerous`), 12 saat; şifrə dəyişəndə/deaktivdə sessiya etibarsız olur (cookie-də şifrə hash-inin sonu). CSRF token bütün POST formlarında; SameSite=Lax. 5 səhv cəhd → 15 dəq blok (proses yaddaşında).
  - `leads.reviewed_by_id/reviewed_at` təsdiq/rədd/qaytarmada yazılır.
  - Testlər: `tests/conftest.py` admin panel testləri üçün "daxil olmuş admin" təyin edir; auth testləri `@pytest.mark.real_auth` ilə real məntiqdən keçir.

- **PR 4 (fürsət/tapşırıq ekranları, açar sözlər bazadan, 24/7 Celery, 2026-09-30):**
  - **"Təsdiqlə" → fürsət + ilk tapşırıq** (`app/crm.py::convert_lead`). Əvvəl xarici CRM-ə göndərirdi və həmişə xəta verirdi — `crm_client.py` silindi, `.env`-dən `CRM_*` çıxarıldı. Lead statusu `converted`, `crm_opportunity_id` = fürsət ID. Təşkilat `organizations`-da tapılır/yaradılır (`app/tenders.py::organization_for`), əlaqə məlumatı lead-dən köçürülür.
  - **İlk tapşırıq:** tender → "Tender sənədlərini yoxla və təklif hazırla", son tarixdən 2 gün əvvəl (ən azı indidən 4 saat sonra), son tarixə ≤3 gün qalıbsa prioritet yüksək; digər lead → "İlk əlaqə: <təşkilat>", 1 gün, AI skoru ≥0.8-dirsə yüksək. Məsul — təsdiqləyən istifadəçi; `created_by_id` boş = sistem.
  - **Ekranlar:** `/opportunities` (mərhələ filtrləri, "mənimkilər", növbəti tapşırığın vaxtı, gecikən qırmızı), `/opportunities/{id}` (mərhələ, uduzma səbəbi, dəyər, məsul, tapşırıqlar, mənbə lead), `/tasks` (təcililər yuxarıda). Rollar: viewer baxır, sales dəyişir, məsulu başqasına yalnız manager+ verir.
  - **Mərhələ qaydaları:** tender fürsətində `contacted`/`negotiation` yoxdur; `lost` səbəbsiz olmaz; `won`/`lost` bağlayanda açıq tapşırıqlar `cancelled` olur; yenidən açmaq mümkündür.
  - **Açar sözlər bazadan** (`app/keywords.py`): scraper-lər (`BaseScraper.select_relevant`) `kind`-ə uyğun (`tender` → all+tender, `news` → all+news) aktiv terminləri oxuyur; **mənfi söz** başlığı həm açar söz, həm AI yolunda bağlayır; `match`: prefix/word/phrase. AI promptları şirkət profilini **maraq sahələrindən** qurur (ad, prioritet, AI təsviri + ilk 20 siqnal sözü); triaj keşi prompt versiyası ilə açarlanır — təsvir dəyişəndə köhnə qərarlar keçərsiz. 60 san keş; panel dəyişiklikdə təmizləyir, Celery worker ≤1 dəq görür. Baza əlçatan deyilsə koddakı siyahılar (fallback). Emlak-ın hərrac sözləri mənbəyə xasdır — `extra` kimi əlavə olunur.
  - **Panel:** `/settings/keywords` — maraq sahələri (ad, AI təsviri, prioritet, aktiv), yeni sahə, açar sözlər (bir neçəsi birdən, dil avtomatik, dublikat atlanır), söndür/sil, filtr. Redaktə: manager+.
  - **24/7:** Redis (`brew services`, yalnız 127.0.0.1) + `scripts/run_worker.sh` (worker + beat, threads pool). Canlı yoxlanıb: tapşırıq Redis → worker → `scrape_runs` (`trigger=schedule`, `ok`). Planlı və əl ilə scrape eyni mənbədə üst-üstə düşmür — Redis kilidi (`app/locks.py`); Redis yoxdursa kilid atlanır.
  - **Açıq:** worker-in kompüter yenidən başlayanda özü qalxması (launchd agent) — hələ qurulmayıb, istifadəçi qərarı; worker logları panelin "Loglar" səhifəsinə düşmür.
  - **Kodda:** `app/crm.py`, `app/crm_routes.py`, `app/keywords.py`, `app/keyword_routes.py`, `app/locks.py`, `app/scheduler.py`, `scripts/run_worker.sh`, şablonlar `opportunities.html`, `opportunity.html`, `tasks.html`, `keywords.html`; testlər `tests/test_crm.py`, `tests/test_keywords_db.py`, `tests/test_locks_and_scheduler.py`

### Phase 0 — etibarlılıq düzəlişləri (2026-10-05)
- **Status:** Decided, deploy olunub
- **Niyə:** 5 günlük production datası (370 lead, 477 run): 1news 112 run-dan 89-u uğursuz (sayt `/az/xronika/`-ni silib, 404); 41 lead heç vaxt skorlanmayıb (`new` — Neon bağlantısı AI mərhələsində düşüb, heç kim təkrar etmirdi); "0 elan" həm "uyğun yoxdur", həm "selector sınıb" demək idi; AI triajı say səhvində (37 başlığa 36 cavab) bütün partiyanı daxil edirdi (oxu: 37/37 tam qiymətləndirməyə getdi).
- **Nə edildi:**
  - 1news → `/az/lenta/` (`.newsList a span.title`).
  - `scrape_runs.items_fetched` (miqrasiya 0003) — filtrdən əvvəlki say; dashboard-da mənbə sağlamlığı (`app/health.py`: OK / Xəta / Gecikir / Boş).
  - Özünü bərpa: skorsuz `new` lead-lər növbəti run-da skorlanır; yarımçıq `running` run-lar `failed` olur; baza bağlantısı düşəndə bir təkrar; psycopg2 keepalive + connect_timeout.
  - Triaj: pozuq partiya yarıya bölünüb yenidən soruşulur; yalnız ≤5 başlıqlıq pozuq partiya daxil edilir və bu "şübhə" cavabları keşlənmir.
  - Testlər müvəqqəti SQLite-da (əvvəl `.env` → Neon idi).
- **Worldbank 0 elan normaldır:** API Azərbaycan üzrə 50 elan qaytarır, açıq olan 1-2-si konsaltinqdir.
- **Nəticə (canlı yoxlanıb):** 1news ok (24 gətirdi), 41 skorsuz lead skorlandı, oxu 37-dən 4-ü seçildi, bütün 13 mənbə "OK".
- **Kodda:** `app/health.py`, `app/pipeline.py`, `app/db/repository.py`, `app/ai_triage.py`, `app/scheduler.py`, `app/db/engine.py`, `migrations/versions/0003_run_items_fetched.py`, `tests/test_pipeline_recovery.py`.

## Açıq suallar (hələ qərar verilməyib)

### CRM real endpoint-i
- **Status:** Köhnəlib (2026-09-28) — öz CRM-imiz qərarı ilə xarici CRM endpoint-i artıq lazım deyil, bax "CRM baza sxemi". Aşağıdakı qeyd tarixçə üçün saxlanılır.
- **Əvvəlki status:** Open
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
