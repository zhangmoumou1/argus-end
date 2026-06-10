from app.crud import Mapper, ModelWrapper
from app.models.notification_template import PityNotificationTemplate


@ModelWrapper(PityNotificationTemplate)
class NotificationTemplateDao(Mapper):

    @classmethod
    async def list_templates(cls, channel_type: int = None):
        condition = [PityNotificationTemplate.deleted_at == 0]
        if channel_type is not None:
            condition.append(PityNotificationTemplate.channel_type == channel_type)
        return await cls.select_list(condition=condition)

    @classmethod
    async def list_by_channel_type(cls, channel_type: int):
        return await cls.select_list(condition=[
            PityNotificationTemplate.deleted_at == 0,
            PityNotificationTemplate.channel_type == channel_type,
            PityNotificationTemplate.enabled == True,
        ])

    @classmethod
    async def get_template(cls, template_id: int):
        return await cls.query_record(id=template_id, deleted_at=0)
