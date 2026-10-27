import json
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError

from . import config

IMMUTABLE = "public, max-age=31536000, immutable"
MISSING = {"NoSuchKey", "404", "NotFound"}


class TooManyObjects(Exception):
    pass


class S3Storage:
    def __init__(self, client=None):
        self.client = client or boto3.client("s3", region_name=config.S3_REGION, endpoint_url=config.S3_ENDPOINT_URL)

    def url(self, key: str) -> str:
        key = quote(key, safe="/")
        if config.S3_PUBLIC_BASE_URL:
            return f"{config.S3_PUBLIC_BASE_URL}/{key}"
        return f"https://{config.S3_BUCKET}.s3.{config.S3_REGION}.amazonaws.com/{key}"

    def list_keys(self, bucket: str, prefix: str, limit: int) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                if len(out) >= limit:
                    raise TooManyObjects(f"more than {limit} objects under {prefix!r}")
                out.append((o["Key"], o["Size"]))
        return out

    def download_file(self, bucket: str, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(bucket, key, str(dest))

    def upload_file(self, bucket: str, key: str, src: Path, content_type: str) -> None:
        self.client.upload_file(
            str(src), bucket, key, ExtraArgs={"ContentType": content_type, "CacheControl": IMMUTABLE},
        )

    def get_json(self, bucket: str, key: str) -> dict | None:
        try:
            obj = self.client.get_object(Bucket=bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in MISSING:
                return None
            raise
        return json.loads(obj["Body"].read())

    def put_json(self, bucket: str, key: str, obj: dict) -> None:
        self.client.put_object(
            Bucket=bucket, Key=key, Body=json.dumps(obj).encode(),
            ContentType="application/json", CacheControl="no-store",
        )

    def head_size(self, bucket: str, key: str) -> int | None:
        try:
            return self.client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in MISSING:
                return None
            raise
