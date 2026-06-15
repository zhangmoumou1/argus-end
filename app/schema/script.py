from pydantic.v1 import BaseModel, validator

from app.schema.base import ArgusModel


class PyScriptForm(BaseModel):
    command: str
    value: str

    @validator("command")
    def name_not_empty(cls, v):
        return ArgusModel.not_empty(v)

