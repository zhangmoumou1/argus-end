from collections import defaultdict
from copy import deepcopy
from typing import List

from sqlalchemy import select, update

from app.crud import Mapper, ModelWrapper
from app.enums.OperationEnum import OperationType
from app.models import async_session
from app.models.constructor import Constructor
from app.models.test_case import TestCase
from app.schema.constructor import ConstructorForm, ConstructorIndex


@ModelWrapper(Constructor)
class ConstructorDao(Mapper):

    @staticmethod
    async def list_constructor(case_id: int) -> List[Constructor]:
        try:
            async with async_session() as session:
                sql = select(Constructor).where(Constructor.case_id == case_id, Constructor.deleted_at == 0) \
                    .order_by(Constructor.index, Constructor.updated_at)
                result = await session.execute(sql)
                return result.scalars().all()
        except Exception as e:
            ConstructorDao.__log__.error(f"获取初始化数据失败, {e}")
            raise Exception(f"获取初始化数据失败, {e}")

    @staticmethod
    async def insert_constructor(data: ConstructorForm, user_id: int) -> None:
        try:
            async with async_session() as session:
                async with session.begin():
                    sql = select(Constructor).where(Constructor.case_id == data.case_id, Constructor.name == data.name,
                                                    Constructor.deleted_at == 0)
                    result = await session.execute(sql)
                    if result.scalars().first() is not None:
                        raise Exception(f"{data.name}已存在")
                    constructor = Constructor(**data.dict(), user_id=user_id)
                    constructor.index = await constructor.get_index(session, data.case_id)
                    session.add(constructor)
                    await session.flush()
                    await ConstructorDao.insert_log(session, user_id, OperationType.INSERT, constructor, key=constructor.id)
        except Exception as e:
            ConstructorDao.__log__.error(f"新增前/后置条件: {data.name}失败, {e}")
            raise Exception(f"新增前/后置条件失败, {e}")

    @staticmethod
    async def update_constructor(data: ConstructorForm, user_id: int) -> None:
        try:
            async with async_session() as session:
                async with session.begin():
                    sql = select(Constructor).where(Constructor.id == data.id)
                    result = await session.execute(sql)
                    query = result.scalars().first()
                    if query is None:
                        raise Exception(f"{data.name}不存在")
                    old = deepcopy(query)
                    changed = ConstructorDao.update_model(query, data, user_id)
                    await session.flush()
                    if changed:
                        await ConstructorDao.insert_log(session, user_id, OperationType.UPDATE, query, old, query.id, changed)
        except Exception as e:
            ConstructorDao.__log__.error(f"编辑前后置条件: {data.name}失败, {e}")
            raise Exception(f"编辑前后置条件失败, {e}")

    @classmethod
    async def delete_constructor(cls, id: int, user_id: int) -> None:
        try:
            async with async_session() as session:
                async with session.begin():
                    sql = select(Constructor).where(Constructor.id == id)
                    result = await session.execute(sql)
                    query = result.scalars().first()
                    if query is None:
                        raise Exception(f"前后置条件{id}不存在")
                    ConstructorDao.delete_model(query, user_id)
                    await session.flush()
                    await cls.insert_log(session, user_id, OperationType.DELETE, query, key=id)
        except Exception as e:
            cls.__log__.error(f"删除前后置条件: {id}失败, {e}")
            raise Exception(f"删除前后置条件失败, {e}")

    @classmethod
    async def update_constructor_index(cls, data: List[ConstructorIndex]) -> None:
        try:
            async with async_session() as session:
                async with session.begin():
                    for item in data:
                        await session.execute(
                            update(Constructor).where(Constructor.id == item.id).values(index=item.index))
        except Exception as e:
            cls.__log__.error(f"更新前后置条件顺序失败, {e}")
            raise Exception("更新前后置条件顺序失败")

    @classmethod
    async def get_constructor_tree(cls, name: str, suffix: bool) -> List[dict]:
        try:
            async with async_session() as session:
                search = [Constructor.public == True, Constructor.suffix == suffix, Constructor.deleted_at == 0]
                if name:
                    search.append(Constructor.name.like("%{}%".format(name)))
                query = await session.execute(select(Constructor).where(*search))
                constructor = query.scalars().all()
                if not constructor:
                    return []
                temp = defaultdict(list)
                for c in constructor:
                    temp[c.case_id].append(c)
                query = await session.execute(select(TestCase).where(TestCase.id.in_(temp.keys())))
                testcases = query.scalars().all()
                testcase_info = {t.id: t for t in testcases}
                result = []
                for k, v in temp.items():
                    result.append({
                        "key": f"caseId_{k}",
                        "disabled": True,
                        "title": testcase_info[k].name,
                        "children": [
                            {"key": f"constructor_{x.id}", "title": x.name, "value": f"constructor_{x.id}"} for x in v
                        ],
                    })
                return result
        except Exception as e:
            cls.__log__.error(f"获取前后置条件树失败, {e}")
            raise Exception("获取前后置条件失败")

    @staticmethod
    async def get_constructor_data(id_: int) -> Constructor:
        async with async_session() as session:
            query = await session.execute(select(Constructor).where(Constructor.id == id_, Constructor.deleted_at == 0))
            data = query.scalars().first()
            if data is None:
                raise Exception("前后置条件不存在")
            return data

    @staticmethod
    async def get_case_and_constructor(constructor_type: int, suffix: bool) -> List[dict]:
        ans = list()
        async with async_session() as session:
            constructors = defaultdict(list)
            query = await session.execute(
                select(Constructor).where(
                    Constructor.suffix == suffix,
                    Constructor.type == constructor_type,
                    Constructor.public == True,
                    Constructor.deleted_at == 0))
            for q in query.scalars().all():
                constructors[q.case_id].append({
                    "title": q.name,
                    "key": f"constructor_{q.id}",
                    "value": f"constructor_{q.id}",
                    "isLeaf": True,
                    "constructor_json": q.constructor_json,
                })
            if len(constructors.keys()) == 0:
                return []
            query = await session.execute(
                select(TestCase).where(TestCase.id.in_(constructors.keys()), TestCase.deleted_at == 0))
            for q in query.scalars().all():
                ans.append({
                    "title": q.name,
                    "key": f"caseId_{q.id}",
                    "disabled": True,
                    "children": constructors[q.id]
                })
        return ans
