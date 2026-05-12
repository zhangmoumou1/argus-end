from pydantic.v1 import BaseModel, validator

from app.exception.error import ParamsError


class KnowledgeBaseCategoryForm(BaseModel):
    id: int = None
    name: str
    sort_order: int = 0

    @validator("name")
    def name_not_empty(cls, v):
        if isinstance(v, str) and len(v.strip()) == 0:
            raise ParamsError("分类名称不能为空")
        return v
