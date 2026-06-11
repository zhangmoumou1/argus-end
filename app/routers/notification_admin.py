import json
from datetime import datetime

from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.crud.config.NotificationChannelDao import NotificationChannelDao
from app.crud.config.NotificationConfigDao import NotificationConfigDao
from app.crud.config.NotificationGroupDao import NotificationGroupDao
from app.crud.config.NotificationTemplateDao import NotificationTemplateDao
from app.handler.fatcory import PityResponse
from app.models.notification_channel import PityNotificationChannel
from app.models.notification_config import PityNotificationConfig
from app.models.notification_group import PityUserGroup
from app.models.notification_template import PityNotificationTemplate
from app.routers import Permission, get_session
from config import Config

router = APIRouter(prefix="/api/notification")

CHANNEL_TYPE_NAMES = {0: "邮件", 1: "钉钉", 2: "企业微信", 3: "飞书"}


# ==================== 通知渠道 ====================

@router.get("/channel/list")
async def list_channels(channel_type: int = None, user_info=Depends(Permission(Config.ADMIN))):
    data = await NotificationChannelDao.list_channels(channel_type)
    result = []
    for item in data:
        cfg = json.loads(item.config_json) if item.config_json else {}
        masked = {}
        for k, v in cfg.items():
            if k in ("password", "secret"):
                masked[k] = "******" if v else ""
            else:
                masked[k] = v
        result.append({
            "id": item.id,
            "name": item.name,
            "channel_type": item.channel_type,
            "channel_type_name": CHANNEL_TYPE_NAMES.get(item.channel_type, "未知"),
            "config_json": masked,
            "enabled": item.enabled,
            "description": item.description,
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else "",
            "updated_at": item.updated_at.strftime("%Y-%m-%d %H:%M:%S") if item.updated_at else "",
        })
    return PityResponse.success(result)


@router.get("/channel/detail")
async def get_channel_detail(id: int, user_info=Depends(Permission(Config.ADMIN))):
    item = await NotificationChannelDao.get_channel(id)
    if item is None:
        return PityResponse.failed("渠道不存在")
    cfg = json.loads(item.config_json) if item.config_json else {}
    return PityResponse.success({
        "id": item.id,
        "name": item.name,
        "channel_type": item.channel_type,
        "config_json": cfg,
        "enabled": item.enabled,
        "description": item.description,
    })


