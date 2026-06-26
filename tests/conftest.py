import os

# Override DATABASE_URL before app modules are imported so the engine points to
# the locally exposed Postgres (127.0.0.1:5432) instead of Docker's 'db:5432'.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:changeme@localhost:5432/marketing",
)
# Keep LangSmith off during tests
os.environ.setdefault("LANGSMITH_TRACING", "false")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app

_test_engine = create_async_engine(
    os.environ["DATABASE_URL"], echo=False, pool_pre_ping=True
)
_TestSessionLocal = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with _TestSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest_asyncio.fixture
async def test_client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
