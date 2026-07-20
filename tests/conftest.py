import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aiis.config import AppConfig
from aiis.db.models import Base

os.environ.setdefault("APPROVAL_SIGNING_SECRET", "test-secret-not-for-production")


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    yield s
    s.close()
