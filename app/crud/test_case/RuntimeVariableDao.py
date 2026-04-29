from typing import Dict, List, Tuple

from sqlalchemy import select, desc

from app.crud import Mapper, ModelWrapper
from app.models import async_session
from app.models.runtime_variable import PityRuntimeVariable


@ModelWrapper(PityRuntimeVariable)
class PityRuntimeVariableDao(Mapper):

    @classmethod
    async def save_many(cls, records: List[Dict], user_id: int = 0):
        if not records:
            return
        try:
            async with async_session() as session:
                async with session.begin():
                    for item in records:
                        rv = PityRuntimeVariable(user_id=user_id, **item)
                        session.add(rv)
        except Exception as e:
            cls.__log__.error(f"保存运行时变量失败: {e}")
            raise Exception(f"保存运行时变量失败: {e}")


    @classmethod
    async def latest_variable_map(cls, case_id: int = None, limit: int = 1000) -> Dict[str, str]:
        """
        读取运行时变量最新值: 每个变量名只保留最新一条
        可按case_id过滤，满足“默认取当前接口变量”
        """
        data = dict()
        try:
            async with async_session() as session:
                sql = select(PityRuntimeVariable).where(PityRuntimeVariable.deleted_at == 0)
                if case_id is not None:
                    sql = sql.where(PityRuntimeVariable.case_id == case_id)
                query = await session.execute(sql.order_by(desc(PityRuntimeVariable.id)).limit(limit))
                for item in query.scalars().all():
                    if item.variable_name in data:
                        continue
                    data[item.variable_name] = item.variable_value
            return data
        except Exception as e:
            cls.__log__.error(f"读取运行时变量失败: {e}")
            return data

    @classmethod
    async def latest_case_variables(cls, pairs: List[Tuple[int, str]], limit: int = 3000) -> Dict[Tuple[int, str], str]:
        """
        按(case_id, variable_name)批量获取最新变量值
        """
        result = dict()
        if not pairs:
            return result
        case_ids = list({x[0] for x in pairs})
        var_names = list({x[1] for x in pairs})
        try:
            async with async_session() as session:
                query = await session.execute(
                    select(PityRuntimeVariable)
                    .where(
                        PityRuntimeVariable.deleted_at == 0,
                        PityRuntimeVariable.case_id.in_(case_ids),
                        PityRuntimeVariable.variable_name.in_(var_names),
                    )
                    .order_by(desc(PityRuntimeVariable.id))
                    .limit(limit)
                )
                pair_set = set(pairs)
                for item in query.scalars().all():
                    key = (item.case_id, item.variable_name)
                    if key not in pair_set:
                        continue
                    if key in result:
                        continue
                    result[key] = item.variable_value
                return result
        except Exception as e:
            cls.__log__.error(f"按case读取运行时变量失败: {e}")
            return result
