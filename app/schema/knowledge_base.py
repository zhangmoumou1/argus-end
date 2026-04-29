from pydantic import BaseModel, validator

from app.exception.error import ParamsError


class KnowledgeBaseForm(BaseModel):
    id: int = None
    title: str
    summary: str = ""
    content: str

    @validator("title", "content")
    def required_not_empty(cls, value):
        if value is None:
            raise ParamsError("不能为空")
        if isinstance(value, str) and len(value.strip()) == 0:
            raise ParamsError("不能为空")
        return value
