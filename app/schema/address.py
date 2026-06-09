from pydantic.v1 import validator, BaseModel

from app.exception.error import ParamsError
from app.schema.base import PityModel


class PityAddressForm(BaseModel):
    id: int = None
    env: int = None
    name: str = ''
    gateway: str = ''
    page_url: str = ''

    @validator("env", 'name')
    def name_not_empty(cls, v):
        return PityModel.not_empty(v)

    @validator('gateway', whole=True)
    def prefix_match(cls, v):
        if not v.startswith(("http://", "https://", "ws://", "wss://")):
            raise ParamsError("前缀不为http或ws")
        return v

    @validator('page_url', whole=True)
    def page_url_match(cls, v):
        value = str(v or '').strip()
        if value and not value.startswith(("/", "http://", "https://")):
            raise ParamsError("页面地址必须以/、http:// 或 https:// 开头")
        return value

