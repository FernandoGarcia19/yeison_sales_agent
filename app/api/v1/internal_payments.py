"""
Payment notices from BMSCYeisonQRGenerator (bank QR through SIP).

The QR service posts {alias, tenantId, estado} with x-api-key = BANK_QR_NOTIFY_KEY
when a charge becomes PAGADO. The body is not trusted: bank_qr.confirm_payment
asks the QR service for the charge before recording the sale. Reached only
inside the docker network (nginx returns 404 for /api/v1/internal/ from outside).
"""

import hmac

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services import bank_qr

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/payments", tags=["internal"])


class BankQrNotice(BaseModel):
    alias: str = Field(..., min_length=1, max_length=100)


@router.post("/bank-qr")
async def bank_qr_paid(notice: BankQrNotice, background_tasks: BackgroundTasks,
                       x_api_key: str = Header(default="")):
    if not settings.bank_qr_notify_key:
        raise HTTPException(status_code=503, detail="bank QR notices are not configured")
    if not hmac.compare_digest(x_api_key.encode(), settings.bank_qr_notify_key.encode()):
        raise HTTPException(status_code=401, detail="invalid key")
    logger.info("bank_qr_notice_received", alias=notice.alias)
    # Answer right away; the QR service retries on errors, and confirm_payment is idempotent
    background_tasks.add_task(bank_qr.confirm_payment, notice.alias, "notice")
    return {"ok": True}
