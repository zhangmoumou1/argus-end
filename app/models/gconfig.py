from sqlalchemy import INT, Column, String, TEXT, BOOLEAN, UniqueConstraint

from app.enums.GconfigEnum import GConfigVariableType
from app.models.basic import PityBase


class GConfig(PityBase):
    __tablename__ = 'pity_gconfig'
    env = Column(INT)
    key = Column(String(64))
    value = Column(TEXT)
    key_type = Column(INT, nullable=False, comment="0: string 1: json 2: yaml")
    type = Column(INT, nullable=False, default=GConfigVariableType.global_var, comment="1:全局 2:运行时 3:特殊")
    project_id = Column(INT, comment="变量来源项目id")
    case_id = Column(INT, comment="变量来源用例id")
    case_name = Column(String(128), comment="变量来源用例名称")
    # 是否可用
    enable = Column(BOOLEAN, default=True)

    __table_args__ = (
        UniqueConstraint('env', 'key', 'type', 'project_id', 'case_id', 'deleted_at'),
    )

    __fields__ = (env, key)
    __tag__ = "全局变量"
    __alias__ = dict(env="环境", key="名称", key_type="类型", value="值", type="变量类型", project_id="项目ID",
                     case_id="用例ID", case_name="用例名称")
    __show__ = 2

    def __init__(self, env, key, value, key_type, enable, user, id=None, type=GConfigVariableType.global_var,
                 project_id=None, case_id=None, case_name=None):
        super().__init__(user, id)
        self.env = env
        self.key = key
        self.value = value
        self.key_type = int(key_type) if key_type is not None else key_type
        self.type = int(type) if type is not None else type
        self.project_id = project_id
        self.case_id = case_id
        self.case_name = case_name
        self.enable = enable
