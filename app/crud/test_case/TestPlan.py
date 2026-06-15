import asyncio
import time
from copy import deepcopy

from sqlalchemy import select, and_, or_, null

from app.crud import Mapper, ModelWrapper
from app.crud.project.ProjectDao import ProjectDao
from app.enums.OperationEnum import OperationType
from app.models import async_session
from app.models.report import ArgusReport
from app.models.test_plan import ArgusTestPlan
from app.models.testplan_follow_user import ArgusTestPlanFollowUserRel
from app.schema.test_plan import ArgusTestPlanForm


@ModelWrapper(ArgusTestPlan)
class ArgusTestPlanDao(Mapper):

    @classmethod
    async def list_test_plan(cls, page: int, size: int, project_id: int = None, name: str = '', priority: str = '',
                             role: str = None, create_user: int = None,
                             user_id: int = None, follow: bool = None):
        try:
            async with async_session() as session:
                conditions = [ArgusTestPlan.deleted_at == 0]
                if project_id:
                    ArgusTestPlanDao.where(project_id, ArgusTestPlan.project_id == project_id, conditions)
                else:
                    # 找出用户能看到的项目
                    projects = await ProjectDao.list_project_id_by_user(session, user_id, role)
                    if projects is None:
                        # 说明用户一个项目都没有，不需要继续查询了
                        return [], 0
                    if len(projects) > 0:
                        cls.where(projects, ArgusTestPlan.project_id.in_(projects), conditions)
                cls.where(name, ArgusTestPlan.name.like(f"%{name}%"), conditions) \
                    .where(priority, ArgusTestPlan.priority == priority, conditions) \
                    .where(create_user, ArgusTestPlan.create_user == create_user, conditions)
                if follow is None:
                    sql = select(ArgusTestPlan, ArgusTestPlanFollowUserRel.id) \
                        .outerjoin(ArgusTestPlanFollowUserRel,
                                   and_(
                                       ArgusTestPlanFollowUserRel.user_id == user_id,
                                       ArgusTestPlanFollowUserRel.deleted_at == 0,
                                       ArgusTestPlanFollowUserRel.plan_id == ArgusTestPlan.id)) \
                        .where(*conditions)
                elif follow:
                    sql = select(ArgusTestPlan, ArgusTestPlanFollowUserRel.id) \
                        .outerjoin(ArgusTestPlanFollowUserRel,
                                   ArgusTestPlanFollowUserRel.plan_id == ArgusTestPlan.id,
                                   ).where(*conditions, ArgusTestPlanFollowUserRel.user_id == user_id,
                                           ArgusTestPlanFollowUserRel.deleted_at == 0)
                else:
                    sql = select(ArgusTestPlan, null().label('null_bar')) \
                        .outerjoin(ArgusTestPlanFollowUserRel,
                                   ArgusTestPlanFollowUserRel.plan_id == ArgusTestPlan.id).where(
                        *conditions, or_(ArgusTestPlanFollowUserRel.id == None,
                                         ArgusTestPlanFollowUserRel.deleted_at != 0))
                result, total = await cls.pagination(page, size, session, sql, False)
                return result, total
        except Exception as e:
            cls.__log__.error(f"获取测试计划失败: {str(e)}")
            raise Exception(f"获取测试计划失败: {str(e)}")

    @staticmethod
    async def insert_test_plan(plan: ArgusTestPlanForm, user: int) -> ArgusTestPlan:
        try:
            async with async_session() as session:
                async with session.begin():
                    query = await session.execute(select(ArgusTestPlan).where(ArgusTestPlan.project_id == plan.project_id,
                                                                             ArgusTestPlan.name == plan.name,
                                                                             ArgusTestPlan.deleted_at == 0))
                    if query.scalars().first() is not None:
                        raise Exception("测试计划已存在")
                    test_plan = ArgusTestPlan(**plan.dict(), user=user)
                    session.add(test_plan)
                    await session.flush()
                    await ArgusTestPlanDao.insert_log(session, user, OperationType.INSERT, test_plan, key=test_plan.id)
                    await session.refresh(test_plan)
                    session.expunge(test_plan)
                    return test_plan
        except Exception as e:
            ArgusTestPlanDao.__log__.error(f"新增测试计划失败: {str(e)}")
            raise Exception(f"添加失败: {str(e)}")

    @classmethod
    async def update_test_plan(cls, plan: ArgusTestPlanForm, user: int, log=False):
        try:
            async with async_session() as session:
                async with session.begin():
                    query = await session.execute(
                        select(ArgusTestPlan).where(ArgusTestPlan.id == plan.id, ArgusTestPlan.deleted_at == 0))
                    data = query.scalars().first()
                    if data is None:
                        raise Exception("测试计划不存在")
                    old = deepcopy(data)
                    plan.env = ",".join(map(str, plan.env))
                    plan.receiver = ",".join(map(str, plan.receiver))
                    plan.case_list = ",".join(map(str, plan.case_list))
                    plan.msg_type = ",".join(map(str, plan.msg_type))
                    # Prevent update_model from clearing notification_config_id with None
                    notify_form_value = plan.notification_config_id
                    plan.notification_config_id = data.notification_config_id
                    changed = cls.update_model(data, plan, user)
                    if notify_form_value is not None:
                        data.notification_config_id = notify_form_value
                        changed.append('notification_config_id')
                    await session.flush()
                    session.expunge(data)
                if log:
                    async with session.begin():
                        await asyncio.create_task(
                            cls.insert_log(session, user, OperationType.UPDATE, data, old, plan.id, changed))
        except Exception as e:
            ArgusTestPlanDao.__log__.exception(f"编辑测试计划失败: {str(e)}")
            ArgusTestPlanDao.__log__.error(f"编辑测试计划失败: {str(e)}")
            raise Exception(f"编辑失败: {str(e)}")

    @staticmethod
    async def update_test_plan_state(id: int, state: int):
        try:
            async with async_session() as session:
                async with session.begin():
                    query = await session.execute(
                        select(ArgusTestPlan).where(ArgusTestPlan.id == id, ArgusTestPlan.deleted_at == 0))
                    data = query.scalars().first()
                    if data is None:
                        raise Exception("测试计划不存在")
                    data.state = state
                    # await session.flush()
                    # session.expunge(data)
                    # return data
        except Exception as e:
            ArgusTestPlanDao.__log__.error(f"编辑测试计划失败: {str(e)}")
            raise Exception(f"编辑失败: {str(e)}")

    @staticmethod
    async def update_test_plan_enabled(id: int, enabled: bool, user_id: int, log: bool = False):
        try:
            async with async_session() as session:
                async with session.begin():
                    query = await session.execute(
                        select(ArgusTestPlan).where(ArgusTestPlan.id == id, ArgusTestPlan.deleted_at == 0))
                    data = query.scalars().first()
                    if data is None:
                        raise Exception("测试计划不存在")
                    old = deepcopy(data)
                    data.enabled = bool(enabled)
                    data.update_user = user_id
                    if log and old.enabled != data.enabled:
                        await session.flush()
                        await ArgusTestPlanDao.insert_log(
                            session,
                            user_id,
                            OperationType.UPDATE,
                            data,
                            old,
                            id,
                            changed=["enabled", "update_user"],
                        )
        except Exception as e:
            ArgusTestPlanDao.__log__.error(f"更新测试计划启用状态失败: {str(e)}")
            raise Exception(f"更新启用状态失败: {str(e)}")

    @staticmethod
    async def query_test_plan(id: int) -> ArgusTestPlan:
        try:
            async with async_session() as session:
                sql = select(ArgusTestPlan).where(ArgusTestPlan.deleted_at == 0, ArgusTestPlan.id == id)
                data = await session.execute(sql)
                return data.scalars().first()
        except Exception as e:
            ArgusTestPlanDao.__log__.error(f"获取测试计划失败: {str(e)}")
            raise Exception(f"获取测试计划失败: {str(e)}")

    # @staticmethod
    # async def delete_test_plan(id: int, user: int):
    #     try:
    #         async with async_session() as session:
    #             async with session.begin():
    #                 query = await session.execute(
    #                     select(ArgusTestPlan).where(ArgusTestPlan.id == id, ArgusTestPlan.deleted_at == 0))
    #                 data = query.scalars().first()
    #                 if data is None:
    #                     raise Exception("测试计划不存在")
    #                 DatabaseHelper.delete_model(data, user)
    #     except Exception as e:
    #         ArgusTestPlanDao.__log__.error(f"删除测试计划失败: {str(e)}")
    #         raise Exception(f"删除失败: {str(e)}")

    @staticmethod
    async def follow_test_plan(plan_id: int, user_id: int):
        """
        关注测试计划
        :param plan_id:
        :param user_id:
        :return:
        """
        async with async_session() as session:
            async with session.begin():
                sql = select(ArgusTestPlanFollowUserRel).where(ArgusTestPlanFollowUserRel.deleted_at == 0,
                                                              ArgusTestPlanFollowUserRel.plan_id == plan_id,
                                                              ArgusTestPlanFollowUserRel.user_id == user_id)
                data = await session.execute(sql)
                ans = data.scalars().first()
                if ans is not None:
                    raise Exception("已关注过此测试计划")
                model = ArgusTestPlanFollowUserRel(plan_id, user_id)
                session.add(model)

    @staticmethod
    async def unfollow_test_plan(plan_id: int, user_id: int):
        """
        取关测试计划
        :param plan_id:
        :param user_id:
        :return:
        """
        async with async_session() as session:
            async with session.begin():
                sql = select(ArgusTestPlanFollowUserRel).where(ArgusTestPlanFollowUserRel.deleted_at == 0,
                                                              ArgusTestPlanFollowUserRel.plan_id == plan_id,
                                                              ArgusTestPlanFollowUserRel.user_id == user_id)
                data = await session.execute(sql)
                ans = data.scalars().first()
                if ans is None:
                    raise Exception("已取关过此测试计划")
                ans.deleted_at = int(time.time() * 1000)

    @staticmethod
    async def query_user_follow_test_plan(user_id: int):
        """
        根据用户id查询出用户关注的测试计划执行数据
        :param user_id:
        :return:
        """
        ans = []
        async with async_session() as session:
            # 找到最近7次通过率
            sql = select(ArgusTestPlan, ArgusTestPlanFollowUserRel.id) \
                .outerjoin(ArgusTestPlanFollowUserRel,
                           ArgusTestPlanFollowUserRel.plan_id == ArgusTestPlan.id,
                           ).where(
                ArgusTestPlanFollowUserRel.user_id == user_id,
                ArgusTestPlanFollowUserRel.deleted_at == 0,
                ArgusTestPlan.deleted_at == 0)
            data = await session.execute(sql)
            for d in data.scalars().all():
                reports = list()
                query = await session.execute(select(ArgusReport).where(ArgusReport.plan_id == d.id).order_by(
                    ArgusReport.start_at.desc()).limit(7))
                for report in query.scalars().all():
                    reports.append(report)
                ans.append({
                    "plan": d,
                    "report": reports,
                })
        return ans
