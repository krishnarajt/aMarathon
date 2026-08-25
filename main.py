import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.database import init_db


def setup_logging():
    """Configure application-wide logging"""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    os.makedirs("logs", exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.handlers.TimedRotatingFileHandler(
                "logs/app.log",
                when="midnight",
                interval=1,
                backupCount=30,
                encoding="utf-8",
            ),
        ],
    )

    for noisy in (
        "httpx",
        "httpcore",
        "uvicorn.access",
        "boto3",
        "botocore",
        "urllib3",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured at {log_level} level")
    return logger


logger = setup_logging()

# Imported after logging is configured. auth_service refuses to import without
# SECRET_KEY, and this way that failure is logged rather than silent.
from app.api.auth_routes import router as auth_router  # noqa: E402
from app.api.challenge_routes import router as challenge_router  # noqa: E402
from app.api.entry_routes import router as entry_router  # noqa: E402
from app.api.friend_routes import router as friend_router  # noqa: E402
from app.api.image_routes import router as image_router  # noqa: E402
from app.api.settings_routes import router as settings_router  # noqa: E402
from app.api.stats_routes import router as stats_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Starting Amarathon Backend...")
    logger.info("=" * 60)

    try:
        init_db()
        logger.info("✓ Database initialized successfully")
    except Exception as e:
        logger.critical(f"✗ Failed to initialize database: {e}", exc_info=True)
        raise

    minio_endpoint = os.getenv("MINIO_ENDPOINT", "")
    if minio_endpoint:
        logger.info(f"✓ MinIO configured at {minio_endpoint}")
    else:
        logger.warning("⚠ MINIO_ENDPOINT not set - using default localhost:9000")

    logger.info("=" * 60)
    logger.info("Amarathon Backend is ready!")
    logger.info("=" * 60)

    yield

    logger.info("Shutting down Amarathon Backend...")


app = FastAPI(
    title="Amarathon Backend",
    description="Step and distance challenges between friends",
    version="1.0.0",
    lifespan=lifespan,
)

cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info(f"CORS enabled for origins: {cors_origins}")

for router in (
    auth_router,
    friend_router,
    challenge_router,
    entry_router,
    image_router,
    stats_router,
    settings_router,
):
    app.include_router(router, prefix="/api")
logger.info("API routers registered")


@app.get("/")
def root():
    return {
        "name": "Amarathon Backend",
        "description": "Step and distance challenges between friends",
        "version": "1.0.0",
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health_check():
    """Health check endpoint for k8s"""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ready")
def readiness_check():
    """Readiness check endpoint for k8s"""
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "false").lower() == "true"
    logger.info(f"Starting server on port {port}, reload={reload}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload, log_level="info")
