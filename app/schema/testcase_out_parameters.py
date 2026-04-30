from typing import Optional

from pydantic import BaseModel, field_validator

from app.schema.base import PityModel


class PityTestCaseOutParametersForm(BaseModel):
    id: int = None
    # case_id = None
    name: str
    expression: Optional[str] = None
    match_index: Optional[str] = None
    source: int

    @field_validator("name", "source")
    @classmethod
    def name_not_empty(cls, v):
        return PityModel.not_empty(v)


class PityTestCaseParametersDto(PityTestCaseOutParametersForm):
    case_id: int = None


class PityTestCaseVariablesDto(BaseModel):
    case_id: int
    step_name: str