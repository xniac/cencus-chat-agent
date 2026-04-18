import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.config import settings
from backend.app.services.snowflake_client import SnowflakeClient
from backend.app.services.llm import get_llm_provider
from backend.app.services.schema_cache import SchemaCache, REFRESH_INTERVAL
from backend.app.services.session import SessionStore
from backend.app.routers.chat import router as chat_router

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def _session_cleanup_loop(session_store: SessionStore):
    while True:
        await asyncio.sleep(300)
        await session_store.cleanup_expired()
        logger.debug(f"Session cleanup complete. Active sessions: {session_store.active_count}")


async def _schema_refresh_loop(schema_cache: SchemaCache):
    """Periodically refresh the schema cache in a worker thread so the
    scheduled refresh never blocks the event loop.
    """
    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        try:
            await asyncio.to_thread(schema_cache.refresh)
            logger.debug("Schema cache refresh complete")
        except Exception as e:
            logger.warning(f"Scheduled schema refresh failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Census Chat Agent...")

    snowflake = SnowflakeClient()
    app.state.snowflake = snowflake
    app.state.database = settings.snowflake_database
    app.state.schema = settings.snowflake_schema

    llm = get_llm_provider()
    app.state.llm = llm
    app.state.llm_provider_name = settings.llm_provider

    schema_cache = SchemaCache(snowflake)
    app.state.schema_cache = schema_cache

    session_store = SessionStore()
    app.state.session_store = session_store

    # Refresh schema cache on startup (runs in a thread to avoid blocking the event loop)
    try:
        await asyncio.to_thread(schema_cache.refresh)
        logger.info("Schema cache initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize schema cache: {e}")

    # Background tasks: session cleanup + periodic schema refresh.
    # The schema refresh runs in a thread so it never blocks the event loop.
    cleanup_task = asyncio.create_task(_session_cleanup_loop(session_store))
    schema_task = asyncio.create_task(_schema_refresh_loop(schema_cache))

    yield

    # Shutdown
    for task in (cleanup_task, schema_task):
        task.cancel()
    for task in (cleanup_task, schema_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("Census Chat Agent shut down.")


app = FastAPI(title=settings.app_title, lifespan=lifespan)

# CORS — allow all origins for demo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(chat_router)

# Mount frontend static files if they exist
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
