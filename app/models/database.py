from sqlalchemy import INT, Column, String, UniqueConstraint

from app.models.basic import PityBase
from app.models.environment import Environment


class PityDatabase(PityBase):
    __tablename__ = "pity_database_info"
    __table_args__ = (
        UniqueConstraint('env', 'name', 'deleted_at'),
    )

    env = Column(INT, nullable=False)  # 对应环境id
    name = Column(String(24), nullable=False)  # 数据库描述名称
    host = Column(String(64), nullable=False)  # 防止超长域名出现
    port = Column(INT, nullable=False)
    username = Column(String(36), nullable=False)
    password = Column(String(64), nullable=False)
    database = Column(String(36), nullable=False)
    sql_type = Column(INT, nullable=False, comment="0: mysql 1: postgresql 2: mongo")
    env_info: Environment
    __tag__ = "数据库配置"
    __fields__ = [name]
    __alias__ = dict(
        name="数据库名称",
        env="环境",
        host="主机",
        port="端口",
        username="用户名",
        password="密码",
        database="库名",
        sql_type="类型",
    )
    __show__ = 1

    def __init__(self, env, name, host, port, username, password, database, sql_type, user, id=None):
        super().__init__(user, id)
        self.env = env
        self.name = name
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.sql_type = sql_type
