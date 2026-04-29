from typing import List

from pydantic.v1 import BaseModel


class NotificationForm(BaseModel):
    personal: List[int] = None
    broadcast: List[int] = None

