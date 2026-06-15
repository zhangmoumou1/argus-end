from typing import Any

from pydantic.v1 import BaseModel, validator

from app.schema.base import ArgusModel


class ArgusPerformancePlanForm(BaseModel):
    id: int = None
    project_id: int
    env: int
    name: str
    description: str = ""

    service_id: int = 0
    endpoint_id: int = 0
    api_version_id: int = 0
    source_type: str = "single"
    case_list: Any = ""
    load_mode: str = "concurrency"
    load_config: Any = {}
    threshold_config: Any = []
    parameter_config: Any = {}
    assertions_config: Any = []

    request_method: str
    request_url: str
    request_headers: str = ""
    request_query: str = ""
    request_body: str = ""

    concurrency: int = 10
    duration_seconds: int = 60
    ramp_up_seconds: int = 0
    think_time_ms: int = 0
    iterations: int = 0
    request_timeout_ms: int = 10000

    expect_p95_ms: int = None
    expect_error_rate: int = None
    enabled: bool = True

    @validator(
        "project_id",
        "env",
        "name",
        "request_method",
        "request_url",
        "concurrency",
        "duration_seconds",
        "request_timeout_ms",
    )
    def fields_not_empty(cls, value):
        return ArgusModel.not_empty(value)


class ArgusPerformanceParameterValidateForm(BaseModel):
    source_type: str = "api_asset"
    request_url: str = ""
    request_headers: str = "{}"
    request_query: str = "{}"
    request_body: str = ""
    case_list: Any = ""
    parameter_config: Any = {}
