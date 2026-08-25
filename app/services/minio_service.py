import io
import logging
import os
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

MINIO_BUCKET = os.getenv("MINIO_BUCKET", "amarathon")


class MinIOService:
    """Object storage for proof screenshots and avatars."""

    def __init__(self):
        self.endpoint_url = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
        self.access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        self.secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
        self.bucket_name = MINIO_BUCKET
        self.region = os.getenv("MINIO_REGION", "us-east-1")
        self._client = None
        self._initialized = False

    @property
    def client(self):
        """Lazy-init the S3 client so the app doesn't crash if MinIO is down at startup."""
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=Config(signature_version="s3v4"),
            )
        if not self._initialized:
            self._ensure_bucket_exists()
            self._initialized = True
        return self._client

    def _ensure_bucket_exists(self):
        """Create the bucket if missing. Non-fatal if MinIO is unreachable."""
        try:
            self._client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket"):
                try:
                    self._client.create_bucket(Bucket=self.bucket_name)
                    logger.info(f"Created MinIO bucket: {self.bucket_name}")
                except ClientError as create_err:
                    logger.error(f"Error creating bucket: {create_err}")
            else:
                logger.warning(f"Could not verify MinIO bucket: {e}")
        except Exception as e:
            # Connection errors, DNS failures — don't crash the app
            logger.warning(f"MinIO not reachable during bucket check: {e}")

    def upload_bytes(self, data: bytes, object_key: str, content_type: str) -> bool:
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=io.BytesIO(data),
                ContentType=content_type,
            )
            return True
        except ClientError as e:
            logger.error(f"Error uploading {object_key}: {e}")
            return False

    def upload_fileobj(self, file_obj, object_key: str, content_type: str) -> bool:
        try:
            self.client.upload_fileobj(
                file_obj,
                self.bucket_name,
                object_key,
                ExtraArgs={"ContentType": content_type},
            )
            return True
        except ClientError as e:
            logger.error(f"Error uploading {object_key}: {e}")
            return False

    def get_presigned_url(self, object_key: str, expiration: int = 3600) -> Optional[str]:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": object_key},
                ExpiresIn=expiration,
            )
        except ClientError as e:
            logger.error(f"Error generating presigned URL for {object_key}: {e}")
            return None

    def delete_object(self, object_key: str) -> bool:
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=object_key)
            return True
        except ClientError as e:
            logger.error(f"Error deleting {object_key}: {e}")
            return False

    def delete_objects_batch(self, object_keys: list) -> bool:
        if not object_keys:
            return True
        try:
            self.client.delete_objects(
                Bucket=self.bucket_name,
                Delete={"Objects": [{"Key": key} for key in object_keys]},
            )
            return True
        except ClientError as e:
            logger.error(f"Error batch deleting images: {e}")
            return False

    def list_objects(self, prefix: str) -> list:
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket_name, Prefix=prefix
            )
            return [obj["Key"] for obj in response.get("Contents", [])]
        except ClientError as e:
            logger.error(f"Error listing objects under {prefix}: {e}")
            return []


_minio_service = None


def get_minio_service() -> MinIOService:
    global _minio_service
    if _minio_service is None:
        _minio_service = MinIOService()
    return _minio_service


# ---------------------------------------------------------------------------
# Key layout
#
#   entries/{challenge_id}/{user_id}/{entry_uuid}/{image_uuid}.{ext}
#   avatars/{user_id}/{image_uuid}.{ext}
#
# Challenge first, so everything belonging to one challenge sits under a
# single prefix and is browsable in the MinIO console.
# ---------------------------------------------------------------------------

def entry_image_key(
    challenge_id: int, user_id: int, entry_uuid: str, image_uuid: str, extension: str
) -> str:
    return f"entries/{challenge_id}/{user_id}/{entry_uuid}/{image_uuid}.{extension}"


def avatar_key(user_id: int, image_uuid: str, extension: str) -> str:
    return f"avatars/{user_id}/{image_uuid}.{extension}"


def upload_image(file_obj, object_key: str, content_type: str) -> str:
    service = get_minio_service()
    if not service.upload_fileobj(file_obj, object_key, content_type):
        raise RuntimeError(f"Failed to upload image to MinIO: {object_key}")
    return object_key


def generate_presigned_url(object_key: str, expiration: int = 3600) -> Optional[str]:
    try:
        return get_minio_service().get_presigned_url(object_key, expiration)
    except Exception as e:
        logger.error(f"Error in generate_presigned_url: {e}")
        return None


def delete_image(object_key: str) -> bool:
    try:
        return get_minio_service().delete_object(object_key)
    except Exception as e:
        logger.error(f"Error in delete_image: {e}")
        return False


def delete_images(object_keys: list) -> bool:
    """Batch-delete by key. Callers read the keys out of the database, so this
    stays exact instead of guessing at prefixes."""
    try:
        return get_minio_service().delete_objects_batch(object_keys)
    except Exception as e:
        logger.error(f"Error in delete_images: {e}")
        return False
