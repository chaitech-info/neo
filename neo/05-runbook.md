# 05 — Runbook

Əməli təlimat: qurulum, işə salma, test, problem həlli.

## İlk qurulum

```bash
cd /Users/user/Desktop/neo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # requirements.txt + pytest
cp .env.example .env
```

> Python 3.9 istifadə edirsənsə, `eval_type_backport` paketi lazımdır (artıq `requirements.txt`-dədir) — 3.10+-da çıxarıla bilər.

## Admin panel (manual test)

```bash
source .venv/bin/activate
uvicorn app.web:app --reload --port 8008
```

Brauzerdə: http://127.0.0.1:8008

- **Dashboard** (`/`) — hər mənbə üçün "Scrape indi" düyməsi
- **Validasiya** (`/leads/pending_review`) — Təsdiqlə → CRM / Rədd et
- **Bütün lead-lər** (`/leads`) — son 200 lead, bütün statuslar

DB defolt olaraq `./neo_leads.db` (SQLite). Sıfırdan başlamaq üçün: `rm neo_leads.db` (server yenidən başlayanda avtomatik yenidən yaradır).

## Testlər

```bash
python -m pytest -q
```

4 test var: `RawLead.dedupe_key()` davranışı, `Normalizer` dedup məntiqi.

## Scraper-i əl ilə, admin paneli olmadan sınamaq

```bash
python -c "
from app.scrapers.etender import EtenderScraper
s = EtenderScraper()
leads = s.fetch_leads()
for l in leads:
    print(l.title, '|', l.source_url)
"
```

Hər scraper sinifi asanlıqla belə tək-tək çağırıla bilər — `app/scrapers/__init__.py::ALL_SCRAPERS` siyahısında hamısı var.

## Production (Celery, avtomatik 24/7)

```bash
celery -A app.celery_app worker --loglevel=info
celery -A app.scheduler beat --loglevel=info
```

Bunun üçün Redis işlək olmalıdır (`REDIS_URL` `.env`-də) və `DATABASE_URL` Postgres-ə işarə etməlidir.

## Tez-tez rast gəlinən problemlər

| Simptom | Səbəb | Həll |
| --- | --- | --- |
| Bütün scraper-lər "0 yeni lead" qaytarır | Normaldır ola bilər — keyword filtrinə uyğun məzmun yoxdursa | `python -c "..."` ilə scraper-i tək-tək çağırıb `fetch_leads()`-in özünün nə qaytardığına bax (keyword filtrindən ƏVVƏL, bax `03-site-research.md`-dəki "candidate link" debug üsulu) |
| Bir scraper həmişə 0 qaytarır, digərləri işləyir | Sayt strukturu dəyişib | Brauzerdə saytı aç, `app/scrapers/<fayl>.py`-dəki seçici/regex-i yenilə, `03-site-research.md`-i güncəllə |
| "CRM-ə göndərilmədi" xətası | `CRM_BASE_URL` hələ real endpoint deyil (gözlənilən) | `04-decisions.md` → "CRM real endpoint-i" bax, CRM komandası ilə əlaqə saxla |
| Validasiya səhifəsi boşdur, amma scrape "N yeni lead" dedi | Adətən normal — sadəcə səhifəni yenilə (`/leads/pending_review`) | — |
| `Address already in use` (port 8008) | Köhnə uvicorn prosesi hələ işləyir | `lsof -ti:8008 \| xargs kill -9` |
| Pydantic/SQLAlchemy `TypeError: unsupported operand type(s) for \|` | Python 3.9-da `str \| None` sintaksisi | `eval_type_backport` quraşdırılıb-quraşdırılmadığını yoxla; yeni fayl yazırsansa, DB modellərində `Optional[str]` istifadə et (`str \| None` yox) — bax `app/db/models.py` |

## Yeni scraper əlavə etmək (checklist)

1. Saytı brauzerdə aç, real strukturunu tap (statik HTML? JSON API? SPA?) — `03-site-research.md`-ə qeyd et
2. `app/scrapers/<yeni>.py` yarat, `BaseScraper`-dan miras al (bax `app/scrapers/base.py`)
3. `app/scrapers/__init__.py::ALL_SCRAPERS`-ə əlavə et
4. `app/web.py::SOURCES` dict-inə əlavə et (admin panelə çıxsın)
5. `app/scheduler.py::SOURCE_TASKS` + `beat_schedule`-ə əlavə et (production cədvəli)
6. Əl ilə test et (yuxarıdakı bölmə), sonra admin paneldən "Scrape indi" ilə yoxla
7. `docs/lead-scraping-spec.md` §2 cədvəlinə və `03-site-research.md`-ə qeyd əlavə et
