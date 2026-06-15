from fastapi import Depends
from sqlalchemy import text

from app.crud.config.AddressDao import ArgusGatewayDao
from app.handler.fatcory import ArgusResponse
from app.models.address import ArgusGateway
from app.routers import Permission, get_session
from app.routers.config.environment import router
from app.schema.address import ArgusAddressForm
from config import Config


async def ensure_gateway_schema():
    if not Config.RUNTIME_SCHEMA_MIGRATION_ENABLED:
        return
    from app.models import async_session

    async with async_session() as session:
        result = await session.execute(text("SHOW COLUMNS FROM argus_gateway LIKE 'page_url'"))
        if result.first() is None:
            await session.execute(text(
                "ALTER TABLE argus_gateway "
                "ADD COLUMN page_url VARCHAR(255) NULL DEFAULT '' COMMENT '页面地址'"
            ))
            await session.commit()


@router.get("/gateway/list", summary="查询网关地址")
async def list_gateway(name: str = '', gateway: str = '', env: int = None, _=Depends(Permission(Config.MEMBER))):
    await ensure_gateway_schema()
    data = await ArgusGatewayDao.select_list(env=env, gateway=f"%{gateway}%", name=f"%{name}%")
    return ArgusResponse.success(data)


@router.post("/gateway/insert", summary="添加网关地址", description="添加网关地址，只有组长可以操作")
async def insert_gateway(form: ArgusAddressForm, user_info=Depends(Permission(Config.MANAGER))):
    await ensure_gateway_schema()
    model = ArgusGateway(**form.dict(), user_id=user_info['id'])
    model = await ArgusGatewayDao.insert(model=model, log=True)
    return ArgusResponse.success(model)


@router.post("/gateway/update", summary="编辑网关地址", description="编辑网关地址，只有组长可以操作")
async def update_gateway(form: ArgusAddressForm, user_info=Depends(Permission(Config.MANAGER))):
    await ensure_gateway_schema()
    model = await ArgusGatewayDao.update_record_by_id(user_info['id'], form, True, log=True)
    return ArgusResponse.success(model)


@router.get("/gateway/delete", summary="删除网关地址", description="根据id删除网关地址，只有组长可以操作")
async def delete_gateway(id: int, user_info=Depends(Permission(Config.MANAGER)), session=Depends(get_session)):
    await ArgusGatewayDao.delete_record_by_id(session, user_info['id'], id, log=True)
    return ArgusResponse.success()
