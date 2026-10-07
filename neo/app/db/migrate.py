"""Bazanı Alembic ilə ən son sxemə gətirir (server başlayanda və scripts/init_db).

Üç hal:
- boş baza            → bütün miqrasiyalar (0001 `leads`-i yaradır);
- köhnə baza (CRM-dən əvvəl: `leads` var, `alembic_version` yox)
                      → 0001 "stamp" edilir (cədvəl artıq var), sonra 0002+;
- artıq idarə olunan  → yalnız çatışmayan miqrasiyalar.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

BASELINE = "0001"
_ROOT = Path(__file__).resolve().parents[2]  # neo/


def alembic_config(connection=None) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    cfg.attributes["skip_logging_config"] = True  # tətbiqin öz log ayarlarını pozmasın
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def upgrade_to_head(engine: Engine) -> None:
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        cfg = alembic_config(connection)
        if "leads" in tables and "alembic_version" not in tables:
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")
        # Mənbə reyestri (app/sources.py) → `sources` cədvəli; yeni mənbə üçün miqrasiya lazım deyil.
        from app.sources import sync_sources

        sync_sources(connection)
