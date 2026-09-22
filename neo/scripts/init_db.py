"""Cədvəlləri yaradır. İşə salma: python -m scripts.init_db"""

from app.db.engine import engine
from app.db.models import Base

if __name__ == "__main__":
    Base.metadata.create_all(engine)
    print("Cədvəllər yaradıldı.")
