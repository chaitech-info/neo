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

## Production: Vercel (panel) + Modal (scraping) + Neon (baza)

Panel Vercel-də (serverless), baza Neon Postgres-də, scraping iki yerdə:
- **Modal** (`modal_app.py`) — 8 mənbə 24/7 (marja, 1news, boss, emlak, bp_edp, worldbank, corporate, linkedin).
- **Mac worker** (Celery + lokal Redis, `NEO_SCRAPE_ROLE=local`) — Modal-ın ABŞ/AB IP-lərini bloklayan 5 mənbə: etender, fed, fed_tenders, oxu, report (`app/scheduler.py::LOCAL_ONLY`; 2026-09-30: 403/timeout). Mac sönülüdürsə bunlar işləmir; qoşulanda `./scripts/run_worker.sh`.
- Apify proxy-si (AZ residential) FREE planda xaricdən işləmir (403) — pullu plan olsa LOCAL_ONLY mənbələri Modal-a proxy ilə köçürmək olar.

- **Sxem (bir dəfə və hər yeni miqrasiyadan sonra, lokal kompüterdən):** Alembic üçün *birbaşa* host (`-pooler` olmadan):
  `DATABASE_URL="<neon URL, -pooler silinmiş>" python -m scripts.init_db`
  Vercel-də startup miqrasiya işlətmir (`SERVERLESS` → `on_startup` erkən qayıdır).
- **Lokal `.env`:** `DATABASE_URL="<neon pooled URL>"` (dırnaqda — `&` shell-i pozur), `SESSION_SECRET`, `TRIGGER_TOKEN`, `NEO_SCRAPE_ROLE=local`, `MODAL_PROFILE=neo`. Worker üçün `pip install -r requirements-worker.txt` (modal paketi). `NEO_SCRAPE_ROLE=local` olmadan worker bütün mənbələri işlədər və Modal ilə dublikat scrape olar.
- **Versiya fərqi:** Vercel və Modal Python 3.12 + ən son paketlər (SQLAlchemy 2.1, Starlette 1.x), lokal venv 3.9-dur. Deploydan əvvəl testləri 3.12-də də işlət. Qalıcı 3.12 venv (modal 1.6 da burada): `uv venv -p 3.12 ~/.local/share/neo-py312 && uv pip install -p ~/.local/share/neo-py312/bin/python -r requirements.txt modal pytest`, sonra `~/.local/share/neo-py312/bin/python -m pytest -q`. (`/tmp`-də saxlama — sistem təmizləyir.)
- **Testlər production bazasına toxunmur:** `tests/conftest.py` app yüklənməmişdən əvvəl `DATABASE_URL`-i müvəqqəti SQLite-a çevirir (əvvəl `.env` → Neon idi, dashboard testi canlı bazanı oxuyurdu).

### Vercel (panel)
- Layihə `neo` (emil0510s-projects), URL https://neo-omega-five.vercel.app. Git-ə bağlı deyil — deploy CLI ilə, `neo/neo` qovluğundan: `vercel deploy --prod` (`.vercelignore` `.env`, `*.db` fayllarını yükləmir). Giriş nöqtəsi `api/index.py`, marşrutlar `vercel.json`.
- Env: `DATABASE_URL` (Neon pooled), `SESSION_SECRET` (lokal `.env`-dəki ilə eyni), `MODAL_TRIGGER_URL`, `TRIGGER_TOKEN` (lokal `.env`-dəki ilə eyni).
- İlk admin: `/setup`. Loglar səhifəsi Vercel-də boşdur — scrape logları Modal dashboard-dadır.

### Modal (scraping)
- App `neo-scraper`, Modal profili `neo` (əsas profil deyil — hər əmrdə `MODAL_PROFILE=neo`). Deploy: `MODAL_PROFILE=neo ~/.local/share/neo-py312/bin/modal deploy modal_app.py`.
- `tick` — 10 dəqiqədən bir cron; `app/scheduler.py::beat_schedule`-a görə vaxtı çatan mənbələri `scrape_source.spawn` ilə başladır (cədvəl bir yerdədir, Celery ilə ortaq). Mənbə başına kilid `modal.Dict` `neo-scrape-locks`-dadır.
- `trigger` — web endpoint (`https://emil-abdurahmanli-project--neo-scraper-trigger.modal.run`); panelin "Scrape indi"/"Hamısını scrape et" düymələri bura POST edir (`app/triggers.py`), Bearer `TRIGGER_TOKEN` olmadan 401. LOCAL_ONLY mənbələri `modal.Queue` `neo-local-requests`-ə qoyur — Mac worker-in `local-bridge` thread-i oradan götürür (Mac sönülüdürsə sorğu növbədə gözləyir).
- Scraper xətası (403 və s.) indi `scrape_runs.status=failed` + `error` kimi yazılır (əvvəl "ok, 0 elan" görünürdü).

### Mənbə sağlamlığı (dashboard "Vəziyyət" sütunu, `app/health.py`)
- **Xəta** — son 3 run uğursuz → sayt dəyişib/bloklayıb, scraper-ə bax.
- **Gecikir** — cədvələ görə 2 qat müddətdən çox işləməyib → Mac sönülüdür (LOCAL_ONLY) və ya Modal cron dayanıb.
- **Boş** — son 3 uğurlu run saytdan 0 elan gətirib (`scrape_runs.items_fetched`) → selector sınıb ola bilər. `items_seen` (filtrdən keçən) 0 olması normaldır.
- Özünü bərpa: `running` qalmış 2 saatdan köhnə run növbəti run-da `failed` ("Yarımçıq qaldı") olur; skorsuz qalan `new` lead-lər (30 dəq-dən köhnə) həmin mənbənin növbəti run-ında skorlanır; baza bağlantısı düşəndə run bir dəfə təkrarlanır.
- Secret `neo-secrets`: `DATABASE_URL`, `OPENAI_API_KEY`, `APIFY_TOKEN`, `TRIGGER_TOKEN` (+ istəyə görə `AI_MODEL`). Açar dəyişəndə: `.env`-i yenilə, secret-i `modal secret create neo-secrets --from-json ... --force` ilə yenidən yarat.
- Loglar: `MODAL_PROFILE=neo modal app logs neo-scraper` və ya Modal dashboard.

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
