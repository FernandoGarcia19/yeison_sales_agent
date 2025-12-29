"""
Twilio signature validation for webhook security.
"""

import hashlib
import hmac
from urllib.parse import urljoin
from fastapi import Request
import structlog

from app.core.config import settings

logger = structlog.get_logger()


async def validate_twilio_signature(
    request: Request,
    form_data: dict
) -> bool:
    """
    Validate that the webhook request came from Twilio.
    
    Twilio signs all webhook requests with your auth token.
    See: https://www.twilio.com/docs/usage/security#validating-requests
    
    Args:
        request: FastAPI request object
        form_data: Form data from the request
    
    Returns:
        True if signature is valid, False otherwise
    """
    
    if not settings.twilio_auth_token:
        logger.warning("twilio_auth_token_not_set")
        # In development, allow requests without validation
        return settings.debug
    
    # Get the signature from headers
    signature = request.headers.get("X-Twilio-Signature", "")
    
    if not signature:
        logger.warning("missing_twilio_signature")
        return False
    
    # Build the full URL (Twilio uses the full URL in signature)
    url = str(request.url)
    
    # Sort parameters and concatenate
    # Format: URLparamname1value1paramname2value2...
    sorted_params = sorted(form_data.items())
    data = url + "".join(f"{k}{v}" for k, v in sorted_params)
    
    # Compute HMAC-SHA256 signature
    computed_signature = hmac.new(
        settings.twilio_auth_token.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha1
    ).digest()
    
    # Encode to base64
    import base64
    computed_signature_b64 = base64.b64encode(computed_signature).decode()
    
    # Compare signatures (timing-safe comparison)
    is_valid = hmac.compare_digest(signature, computed_signature_b64)
    
    if not is_valid:
        logger.warning(
            "signature_mismatch",
            expected=computed_signature_b64[:10] + "...",
            received=signature[:10] + "..."
        )
    
    return is_valid
