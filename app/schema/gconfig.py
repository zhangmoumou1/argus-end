from pydantic.v1 import BaseModel, validator

from app.exception.error import ParamsError


class GConfigForm(BaseModel):
    id: int = None
    key: str
    value: str
    env: str = None
    key_type: int
    enable: bool
    type: int = 1
    project_id: int = None
    case_id: int = None
    case_name: str = None

    @validator("key", "value", "key_type", "enable")
    def name_not_empty(cls, v):
        if isinstance(v, str) and len(v.strip()) == 0:
            raise ParamsError("不能为空")
        if not isinstance(v, int):
            if not v:
                raise ParamsError("不能为空")
        return v

