from sqlalchemy import INT, VARCHAR, Column

from app.models.basic import ArgusBase, Base


class ArgusUserGroup(ArgusBase):
    __tablename__ = "argus_user_group"
    name = Column(VARCHAR(64), nullable=False, comment="用户组名称")
    description = Column(VARCHAR(200), nullable=True, comment="用户组描述")

    __tag__ = "用户组"
    __fields__ = (name, description)
    __alias__ = dict(name="用户组名称", description="描述")

    def __init__(self, name, create_user, description=None):
        super().__init__(create_user)
        self.name = name
        self.description = description


class ArgusUserGroupMember(Base):
    __tablename__ = "argus_user_group_member"
    id = Column(INT, primary_key=True)
    group_id = Column(INT, nullable=False, comment="用户组ID")
    user_id = Column(INT, nullable=False, comment="用户ID")
    created_at = Column(INT, nullable=False, default=0)
    deleted_at = Column(INT, nullable=False, default=0)

    def __init__(self, group_id, user_id):
        self.group_id = group_id
        self.user_id = user_id
        self.created_at = 0
        self.deleted_at = 0
