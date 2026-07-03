import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


class _DropCancelledError(logging.Filter):
    """Suppress CancelledError tracebacks logged by langchain-core / LangSmith callbacks.

    langchain-core ≥1.4 catches BaseException in astream and fires on_llm_error
    for every exception, including CancelledError from browser disconnects.
    LangSmith's tracer then logs the full traceback even though it is not a real
    LLM failure.  This filter drops those records before they reach any handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and isinstance(record.exc_info[1], asyncio.CancelledError):
            return False
        return "CancelledError" not in record.getMessage()


_cancelled_filter = _DropCancelledError()
for _noisy_logger in ("langchain_core", "langsmith", "langchain_google_genai"):
    logging.getLogger(_noisy_logger).addFilter(_cancelled_filter)

from app.config import settings
from app.database import engine
from app.routers import health
from app.routers import workspaces, brand_profiles, plans, posts, connections, admin, knowledge, chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    from psycopg_pool import AsyncConnectionPool
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.agents.graph import init_graph
    from app.services.recovery import recover_stuck_plans

    pool = AsyncConnectionPool(
        conninfo=settings.checkpointer_conn_str,
        open=False,
        kwargs={"autocommit": True},
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()  # creates checkpoint tables; intentionally bypasses Alembic
    init_graph(checkpointer)
    await recover_stuck_plans(checkpointer)

    yield

    await pool.close()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/api/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Admin-Key"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(workspaces.router, prefix="/api")
    app.include_router(brand_profiles.router, prefix="/api")
    app.include_router(plans.router, prefix="/api")
    app.include_router(posts.router, prefix="/api")
    app.include_router(connections.router, prefix="/api")
    app.include_router(knowledge.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    return app


app = create_app()
