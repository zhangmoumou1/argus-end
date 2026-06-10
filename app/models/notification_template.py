from sqlalchemy import SMALLINT, TEXT, VARCHAR, Column, BOOLEAN

from app.models.basic import PityBase


class PityNotificationTemplate(PityBase):
    __tablename__ = "pity_notification_template"
    name = Column(VARCHAR(64), nullable=False, comment="模板名称")
    channel_type = Column(SMALLINT, nullable=False, comment="0=邮件 1=钉钉 2=企业微信 3=飞书")
    subject_template = Column(VARCHAR(256), nullable=True, comment="主题模板")
    content_template = Column(TEXT, nullable=False, comment="内容模板")
    enabled = Column(BOOLEAN, default=True, comment="是否启用")

    __tag__ = "通知模板"
    __fields__ = (name, channel_type, enabled)
    __alias__ = dict(name="模板名称", channel_type="适配渠道", enabled="是否启用")

    def __init__(self, name, channel_type, content_template, create_user, subject_template=None, enabled=True):
        super().__init__(create_user)
        self.name = name
        self.channel_type = channel_type
        self.content_template = content_template
        self.subject_template = subject_template
        self.enabled = enabled
