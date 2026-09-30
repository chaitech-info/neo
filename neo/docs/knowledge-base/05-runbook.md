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

- **Dashboard** (`/`) — hər mənbə üçün "Scrape indi" düyməsi; **"Hamısını scrape et"** bütün mənbələri paralel, fonda işə salır (hər mənbə ayrı thread + öz DB sessiyası; eyni mənbə iki dəfə eyni vaxtda işləmir). İşləyərkən kartlar "İşləyir…" göstərir, səhifə 5 saniyədən bir yenilənir. 13 mənbə ≈ 1-4 dəq (ən uzun: boss.az). ⚠️ LinkedIn də daxildir — Apify xərci (~$0.4/run).
- **Validasiya** (`/leads/pending_review`) — Təsdiqlə → CRM / Rədd et
- **Bütün lead-lər** (`/leads`) — son 200 lead, bütün statuslar
- **Loglar** (`/logs`) — scraping prosesi başdan-sona canlı (2 saniyədən bir): hər HTTP sorğusu (sayt, robots.txt, Apify, OpenAI), `pipeline_started` → `scraper_completed` → `dedup_done` → hər lead üçün `ai_scored` (skor + status) → `pipeline_completed`, xətalar (exception mətni ilə). Mənbə/səviyyə/mətn filtri. Son 5000 yazı yaddaşda (`app/logbuffer.py`) — server yenidən başlayanda sıfırlanır; terminal logu əvvəlki kimidir. URL-dəki `token=`/`key=` dəyərləri `***` ilə gizlədilir. JSON: `GET /api/logs?after=<id>&source=<açar>&level=<səviyyə>`.

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

## Avtomatik 24/7 scraping (Redis + Celery)

```bash
brew install redis
brew services start redis        # arxa fon xidməti, kompüter açılanda özü başlayır; yalnız 127.0.0.1-də dinləyir
redis-cli ping                   # PONG
./scripts/run_worker.sh          # Celery worker + beat bir prosesdə (threads pool, 4 paralel mənbə)
```

- **Cədvəl:** `app/scheduler.py::beat_schedule` (Bakı vaxtı) — xəbərlər saatlıq, fed/report tenderləri 2 saatdan bir, etender/emlak/Dünya Bankı/bp/LinkedIn/boss gündə 1, şirkət saytları gündə 2 dəfə. Hər planlı run `scrape_runs`-a `trigger=schedule` ilə yazılır, dashboard "Son nəticə"-də görünür.
- **Əl ilə bir tapşırıq göndərmək (test):** `celery -A app.scheduler:celery_app call scrape_source --args='["report"]'`
- **Üst-üstə düşmə yoxdur:** planlı və paneldən "Scrape indi" eyni mənbəni eyni vaxtda işlətmir — Redis kilidi `neo:scrape:<mənbə>` (`app/locks.py`). Redis yoxdursa kilid atlanır, scrape dayanmır.
- **Köhnə əmr işləmir:** `celery -A app.celery_app worker` `scrape_source` tapşırığını qeydiyyata salmır (tapşırıq `app/scheduler.py`-dadır) — həmişə `run_worker.sh` və ya `-A app.scheduler:celery_app`.
- **Loglar:** worker-in logu öz terminalındadır; panelin "Loglar" səhifəsi yalnız paneldən işə salınan scrape-ləri göstərir.
- **Kompüter yenidən başlayanda:** Redis özü qalxır; worker-i yenidən `./scripts/run_worker.sh` ilə başlat (və ya launchd agenti qur — bax 04-decisions.md → "CRM PR 4").
- **Production:** `DATABASE_URL` Postgres-ə işarə etməlidir (SQLite bir neçə yazan prosesə görə deyil).

## Tez-tez rast gəlinən problemlər

| Simptom | Səbəb | Həll |
| --- | --- | --- |
| Bütün scraper-lər "0 yeni lead" qaytarır | Normaldır ola bilər — keyword filtrinə uyğun məzmun yoxdursa | `python -c "..."` ilə scraper-i tək-tək çağırıb `fetch_leads()`-in özünün nə qaytardığına bax (keyword filtrindən ƏVVƏL, bax `03-site-research.md`-dəki "candidate link" debug üsulu) |
| Bir scraper həmişə 0 qaytarır, digərləri işləyir | Sayt strukturu dəyişib | Brauzerdə saytı aç, `app/scrapers/<fayl>.py`-dəki seçici/regex-i yenilə, `03-site-research.md`-i güncəllə |
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
