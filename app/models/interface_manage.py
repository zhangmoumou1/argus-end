from sqlalchemy import Column, ForeignKey, INT, String, TEXT

from app.models.basic import PityBase


class PityApiService(PityBase):
    __tablename__ = "pity_api_service"

    project_id = Column(INT, ForeignKey("pity_project.id"), index=True, nullable=False, default=0, comment="所属项目")
    name = Column(String(128), nullable=False, comment="服务名称")
    base_url = Column(String(255), nullable=True, comment="基础地址")
    developer = Column(String(128), nullable=True, comment="开发人员")
    tester = Column(String(128), nullable=True, comment="测试人员")
    source_type = Column(String(32), nullable=False, default="manual", comment="来源类型 manual/swagger/yapi")
    source_config = Column(TEXT, nullable=True, comment="来源配置")
    sync_enabled = Column(INT, nullable=False, default=0, comment="是否开启定时同步")
    sync_cron = Column(String(64), nullable=True, comment="定时表达式")
    last_sync_status = Column(String(32), nullable=True, comment="最近同步状态")
    last_sync_at = Column(String(32), nullable=True, comment="最近同步时间")

    def __init__(self, project_id, name, user, base_url="", developer="", tester="", source_type="manual", source_config=None):
        super().__init__(user)
        self.project_id = project_id
        self.name = name
        self.base_url = base_url
        self.developer = developer
        self.tester = tester
        self.source_type = source_type
        self.source_config = source_config


class PityApiEndpoint(PityBase):
    __tablename__ = "pity_api_endpoint"

    service_id = Column(INT, ForeignKey("pity_api_service.id"), index=True, nullable=False, default=0, comment="服务ID")
    name = Column(String(255), nullable=False, comment="接口名称")
    method = Column(String(16), nullable=False, default="GET", comment="请求方法")
    module_name = Column(String(128), nullable=False, default="默认模块", comment="功能模块")
    endpoint_status = Column(String(16), nullable=False, default="available", comment="接口状态 available/deprecated")
    path = Column(String(512), nullable=False, comment="接口路径")
    full_url = Column(String(1024), nullable=True, comment="完整URL")
    request_headers = Column(TEXT, nullable=True, comment="请求头JSON")
    request_params = Column(TEXT, nullable=True, comment="请求参数JSON")
    response_body = Column(TEXT, nullable=True, comment="响应示例JSON")
    endpoint_key = Column(String(768), index=True, nullable=False, comment="唯一键")
    current_version_id = Column(INT, nullable=False, default=0, comment="当前版本ID")
    current_version_no = Column(String(32), nullable=False, default="v1", comment="当前版本号")

    def __init__(self, service_id, name, method, path, user, endpoint_key, full_url="", request_headers=None, request_params=None, response_body=None, module_name="默认模块", endpoint_status="available"):
        super().__init__(user)
        self.service_id = service_id
        self.name = name
        self.method = method
        self.module_name = module_name
        self.endpoint_status = endpoint_status
        self.path = path
        self.full_url = full_url
        self.request_headers = request_headers
        self.endpoint_key = endpoint_key
        self.request_params = request_params
        self.response_body = response_body


class PityApiEndpointVersion(PityBase):
    __tablename__ = "pity_api_endpoint_version"

    endpoint_id = Column(INT, ForeignKey("pity_api_endpoint.id"), index=True, nullable=False, default=0, comment="接口ID")
    version_no = Column(String(32), nullable=False, default="v1", comment="版本号")
    name = Column(String(255), nullable=False, comment="接口名称")
    method = Column(String(16), nullable=False, default="GET", comment="请求方法")
    module_name = Column(String(128), nullable=False, default="默认模块", comment="功能模块")
    endpoint_status = Column(String(16), nullable=False, default="available", comment="接口状态 available/deprecated")
    path = Column(String(512), nullable=False, comment="接口路径")
    full_url = Column(String(1024), nullable=True, comment="完整URL")
    request_headers = Column(TEXT, nullable=True, comment="请求头JSON")
    request_params = Column(TEXT, nullable=True, comment="请求参数JSON")
    response_body = Column(TEXT, nullable=True, comment="响应示例JSON")

    def __init__(self, endpoint_id, version_no, name, method, path, user, full_url="", request_headers=None, request_params=None, response_body=None, module_name="默认模块", endpoint_status="available"):
        super().__init__(user)
        self.endpoint_id = endpoint_id
        self.version_no = version_no
        self.name = name
        self.method = method
        self.module_name = module_name
        self.endpoint_status = endpoint_status
        self.path = path
        self.full_url = full_url
        self.request_headers = request_headers
        self.request_params = request_params
        self.response_body = response_body
