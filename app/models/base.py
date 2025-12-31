"""
Base model class for SQLAlchemy models.
"""

from datetime import datetime
from sqlalchemy import DateTime, Boolean, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""
    
    pass


class TimestampMixin:
    """Mixin for created_at and last_update timestamps."""
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )
    last_update: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )


class ActiveMixin:
    """Mixin for soft delete functionality."""
    
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )
