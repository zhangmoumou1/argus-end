from typing import List

from fastapi import APIRouter, Depends
from pydantic.v1 import BaseModel, Field, StrictInt, StrictStr

from app.core.menu_permission import MenuPermissionService
from app.handler.fatcory import ArgusResponse
from app.routers import Permission
from config import Config

router = APIRouter(prefix="/menu", tags=["菜单管理"])


class MenuPermissionForm(BaseModel):
    role: StrictInt
    paths: List[StrictStr] = Field(..., max_items=200)


def require_admin(user):
    if user.get("role") != Config.ADMIN:
        return ArgusResponse.forbidden()
    return None


@router.get("/manage")
async def menu_management(user=Depends(Permission(Config.ADMIN))):
    denied = require_admin(user)
    if denied:
        return denied
    return ArgusResponse.success(await MenuPermissionService.overview())


@router.put("/permissions")
async def update_menu_permissions(data: MenuPermissionForm, user=Depends(Permission(Config.ADMIN))):
    denied = require_admin(user)
    if denied:
        return denied
    try:
        result = await MenuPermissionService.save(data.role, data.paths, int(user["id"]))
        return ArgusResponse.success(result, msg="菜单权限已保存")
    except ValueError as error:
        return ArgusResponse.failed(str(error), code=101)


@router.get("/me")
async def my_menu_permissions(user=Depends(Permission())):
    return ArgusResponse.success(await MenuPermissionService.get(int(user["role"])))
