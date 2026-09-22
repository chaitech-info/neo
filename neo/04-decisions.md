# 04 — Decisions & Open Questions

## Qəbul edilmiş qərarlar

### Human-in-the-loop (AI heç vaxt birbaşa CRM-ə göndərmir)
- **Status:** Decided (2026-09-22)
- **Qərar:** AI skorlama `ai_score >= 0.6` olan lead-ləri `pending_review` statusuna qoyur, CRM-ə YOX. Yalnız admin paneldə insan "Təsdiqlə" düyməsini basanda CRM-ə göndərilir.
- **Niyə:** yanlış/qərəzli AI qərarının CRM-i "zibilləməsinin" qarşısını almaq üçün. Sənədin əsas mesajı ilə üst-üstə düşür: "AI analiz edir və xəbərdar edir, yekun qərar isə səlahiyyətli insan tərəfindən verilir."
- **Kodda:** `app/ai_filter.py`, `app/pipeline.py::send_lead_to_crm`

### AI xətası zamanı fail-safe davranış
- **Status:** Decided (2026-09-23)
- **Qərar:** Claude API əlçatan olmasa (API key yoxdur, şəbəkə xətası və s.), lead avtomatik `rejected`-ə düşmür — `pending_review`-a düşür, `ai_score=None`, `ai_reason="AI qiymətləndirməsi alınmadı — əl ilə yoxlanmalıdır"`.
- **Niyə:** ilk versiyada AI xətası = avtomatik rədd idi, bu isə API key olmadıqda BÜTÜN lead-lərin səssizcə itməsinə səbəb olurdu (data itkisi, sükutla). İndi insan hələ də görə bilir.
- **Kodda:** `app/ai_filter.py::AIFilter.score`

### Development/test üçün SQLite, production üçün Postgres
- **Status:** Decided (2026-09-22)
- **Qərar:** `DATABASE_URL` təyin olunmayıbsa, `sqlite:///./neo_leads.db` işlədilir. Production-da `.env`-də Postgres URL-i verilməlidir.
- **Niyə:** development komandası admin paneli test edərkən Postgres qurmaq məcburiyyətində qalmasın.
- **Kodda:** `app/db/engine.py`

### Azerweb.az-da yalnız tender/announcement, vacancy yox
- **Status:** Decided (2026-09-23)
- **Qərar:** Azerweb API-də "catering" axtarışı iş elanlarını da qaytarır (Chef, Housekeeper və s.) — bunlar müştəri lead-i deyil. Yalnız `type in {tender, announcement}` saxlanılır.
- **Kodda:** `app/scrapers/azerweb.py::RELEVANT_TYPES`

## Açıq suallar (hələ qərar verilməyib)

### LinkedIn scraping yanaşması
- **Status:** Open
- **Sual:** Rəsmi API/lisenziyalı 3-cü tərəf provider (Proxycurl, Phantombuster və s.) istifadə olunacaq, yoxsa LinkedIn ilk MVP-dən tamamilə çıxarılacaq?
- **Niyə açıqdır:** açıq HTML scraping LinkedIn ToS-unu pozur, hesab bloklanma riski var.
- **Kim qərar verməlidir:** hüquq şöbəsi + məhsul rəhbəri
- **Hazırkı vəziyyət:** `app/scrapers/linkedin.py` boş stub, scheduler-ə qoşulmayıb

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
- **Qeyd:** development başlamazdan (yəni production-a keçmədən) əvvəl hər 7 mənbə üçün hüquq şöbəsindən yazılı təsdiq alınmalıdır. Bu, kodun yazılmasını əngəlləmədi, amma **production deploy-u əngəlləməlidir**.
