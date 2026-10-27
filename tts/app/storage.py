from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError

from . import config

IMMUTABLE = "public, max-age=31536000, immutable"


class S3Storage:
    def __init__(self, client=None):
        self.client = client or boto3.client("s3", region_name=config.S3_REGION, endpoint_url=config.S3_ENDPOINT_URL)

    def url(self, key: str) -> str:
        key = quote(key, safe="/")
        if config.S3_PUBLIC_BASE_URL:
            return f"{config.S3_PUBLIC_BASE_URL}/{key}"
        return f"https://{config.S3_BUCKET}.s3.{config.S3_REGION}.amazonaws.com/{key}"

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=config.S3_BUCKET, Key=key)
            return True
        except ClientError:
            return False

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=config.S3_BUCKET, Key=key, Body=data, ContentType=content_type,
            CacheControl=IMMUTABLE,
        )
