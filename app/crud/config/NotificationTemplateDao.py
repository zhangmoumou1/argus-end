from app.crud import Mapper, ModelWrapper
from app.models.notification_template import ArgusNotificationTemplate


@ModelWrapper(ArgusNotificationTemplate)
class NotificationTemplateDao(Mapper):

    @classmethod
    async def list_templates(cls, channel_type: int = None):
        condition = [ArgusNotificationTemplate.deleted_at == 0]
        if channel_type is not None:
            condition.append(ArgusNotificationTemplate.channel_type == channel_type)
        return await cls.select_list(condition=condition)

    @classmethod
    async def list_by_channel_type(cls, channel_type: int):
        return await cls.select_list(condition=[
            ArgusNotificationTemplate.deleted_at == 0,
            ArgusNotificationTemplate.channel_type == channel_type,
            ArgusNotificationTemplate.enabled == True,
        ])

    @classmethod
    async def get_template(cls, template_id: int):
        return await cls.query_record(id=template_id, deleted_at=0)
