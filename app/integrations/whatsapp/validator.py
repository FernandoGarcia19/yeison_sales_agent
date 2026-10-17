"""
Twilio webhook signature validation.

Each tenant's Twilio subaccount signs webhooks with its own auth token —
not the master account token. We look up the subaccount token by the
recipient phone number (the "To" field in the form payload) and use
Twilio's official RequestValidator so the algorithm stays in sync with
Twilio's implementation.

Fallback: if no subaccount is found (e.g. sandbox testing, unconfigured
agent), we fall back to ``settings.twilio_auth_token`` so local development
still works without a full ISV setup.
"""

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
    _, auth_token = await get_subaccount_credentials(to_phone)

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
