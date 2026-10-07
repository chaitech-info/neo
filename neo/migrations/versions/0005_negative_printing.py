"""Mənfi açar söz "mətbəə" (tender): çap məhsulları tenderləri.

2026-10-05 canlı yoxlama: gpt-4.1-mini "Mətbəə məhsullarının satın alınması"
tenderini promptdakı açıq qeydə baxmayaraq "qida məhsulu" kimi qiymətləndirdi
(0.82) — "mətbəə" (çap evi) ilə "mətbəx" (mətbəx) qarışdırılır. Mənfi söz
başlığı triajda deterministik bağlayır (açar söz də, AI də keçirə bilmir).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    area_id = conn.execute(sa.text("SELECT id FROM interest_areas WHERE code = 'staff_meals'")).scalar()
    if area_id is None:
        return
    exists = conn.execute(sa.text(
        "SELECT 1 FROM keywords WHERE term = 'mətbəə' AND applies_to = 'tender' AND is_negative = :neg"),
        {"neg": True}).first()
    if not exists:
        conn.execute(sa.text(
            "INSERT INTO keywords (interest_area_id, term, language, match, applies_to, is_negative, is_active) "
            "VALUES (:a, 'mətbəə', 'az', 'prefix', 'tender', :neg, :act)"), {"a": area_id, "neg": True, "act": True})


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM keywords WHERE term = 'mətbəə' AND is_negative = :neg"), {"neg": True})
