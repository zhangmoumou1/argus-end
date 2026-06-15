from pydantic.v1 import validator, BaseModel

from app.schema.base import ArgusModel


class DatabaseForm(BaseModel):
    id: int = None
    name: str
    host: str
    port: int = None
    username: str
    password: str
    database: str
    sql_type: int
    env: int

    @validator("name", "host", "port", "username", "password", "database", "sql_type", "env")
    def data_not_empty(cls, v):
        return ArgusModel.not_empty(v)

