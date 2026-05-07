"""
Cloudflare R2 storage service (S3-compatible).
Presigned URL generation is a local crypto operation — no HTTP call, ~1ms.
Uploads use aioboto3 for async compatibility.
"""

import io
import aioboto3
import boto3
from app.core.config import settings


def _sync_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def get_presigned_url(key: str, expires_in: int = 900) -> str:
    """Return a presigned GET URL for a private R2 object. Local crypto, no network call."""
    client = _sync_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket_name, "Key": key},
        ExpiresIn=expires_in,
    )


async def upload_bytes(key: str, data: bytes, content_type: str) -> str:
    """Upload raw bytes to R2 and return the object key."""
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    ) as s3:
        await s3.upload_fileobj(
            io.BytesIO(data),
            settings.r2_bucket_name,
            key,
            ExtraArgs={"ContentType": content_type},
        )
    return key
