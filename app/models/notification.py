from sqlalchemy import SMALLINT, Column, VARCHAR, INT

from app.models.basic import ArgusBase


class ArgusNotification(ArgusBase):
    msg_type = Column(SMALLINT, comment="消息类型 1: 系统消息 2: 其他消息")
    msg_title = Column(VARCHAR(32), comment="消息标题", nullable=False)
    msg_content = Column(VARCHAR(200), comment="消息内容", nullable=True)
    msg_link = Column(VARCHAR(128), comment="消息链接")
    msg_status = Column(SMALLINT, comment="消息状态 1: 未读 2: 已读")
    sender = Column(INT, comment="消息发送人, 0则是CPU 非0则是其他用户")
    receiver = Column(INT, comment="消息接收人, 系统消息则该字段为空")

    __tablename__ = "argus_notification"
    __fields__ = [msg_title, receiver]
    __tag__ = "消息通知"
    __alias__ = dict(
        msg_type="消息类型",
        msg_title="消息标题",
        msg_content="消息内容",
        msg_link="消息链接",
        msg_status="消息状态",
        sender="发送人",
        receiver="接收人",
    )
    __show__ = 1

    def __init__(self, msg_type, msg_title, msg_content, sender, receiver, user, msg_link=None, msg_status=0):
        super().__init__(user)
        self.msg_type = msg_type
        self.msg_title = msg_title
        self.receiver = receiver
        self.msg_content = msg_content
        self.sender = sender
        self.msg_link = msg_link
        self.msg_status = msg_status
