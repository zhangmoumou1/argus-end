from typing import List

from app.crud import Mapper, ModelWrapper
from app.models.notification_config import PityNotificationConfig


@ModelWrapper(PityNotificationConfig)
class NotificationConfigDao(Mapper):

    @classmethod
    async def list_configs(cls):
        return await cls.select_list(condition=[PityNotificationConfig.deleted_at == 0])

    @classmethod
    async def get_config(cls, config_id: int):
        return await cls.query_record(id=config_id, deleted_at=0)

    @classmethod
    async def get_config_detail(cls, config_id: int):
        """获取通知配置详情，含展开的channel/template/group信息"""
        config = await cls.get_config(config_id)
        if config is None:
            return None
        result = {
            "id": config.id,
            "name": config.name,
        }
        # channels
        from app.crud.config.NotificationChannelDao import NotificationChannelDao
        if config.channel_ids:
            ids = [int(x) for x in config.channel_ids.split(",") if x.strip().isdigit()]
            channels = await NotificationChannelDao.list_by_ids(ids)
            result["channels"] = [
                {"id": c.id, "name": c.name, "channel_type": c.channel_type, "enabled": c.enabled}
                for c in channels
            ]
        else:
            result["channels"] = []
        # template
        if config.template_id:
            from app.crud.config.NotificationTemplateDao import NotificationTemplateDao
            tpl = await NotificationTemplateDao.get_template(config.template_id)
            result["template"] = {"id": tpl.id, "name": tpl.name, "channel_type": tpl.channel_type} if tpl else None
        else:
            result["template"] = None
        # receiver
        result["receiver"] = config.receiver or ""
        # groups
        result["group_ids"] = config.group_ids or ""
        return result

    @classmethod
    async def channel_count(cls, config) -> int:
        if not config.channel_ids:
            return 0
        return len([x for x in config.channel_ids.split(",") if x.strip()])
