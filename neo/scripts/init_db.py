"""Bazanı ən son sxemə gətirir (Alembic). İşə salma: python -m scripts.init_db"""

from app.db.engine import engine
from app.db.migrate import upgrade_to_head

if __name__ == "__main__":
    upgrade_to_head(engine)
    print("Baza ən son sxemdədir.")
