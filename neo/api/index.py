"""Vercel giriş nöqtəsi — admin panel (FastAPI) serverless funksiya kimi.

Vercel layihəsində Root Directory = `neo`. Scrape-lər burada işləmir (vaxt limiti,
fon işi yoxdur) — onları Celery worker edir, panel yalnız növbəyə qoyur (app/web.py::_enqueue).
Bax docs/knowledge-base/05-runbook.md → "Vercel".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.web import app  # noqa: E402,F401

# Soyuq başlanğıc: baza koddan geridədirsə (yeni miqrasiyalı deploy) miqrasiya işləyir — bir sorğu ilə yoxlanılır.
# Modul yüklənəndə edilir, çünki Vercel-də FastAPI-nin "startup" hadisəsinə arxalanmaq olmur. Alınmasa panel
# yenə açılsın (xəta logda qalır).
try:
    from app.db.engine import engine
    from app.db.migrate import upgrade_if_behind

    if upgrade_if_behind(engine):
        print("database migrated on cold start", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"database migration on cold start failed: {type(exc).__name__}: {exc}", flush=True)
