import time
from copy import deepcopy
from datetime import datetime
from typing import List

from sqlalchemy import select, update

from app.crud import Mapper, ModelWrapper
from app.enums.OperationEnum import OperationType
from app.middleware.RedisManager import RedisHelper
from app.models import async_session
from app.models.out_parameters import ArgusTestCaseOutParameters
from app.schema.testcase_out_parameters import ArgusTestCaseOutParametersForm


@ModelWrapper(ArgusTestCaseOutParameters)
class ArgusTestCaseOutParametersDao(Mapper):

    @classmethod
    async def should_remove(cls, before, after):
        """
        找出要删除的数据
        :param before:
        :param after:
        :return:
        """
        data = []
        for b in before:
            if b.id not in after:
                data.append(b.id)
        return data

    @classmethod
    @RedisHelper.up_cache("dao")
    async def update_many(cls, case_id: int, data: List[ArgusTestCaseOutParametersForm], user_id: int):
        result = []
        try:
            async with async_session() as session:
                async with session.begin():
                    source = await session.execute(select(ArgusTestCaseOutParameters).where(
                        ArgusTestCaseOutParameters.case_id == case_id,
                        ArgusTestCaseOutParameters.deleted_at == 0,
                    ))
                    before = source.scalars().all()
                    for item in data:
                        # if item.id is None:
                        #     # add
                        #     temp = ArgusTestCaseOutParameters(**item.dict(), case_id=case_id, user_id=user_id)
                        #     session.add(temp)
                        # else:
                        query = await session.execute(select(ArgusTestCaseOutParameters).where(
                            ArgusTestCaseOutParameters.name == item.name, ArgusTestCaseOutParameters.case_id == case_id,
                            ArgusTestCaseOutParameters.deleted_at == 0
                        ))
                        temp = query.scalars().first()
                        if temp is None:
                            # 走新增逻辑
                            temp = ArgusTestCaseOutParameters(**item.dict(), case_id=case_id, user_id=user_id)
                            session.add(temp)
                            await session.flush()
                            await cls.insert_log(session, user_id, OperationType.INSERT, temp, key=temp.id)
                        else:
                            old = deepcopy(temp)
                            temp.name = item.name
                            # temp.case_id = case_id
                            temp.expression = item.expression
                            temp.source = item.source
                            temp.match_index = item.match_index
                            temp.update_user = user_id
                            temp.updated_at = datetime.now()
                            await session.flush()
                            await cls.insert_log(
                                session,
                                user_id,
                                OperationType.UPDATE,
                                temp,
                                old,
                                temp.id,
                                changed=["name", "expression", "source", "match_index"],
                            )
                        session.expunge(temp)
                        result.append(temp)
                    should_remove = await cls.should_remove(before, [x.id for x in result])
                    if should_remove:
                        remove_query = await session.execute(
                            select(ArgusTestCaseOutParameters).where(
                                ArgusTestCaseOutParameters.id.in_(should_remove),
                                ArgusTestCaseOutParameters.deleted_at == 0,
                            )
                        )
                        remove_rows = remove_query.scalars().all()
                        await session.execute(
                            update(ArgusTestCaseOutParameters).where(
                                ArgusTestCaseOutParameters.id.in_(should_remove)).values(
                                deleted_at=int(time.time() * 1000)))
                        await session.flush()
                        for row in remove_rows:
                            row.deleted_at = int(time.time() * 1000)
                            await cls.insert_log(session, user_id, OperationType.DELETE, row, key=row.id)
            return result
        except Exception as e:
            cls.__log__.error(f"批量更新出参数据失败: {e}")
            raise Exception(f"批量更新出参数据失败: {e}")
