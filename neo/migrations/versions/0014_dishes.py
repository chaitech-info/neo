"""Yemək kataloqu və şəkilli qiymət təklifi:
- dishes: yeməyin adı (az/en/ru), kateqoriyası və şəkli (JPEG baytları bazada — Vercel-də disk yoxdur);
  `key` — adın normal forması (app/dishes.py::dish_key), təklifdəki maddə ilə uyğunlaşdırmaq üçün;
- offers.pictures: təklif şəkilli PDF ilə yaradılıb (tarixçədən açanda da şəkilli açılsın).

Kataloq Seaport nümunə menyularının (Standard 6 AZN, Medium Plus 10 AZN) yeməkləri ilə doldurulur —
nümunə PDF-lərdə yemək şəkli yoxdur, şəkilləri istifadəçilər "Yemək şəkilləri" səhifəsindən yükləyir.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-03
"""
import re
import unicodedata
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

SEED = {
    "Şorba": ["Mərcimək şorbası", "Tərəvəz şorbası", "Düyü şorbası", "Yarım noxud şorbası", "Göbələk krem-şorbası",
              "Pomidor-reyhan şorbası", "Minestrone şorbası", "Fransız soğan şorbası",
              "Meksika üsulu tərəvəz şorbası"],
    "Əsas yemək": ["Çoban qovurması", "Toyuq tabaka", "Pomidorla təqdim olunan qril kotlet", "Toyuq şnitseli",
                   "Mal əti ilə tərəvəz sotesi", "Pendirli burger", "Adana lülə kababı", "Toyuq kordon-blyu",
                   "Yarpaq dolması", "Langet", "Mal əti stroqonofu", "Kəklikotulu toyuq filesi", "Toyuq fajitası",
                   "Mal əti qulyaşı", "Limon sousu ilə sobada bişmiş ağ balıq", "Lazanya Bolonez"],
    "Qarnir": ["Südlü kartof püresi", "Qril tərəvəzlər", "Sobada bişmiş kartof", "Qarabaşaq sıyığı", "Şüyüdlü plov",
               "Kartof püresi", "Göyərtili düyü", "Qratin kartof", "Buxarda bişmiş tərəvəzlər", "Basmati düyüsü",
               "Qarabaşaq"],
    "Salat": ["Kələm və kök salatı", "Pomidor-xiyar salatı", "Çuğundur salatı", "Paytaxt salatı", "Vineqret salatı",
              "Tbilisi salatı", "Yunan salatı", "Toyuq salatı", "Balqabaq, ağ pendir və qoz salatı", "Nar salatı",
              "Mimoza salatı", "Mövsümi tərəvəz salatı"],
    "İçki": ["Qazsız su", "Qazlı su", "Təbii meyvə şirələri", "Coca-Cola", "Coca-Cola Zero", "Fanta", "Ice Tea"],
    "Çörək": ["Çörək assortisi", "Çörək çeşidləri"],
}


def _key(name: str) -> str:
    # app/dishes.py::dish_key ilə eyni (miqrasiya tətbiq koduna bağlı qalmasın deyə surəti)
    s = unicodedata.normalize("NFC", name).replace("İ", "i").replace("I", "ı").lower()
    s = re.sub(r"[\s\-–—_/,.;:()\"'«»]+", " ", s)
    return s.strip()[:200]


def upgrade() -> None:
    dishes = op.create_table(
        "dishes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=True),
        sa.Column("name_ru", sa.Text(), nullable=True),
        sa.Column("category", sa.String(60), nullable=True),
        sa.Column("image", sa.LargeBinary(), nullable=True),
        sa.Column("image_type", sa.String(32), nullable=True),
        sa.Column("image_updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_dishes_updated_by_id_users"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("key", name="uq_dishes_key"),
    )
    with op.batch_alter_table("offers") as batch:
        batch.add_column(sa.Column("pictures", sa.Boolean(), nullable=False, server_default=sa.false()))

    now = datetime.utcnow()
    rows, seen = [], set()
    for category, names in SEED.items():
        for name in names:
            if _key(name) not in seen:
                seen.add(_key(name))
                rows.append({"key": _key(name), "name": name, "category": category, "created_at": now})
    op.bulk_insert(dishes, rows)


def downgrade() -> None:
    with op.batch_alter_table("offers") as batch:
        batch.drop_column("pictures")
    op.drop_table("dishes")
