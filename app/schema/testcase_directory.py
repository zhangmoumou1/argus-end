from typing import List, Optional

from pydantic import BaseModel, validator

from app.schema.base import PityModel


class PityTestcaseDirectoryForm(BaseModel):
    id: Optional[int] = None
    name: str
    project_id: int
    parent: Optional[int] = None
    sort_index: Optional[int] = None

    @validator("name", "project_id")
    def name_not_empty(cls, v):
        return PityModel.not_empty(v)


class PityMoveTestCaseDto(BaseModel):
    project_id: int
    id_list: List[int]
    directory_id: int

    @validator("id_list", "project_id", "directory_id")
    def name_not_empty(cls, v):
        return PityModel.not_empty(v)


class PityTestcaseDirectoryUpdateForm(BaseModel):
    id: int
    project_id: int
    name: Optional[str] = None
    parent: Optional[int] = None
    sort_index: Optional[int] = None

    @validator("id", "project_id")
    def required_not_empty(cls, v):
        return PityModel.not_empty(v)
