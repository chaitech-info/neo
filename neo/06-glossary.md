# 06 — Glossary

| Termin | Mənası |
| --- | --- |
| **RawLead** | Bütün scraper-lərin qaytardığı vahid data formatı (Pydantic model, `app/models.py`) |
| **dedupe_key** | `source` + `source_url`-un SHA-256 hash-i — eyni elanın CRM-ə təkrar getməsinin qarşısını alır |
| **pending_review** | AI-nin uyğun saydığı (və ya AI xəta verdiyi) lead-in statusu — CRM-ə getməzdən əvvəl insan təsdiqi gözləyir |
| **sent_to_crm** | İnsan təsdiqlədikdən və CRM-ə uğurla göndərildikdən sonrakı status |
| **human-in-the-loop** | AI qərar vermir, yalnız tövsiyə edir; yekun qərarı (CRM-ə göndər/rədd et) insan verir — bu layihənin əsas prinsipi |
| **NeoAnalyze** | Orijinal məhsul konsepsiyasında arxa planda 24/7 işləyən AI monitorinq mühərriki; bu lead-scraping modulu həmin konsepsiyanın bir tətbiqidir |
| **ai_score** | 0–1 aralığında, lead-in catering ehtiyacına uyğunluğunu göstərən Claude API qiyməti |
| **ai_reason** | AI-nin `ai_score`-u niyə verdiyini izah edən bir cümlə (Azərbaycan dilində) |
| **BaseScraper** | Bütün mənbə-spesifik scraper-lərin miras aldığı ortaq sinif — rate limiting, retry, xəta tutma (`app/scrapers/base.py`) |
| **fail-safe (AI scoring)** | AI xətası zamanı lead-in səssizcə silinməməsi — `pending_review`-a düşməsi (bax `04-decisions.md`) |
| **Normalizer** | Ham scrape nəticələrini dedup-dan keçirən komponent (`app/normalizer.py`) |
| **CRM Opportunity** | CRM sistemində yaradılan "potensial iş fürsəti" qeydi — təsdiqlənmiş lead buraya düşür |
