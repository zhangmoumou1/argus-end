import os
from typing import Optional
from urllib.parse import quote, urlsplit, urlunsplit

import boto3
from botocore.client import Config as BotoConfig
from awaits.awaitable import awaitable

from app.middleware.oss.files import OssFile


class S3CompatibleOss(OssFile):
    def __init__(
        self,
        endpoint: str,
        access_key_id: str,
        access_key_secret: str,
        default_bucket: str,
        region: str = "us-east-1",
        use_ssl: bool = False,
        force_path_style: bool = True,
        presign_expire: int = 3600,
    ):
        self.default_bucket = default_bucket
        self.presign_expire = presign_expire
        self.force_path_style = force_path_style
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=access_key_secret,
            region_name=region,
            use_ssl=use_ssl,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path" if force_path_style else "auto"},
            ),
        )

    @staticmethod
    def _normalize_key(filepath: str, base_path: Optional[str] = None) -> str:
        key = str(filepath or "").replace("\\", "/").strip("/")
        prefix = str(base_path or "").replace("\\", "/").strip("/")
        if prefix and key:
            return f"{prefix}/{key}"
        return prefix or key

    def get_real_path(self, filepath, base_path=None):
        return self._normalize_key(filepath, base_path)

    def _resolve_bucket(self, bucket_name: str = None) -> str:
        return str(bucket_name or self.default_bucket or "").strip()

    def _build_object_url(self, bucket_name: str, key: str) -> str:
        endpoint = str(self.client.meta.endpoint_url or "").rstrip("/")
        encoded_key = quote(str(key or "").lstrip("/"), safe="/")
        parts = urlsplit(endpoint)
        if not parts.scheme or not parts.netloc:
            return f"{endpoint}/{bucket_name}/{encoded_key}"
        if self.force_path_style:
            return f"{endpoint}/{bucket_name}/{encoded_key}"
        return urlunsplit((parts.scheme, f"{bucket_name}.{parts.netloc}", f"/{encoded_key}", "", ""))

    def _presigned_url(self, bucket_name: str, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": key},
            ExpiresIn=self.presign_expire,
        )

    @staticmethod
    def _format_size(file_size: int) -> str:
        units = ("B", "KB", "MB", "GB", "TB")
        size = int(file_size or 0)
        unit_index = 0
        while size >= 1024 and unit_index < len(units) - 1:
            size //= 1024
            unit_index += 1
        return f"{size}{units[unit_index]}"

    @staticmethod
    def _format_datetime(value) -> Optional[str]:
        if not value:
            return None
        try:
            return value.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(value)

    @awaitable
    def create_file(self, filepath: str, content: bytes, base_path: str = None, bucket_name: str = None,
                    content_type: str = None):
        bucket = self._resolve_bucket(bucket_name)
        key = self.get_real_path(filepath, base_path)
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self.client.put_object(Bucket=bucket, Key=key, Body=content, **extra_args)
        return {
            "bucket": bucket,
            "key": key,
            "object_url": self._build_object_url(bucket, key),
        }, len(content)

    @awaitable
    def delete_file(self, filepath: str, base_path: str = None, bucket_name: str = None):
        bucket = self._resolve_bucket(bucket_name)
        key = self.get_real_path(filepath, base_path)
        self.client.delete_object(Bucket=bucket, Key=key)

    @awaitable
    def delete_prefix(self, filepath: str, base_path: str = None, bucket_name: str = None):
        bucket = self._resolve_bucket(bucket_name)
        prefix = self.get_real_path(filepath, base_path).strip("/")
        if not prefix:
            return
        paginator = self.client.get_paginator("list_objects_v2")
        objects = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents") or []:
                key = str(item.get("Key") or "").strip("/")
                if not key:
                    continue
                if key == prefix or key.startswith(f"{prefix}/"):
                    objects.append({"Key": key})
        for index in range(0, len(objects), 1000):
            self.client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": objects[index:index + 1000], "Quiet": True},
            )

    @awaitable
    def download_file(self, filepath, base_path: str = None, bucket_name: str = None):
        bucket = self._resolve_bucket(bucket_name)
        key = self.get_real_path(filepath, base_path)
        filename = os.path.basename(key)
        path = rf'./{self.get_random_filename(filename)}'
        self.client.download_file(bucket, key, path)
        return path, filename

    @awaitable
    def get_file_object(self, filepath, bucket_name: str = None):
        bucket = self._resolve_bucket(bucket_name)
        key = self.get_real_path(filepath)
        response = self.client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    @awaitable
    def get_object_detail(self, filepath: str, bucket_name: str = None):
        bucket = self._resolve_bucket(bucket_name)
        key = self.get_real_path(filepath)
        response = self.client.head_object(Bucket=bucket, Key=key)
        size = int(response.get("ContentLength") or 0)
        return {
            "bucket": bucket,
            "file_path": key,
            "key": key,
            "name": os.path.basename(key),
            "size": size,
            "file_size": self._format_size(size),
            "content_type": response.get("ContentType") or "",
            "etag": str(response.get("ETag") or "").strip('"'),
            "last_modified": self._format_datetime(response.get("LastModified")),
            "storage_class": response.get("StorageClass") or "STANDARD",
            "metadata": response.get("Metadata") or {},
            "cache_control": response.get("CacheControl") or "",
            "content_disposition": response.get("ContentDisposition") or "",
            "content_encoding": response.get("ContentEncoding") or "",
            "expires": self._format_datetime(response.get("Expires")),
            "version_id": response.get("VersionId") or "",
            "accept_ranges": response.get("AcceptRanges") or "",
            "view_url": self._presigned_url(bucket, key),
        }

    @awaitable
    def list_objects(self, prefix: str = "", recursive: bool = True, bucket_name: str = None, suffix: str = None):
        bucket = self._resolve_bucket(bucket_name)
        normalized_prefix = str(prefix or "").replace("\\", "/").strip("/")
        if normalized_prefix:
            normalized_prefix = f"{normalized_prefix}/" if not normalized_prefix.endswith("/") else normalized_prefix

        paginator = self.client.get_paginator("list_objects_v2")
        pagination_args = {"Bucket": bucket, "Prefix": normalized_prefix}
        if not recursive:
            pagination_args["Delimiter"] = "/"

        ans = []
        for page in paginator.paginate(**pagination_args):
            for item in page.get("CommonPrefixes") or []:
                dir_key = str(item.get("Prefix") or "").strip("/")
                if not dir_key:
                    continue
                ans.append({
                    "bucket": bucket,
                    "file_path": dir_key,
                    "key": dir_key,
                    "name": os.path.basename(dir_key),
                    "is_dir": True,
                    "size": 0,
                    "file_size": "0B",
                    "view_url": "",
                    "updated_at": None,
                })

            for item in page.get("Contents") or []:
                key = str(item.get("Key") or "").strip("/")
                if not key or key == normalized_prefix.strip("/"):
                    continue
                if suffix and not key.lower().endswith(str(suffix).lower()):
                    continue
                ans.append({
                    "bucket": bucket,
                    "file_path": key,
                    "key": key,
                    "name": os.path.basename(key),
                    "is_dir": False,
                    "size": int(item.get("Size") or 0),
                    "file_size": self._format_size(int(item.get("Size") or 0)),
                    "view_url": self._presigned_url(bucket, key),
                    "updated_at": item.get("LastModified").strftime("%Y-%m-%d %H:%M:%S") if item.get("LastModified") else None,
                })
        return ans
