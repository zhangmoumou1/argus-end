from fastapi import Depends
from starlette.background import BackgroundTasks

from app.crud.config.RedisConfigDao import ArgusRedisConfigDao
from app.handler.fatcory import ArgusResponse
from app.middleware.RedisManager import ArgusRedisManager
from app.models import DatabaseHelper
from app.models.redis_config import ArgusRedis
from app.routers import Permission, get_session
from app.routers.config.environment import router
from app.schema.online_redis import OnlineRedisForm
from app.schema.redis_config import RedisConfigForm
from config import Config


@router.get("/redis/list")
async def list_redis_config(name: str = '', addr: str = '', env: int = None,
                            cluster: bool = None, _=Depends(Permission(Config.MEMBER))):
    try:
        data = await ArgusRedisConfigDao.select_list(
            name=ArgusRedisConfigDao.like(name), addr=ArgusRedisConfigDao.like(addr),
            env=env, cluster=cluster
        )
        return ArgusResponse.success(data=data)
    except Exception as err:
        return ArgusResponse.failed(err)


@router.post("/redis/insert")
async def insert_redis_config(form: RedisConfigForm,
                              user_info=Depends(Permission(Config.ADMIN))):
    try:
        query = await ArgusRedisConfigDao.query_record(name=form.name, env=form.env)
        if query is not None:
            raise Exception("数据已存在, 请勿重复添加")
        data = ArgusRedis(**form.dict(), user=user_info['id'])
        result = await ArgusRedisConfigDao.insert(model=data, log=True)
        return ArgusResponse.success(data=result)
    except Exception as err:
        return ArgusResponse.failed(err)


@router.post("/redis/update")
async def update_redis_config(form: RedisConfigForm,
                              background_tasks: BackgroundTasks,
                              user_info=Depends(Permission(Config.ADMIN))):
    try:
        result = await ArgusRedisConfigDao.update_record_by_id(user_info['id'], form, log=True)
        if result.cluster:
            background_tasks.add_task(ArgusRedisManager.refresh_redis_cluster, *(result.id, result.addr))
        else:
            background_tasks.add_task(ArgusRedisManager.refresh_redis_client,
                                      *(result.id, result.addr, result.password, result.db))
        return ArgusResponse.success(data=result)
    except Exception as err:
        return ArgusResponse.failed(err)


@router.get("/redis/delete")
async def delete_redis_config(id: int, background_tasks: BackgroundTasks,
                              user_info=Depends(Permission(Config.ADMIN)), session=Depends(get_session)):
    try:
        ans = await ArgusRedisConfigDao.delete_record_by_id(session, user_info['id'], id, log=True)
        # 更新缓存
        background_tasks.add_task(ArgusRedisManager.delete_client, *(id, ans.cluster))
        return ArgusResponse.success()
    except Exception as err:
        return ArgusResponse.failed(err)


@router.post("/redis/command")
async def test_redis_command(form: OnlineRedisForm):
    try:
        res = await ArgusRedisConfigDao.execute_command(form.command, id=form.id)
        return ArgusResponse.success(res)
    except Exception as err:
        return ArgusResponse.failed(err)
