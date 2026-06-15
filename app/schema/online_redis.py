from pydantic.v1 import BaseModel, validator

from app.schema.base import ArgusModel


class OnlineRedisForm(BaseModel):
    id: int = None
    command: str

    @validator("command", 'id')
    def name_not_empty(cls, v):
        return ArgusModel.not_empty(v)

