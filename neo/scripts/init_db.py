"""Bazanı ən son sxemə gətirir (Alembic). İşə salma: python -m scripts.init_db"""

from sqlalchemy.orm import Session

from app.db.backfill import backfill
from app.db.engine import engine
from app.db.migrate import upgrade_to_head

if __name__ == "__main__":
    upgrade_to_head(engine)
    with Session(engine) as session:
        stats = backfill(session)
    print(f"Baza ən son sxemdədir. Dolduruldu: {stats}")
