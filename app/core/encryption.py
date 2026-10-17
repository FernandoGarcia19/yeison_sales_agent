"""
Symmetric decryption for Twilio subaccount auth tokens stored by yeison_panel_backend.

Uses the same Fernet (AES-128-CBC + HMAC-SHA256) key configured via
ENCRYPTION_KEY. The sales agent only needs to decrypt — it never encrypts.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)


class EncryptionError(RuntimeError):
    """Raised when decryption fails."""


def _validate_key(raw_key: str) -> bytes:
    if not raw_key:
        raise EncryptionError(
            "ENCRYPTION_KEY is not set. Add it to the sales agent .env "
            "(same value as yeison_panel_backend ENCRYPTION_KEY)."
        )
    try:
        decoded = base64.urlsafe_b64decode(raw_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise EncryptionError("ENCRYPTION_KEY is not valid urlsafe base64.") from exc
    if len(decoded) != 32:
        raise EncryptionError("ENCRYPTION_KEY must decode to exactly 32 bytes.")
    return raw_key.encode("utf-8")


@lru_cache(maxsize=1)
def _get_cipher() -> Fernet:
    return Fernet(_validate_key(settings.encryption_key or ""))


class TokenCipher:
    @staticmethod
    def decrypt_token(encrypted_token: str) -> str:
        if not isinstance(encrypted_token, str) or not encrypted_token:
            raise EncryptionError("Cannot decrypt an empty or non-string token.")
        try:
            plain = _get_cipher().decrypt(encrypted_token.encode("utf-8"))
        except InvalidToken as exc:
            logger.warning("Attempted to decrypt a token with an invalid signature.")
            raise EncryptionError(
                "Encrypted token is invalid or was signed with a different key."
            ) from exc
        except Exception as exc:
            logger.exception("Token decryption failed.")
            raise EncryptionError("Failed to decrypt token.") from exc
        return plain.decode("utf-8")
