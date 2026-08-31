import secrets
from typing import Iterator

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import session_scope


def get_db() -> Iterator[Session]:
    with session_scope() as s:
        yield s


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Shared-secret gate for backend -> catalog-service calls, active
    only once `api_key` is configured. Same pattern the backend itself
    uses for its own callers - see app/api/deps.py there."""
    expected = settings.api_key
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or invalid API key")
