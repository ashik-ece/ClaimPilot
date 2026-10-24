from typing import Protocol

import boto3
from botocore.client import Config

from app.config import settings


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...


class S3Storage:
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            region_name="us-east-1",
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
        )

    def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=settings.s3_bucket, Key=key)
        return bytes(obj["Body"].read())


def get_storage() -> Storage:
    return S3Storage()
