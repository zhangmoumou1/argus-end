from typing import List

from pydantic.v1 import BaseModel, validator

from app.schema.base import PityModel


class PityTestPlanForm(BaseModel):
    id: int = None
    project_id: int
    name: str
    priority: str
    env: List[int]
    cron: str
    ordered: bool
    case_list: List[int]
    pass_rate: int = None
    receiver: List[int] = list()
    msg_type: List[int] = list()
    retry_minutes: int = 0
    enabled: bool = True
    notification_config_id: int = None

    @validator("case_list", "project_id", "env", "cron", "ordered", "priority", "name")
    def name_not_empty(cls, v):
        return PityModel.not_empty(v)

    @validator("pass_rate")
    def validate_pass_rate(cls, v):
        if v is None or v == "":
            return None
        value = int(v)
        if value < 1 or value > 100:
            raise ValueError("成功率阈值必须在1-100之间")
        return value
