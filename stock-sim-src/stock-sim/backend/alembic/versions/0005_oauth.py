"""소셜 로그인(카카오, 구글): oauth_accounts, users.email/password_hash 비울 수 있게, users.display_name

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24

카카오는 이메일 제공 동의가 선택이라 이메일 없는 사용자가 생긴다. 소셜로만 가입한 사용자는 비밀번호가 없다.
같은 (provider, subject) 는 항상 같은 사용자다.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(255), nullable=True)
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=True)
    op.add_column("users", sa.Column("display_name", sa.String(100), nullable=True))
    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),  # kakao | google
        sa.Column("subject", sa.String(255), nullable=False),  # 제공자 쪽 사용자 ID
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("provider", "subject", name="uq_oauth_provider_subject"),
    )
    op.create_index("ix_oauth_accounts_user_id", "oauth_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_accounts_user_id", table_name="oauth_accounts")
    op.drop_table("oauth_accounts")
    op.drop_column("users", "display_name")
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=False)
    op.alter_column("users", "email", existing_type=sa.String(255), nullable=False)
