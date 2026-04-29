from sqlalchemy import Column, INT, String, TEXT

from app.models.basic import PityBase


class PityRuntimeVariable(PityBase):
    __tablename__ = "pity_runtime_variable"

    case_id = Column(INT, nullable=False, index=True, comment="产生变量的接口(case)ID")
    case_name = Column(String(128), nullable=True, comment="产生变量的接口(case)名称")
    stage = Column(String(32), nullable=False, comment="变量产生阶段: pre_constructor/post_constructor/out_parameter")
    source_id = Column(INT, nullable=True, comment="来源ID: constructor.id/out_parameter.id")
    source_name = Column(String(128), nullable=True, comment="来源名称: 构造器名/出参名")
    variable_name = Column(String(128), nullable=False, index=True, comment="变量名")
    variable_value = Column(TEXT, nullable=True, comment="变量值(JSON字符串或普通字符串)")
    value_type = Column(String(32), nullable=True, comment="变量值类型")
    request_method = Column(String(12), nullable=True, comment="产生变量接口请求方法")
    api_url = Column(TEXT, nullable=True, comment="产生变量接口URL")
    run_path = Column(String(255), nullable=True, comment="运行路径(用于追踪嵌套前后置场景)")

    def __init__(
            self,
            case_id: int,
            variable_name: str,
            user_id: int,
            stage: str,
            case_name: str = None,
            source_id: int = None,
            source_name: str = None,
            variable_value: str = None,
            value_type: str = None,
            request_method: str = None,
            api_url: str = None,
            run_path: str = None,
            id=None,
    ):
        super().__init__(user_id, id)
        self.case_id = case_id
        self.case_name = case_name
        self.stage = stage
        self.source_id = source_id
        self.source_name = source_name
        self.variable_name = variable_name
        self.variable_value = variable_value
        self.value_type = value_type
        self.request_method = request_method
        self.api_url = api_url
        self.run_path = run_path
