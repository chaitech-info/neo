from __future__ import annotations

import os

from sqlalchemy import create_engine

# Production-da DATABASE_URL Postgres-ə işarə etməlidir (sənəd §8 Tech stack).
# .env-də DATABASE_URL verilməyibsə, yerli admin panel testi üçün SQLite
# faylına düşür — heç bir əlavə qurulum tələb etmir.
DATABASE_URL = os.environ.get("DATABASE_URL") or "sqlite:///./neo_leads.db"

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
