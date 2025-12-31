"""
API v1 package.
"""

from app.api.v1.webhooks import router as webhook_router

__all__ = ["webhook_router"]
