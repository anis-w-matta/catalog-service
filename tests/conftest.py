"""Shared fixtures for catalog-service's regression suite.

This is the first test suite catalog-service has ever had (see
vendo-intelligence-web/docs/audit/ - the phase 1 audit found `pytest` was
listed in requirements.txt but nothing used it). Mirrors the backend's
existing pattern in ../../backend/tests/conftest.py: an isolated SQL Server
schema (`catalog_test`, same database as the real `voiceorder` DB, selected
via SQLAlchemy's schema_translate_map - SQL Server has no Postgres-style
search_path connection option), each test wrapped in a rolled-back
SAVEPOINT so nothing persists and tests never depend on each other's data.

That schema must exist and be at the current Alembic head before running
this suite:

    CREATE SCHEMA catalog_test;   -- once, via SSMS/sqlcmd
    ALEMBIC_SCHEMA=catalog_test .venv/Scripts/python -m alembic upgrade head

(see alembic/env.py's ALEMBIC_SCHEMA handling, which now redirects both the
alembic_version bookkeeping table AND every migration's unqualified table
DDL into that schema.) Unlike the backend, this service owns the
Customer/Item/Order* models directly, so tests build fixture data with
plain SQLAlchemy inserts - no raw-SQL/HTTP workaround needed.
"""
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Customer, Item

_TEST_SCHEMA = "catalog_test"


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.database_url, future=True).execution_options(
        schema_translate_map={None: _TEST_SCHEMA})
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine) -> Session:
    """A session bound to one connection/outer transaction, rolled back
    after the test. Uses a SAVEPOINT (begin_nested) so application code
    calling session.commit() doesn't end the outer transaction early -
    restarted automatically after each commit, same technique as the
    backend's conftest.py.
    """
    connection = engine.connect()
    outer = connection.begin()
    SessionLocal = sessionmaker(bind=connection, future=True,
                                expire_on_commit=False)
    session = SessionLocal()
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def _unique(prefix: str, max_len: int) -> str:
    return f"{prefix}{uuid.uuid4().hex}"[:max_len]


@pytest.fixture
def customer(db_session) -> Customer:
    c = Customer(customer_number=_unique("C", 20),
                customer_name="Test Customer", salesman_id=None)
    db_session.add(c)
    db_session.flush()
    return c


@pytest.fixture
def item(db_session) -> Item:
    i = Item(item_number=_unique("I", 30), item_desc="Test Item",
            category="Test Category")
    db_session.add(i)
    db_session.flush()
    return i
