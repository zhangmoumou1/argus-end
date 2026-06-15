from sqlalchemy import SMALLINT, TEXT, VARCHAR, Column, BOOLEAN

from app.models.basic import ArgusBase


class ArgusNotificationChannel(ArgusBase):
    __tablename__ = "argus_notification_channel"
    name = Column(VARCHAR(64), nullable=False, comment="渠道名称")
    channel_type = Column(SMALLINT, nullable=False, comment="0=邮件 1=钉钉 2=企业微信 3=飞书")
    config_json = Column(TEXT, nullable=False, comment="渠道配置JSON")
    enabled = Column(BOOLEAN, default=True, comment="是否启用")
    description = Column(VARCHAR(200), nullable=True, comment="渠道描述")

    __tag__ = "通知渠道"
    __fields__ = (name, channel_type, enabled, description)
    __alias__ = dict(name="渠道名称", channel_type="渠道类型", enabled="是否启用", description="描述")

    def __init__(self, name, channel_type, config_json, create_user, enabled=True, description=None):
        super().__init__(create_user)
        self.name = name
        self.channel_type = channel_type
        self.config_json = config_json
        self.enabled = enabled
        self.description = description
