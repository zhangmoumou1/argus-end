import json

from fastapi import Depends

from app.crud.config.GConfigDao import GConfigDao
from app.handler.fatcory import PityResponse
from app.routers import Permission, get_session
from app.routers.config.environment import router
from app.schema.gconfig import GConfigForm
from config import Config


@router.get("/gconfig/list")
async def list_gconfig(page: int = 1, size: int = 8, env=None, key: str = "", var_type=None,
                       project_id=None, case_name="", create_user="", _=Depends(Permission())):
    # 兼容查询参数 env= 空字符串场景：不作为 env 过滤
    if env == "":
        env = None
    elif isinstance(env, str) and env.isdigit():
        env = int(env)
    if var_type == "":
        var_type = None
    elif isinstance(var_type, str) and var_type.isdigit():
        var_type = int(var_type)
    if project_id == "":
        project_id = None
    elif isinstance(project_id, str) and project_id.isdigit():
        project_id = int(project_id)
    if case_name is None:
        case_name = ""
    if create_user is None:
        create_user = ""

    data, total = await GConfigDao.list_gconfig_page(
        page, size, env=env, key=key, var_type=var_type, project_id=project_id, case_name=case_name,
        create_user=create_user
    )
    return PityResponse.success_with_size(data=data, total=total)


@router.post("/gconfig/insert")
async def insert_gconfig(data: GConfigForm, user_info=Depends(Permission(Config.ADMIN))):
    await GConfigDao.insert_gconfig(data, user_info['id'])
    return PityResponse.success()


@router.post("/gconfig/update")
async def update_gconfig(data: GConfigForm, user_info=Depends(Permission(Config.ADMIN))):
    await GConfigDao.update_record_by_id(user_info['id'], data, True, True)
    return PityResponse.success()


@router.get("/gconfig/delete")
async def delete_gconfig(id: int, user_info=Depends(Permission(Config.ADMIN)), session=Depends(get_session)):
    await GConfigDao.delete_record_by_id(session, user_info['id'], id, log=True)
    return PityResponse.success()
