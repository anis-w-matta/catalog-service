from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import CATALOG_SCHEMA, settings

# This service's tables live in the `catalog` schema (see app/config.py) -
# still fully separate tables/migration history from the backend's own
# `dbo`-schema tables, reached only over this service's API, same isolation
# idea as a separate database. SQL Server has no Postgres-style search_path
# connection option, so this is done via SQLAlchemy's schema_translate_map
# instead: every unqualified table reference this service's code makes
# (models have no explicit schema=) is redirected to `catalog` here.
engine = create_engine(settings.database_url, pool_pre_ping=True,
                       pool_size=10, max_overflow=5, future=True
                       ).execution_options(
    schema_translate_map={None: CATALOG_SCHEMA})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
