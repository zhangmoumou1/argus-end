from sqlalchemy import BOOLEAN, Column, INT, TEXT, String
from sqlalchemy.dialects.mysql import SMALLINT

from app.models.basic import PityBase


class PityPerformancePlan(PityBase):
    __tablename__ = "pity_performance_plan"

    project_id = Column(INT, nullable=False, index=True)
    env = Column(INT, nullable=False, index=True)
    name = Column(String(64), nullable=False)
    description = Column(TEXT, nullable=True)

    service_id = Column(INT, nullable=False, default=0)
    endpoint_id = Column(INT, nullable=False, default=0)
    api_version_id = Column(INT, nullable=False, default=0)
    source_type = Column(String(16), nullable=False, default="single")
    case_list = Column(TEXT, nullable=True)
    load_mode = Column(String(16), nullable=False, default="concurrency")
    load_config = Column(TEXT, nullable=True)
    threshold_config = Column(TEXT, nullable=True)
    parameter_config = Column(TEXT, nullable=True)
    assertions_config = Column(TEXT, nullable=True)

    request_method = Column(String(16), nullable=False, default="GET")
    request_url = Column(String(1024), nullable=False)
    request_headers = Column(TEXT, nullable=True)
    request_query = Column(TEXT, nullable=True)
    request_body = Column(TEXT, nullable=True)

    concurrency = Column(INT, nullable=False, default=10)
    duration_seconds = Column(INT, nullable=False, default=60)
    ramp_up_seconds = Column(INT, nullable=False, default=0)
    think_time_ms = Column(INT, nullable=False, default=0)
    iterations = Column(INT, nullable=False, default=0)
    request_timeout_ms = Column(INT, nullable=False, default=10000)

    expect_p95_ms = Column(INT, nullable=True)
    expect_error_rate = Column(INT, nullable=True)
    enabled = Column(BOOLEAN, nullable=False, default=True)

    __fields__ = (name, project_id, env, source_type, load_mode, service_id, endpoint_id, api_version_id)
    __tag__ = "性能计划"
    __alias__ = dict(
        name="名称",
        project_id="项目",
        env="环境",
        service_id="服务",
        endpoint_id="接口",
        api_version_id="接口版本",
        source_type="压测模式",
        case_list="接口用例",
        load_mode="负载模型",
        parameter_config="参数化配置",
        assertions_config="断言配置",
        request_method="请求方式",
        request_url="请求地址",
        concurrency="并发数",
        duration_seconds="压测时长",
        iterations="总次数",
        enabled="是否启用",
    )

    def __init__(self, user, **kwargs):
        super().__init__(user)
        for key, value in kwargs.items():
            setattr(self, key, value)


class PityPerformanceReport(PityBase):
    __tablename__ = "pity_performance_report"

    plan_id = Column(INT, nullable=False, index=True)
    executor = Column(INT, nullable=False, index=True)
    env = Column(INT, nullable=False, index=True)
    status = Column(SMALLINT, nullable=False, default=0, comment="0: pending, 1: running, 2: stopped, 3: finished")

    plan_name = Column(String(64), nullable=False)
    request_method = Column(String(16), nullable=False, default="GET")
    request_url = Column(String(1024), nullable=False)

    concurrency = Column(INT, nullable=False, default=0)
    duration_seconds = Column(INT, nullable=False, default=0)
    total_requests = Column(INT, nullable=False, default=0)
    success_count = Column(INT, nullable=False, default=0)
    failed_count = Column(INT, nullable=False, default=0)

    avg_rt_ms = Column(String(32), nullable=True)
    min_rt_ms = Column(String(32), nullable=True)
    max_rt_ms = Column(String(32), nullable=True)
    p50_rt_ms = Column(String(32), nullable=True)
    p90_rt_ms = Column(String(32), nullable=True)
    p95_rt_ms = Column(String(32), nullable=True)
    p99_rt_ms = Column(String(32), nullable=True)
    avg_rps = Column(String(32), nullable=True)
    error_rate = Column(String(32), nullable=True)
    cost = Column(String(16), nullable=True)

    summary_json = Column(TEXT, nullable=True)
    timeline_json = Column(TEXT, nullable=True)
    errors_json = Column(TEXT, nullable=True)

    __fields__ = (plan_name, plan_id, env, executor)
    __tag__ = "性能报告"
    __alias__ = dict(
        plan_name="计划名称",
        plan_id="计划ID",
        env="环境",
        executor="执行人",
        total_requests="请求总数",
        success_count="成功数",
        failed_count="失败数",
        avg_rps="平均RPS",
        p95_rt_ms="P95耗时",
        error_rate="错误率",
    )

    def __init__(self, user, **kwargs):
        super().__init__(user)
        self.executor = user
        for key, value in kwargs.items():
            setattr(self, key, value)


class PityPerformanceParameterFile(PityBase):
    __tablename__ = "pity_performance_parameter_file"

    project_id = Column(INT, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(16), nullable=False, default="csv")
    columns = Column(TEXT, nullable=True)
    row_count = Column(INT, nullable=False, default=0)
    encoding = Column(String(32), nullable=False, default="utf-8")
    delimiter = Column(String(8), nullable=False, default=",")

    __fields__ = (name, project_id, file_name, file_type, row_count)
    __tag__ = "性能参数文件"
    __alias__ = dict(
        name="名称",
        project_id="项目",
        file_name="文件名",
        file_type="文件类型",
        row_count="数据行数",
    )

    def __init__(self, user, **kwargs):
        super().__init__(user)
        for key, value in kwargs.items():
            setattr(self, key, value)


class PityPerformanceRunLog(PityBase):
    __tablename__ = "pity_performance_run_log"

    run_id = Column(INT, nullable=False, index=True)
    level = Column(String(16), nullable=False, default="INFO")
    message = Column(String(255), nullable=False)
    detail = Column(TEXT, nullable=True)

    __fields__ = (run_id, level, message)
    __tag__ = "性能执行日志"
    __alias__ = dict(
        run_id="执行记录",
        level="日志级别",
        message="日志消息",
    )

    def __init__(self, user, **kwargs):
        super().__init__(user)
        for key, value in kwargs.items():
            setattr(self, key, value)
