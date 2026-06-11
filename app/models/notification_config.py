from sqlalchemy import TEXT, INT, VARCHAR, Column

from app.models.basic import PityBase


class PityNotificationConfig(PityBase):
    __tablename__ = "pity_notification_config"
    name = Column(VARCHAR(64), nullable=False, comment="配置名称")
    channel_ids = Column(TEXT, nullable=False, comment="渠道ID列表，逗号分隔")
    template_id = Column(INT, nullable=True, comment="模板ID")
    receiver = Column(TEXT, nullable=True, comment="接收人用户ID列表，逗号分隔")
    group_ids = Column(TEXT, nullable=True, comment="用户组ID列表，逗号分隔")
    __tag__ = "通知配置"
    __fields__ = (name,)
    __alias__ = dict(name="配置名称")

    def __init__(self, name, channel_ids, create_user, template_id=None, receiver=None, group_ids=None):
        super().__init__(create_user)
        self.name = name
        self.channel_ids = channel_ids
        self.template_id = template_id
        self.receiver = receiver
        self.group_ids = group_ids
