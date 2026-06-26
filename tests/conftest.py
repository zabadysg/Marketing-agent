import os

# Set before any app imports so settings and engine pick up the local URL.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:changeme@localhost:5432/marketing",
)
os.environ.setdefault("LANGSMITH_TRACING", "false")

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import get_db
from app.main import app

# NullPool: each test gets a fresh connection, no loop-affinity issues between tests.
_test_engine = create_async_engine(
    os.environ["DATABASE_URL"], echo=False, poolclass=NullPool
)
_TestSessionLocal = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    async with _TestSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


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
