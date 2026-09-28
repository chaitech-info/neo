from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app  # noqa: F401 — .env yüklənsin
from app.db.engine import DATABASE_URL
from app.db.models import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite ALTER TABLE-ı məhdud dəstəkləyir — batch rejimi cədvəli
        # nüsxələyib yenidən qurur. Postgres-də adi ALTER işləyir.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Proqramdan (app/db/migrate.py, testlər) hazır bağlantı ötürülə bilər.
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        {"sqlalchemy.url": DATABASE_URL}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
