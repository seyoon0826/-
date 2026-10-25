"""관리자 기능: users.suspended_at, users.hidden_from_ranking, admin_audit

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25

관리자는 ADMIN_EMAILS 설정으로 정한다 (DB 에 역할 열을 두지 않는다). 관리자가 한 일은 admin_audit 에 남긴다.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("suspend_reason", sa.String(200), nullable=True))
    op.add_column(
        "users", sa.Column("hidden_from_ranking", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("admin_user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admin_audit_created", "admin_audit", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_created", table_name="admin_audit")
    op.drop_table("admin_audit")
    op.drop_column("users", "hidden_from_ranking")
    op.drop_column("users", "suspend_reason")
    op.drop_column("users", "suspended_at")
