"""prices_daily: KRX 일별 PER/PBR/배당수익률 (모델 피처 earn_yield, book_to_price, div_yield)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24

값은 KRX 가 그날 종가로 계산해 공표한 그대로 저장한다 (PER/PBR 배수, 배당수익률 %).
적자·자본잠식·펀드 등은 0 또는 결측으로 오므로 피처 단계에서 결측 처리한다.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prices_daily", sa.Column("per", sa.Numeric(12, 2), nullable=True))
    op.add_column("prices_daily", sa.Column("pbr", sa.Numeric(12, 2), nullable=True))
    op.add_column("prices_daily", sa.Column("div_yield", sa.Numeric(8, 2), nullable=True))


def downgrade() -> None:
    for c in ("div_yield", "pbr", "per"):
        op.drop_column("prices_daily", c)
