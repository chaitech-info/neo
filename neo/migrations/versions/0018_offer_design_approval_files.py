"""Təklif PDF-inin dizaynı və təsdiqə göndərilən məktubun faylları:
- offers.design: təklif hansı dizaynla yaradılıb (app/offer_pdf.py::DESIGNS); boş — klassik;
- letter_approval_files: "Təsdiqə göndər" ilə gedən məktuba qoşulmuş fayllar (əvvəl fayllar təsdiqə getmirdi —
  CEO qoşulmuş təklif PDF-ini görmürdü). Məzmun bazadadır (Vercel-də disk yoxdur), ən çox 5 fayl / 4 MB.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("offers") as batch:
        batch.add_column(sa.Column("design", sa.String(16), nullable=True))
    op.create_table(
        "letter_approval_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("approval_id", sa.Integer(), sa.ForeignKey("letter_approvals.id"), nullable=False),
        sa.Column("filename", sa.String(120), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_letter_approval_files_approval_id", "letter_approval_files", ["approval_id"])


def downgrade() -> None:
    op.drop_index("ix_letter_approval_files_approval_id", table_name="letter_approval_files")
    op.drop_table("letter_approval_files")
    with op.batch_alter_table("offers") as batch:
        batch.drop_column("design")