@router.put("/channel/insert")
async def insert_channel(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    name = body.get("name")
    channel_type = int(body.get("channel_type", 0))
    config_json = json.dumps(body.get("config_json", {}), ensure_ascii=False)
    enabled = bool(body.get("enabled", True))
    description = body.get("description", "")
    model = PityNotificationChannel(name, channel_type, config_json, user_info["id"], enabled, description)
    result = await NotificationChannelDao.insert(model=model)
    return PityResponse.success(result)


@router.post("/channel/update")
async def update_channel(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    channel_id = int(body.get("id", 0))
    channel = await NotificationChannelDao.get_channel(channel_id)
    if channel is None:
        return PityResponse.failed("渠道不存在")
    if "name" in body:
        channel.name = body["name"]
    if "channel_type" in body:
        channel.channel_type = int(body["channel_type"])
    if "config_json" in body:
        channel.config_json = json.dumps(body["config_json"], ensure_ascii=False)
    if "enabled" in body:
        channel.enabled = bool(body["enabled"])
    if "description" in body:
        channel.description = body.get("description", "")
    channel.updated_at = datetime.now()
    channel.update_user = user_info["id"]
    from app.crud import async_session
    async with async_session() as session:
        await session.merge(channel)
        await session.commit()
    return PityResponse.success()


@router.post("/channel/delete")
async def delete_channel(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    channel_id = int(body.get("id", 0))
    from app.crud import async_session
    async with async_session() as session:
        await NotificationChannelDao.delete_by_id(channel_id, session=session)
    return PityResponse.success()


@router.post("/channel/test")
async def test_channel(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    """发送测试消息"""
    body = await request.json()
    channel_id = int(body.get("id", 0))
    channel = await NotificationChannelDao.get_channel(channel_id)
    if channel is None:
        return PityResponse.failed("渠道不存在")
    return await _send_test(channel, user_info)


async def _send_test(channel, user_info):
    """根据渠道类型发送测试消息"""
    import time as time_module
    cfg = json.loads(channel.config_json) if channel.config_json else {}
    test_content = f"这是一条测试消息\n发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    from app.core.msg.mail import Email
    from app.core.msg.dingtalk import DingTalk
    from app.core.msg.wecom import WeCom
    from app.core.msg.feishu import FeiShu

    try:
        if channel.channel_type == 0:
            host = cfg.get("host", "")
            sender = cfg.get("sender", "")
            password = cfg.get("password", "")
            import smtplib
            from email.header import Header
            from email.mime.text import MIMEText
            from email.utils import make_msgid
            message = MIMEText(test_content, 'plain', 'utf-8')
            message['From'] = sender
            message['Subject'] = Header("通知渠道测试", 'utf-8')
            message['Message-ID'] = make_msgid()
            smtp = smtplib.SMTP()
            smtp.connect(host)
            smtp.login(sender, password)
            smtp.sendmail(sender, [sender], message.as_string())
            smtp.quit()
        elif channel.channel_type == 1:
            ding = DingTalk(cfg.get("webhook_url", ""), cfg.get("secret"))
            await ding.send_msg("通知渠道测试", test_content, link="http://localhost:7777")
        elif channel.channel_type == 2:
            wc = WeCom(cfg.get("webhook_url", ""))
            await wc.send_msg("通知渠道测试", test_content)
        elif channel.channel_type == 3:
            fs = FeiShu(cfg.get("webhook_url", ""))
            await fs.send_msg("通知渠道测试", test_content)
        else:
            return PityResponse.failed("不支持的渠道类型")
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(f"发送失败: {str(e)}")


# ==================== 通知模板 ====================

@router.get("/template/list")
async def list_templates(channel_type: int = None, user_info=Depends(Permission(Config.ADMIN))):
    data = await NotificationTemplateDao.list_templates(channel_type)
    result = []
    for item in data:
        result.append({
            "id": item.id,
            "name": item.name,
            "channel_type": item.channel_type,
            "channel_type_name": CHANNEL_TYPE_NAMES.get(item.channel_type, "未知"),
            "enabled": item.enabled,
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else "",
            "updated_at": item.updated_at.strftime("%Y-%m-%d %H:%M:%S") if item.updated_at else "",
        })
    return PityResponse.success(result)


@router.get("/template/detail")
async def get_template_detail(id: int, user_info=Depends(Permission(Config.ADMIN))):
    item = await NotificationTemplateDao.get_template(id)
    if item is None:
        return PityResponse.failed("模板不存在")
    return PityResponse.success({
        "id": item.id,
        "name": item.name,
        "channel_type": item.channel_type,
        "subject_template": item.subject_template or "",
        "content_template": item.content_template,
        "enabled": item.enabled,
    })


@router.put("/template/insert")
async def insert_template(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    name = body.get("name")
    channel_type = int(body.get("channel_type", 0))
    content_template = body.get("content_template", "")
    subject_template = body.get("subject_template", "")
    enabled = bool(body.get("enabled", True))
    model = PityNotificationTemplate(name, channel_type, content_template, user_info["id"], subject_template, enabled)
    result = await NotificationTemplateDao.insert(model=model)
    return PityResponse.success(result)


@router.post("/template/update")
async def update_template(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    tpl_id = int(body.get("id", 0))
    tpl = await NotificationTemplateDao.get_template(tpl_id)
    if tpl is None:
        return PityResponse.failed("模板不存在")
    for field in ("name", "channel_type", "content_template", "subject_template", "enabled"):
        if field in body:
            setattr(tpl, field, body[field])
    tpl.updated_at = datetime.now()
    tpl.update_user = user_info["id"]
    from app.crud import async_session
    async with async_session() as session:
        await session.merge(tpl)
        await session.commit()
    return PityResponse.success()


@router.post("/template/delete")
async def delete_template(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    tpl_id = int(body.get("id", 0))
    from app.crud import async_session
    async with async_session() as session:
        await NotificationTemplateDao.delete_by_id(tpl_id, session=session)
    return PityResponse.success()


# ==================== 用户组 ====================

@router.get("/group/list")
async def list_groups(user_info=Depends(Permission(Config.ADMIN))):
    data = await NotificationGroupDao.list_groups()
    result = []
    for item in data:
        members = await NotificationGroupDao.get_members(item.id)
        result.append({
            "id": item.id,
            "name": item.name,
            "description": item.description or "",
            "member_count": len(members),
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else "",
        })
    return PityResponse.success(result)


@router.put("/group/insert")
async def insert_group(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    name = body.get("name")
    description = body.get("description", "")
    member_ids = body.get("members", [])
    from app.crud import async_session
    async with async_session() as session:
        async with session.begin():
            model = PityUserGroup(name, user_info["id"], description)
            session.add(model)
            await session.flush()
            if member_ids:
                await NotificationGroupDao.add_members(session, model.id, member_ids)
            session.expunge(model)
    return PityResponse.success(model)


@router.post("/group/update")
async def update_group(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    group_id = int(body.get("id", 0))
    from app.crud import async_session
    from sqlalchemy import update
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                update(PityUserGroup)
                .where(PityUserGroup.id == group_id)
                .values(name=body.get("name"), description=body.get("description", ""),
                        updated_at=datetime.now(), update_user=user_info["id"])
            )
            member_ids = body.get("members", [])
            from app.models.notification_group import PityUserGroupMember
            await session.execute(
                update(PityUserGroupMember)
                .where(PityUserGroupMember.group_id == group_id, PityUserGroupMember.deleted_at == 0)
                .values(deleted_at=0)
            )
            for uid in member_ids:
                session.add(PityUserGroupMember(group_id, uid))
    return PityResponse.success()


@router.post("/group/delete")
async def delete_group(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    group_id = int(body.get("id", 0))
    from app.crud import async_session
    async with async_session() as session:
        await NotificationGroupDao.delete_by_id(group_id, session=session)
    return PityResponse.success()


@router.get("/group/detail")
async def get_group_detail(id: int, user_info=Depends(Permission(Config.ADMIN))):
    group = await NotificationGroupDao.get_group(id)
    if group is None:
        return PityResponse.failed("用户组不存在")
    members = await NotificationGroupDao.get_members(id)
    return PityResponse.success({
        "id": group.id,
        "name": group.name,
        "description": group.description or "",
        "members": members,
    })


# ==================== 通知配置 ====================

@router.get("/config/list")
async def list_configs(user_info=Depends(Permission(Config.ADMIN))):
    data = await NotificationConfigDao.list_configs()
    result = []
    for item in data:
        ch_count = len([x for x in (item.channel_ids or "").split(",") if x.strip().isdigit()])
        receiver_count = len([x for x in (item.receiver or "").split(",") if x.strip().isdigit()])
        group_count = len([x for x in (item.group_ids or "").split(",") if x.strip().isdigit()])
        result.append({
            "id": item.id,
            "name": item.name,
            "channel_count": ch_count,
            "receiver_count": receiver_count,
            "group_count": group_count,
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else "",
            "updated_at": item.updated_at.strftime("%Y-%m-%d %H:%M:%S") if item.updated_at else "",
        })
    return PityResponse.success(result)


@router.get("/config/detail")
async def get_config_detail(id: int, user_info=Depends(Permission(Config.ADMIN))):
    detail = await NotificationConfigDao.get_config_detail(id)
    if detail is None:
        return PityResponse.failed("通知配置不存在")
    return PityResponse.success(detail)


@router.put("/config/insert")
async def insert_config(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    name = body.get("name")
    channel_ids = ",".join(str(x) for x in (body.get("channel_ids") or []) if str(x).strip().isdigit())
    template_id = body.get("template_id")
    receiver = ",".join(str(x) for x in (body.get("receiver") or []) if str(x).strip().isdigit())
    group_ids = ",".join(str(x) for x in (body.get("group_ids") or []) if str(x).strip().isdigit())
    model = PityNotificationConfig(name, channel_ids, user_info["id"], template_id, receiver, group_ids)
    result = await NotificationConfigDao.insert(model=model)
    return PityResponse.success(result)


@router.post("/config/update")
async def update_config(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    config_id = int(body.get("id", 0))
    config = await NotificationConfigDao.get_config(config_id)
    if config is None:
        return PityResponse.failed("通知配置不存在")
    for field in ("name",):
        if field in body:
            setattr(config, field, body[field])
    if "channel_ids" in body:
        config.channel_ids = ",".join(str(x) for x in body["channel_ids"] if str(x).strip().isdigit())
    if "template_id" in body:
        config.template_id = body.get("template_id")
    if "receiver" in body:
        config.receiver = ",".join(str(x) for x in body["receiver"] if str(x).strip().isdigit())
    if "group_ids" in body:
        config.group_ids = ",".join(str(x) for x in body["group_ids"] if str(x).strip().isdigit())
    config.updated_at = datetime.now()
    config.update_user = user_info["id"]
    from app.crud import async_session
    async with async_session() as session:
        await session.merge(config)
        await session.commit()
    return PityResponse.success()


@router.post("/config/delete")
async def delete_config(request: Request, user_info=Depends(Permission(Config.ADMIN))):
    body = await request.json()
    config_id = int(body.get("id", 0))
    from app.crud import async_session
    async with async_session() as session:
        await NotificationConfigDao.delete_by_id(config_id, session=session)
    return PityResponse.success()


# ==================== 对外查询接口（供测试计划下拉选择使用） ====================

@router.get("/config/list_all")
async def list_all_configs(user_info=Depends(Permission())):
    """所有用户可用的通知配置列表（仅id和name）"""
    data = await NotificationConfigDao.list_configs()
    result = [{"id": item.id, "name": item.name} for item in data]
    return PityResponse.success(result)


@router.get("/channel/list_enabled")
async def list_enabled_channels(user_info=Depends(Permission())):
    """所有用户可用的启用渠道列表"""
    data = await NotificationChannelDao.list_enabled()
    result = [{"id": item.id, "name": item.name, "channel_type": item.channel_type,
               "channel_type_name": CHANNEL_TYPE_NAMES.get(item.channel_type, "未知")} for item in data]
    return PityResponse.success(result)


@router.get("/template/list_enabled")
async def list_enabled_templates(channel_type: int = None, user_info=Depends(Permission())):
    data = await NotificationTemplateDao.list_templates(channel_type)
    result = [{"id": item.id, "name": item.name, "channel_type": item.channel_type} for item in data if item.enabled]
    return PityResponse.success(result)
