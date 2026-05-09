from sqlalchemy import Boolean, Column, INT, String, UniqueConstraint

from app.models.basic import PityBase


class PityMQConfig(PityBase):
    __tablename__ = "pity_mq_config"
    __table_args__ = (
        UniqueConstraint('env', 'name', 'mq_type', 'deleted_at'),
    )

    env = Column(INT, nullable=False, comment="环境ID")
    name = Column(String(64), nullable=False, comment="连接名称")
    mq_type = Column(String(16), nullable=False, comment="kafka/rabbitmq")
    host = Column(String(128), nullable=False, comment="主机")
    port = Column(INT, nullable=False, default=0, comment="端口")
    username = Column(String(64), nullable=False, default="", comment="用户名")
    password = Column(String(128), nullable=False, default="", comment="密码")
    virtual_host = Column(String(64), nullable=False, default="/", comment="RabbitMQ虚拟主机")
    use_ssl = Column(Boolean, nullable=False, default=False, comment="是否SSL")
    description = Column(String(255), nullable=True, comment="描述")

    __tag__ = "消息中间件配置"
    __fields__ = (name, env, mq_type, host, port, username, password, virtual_host, use_ssl, description)
    __alias__ = dict(
        name="连接名称",
        env="环境",
        mq_type="中间件类型",
        host="主机",
        port="端口",
        username="用户名",
        password="密码",
        virtual_host="虚拟主机",
        use_ssl="SSL",
        description="描述",
    )

    def __init__(self, env, name, mq_type, host, port, user, username="", password="",
                 virtual_host="/", use_ssl=False, description="", id=None):
        super().__init__(user, id=id)
        self.env = env
        self.name = name
        self.mq_type = mq_type
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.virtual_host = virtual_host
        self.use_ssl = use_ssl
        self.description = description
