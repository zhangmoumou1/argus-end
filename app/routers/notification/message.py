from typing import List

from fastapi import APIRouter, Depends

from app.crud.notification.BroadcastReadDao import BroadcastReadDao
from app.crud.notification.NotificationDao import ArgusNotificationDao
from app.enums.MessageEnum import MessageStateEnum
from app.handler.fatcory import ArgusResponse
from app.models.broadcast_read_user import ArgusBroadcastReadUser
from app.models.notification import ArgusNotification
from app.schema.notification import NotificationForm
from app.routers import Permission, get_session

router = APIRouter(prefix="/notification")


@router.get("/list", description="获取用户消息列表")
async def list_msg(msg_status: int, msg_type: int, user_info=Depends(Permission())):
    try:
        data = await ArgusNotificationDao.list_messages(msg_type=msg_type, msg_status=msg_status,
                                                       receiver=user_info['id'])
        return ArgusResponse.success(data)
    except Exception as e:
        return ArgusResponse.failed(str(e))


@router.post("/read", description="用户读取消息")
async def read_msg(form: NotificationForm, user_info=Depends(Permission())):
    try:
        if form.personal:
            await ArgusNotificationDao.update_by_map(user_info['id'],
                                                    ArgusNotification.id.in_(form.personal),
                                                    ArgusNotification.receiver == user_info['id'],
                                                    msg_status=MessageStateEnum.read.value,
                                                    log=True)
        if form.broadcast:
            user_id = user_info['id']
            for f in form.broadcast:
                model = ArgusBroadcastReadUser(f, user_id)
                await BroadcastReadDao.insert(model=model)
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(str(e))


@router.post("/delete", description="用户删除消息")
async def read_msg(msg_id: List[int], user_info=Depends(Permission()), session=Depends(get_session)):
    try:
        await ArgusNotificationDao.delete_message(session, msg_id, user_info['id'])
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(str(e))
