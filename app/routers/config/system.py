import json
from copy import deepcopy

from fastapi import Depends

from app.enums.OperationEnum import OperationType
from app.core.configuration import SystemConfiguration
from app.handler.fatcory import PityResponse
from app.models import async_session
from app.models.operation_log import PityOperationLog
from app.routers import Permission
from app.routers.config.gconfig import router
from config import Config


@router.get("/system", description="获取系统配置")
def get_system_config(_=Depends(Permission(Config.ADMIN))):
    configuration = SystemConfiguration.get_config()
    return PityResponse.success(configuration)


@router.post("/system/update", description="更新系统配置")
async def update_system_config(data: dict, user_info=Depends(Permission(Config.ADMIN))):
    old = deepcopy(SystemConfiguration.get_config())
    SystemConfiguration.update_config(data)
    async with async_session() as session:
        async with session.begin():
            session.add(PityOperationLog(
                user_info["id"],
                OperationType.UPDATE,
                "配置项=系统配置",
                "系统配置",
                json.dumps([{"name": "系统配置", "old": old, "now": data}], ensure_ascii=False),
                0,
            ))
    return PityResponse.success()
