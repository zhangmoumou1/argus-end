from pydantic import BaseModel, field_validator

from app.schema.base import ArgusModel


class ArgusTestcaseDataForm(BaseModel):
    id: int = None
    case_id: int = None
    name: str
    json_data: str
    env: int

    @field_validator("env", "name", "json_data")
    @classmethod
    def name_not_empty(cls, v):
        return ArgusModel.not_empty(v)

