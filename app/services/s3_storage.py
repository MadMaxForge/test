import boto3
import httpx
import logging
import os
from botocore.config import Config

logger = logging.getLogger(__name__)


BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "m1wg8x4xm6")


def _get_s3_client():
    """Get S3 client configured for RunPod S3-compatible storage."""
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "https://s3api-eur-no-1.runpod.io"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY", ""),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY", ""),
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "adaptive"},
            connect_timeout=30,
            read_timeout=60,
        ),
        region_name="eur-no-1",
    )


def ensure_bucket_exists() -> None:
    """Verify the S3 bucket (network volume) is accessible."""
    client = _get_s3_client()
    try:
        client.head_bucket(Bucket=BUCKET_NAME)
    except Exception:
        pass  # Network volume bucket is managed by RunPod


def upload_file(file_bytes: bytes, key: str, content_type: str = "application/octet-stream") -> str:
    """Upload file bytes to S3 and return the URL.

    Tries direct boto3 upload first. If that fails (e.g. 502 from certain
    networks), falls back to uploading via a presigned PUT URL with httpx.
    """
    client = _get_s3_client()
    endpoint = os.getenv("S3_ENDPOINT_URL", "https://s3api-eur-no-1.runpod.io")

    # Attempt 1: direct boto3 upload
    try:
        client.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
        )
        return f"{endpoint}/{BUCKET_NAME}/{key}"
    except Exception as e:
        logger.warning("Direct S3 upload failed (%s), trying presigned URL fallback", e)

    # Attempt 2: presigned PUT URL via httpx (different HTTP path)
    try:
        presigned_url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=300,
        )
        resp = httpx.put(
            presigned_url,
            content=file_bytes,
            headers={"Content-Type": content_type},
            timeout=httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=10.0),
        )
        resp.raise_for_status()
        return f"{endpoint}/{BUCKET_NAME}/{key}"
    except Exception as e2:
        logger.error("Presigned URL upload also failed: %s", e2)
        raise


def generate_presigned_url(key: str, expiration: int = 3600) -> str:
    """Generate a presigned URL for downloading a file."""
    client = _get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=expiration,
    )


def download_file(key: str) -> bytes:
    """Download file from S3."""
    client = _get_s3_client()
    resp = client.get_object(Bucket=BUCKET_NAME, Key=key)
    return resp["Body"].read()
