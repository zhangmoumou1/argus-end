from typing import Optional

from pydantic import BaseModel, field_validator

from app.schema.base import ArgusModel


class ArgusTestCaseOutParametersForm(BaseModel):
    id: int = None
    # case_id = None
    name: str
    expression: Optional[str] = None
    match_index: Optional[str] = None
    source: int

    @field_validator("name", "source")
    @classmethod
    def name_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class ArgusTestCaseParametersDto(ArgusTestCaseOutParametersForm):
    case_id: int = None


class ArgusTestCaseVariablesDto(BaseModel):
    case_id: int
    step_name: str