import boto3
import os
from botocore.config import Config


BUCKET_NAME = "ai-content-factory"


def _get_s3_client():
    """Get S3 client configured for RunPod S3-compatible storage."""
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "https://eu-central-1.storage.runpod.io"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY", ""),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY", ""),
        config=Config(signature_version="s3v4"),
        region_name="eu-central-1",
    )


def ensure_bucket_exists() -> None:
    """Create the S3 bucket if it doesn't exist."""
    client = _get_s3_client()
    try:
        client.head_bucket(Bucket=BUCKET_NAME)
    except client.exceptions.ClientError:
        try:
            client.create_bucket(Bucket=BUCKET_NAME)
        except Exception:
            pass  # Bucket may already exist or creation not supported


def upload_file(file_bytes: bytes, key: str, content_type: str = "application/octet-stream") -> str:
    """Upload file bytes to S3 and return the URL."""
    client = _get_s3_client()
    client.put_object(
        Bucket=BUCKET_NAME,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    endpoint = os.getenv("S3_ENDPOINT_URL", "https://eu-central-1.storage.runpod.io")
    return f"{endpoint}/{BUCKET_NAME}/{key}"


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
