"""DB 테스트 공통 fixture.

개발 DB 를 건드리지 않도록 `<DB명>_test` 데이터베이스를 만들어 alembic 으로 마이그레이션하고,
각 테스트는 외부 트랜잭션 안에서 실행한 뒤 롤백한다. Postgres 에 접속할 수 없으면 DB 테스트는 skip.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.db import Base
from app.ml.features import ML_ROOT  # noqa: F401  (ml/ 을 sys.path 에 넣어 `src` 패키지를 쓸 수 있게 한다)

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _test_url() -> str:
    url = make_url(settings.database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def engine():
    base = make_url(settings.database_url)
    test_url = _test_url()
    try:
        admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            name = make_url(test_url).database
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
        admin.dispose()
    except OperationalError as e:
        pytest.skip(f"Postgres 에 접속할 수 없어 DB 테스트를 건너뜁니다: {e.__class__.__name__}")

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url.replace("%", "%%"))
    command.upgrade(cfg, "head")

    eng = create_engine(test_url)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", autoflush=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def sf(engine):
    """파이프라인 테스트용 세션 팩토리. 실제로 커밋하므로 테스트 전후로 테이블을 비운다.
    fee_config 는 마이그레이션이 넣은 기준 데이터(실제 요율)라 비우지 않는다."""
    keep = {"fee_config"}
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name not in keep)

    def wipe():
        with engine.begin() as c:
            c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    wipe()
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    wipe()
