from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

# Production-da DATABASE_URL Postgres-ə işarə etməlidir (sənəd §8 Tech stack).
# .env-də DATABASE_URL verilməyibsə, yerli admin panel testi üçün SQLite
# faylına düşür — heç bir əlavə qurulum tələb etmir.
DATABASE_URL = os.environ.get("DATABASE_URL") or "sqlite:///./neo_leads.db"
# Sürücü açıq yazılır: SQLAlchemy 2.1 "postgresql://" üçün defolt olaraq psycopg (v3)
# axtarır, biz isə psycopg2-binary quraşdırırıq (Vercel-də 2.1 düşür → ModuleNotFoundError).
for _prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_prefix):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(_prefix):]

# timeout: "Hamısını scrape et" 13 mənbəni paralel işlədir — SQLite yazıları
# bir-birini gözləsin ("database is locked" xətası verməsin).
_connect_args = (
    {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
)
# Serverless (Vercel): hər sorğu qısa ömürlü prosesdədir — öz bağlantı hovuzu
# saxlanmır, hovuzlamanı Neon-un "-pooler" endpoint-i edir (DATABASE_URL onu göstərməlidir).
SERVERLESS = bool(os.environ.get("VERCEL"))
if SERVERLESS and not os.environ.get("DATABASE_URL"):
    # Vercel-in fayl sistemi yalnız oxunandır — SQLite-a düşmək səssiz xəta olardı.
    raise RuntimeError("Vercel-də DATABASE_URL təyin edilməyib (Neon Postgres bağlantısı, '-pooler' host)")
_pool = {"poolclass": NullPool} if SERVERLESS else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args, **_pool)
