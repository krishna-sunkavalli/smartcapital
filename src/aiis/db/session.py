from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from aiis.config import secrets
from aiis.db.models import Base

_engine: Engine | None = None
_factory: sessionmaker | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine, _factory
    if _engine is None:
        _engine = create_engine(url or secrets().aiis_database_url, future=True)
        _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))


@contextmanager
def get_session() -> Iterator[Session]:
    get_engine()
    assert _factory is not None
    session = _factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
