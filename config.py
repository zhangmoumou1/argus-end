# 基础配置类
import os
from typing import Any, ClassVar, Dict, List, Tuple

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = os.path.dirname(os.path.abspath(__file__))


class BaseConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    LOG_DIR: ClassVar[str] = os.path.join(ROOT, "logs")
    LOG_NAME: ClassVar[str] = os.path.join(LOG_DIR, "pity.log")

    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int

    HEARTBEAT: int = 48

    # mock server
    MOCK_ON: bool
    PROXY_ON: bool
    PROXY_PORT: int
    MYSQL_HOST: str
    MYSQL_PORT: int
    MYSQL_USER: str
    MYSQL_PWD: str
    DBNAME: str

    # etcd server
    ETCD_ENDPOINT: str

    # WARNING: close redis can make job run multiple times at the same time
    REDIS_ON: bool
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int
    REDIS_PASSWORD: str
    # Redis连接信息
    REDIS_NODES: List[Dict[str, Any]] = Field(default_factory=list)

    # sqlalchemy
    SQLALCHEMY_DATABASE_URI: str = ""
    # 异步URI
    ASYNC_SQLALCHEMY_URI: str = ""
    SQLALCHEMY_TRACK_MODIFICATIONS: ClassVar[bool] = False

    # 权限 0 普通用户 1 组长 2 管理员
    MEMBER: ClassVar[int] = 0
    MANAGER: ClassVar[int] = 1
    ADMIN: ClassVar[int] = 2

    # github access_token地址
    GITHUB_ACCESS: ClassVar[str] = "https://github.com/login/oauth/access_token"

    # github获取用户信息
    GITHUB_USER: ClassVar[str] = "https://api.github.com/user"

    # client_id
    CLIENT_ID: str

    # SECRET
    SECRET_KEY: str

    # Kimi / Moonshot
    KIMI_API_KEY: str = "sk-e5OUafm3yFex3hWjsgS7rZw68hNtYJsJLji0u7ruEKqhQavU"
    KIMI_BASE_URL: str = "https://api.moonshot.cn/v1"
    KIMI_MODEL: str = "kimi-k2.6"

    # 测试报告路径
    REPORT_PATH: ClassVar[str] = os.path.join(ROOT, "templates", "report.html")

    # 重置密码路径
    PASSWORD_HTML_PATH: ClassVar[str] = os.path.join(ROOT, "templates", "reset_password.html")

    # APP 路径
    APP_PATH: ClassVar[str] = os.path.join(ROOT, "app")

    # dao路径
    DAO_PATH: ClassVar[str] = os.path.join(APP_PATH, "crud")

    # markdown地址
    MARKDOWN_PATH: ClassVar[str] = os.path.join(ROOT, "templates", "markdown")

    SERVER_REPORT: ClassVar[str] = "http://localhost:8000/#/record/report/"

    OSS_URL: ClassVar[str] = "http://oss.pity.fun"

    # 七牛云链接地址，如果采用七牛oss，需要自行替换
    QINIU_URL: ClassVar[str] = "https://static.pity.fun"

    RELATION: ClassVar[str] = "pity_relation"
    ALIAS: ClassVar[str] = "__alias__"
    TABLE_TAG: ClassVar[str] = "__tag__"
    # 数据库表展示的变更字段
    FIELD: ClassVar[str] = "__fields__"
    SHOW_FIELD: ClassVar[str] = "__show__"
    IGNORE_FIELDS: ClassVar[Tuple[str, ...]] = (
        "created_at",
        "updated_at",
        "deleted_at",
        "create_user",
        "update_user",
    )

    # 测试计划中，case默认重试次数
    RETRY_TIMES: ClassVar[int] = 1

    # 日志名
    PITY_ERROR: ClassVar[str] = "pity_error"
    PITY_INFO: ClassVar[str] = "pity_info"


class DevConfig(BaseConfig):
    model_config = SettingsConfigDict(
        env_file=os.path.join(ROOT, "conf", "dev.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class ProConfig(BaseConfig):
    model_config = SettingsConfigDict(
        env_file=os.path.join(ROOT, "conf", "pro.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    SERVER_REPORT: ClassVar[str] = "https://pity.fun/#/record/report/"
    SERVER_HOST: str = "127.0.0.1"


# 获取pity环境变量
PITY_ENV = os.environ.get("pity_env", "dev")
# 如果pity_env存在且为prod
Config = ProConfig() if PITY_ENV and PITY_ENV.lower() == "pro" else DevConfig()

# init redis
Config.REDIS_NODES = [
    {
        "host": Config.REDIS_HOST,
        "port": Config.REDIS_PORT,
        "db": Config.REDIS_DB,
        "password": Config.REDIS_PASSWORD,
    }
]

# init sqlalchemy (used by apscheduler)
Config.SQLALCHEMY_DATABASE_URI = "mysql+mysqlconnector://{}:{}@{}:{}/{}".format(
    Config.MYSQL_USER,
    Config.MYSQL_PWD,
    Config.MYSQL_HOST,
    Config.MYSQL_PORT,
    Config.DBNAME,
)

# init async sqlalchemy
Config.ASYNC_SQLALCHEMY_URI = (
    f"mysql+aiomysql://{Config.MYSQL_USER}:{Config.MYSQL_PWD}"
    f"@{Config.MYSQL_HOST}:{Config.MYSQL_PORT}/{Config.DBNAME}"
)

BANNER = """
 ________        ___          _________         ___    ___ 
|\   __  \      |\  \        |\___   ___\      |\  \  /  /|
\ \  \|\  \     \ \  \       \|___ \  \_|      \ \  \/  / /
 \ \   ____\     \ \  \           \ \  \        \ \    / / 
  \ \  \___|      \ \  \           \ \  \        \/  /  /  
   \ \__\          \ \__\           \ \__\     __/  / /    
    \|__|           \|__|            \|__|    |\___/ /     
                                              \|___|/      

"""
