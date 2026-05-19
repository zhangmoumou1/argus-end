from copy import deepcopy

from sqlalchemy import select

from app.crud import ModelWrapper, Mapper
from app.crud.operation.PityOperationDao import PityOperationDao
from app.enums.OperationEnum import OperationType
from app.models import async_session
from app.models.environment import Environment
from app.schema.environment import EnvironmentForm


@ModelWrapper(Environment)
class EnvironmentDao(Mapper):

    @staticmethod
    async def query_env(id: int):
        """
        环境id
        :param id:
        :return:
        """
        async with async_session() as session:
            ans = await session.execute(select(Environment).where(Environment.id == id, Environment.deleted_at == 0))
            if ans is None:
                raise Exception(f"环境: {id}不存在")
            return ans.scalars().first()

    @classmethod
    async def insert_env(cls, data: EnvironmentForm, user):
        try:
            async with async_session() as session:
                async with session.begin():
                    query = await session.execute(
                        select(Environment).where(Environment.name == data.name, Environment.deleted_at == 0))
                    if query.scalars().first() is not None:
                        raise Exception(f"环境已存在")
                    env = Environment(**data.dict(), user=user)
                    session.add(env)
                    await session.flush()
                    await cls.insert_log(session, user, OperationType.INSERT, env, key=env.id)
        except Exception as e:
            EnvironmentDao.__log__.error(f"新增环境: {data.name}失败, {e}")
            raise Exception(f"添加失败: {str(e)}")

    @classmethod
    async def update_env_enabled(cls, data: EnvironmentForm, user: int):
        async with async_session() as session:
            async with session.begin():
                query = await session.execute(
                    select(Environment).where(Environment.id == data.id, Environment.deleted_at == 0)
                )
                env = query.scalars().first()
                if env is None:
                    raise Exception("环境不存在")
                old = deepcopy(env)
                changed = cls.update_model(env, data, user, True)
                await session.flush()
                if changed:
                    await PityOperationDao.insert_log(
                        session,
                        user,
                        OperationType.UPDATE,
                        env,
                        old,
                        env.id,
                        changed=changed,
                    )

    @classmethod
    async def list_env(cls, page, size, name=None, exactly=False):
        try:
            search = [Environment.deleted_at == 0]
            async with async_session() as session:
                if name:
                    search.append(Environment.name.like("%{}%".format(name)))
                sql = select(Environment).where(*search)
                query = await session.execute(sql)
                if exactly:
                    data = query.scalars().all()
                    return data, len(data)
                total = query.raw.rowcount
                if total == 0:
                    return [], 0
                sql = sql.offset((page - 1) * size).limit(size)
                data = await session.execute(sql)
                return data.scalars().all(), total
        except Exception as e:
            cls.__log__.error(f"获取环境列表失败, {str(e)}")
            raise Exception(f"获取环境数据失败: {str(e)}")
