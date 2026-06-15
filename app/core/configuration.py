import os

from app.middleware.RedisManager import RedisHelper
from config import Config, ARGUS_ENV, ROOT


class SystemConfiguration(object):
    """
    系统配置
    """

    @staticmethod
    def env_filepath():
        if ARGUS_ENV and ARGUS_ENV.lower() == "pro":
            return os.path.join(ROOT, "conf", "pro.env")
        return os.path.join(ROOT, "conf", "dev.env")

    @staticmethod
    def _serialize():
        return {
            "email": {
                "sender": str(getattr(Config, "EMAIL_SENDER", "") or ""),
                "password": str(getattr(Config, "EMAIL_PASSWORD", "") or ""),
                "host": str(getattr(Config, "EMAIL_HOST", "") or ""),
                "to": str(getattr(Config, "EMAIL_TO", "") or ""),
            },
            "yapi": {
                "token": str(getattr(Config, "YAPI_TOKEN", "") or ""),
            },
        }

    @staticmethod
    def _flatten_config(config: dict):
        config = config or {}
        email = config.get("email") if isinstance(config.get("email"), dict) else {}
        yapi = config.get("yapi") if isinstance(config.get("yapi"), dict) else {}
        return {
            "EMAIL_SENDER": str(email.get("sender") or ""),
            "EMAIL_PASSWORD": str(email.get("password") or ""),
            "EMAIL_HOST": str(email.get("host") or ""),
            "EMAIL_TO": str(email.get("to") or ""),
            "YAPI_TOKEN": str(yapi.get("token") or ""),
        }

    @staticmethod
    def _quote_env_value(value: str):
        text = str(value or "")
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    @RedisHelper.cache("configuration", 24 * 3600)
    def get_config():
        try:
            return SystemConfiguration._serialize()
        except Exception as e:
            raise Exception(f"获取系统设置失败, {e}")

    @staticmethod
    @RedisHelper.up_cache("configuration")
    def update_config(config):
        try:
            filepath = SystemConfiguration.env_filepath()
            if not os.path.exists(filepath):
                raise Exception("没找到环境配置文件，请检查 conf/*.env 是否存在")

            target_mapping = SystemConfiguration._flatten_config(config)
            with open(filepath, mode="r", encoding="utf-8") as f:
                lines = f.readlines()

            seen = set()
            updated_lines = []
            for raw_line in lines:
                stripped = raw_line.strip()
                replaced = False
                for key, value in target_mapping.items():
                    if stripped.startswith(f"{key}="):
                        updated_lines.append(f"{key}={SystemConfiguration._quote_env_value(value)}\n")
                        seen.add(key)
                        replaced = True
                        break
                if not replaced:
                    updated_lines.append(raw_line)

            if updated_lines and updated_lines[-1] and not updated_lines[-1].endswith("\n"):
                updated_lines[-1] = f"{updated_lines[-1]}\n"

            missing_lines = [
                f"{key}={SystemConfiguration._quote_env_value(value)}\n"
                for key, value in target_mapping.items()
                if key not in seen
            ]
            if missing_lines:
                if updated_lines and updated_lines[-1].strip():
                    updated_lines.append("\n")
                updated_lines.extend(missing_lines)

            with open(filepath, mode="w", encoding="utf-8") as f:
                f.writelines(updated_lines)

            for key, value in target_mapping.items():
                setattr(Config, key, value)
        except Exception as e:
            raise Exception(f"更新系统设置失败, {e}")
