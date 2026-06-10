from typing import List

from app.crud import Mapper, ModelWrapper
from app.models.notification_channel import PityNotificationChannel


@ModelWrapper(PityNotificationChannel)
class NotificationChannelDao(Mapper):

    @classmethod
    async def list_channels(cls, channel_type: int = None):
        condition = [PityNotificationChannel.deleted_at == 0]
        if channel_type is not None:
            condition.append(PityNotificationChannel.channel_type == channel_type)
        return await cls.select_list(condition=condition)

    @classmethod
    async def list_by_ids(cls, ids: List[int]):
        if not ids:
            return []
        condition = [
            PityNotificationChannel.deleted_at == 0,
            PityNotificationChannel.id.in_(ids),
        ]
        return await cls.select_list(condition=condition)

    @classmethod
    async def list_enabled(cls, channel_type: int = None):
        condition = [
            PityNotificationChannel.deleted_at == 0,
            PityNotificationChannel.enabled == True,
        ]
        if channel_type is not None:
            condition.append(PityNotificationChannel.channel_type == channel_type)
        return await cls.select_list(condition=condition)

    @classmethod
    async def get_channel(cls, channel_id: int):
        return await cls.query_record(id=channel_id, deleted_at=0)
