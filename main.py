from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.database import close_db_connections
from app.core.redis_client import close_redis_connection
from app.api.v1.webhooks import router as webhook_router
from app.api.v1.telegram_webhook import router as telegram_webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - handles startup and shutdown."""
    # Startup
    print(f"Starting {settings.app_name} v{settings.version}")
    yield
    # Shutdown
    print("Shutting down...")
    await close_db_connections()
    await close_redis_connection()


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    debug=settings.debug,
    lifespan=lifespan
)

# Include routers
app.include_router(webhook_router, prefix="/api/v1")
app.include_router(telegram_webhook_router, prefix="/api/v1")

# Basic route
@app. get("/")
async def root():
    return {
        "message": f"Welcome to {settings.app_name}!",
        "version": settings.version,
        "status": "running"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.version
    }

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug
    )