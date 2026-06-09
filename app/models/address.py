"""
Python请求网关地址表
"""

from sqlalchemy import Column, INT, String, UniqueConstraint

from app.models.basic import PityBase


class PityGateway(PityBase):
    __tablename__ = 'pity_gateway'
    __table_args__ = (
        UniqueConstraint('env', 'name', 'deleted_at'),
    )
    env = Column(INT, comment='对应环境')
    name = Column(String(32), comment="网关名称")
    gateway = Column(String(128), comment="网关地址")
    page_url = Column(String(255), comment="页面地址")

    __fields__ = (name, env, gateway, page_url)
    __tag__ = "网关"
    __alias__ = dict(name="网关名称", env="环境", gateway="网关地址", page_url="页面地址")
    __show__ = 2

    def __init__(self, env, name, gateway, user_id, id=None, page_url=""):
        super().__init__(user_id, id)
        self.name = name
        self.env = env
        self.gateway = gateway
        self.page_url = page_url
