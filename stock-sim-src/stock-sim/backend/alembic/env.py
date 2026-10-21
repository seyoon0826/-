from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import settings
from app.core.db import Base
import app.models  # noqa: F401  (metadata 등록)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# TimescaleDB 가 hypertable 생성 시 자동으로 만드는 인덱스는 모델에 없으므로 비교에서 제외
_TIMESCALE_AUTO_INDEXES = {"prices_daily_date_idx"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "index" and name in _TIMESCALE_AUTO_INDEXES:
        return False
    return True


def _url() -> str:
    # 테스트 등에서 config 로 덮어쓸 수 있고, 기본은 설정(DATABASE_URL)
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True,
                      include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
