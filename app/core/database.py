"""
Database session management and connection pooling.
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool, QueuePool

from app.core.config import settings


# Global engine instance
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """
    Get or create the database engine.
    
    Uses connection pooling for production environments.
    """
    global _engine
    
    if _engine is None:
        if not settings.database_url:
            raise ValueError("DATABASE_URL environment variable is not set")
        
        # Convert postgresql:// to postgresql+asyncpg://
        db_url = settings.database_url
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif not db_url.startswith("postgresql+asyncpg://"):
            raise ValueError("Database URL must start with 'postgresql://' or 'postgresql+asyncpg://'")
        
        # Build engine kwargs based on debug mode
        engine_kwargs = {
            "echo": settings.debug,  # Log SQL queries in debug mode
        }
        
        if settings.debug:
            # NullPool for debug mode - no pooling parameters
            engine_kwargs["poolclass"] = NullPool
        else:
            # QueuePool for production with pooling parameters
            engine_kwargs.update({
                "poolclass": QueuePool,
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout,
                "pool_pre_ping": True,  # Verify connections before using
            })
        
        # Create engine
        _engine = create_async_engine(db_url, **engine_kwargs)
    
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get or create the session factory.
    """
    global _session_factory
    
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,  # Don't expire objects after commit
            autocommit=False,
            autoflush=False,
        )
    
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for FastAPI to get database sessions.
    
    Usage:
        @app.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db_connections():
    """
    Close all database connections.
    
    Should be called on application shutdown.
    """
    global _engine, _session_factory
    
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    
    _session_factory = None
