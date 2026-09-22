# 02 — Architecture

## Diaqram

```mermaid
flowchart LR
  A[Scheduler<br/>Celery Beat] --> B1[7 scraper<br/>marja/azerweb/1news/oxu/fed/etender/emlak]
  A -.disabled.-> B0[LinkedIn scraper]
  B1 --> C[Normalizer<br/>dedup hash]
  C --> D[AI Filter<br/>Claude API]
  D --> E{ai_score >= 0.6?}
  E -->|Bəli| F[pending_review<br/>admin paneldə gözləyir]
  E -->|Xeyr| G[rejected]
  F --> H{İnsan qərarı<br/>admin panel}
  H -->|Təsdiqlə| I[CRM API<br/>/opportunities]
  H -->|Rədd et| G
  I --> J[sent_to_crm]
```

## Komponentlər və fayllar

| Komponent | Fayl | Məsuliyyət |
| --- | --- | --- |
| Data modeli | [`app/models.py`](../../app/models.py) | `RawLead`, enum-lar, dedup hash |
| Scraper-lar | [`app/scrapers/`](../../app/scrapers) | Hər mənbə üçün ayrıca sinif, ortaq `BaseScraper` |
| Normalizer | [`app/normalizer.py`](../../app/normalizer.py) | Dublikat yoxlaması (DB-yə əsaslanan) |
| AI Filter | [`app/ai_filter.py`](../../app/ai_filter.py) | Claude API ilə skorlama, fail-safe (xəta → `pending_review`, YOX `rejected`) |
| CRM Client | [`app/crm_client.py`](../../app/crm_client.py) | `/opportunities` POST sorğusu |
| Pipeline | [`app/pipeline.py`](../../app/pipeline.py) | Yuxarıdakıları bir-birinə bağlayan orkestrator |
| DB qatı | [`app/db/`](../../app/db) | SQLAlchemy modeli + repository (CRUD, status keçidləri) |
| Admin panel | [`app/web.py`](../../app/web.py) + [`app/templates/`](../../app/templates) | Manual scrape tetiklənməsi, validasiya UI |
| Scheduler | [`app/scheduler.py`](../../app/scheduler.py) | Celery Beat cədvəli (production, avtomatik rejim) |

## İki işləmə rejimi

1. **Manual/test rejimi** — admin panel (`uvicorn app.web:app`), "Scrape indi" düyməsi `run_pipeline()`-i sinxron çağırır, SQLite DB.
2. **Production rejimi** — Celery worker + Beat, `app/scheduler.py`-dəki cədvəllə avtomatik işləyir, Postgres DB.

Hər ikisi **eyni** `run_pipeline()` funksiyasından istifadə edir (`app/pipeline.py`) — məntiq təkrarlanmır.

## Status maşını (`LeadStatus`)

```mermaid
stateDiagram-v2
  [*] --> new: scrape edildi
  new --> pending_review: AI score >= 0.6 (və ya AI xətası)
  new --> rejected: AI score < 0.6
  pending_review --> sent_to_crm: insan Təsdiqlə
  pending_review --> rejected: insan Rədd et
  new --> duplicate: dedup hash mövcuddur (əslində save olunmur belə)
```

**Vacib qərar:** AI heç vaxt birbaşa `sent_to_crm`-ə keçmir — yalnız insan bunu edə bilər (bax `04-decisions.md` → Human-in-the-loop).

## Data axını nümunəsi

1. `scrape_source("etender")` çağırılır (admin panel düyməsi və ya Celery task)
2. `EtenderScraper.fetch_leads()` → real API-dən `RawLead` siyahısı
3. `Normalizer.process()` → dedup hash yoxlanılır, yeni olanlar DB-yə yazılır
4. `AIFilter.score_batch()` → Claude API çağırılır, `ai_score`/`ai_reason` təyin olunur
5. Repository yenilənir, status `pending_review` və ya `rejected`
6. Admin panel `/leads/pending_review`-də insan görür, Təsdiqlə/Rədd et
7. Təsdiqlənərsə → `CRMClient.create_opportunity()` → status `sent_to_crm`
