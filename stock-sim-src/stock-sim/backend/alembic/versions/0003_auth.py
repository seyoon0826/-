"""auth: users.token_version (리프레시 토큰 일괄 무효화), 이메일 소문자 유일 인덱스 (FR-AU-1/2)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23

리프레시 토큰은 서버에 저장하지 않는다(JWT). 대신 토큰에 users.token_version 을 넣고,
로그아웃 때 이 값을 올려 그 사용자의 기존 리프레시 토큰을 모두 무효로 만든다.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.execute("UPDATE users SET email = lower(email)")
    op.create_index("ix_users_email_lower", "users", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email_lower", table_name="users")
    op.drop_column("users", "token_version")
