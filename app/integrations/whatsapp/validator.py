"""
Webhook validation for WhatsApp providers.

Evolution API is the primary provider. Twilio validation remains available as
deprecated compatibility for legacy tenants.
"""

import hmac

from fastapi import Request
from twilio.request_validator import RequestValidator
import structlog

from app.core.config import settings
from app.services.twilio_credentials import get_subaccount_credentials

logger = structlog.get_logger()


async def validate_twilio_signature(request: Request, form_data: dict) -> bool:
    """
    Validate the X-Twilio-Signature header using the per-tenant subaccount
    auth token.  Returns True if the signature is valid, False otherwise.
    """
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        logger.warning("missing_twilio_signature")
        return False

    # Resolve the auth token: per-tenant subaccount first, global fallback.
    to_phone = form_data.get("To", "")
    _, auth_token = await get_subaccount_credentials(to_phone)  # the subaccount's own, decrypted

    if not auth_token:
        auth_token = settings.twilio_auth_token

    if not auth_token:
        logger.warning("no_auth_token_available_for_validation", to=to_phone)
        # Allow in debug mode so local sandbox testing still works.
        return settings.debug

    url = str(request.url)
    validator = RequestValidator(auth_token)
    is_valid = validator.validate(url, form_data, signature)

    if not is_valid:
        logger.warning(
            "twilio_signature_invalid",
            to=to_phone,
            url=url,
        )

    return is_valid


async def validate_evolution_webhook(request: Request, payload: dict) -> bool:
    """The shared secret Evolution sends in a header (set by the panel when it creates the instance, Y-10).

    Without EVOLUTION_WEBHOOK_SECRET every webhook is refused, except in debug (local development).
    """
    expected_secret = settings.evolution_webhook_secret
    if not expected_secret:
        logger.warning("evolution_webhook_secret_missing")
        return bool(settings.debug)

    received_secret = (
        request.headers.get("x-evolution-webhook-secret")
        or request.headers.get("x-evolution-secret")
        or ""
    )
    if not hmac.compare_digest(received_secret.encode(), expected_secret.encode()):
        logger.warning("evolution_webhook_secret_invalid")
        return False
    return True
