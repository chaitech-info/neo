"""Tədbir təşkili tenderləri üçün açar sözlər: "təşkili", "keçirilməsi" (tender).

2026-10-05 ölçüsü (scripts/eval_filter.py, 945 başlıq): AI triajı "…ideatonu
layihəsinin təşkili xidmətinin satın alınması", "…baxış-müsabiqəsinin təşkili"
kimi tədbir tenderlərini buraxırdı. Tender başlığında "təşkili/keçirilməsi" demək
olar həmişə tədbir və ya xidmət təşkilidir; uyğun olmayanları tam qiymətləndirmə
(AI) rədd edir — recall birinci.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TERMS = ["təşkili", "keçirilməsi"]


def upgrade() -> None:
    conn = op.get_bind()
    area_id = conn.execute(sa.text("SELECT id FROM interest_areas WHERE code = 'events'")).scalar()
    if area_id is None:
        return
    for term in TERMS:
        exists = conn.execute(sa.text(
            "SELECT 1 FROM keywords WHERE lower(term) = :t AND interest_area_id = :a AND applies_to = 'tender' "
            "AND is_negative = :neg"), {"t": term, "a": area_id, "neg": False}).first()
        if not exists:
            conn.execute(sa.text(
                "INSERT INTO keywords (interest_area_id, term, language, match, applies_to, is_negative, is_active) "
                "VALUES (:a, :t, 'az', 'prefix', 'tender', :neg, :act)"),
                {"a": area_id, "t": term, "neg": False, "act": True})


def downgrade() -> None:
    conn = op.get_bind()
    for term in TERMS:
        conn.execute(sa.text("DELETE FROM keywords WHERE term = :t AND applies_to = 'tender'"), {"t": term})
