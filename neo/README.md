# Lead Scraping — Catering AI Ecosystem

Bu modul catering şirkəti üçün potensial müştəriləri (lead) internetdən
avtomatik aşkarlayır, AI ilə qiymətləndirir və CRM-ə ötürür.

## Sənədlər

- **[docs/knowledge-base/](docs/knowledge-base)** — layihənin bilgi bazası: arxitektura, sayt tədqiqatları, qərarlar/açıq suallar, runbook, lüğət. **Yeni qoşulan development komandası üzvü buradan başlamalıdır.**
- [docs/lead-scraping-spec.md](docs/lead-scraping-spec.md) — tam development spesifikasiyası (canlı versiya: [Claude Docs](https://claude.ai/code/artifact/bf60eaf0-8d39-447d-8138-fa2f6558a7ff))
- [docs/presentation/](docs/presentation) — müştəri təqdimatının mənbə faylları (canlı/render olunmuş versiya: [Claude Artifact](https://claude.ai/artifact/5iiXFbUsApRoa9khF1PENb))

## Struktur

```
app/
  models.py          RawLead / enum-lar (data modeli)
  scrapers/           hər mənbə üçün ayrıca scraper (marja, azerweb, news, etender, emlak, linkedin)
  normalizer.py       dedup məntiqi
  ai_filter.py        LLM ilə skorlama (§6)
  crm.py              Öz CRM: fürsətlər, tapşırıqlar, "Təsdiqlə" → fürsət
  pipeline.py         scrape -> normalize -> score axını + CRM-ə göndərmə/rədd funksiyaları
  celery_app.py       Celery broker konfiqurasiyası
  scheduler.py         Celery Beat cədvəli (§7, avtomatik/production üçün)
  web.py              Admin panel (FastAPI) — mənbəni əl ilə scrape etmək + validasiya UI
  templates/          admin panelin HTML şablonları
  db/                 SQLAlchemy modelləri + repository
scripts/init_db.py    bazanı ən son sxemə gətirir (Alembic)
scripts/create_user.py  admin panel istifadəçisi yaradır
migrations/           Alembic miqrasiyaları (sxem dəyişiklikləri yalnız buradan)
tests/                pytest testləri (models, normalizer)
```

## Qurulum

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # requirements.txt + pytest
cp .env.example .env   # dəyərləri doldur
python -m scripts.init_db                 # baza → ən son sxem (köhnə baza itkisiz yenilənir)
python -m scripts.create_user --email ad@sirket.az --name "Ad Soyad" --role admin   # şifrə gizli soruşulur
```

## Giriş və rollar

Admin panelin bütün səhifələri giriş tələb edir. **İlk dəfə** açanda (bazada istifadəçi yoxdursa) `/setup` səhifəsi ilk **admin** hesabını yaratmağı təklif edir; bir hesab yarananda bu səhifə bağlanır. Digər istifadəçiləri admin **İstifadəçilər** səhifəsindən yaradır (CLI da işləyir: `python -m scripts.create_user`).

| Rol | Nə edə bilir |
|---|---|
| Müşahidəçi (`viewer`) | yalnız baxır |
| Satış (`sales`) | + lead-ləri təsdiqləyir / rədd edir / validasiyaya qaytarır |
| Menecer (`manager`) | + scrape-i işə salır |
| Administrator (`admin`) | + istifadəçiləri yaradır, rolunu dəyişir, deaktiv edir, şifrəsini sıfırlayır |

- Sessiya imzalı cookie-dədir (12 saat). Açar: `.env`-dəki `SESSION_SECRET`; yoxdursa bir dəfə yaradılıb `neo/.session_secret`-ə yazılır (gitignore-da). **Production-da `.env`-də təyin et.**
- Şifrə dəyişəndə və ya istifadəçi deaktiv ediləndə onun açıq sessiyaları avtomatik bağlanır. 5 səhv cəhddən sonra giriş 15 dəqiqə bloklanır. Bütün POST formları CSRF token ilə qorunur.
- Sonuncu aktiv admin-i deaktiv etmək və ya rolunu endirmək olmur.

## Baza sxemi və miqrasiyalar

Sxem layihəsi və qərarlar: `docs/knowledge-base/04-decisions.md` → "CRM baza sxemi". Hazırda SQLite, sonra PostgreSQL — sxem hər ikisində dəyişmədən işləyir (native enum/citext/jsonb yoxdur).

- Server başlayanda və `scripts.init_db` işləyəndə baza avtomatik ən son miqrasiyaya gətirilir (`app/db/migrate.py`). CRM-dən əvvəlki köhnə baza (yalnız `leads`) avtomatik tanınır və itkisiz yenilənir.
- **Modeli dəyişdinsə, miqrasiya da yaz:** `alembic revision --autogenerate -m "qısa izah"`, yaranan faylı yoxla, sonra `alembic upgrade head`. `tests/test_migrations.py::test_models_match_migrations` model ilə miqrasiya uyğun gəlmirsə düşür.
- Geri qaytarma: `alembic downgrade -1`.

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
brew install redis && brew services start redis   # bir dəfə; login-də özü başlayır
./scripts/run_worker.sh                            # Celery worker + beat (cədvəl) bir prosesdə
```

Ətraflı: `docs/knowledge-base/05-runbook.md` → "Avtomatik 24/7 scraping".

## Testlər

```bash
pytest
```

## TODO (development komandası üçün, sənəddəki açıq suallar)

- Hər scraper-in CSS seçiciləri (`TODO(dev)` şərhli) real sayt strukturuna uyğunlaşdırılmalıdır.
- LinkedIn: rəsmi API / lisenziyalı provider qərarı gözlənilir (`app/scrapers/linkedin.py`).
- Hər saytın ToS/robots.txt-i development başlamazdan əvvəl hüquq şöbəsi ilə yoxlanılmalıdır.
