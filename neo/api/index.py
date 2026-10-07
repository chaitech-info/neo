"""Vercel giriş nöqtəsi — admin panel (FastAPI) serverless funksiya kimi.

Vercel layihəsində Root Directory = `neo`. Scrape-lər burada işləmir (vaxt limiti,
fon işi yoxdur) — onları Celery worker edir, panel yalnız növbəyə qoyur (app/web.py::_enqueue).
Bax docs/knowledge-base/05-runbook.md → "Vercel".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.web import app  # noqa: E402,F401
