from pydantic import BaseModel, validator

from app.exception.error import ParamsError


def _sanitize_mysql_utf8(value: str):
    """
    兼容 MySQL utf8(3-byte) 场景：
    去除无法落库的 4-byte 字符（如部分 emoji），避免 1366 错误。
    """
    if not isinstance(value, str):
        return value
    return ''.join(ch for ch in value if ord(ch) <= 0xFFFF)


class KnowledgeBaseForm(BaseModel):
    id: int = None
    title: str
    summary: str = ""
    content: str
    category: str = ""

    @validator("title", "content")
    def required_not_empty(cls, value):
        if value is None:
            raise ParamsError("不能为空")
        if isinstance(value, str) and len(value.strip()) == 0:
            raise ParamsError("不能为空")
        return value

    @validator("content", pre=True, always=True)
    def sanitize_content_for_mysql(cls, value):
        if value is None:
            return value
        return _sanitize_mysql_utf8(str(value))
