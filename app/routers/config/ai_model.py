from fastapi import Depends

from app.crud.config.GConfigDao import GConfigDao
from app.handler.fatcory import PityResponse
from app.routers import Permission
from app.routers.config.environment import router
from config import Config


@router.get("/ai-model/config", summary="获取AI模型配置")
async def get_ai_model_config(_=Depends(Permission(Config.ADMIN))):
    try:
        data = await GConfigDao.get_ai_model_config()
        return PityResponse.success(data)
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/ai-model/config/update", summary="更新AI模型配置")
async def update_ai_model_config(form: dict, user_info=Depends(Permission(Config.ADMIN))):
    try:
        data = await GConfigDao.update_ai_model_config(form, user_info["id"])
        return PityResponse.success(data)
    except Exception as err:
        return PityResponse.failed(err)


@router.get("/ai-model/providers", summary="获取AI模型供应商和默认版本")
async def list_ai_model_providers(_=Depends(Permission(Config.ADMIN))):
    try:
        data = await GConfigDao.get_ai_model_config()
        return PityResponse.success(data)
    except Exception as err:
        return PityResponse.failed(err)
