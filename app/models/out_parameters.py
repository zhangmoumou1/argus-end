from sqlalchemy import Column, String, INT, UniqueConstraint

from app.models.basic import ArgusBase


class ArgusTestCaseOutParameters(ArgusBase):
    """
    argus用例出参数据，与用例绑定
    """
    __tablename__ = 'argus_out_parameters'
    __table_args__ = (
        UniqueConstraint('case_id', 'name', 'deleted_at'),
    )
    # 用例id
    case_id = Column(INT, nullable=False)
    # 参数名
    name = Column(String(24), nullable=False)
    # 来源类型
    source = Column(INT, nullable=False, default=0,
                    comment="0: Body(TEXT) 1: Body(JSON) 2: Header 3: Cookie 4: HTTP状态码")
    # 表达式
    expression = Column(String(128))
    # 获取结果索引, 可以是random，也可以是all，还可以是数字
    match_index = Column(String(16))

    __fields__ = [name, case_id, source, expression, match_index]
    __tag__ = "出参变量"
    __alias__ = dict(
        case_id="测试用例",
        name="变量名",
        source="来源类型",
        expression="表达式",
        match_index="匹配索引",
    )
    __show__ = 1

    def __init__(self, name, source, case_id, user_id, expression=None, match_index=None, id=None):
        super().__init__(user_id, id)
        self.name = name
        self.case_id = case_id
        self.expression = expression
        self.match_index = match_index
        self.source = source
