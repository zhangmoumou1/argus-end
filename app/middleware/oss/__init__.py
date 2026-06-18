from app.core.configuration import SystemConfiguration
from app.enums.OssEnum import OssEnum
from app.middleware.oss.aliyun import AliyunOss
from app.middleware.oss.files import OssFile
from app.middleware.oss.qiniu import QiniuOss
from app.middleware.oss.s3 import S3CompatibleOss
from app.middleware.oss.tencent import TencentCos
from config import Config


def normalize_oss_upload_result(client: OssFile, upload_result, filepath: str, bucket_name: str = None, base_path: str = None):
    if isinstance(upload_result, dict):
        object_key = upload_result.get("key") or client.get_real_path(filepath, base_path)
        return {
            "file_url": upload_result.get("object_url") or "",
            "bucket_name": upload_result.get("bucket") or bucket_name or "",
            "object_key": object_key,
        }
    object_key = client.get_real_path(filepath, base_path)
    return {
        "file_url": str(upload_result),
        "bucket_name": bucket_name or "",
        "object_key": object_key,
    }


def get_public_bucket_name():
    return str(getattr(Config, "OSS_AVATAR_BUCKET", "") or getattr(Config, "OSS_BUCKET", "") or "").strip()


def get_default_bucket_name():
    return str(
        getattr(Config, "OSS_BUCKET", "")
        or getattr(Config, "OSS_DEFAULT_BUCKET", "")
        or "argus-end"
    ).strip()


def get_avatar_bucket_name():
    return get_public_bucket_name()


class OssClient(object):
    _client = None

    @classmethod
    def _get_env_oss_config(cls):
        oss_type = str(getattr(Config, "OSS_TYPE", "") or "").strip().lower()
        if not oss_type:
            return None
        return {
            "oss_type": oss_type,
            "endpoint": str(getattr(Config, "OSS_ENDPOINT", "") or "").strip(),
            "access_key_id": str(getattr(Config, "OSS_ACCESS_KEY_ID", "") or "").strip(),
            "access_key_secret": str(getattr(Config, "OSS_ACCESS_KEY_SECRET", "") or "").strip(),
            "bucket": str(getattr(Config, "OSS_BUCKET", "") or "").strip(),
            "avatar_bucket": str(getattr(Config, "OSS_AVATAR_BUCKET", "") or "").strip(),
            "region": str(getattr(Config, "OSS_REGION", "") or "us-east-1").strip(),
            "use_ssl": bool(getattr(Config, "OSS_USE_SSL", False)),
            "force_path_style": bool(getattr(Config, "OSS_FORCE_PATH_STYLE", True)),
            "presign_expire": int(getattr(Config, "OSS_PRESIGN_EXPIRE", 3600) or 3600),
        }

    @classmethod
    def _get_system_oss_config(cls):
        cfg = SystemConfiguration.get_config() or {}
        return cfg.get("oss")

    @classmethod
    def _require_fields(cls, oss_type: str, access_key_id: str, access_key_secret: str, bucket: str, endpoint: str):
        missing = []
        if not access_key_id:
            missing.append("access_key_id")
        if not access_key_secret:
            missing.append("access_key_secret")
        if not bucket:
            missing.append("bucket")
        if oss_type in {OssEnum.ALIYUN.value, OssEnum.TENCENT.value, OssEnum.S3.value} and not endpoint:
            missing.append("endpoint")
        if missing:
            raise Exception(f"oss配置不完整: 缺少 {', '.join(missing)}")

    @classmethod
    def get_oss_client(cls) -> OssFile:
        """
        通过oss配置拿到oss客户端
        :return:
        """
        if OssClient._client is None:
            oss_config = cls._get_env_oss_config() or cls._get_system_oss_config()
            if oss_config is None:
                raise Exception("服务器未配置oss信息, 请在 conf/*.env 中添加")

            access_key_id = oss_config.get("access_key_id")
            access_key_secret = oss_config.get("access_key_secret")
            bucket = oss_config.get("bucket")
            endpoint = oss_config.get("endpoint")
            oss_type = str(oss_config.get("oss_type") or "").lower()
            cls._require_fields(oss_type, access_key_id, access_key_secret, bucket, endpoint)

            if oss_type == OssEnum.ALIYUN.value:
                cls._client = AliyunOss(access_key_id, access_key_secret, endpoint, bucket)
            elif oss_type == OssEnum.QINIU.value:
                cls._client = QiniuOss(access_key_id, access_key_secret, bucket, endpoint)
            elif oss_type == OssEnum.TENCENT.value:
                cls._client = TencentCos(access_key_id, access_key_secret, endpoint, bucket)
            elif oss_type == OssEnum.S3.value:
                cls._client = S3CompatibleOss(
                    endpoint=endpoint,
                    access_key_id=access_key_id,
                    access_key_secret=access_key_secret,
                    default_bucket=bucket,
                    region=oss_config.get("region") or "us-east-1",
                    use_ssl=bool(oss_config.get("use_ssl", False)),
                    force_path_style=bool(oss_config.get("force_path_style", True)),
                    presign_expire=int(oss_config.get("presign_expire") or 3600),
                )
            else:
                raise Exception("不支持的oss类型")
        return OssClient._client
