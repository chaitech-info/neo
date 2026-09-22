# Lead Scraping — Catering AI Ecosystem

Bu modul catering şirkəti üçün potensial müştəriləri (lead) internetdən
avtomatik aşkarlayır, AI ilə qiymətləndirir və CRM-ə ötürür.

## Sənədlər

- [docs/lead-scraping-spec.md](docs/lead-scraping-spec.md) — tam development spesifikasiyası (canlı versiya: [Claude Docs](https://claude.ai/code/artifact/bf60eaf0-8d39-447d-8138-fa2f6558a7ff))
- [docs/presentation/](docs/presentation) — müştəri təqdimatının mənbə faylları (canlı/render olunmuş versiya: [Claude Artifact](https://claude.ai/artifact/5iiXFbUsApRoa9khF1PENb))

## Struktur

```
app/
  models.py          RawLead / enum-lar (data modeli)
  scrapers/           hər mənbə üçün ayrıca scraper (marja, azerweb, news, etender, emlak, linkedin)
  normalizer.py       dedup məntiqi
  ai_filter.py        LLM ilə skorlama (§6)
  crm_client.py       CRM API inteqrasiyası (§9)
  pipeline.py         scrape -> normalize -> score axını + CRM-ə göndərmə/rədd funksiyaları
  celery_app.py       Celery broker konfiqurasiyası
  scheduler.py         Celery Beat cədvəli (§7, avtomatik/production üçün)
  web.py              Admin panel (FastAPI) — mənbəni əl ilə scrape etmək + validasiya UI
  templates/          admin panelin HTML şablonları
  db/                 SQLAlchemy modelləri + repository
scripts/init_db.py    cədvəlləri yaradır
tests/                pytest testləri (models, normalizer)
```

## Qurulum

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # requirements.txt + pytest
cp .env.example .env   # dəyərləri doldur
python -m scripts.init_db
```

> Qeyd: Python 3.9-da `eval_type_backport` paketi (requirements.txt-də var) `str | None` tipli annotasiyalar üçün lazımdır. Python 3.10+ istifadə edən mühitlərdə bu paket çıxarıla bilər.

## Admin panel (manual test üçün)

```bash
uvicorn app.web:app --reload --port 8008
```

Sonra brauzerdə: **http://127.0.0.1:8008**

- **Dashboard** (`/`) — hər mənbə üçün kart və **"Scrape indi"** düyməsi: basanda o mənbəni dərhal scrape edir, AI ilə skorlayır və nəticələri Validasiya səhifəsinə göndərir (birbaşa CRM-ə getmir).
- **Validasiya** (`/leads/pending_review`) — AI-nin uyğun saydığı lead-lər burada gözləyir; **Təsdiqlə → CRM** və **Rədd et** düymələri var (human-in-the-loop, bax sənəd §11).
- **Bütün lead-lər** (`/leads`) — son 200 lead, bütün statuslarla.

DB defolt olaraq layihə qovluğunda `neo_leads.db` (SQLite) faylıdır — Postgres qurmadan test edə bilərsən. Production üçün `.env`-də `DATABASE_URL`-i Postgres-ə yönəlt.

**Validasiya qaydaları (server tərəfində yoxlanılır):**
- Naməlum/yalnış mənbə açarı ilə scrape çağırışı → xəta mesajı ilə geri qaytarılır, heç nə işə salınmır.
- Scraper xəta versə (sayt cavab verməsə, struktur dəyişsə və s.) pipeline uğursuz olmur — o mənbə üçün sadəcə 0 nəticə qeyd olunur və log-a yazılır.
- Approve/Reject yalnız `pending_review` statusundakı lead üzərində işləyir; artıq CRM-ə göndərilmiş və ya mövcud olmayan lead ID üçün aydın xəta mesajı göstərilir.

## İşə salma (production — avtomatik 24/7 scraping)

```bash
# Celery worker
celery -A app.celery_app worker --loglevel=info

# Celery beat (scheduler, §7-dəki cədvəllə)
celery -A app.scheduler beat --loglevel=info
```

## Testlər

```bash
pytest
```

## TODO (development komandası üçün, sənəddəki açıq suallar)

- Hər scraper-in CSS seçiciləri (`TODO(dev)` şərhli) real sayt strukturuna uyğunlaşdırılmalıdır.
- LinkedIn: rəsmi API / lisenziyalı provider qərarı gözlənilir (`app/scrapers/linkedin.py`).
- CRM `/opportunities` endpoint-inin dəqiq forması CRM komandası ilə təsdiqlənməlidir (`app/crm_client.py`).
- Hər saytın ToS/robots.txt-i development başlamazdan əvvəl hüquq şöbəsi ilə yoxlanılmalıdır.
