"""paper engine: orders.reserve_price, orders.code 외래키 제거, fee_config 정밀도, 실제 수수료/세율 시드

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21

수수료/세율 시드 (오너 결정: 실제 값 사용, 2026-10-21)
- 매수/매도 수수료 0.0176923% = 한국투자증권 뱅키스 온라인 0.0140527% + 유관기관 제비용(KRX) 0.0036396%
  (한국투자증권 수수료 안내: 유관기관 제비용은 고객부담으로 별도 표기)
- 매도 거래세 0.20% = 증권거래세 0.05% + 농어촌특별세 0.15% (코스피, 2026-01-01 양도분부터, 증권거래세법 시행령 개정)
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

RATE = sa.Numeric(precision=14, scale=10)
OLD_RATE = sa.Numeric(precision=12, scale=8)


def upgrade() -> None:
    op.add_column("orders", sa.Column("reserve_price", sa.Integer(), nullable=True))
    # 유니버스에 없는 종목 주문도 REJECTED(UNKNOWN_SYMBOL)로 저장해야 하므로 종목 외래키를 없앤다 (SPEC 7.2)
    op.drop_constraint("orders_code_fkey", "orders", type_="foreignkey")
    for col in ("buy_fee_rate", "sell_fee_rate", "sell_tax_rate"):
        op.alter_column("fee_config", col, type_=RATE, existing_type=OLD_RATE, existing_nullable=False)
    op.execute(
        "INSERT INTO fee_config (effective_from, buy_fee_rate, sell_fee_rate, sell_tax_rate, note) VALUES "
        "('2026-01-01', 0.000176923, 0.000176923, 0.002, "
        "'수수료: 한국투자증권 뱅키스 온라인 0.0140527% + 유관기관 제비용 0.0036396%. "
        "거래세: 코스피 증권거래세 0.05% + 농어촌특별세 0.15% (2026-01-01 양도분부터)') "
        "ON CONFLICT (effective_from) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM fee_config WHERE effective_from = '2026-01-01'")
    for col in ("buy_fee_rate", "sell_fee_rate", "sell_tax_rate"):
        op.alter_column("fee_config", col, type_=OLD_RATE, existing_type=RATE, existing_nullable=False)
    op.create_foreign_key("orders_code_fkey", "orders", "stocks", ["code"], ["code"])
    op.drop_column("orders", "reserve_price")
