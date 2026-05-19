import asyncio
from types import SimpleNamespace

from apscheduler.jobstores.base import JobLookupError
from fastapi import Depends
from sqlalchemy import text

from app.core.executor import Executor
from app.crud.operation.PityOperationDao import PityOperationDao
from app.crud.test_case.TestPlan import PityTestPlanDao
from app.enums.OperationEnum import OperationType
from app.handler.fatcory import PityResponse
from app.models import async_session
from app.schema.test_plan import PityTestPlanForm
from app.routers import Permission, get_session
from app.routers.testcase.testcase import router
from app.utils.scheduler import Scheduler
from config import Config


def normalize_plan_cron(cron: str) -> str:
    """
    兼容 Quartz 风格 '?'，转换为 APScheduler 可识别的 '*'
    - 5位: minute hour day month day_of_week
    - 6位: second minute hour day month day_of_week
    - 7位: second minute hour day month day_of_week year
    """
    fields = [x.strip() for x in str(cron or "").split() if x.strip()]
    if not fields:
        return cron
    normalized = ["*" if f == "?" else f for f in fields]
    return " ".join(normalized)


async def ensure_plan_enabled_column():
    async with async_session() as session:
        result = await session.execute(text("SHOW COLUMNS FROM pity_test_plan LIKE 'enabled'"))
        row = result.first()
        if row is None:
            await session.execute(text(
                "ALTER TABLE pity_test_plan "
                "ADD COLUMN enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否开启计划调度'"
            ))
            await session.commit()


@router.get("/plan/list")
async def list_test_plan(page: int, size: int, project_id: int = None, name: str = "", priority: str = '',
                         create_user: int = None, follow: bool = None, user_info=Depends(Permission())):
    try:
        await ensure_plan_enabled_column()
        data, total = await PityTestPlanDao.list_test_plan(page, size, project_id=project_id, name=name,
                                                           follow=follow, priority=priority, role=user_info['role'],
                                                           create_user=create_user, user_id=user_info['id'])

        ans = Scheduler.list_test_plan(data)
        # 兜底补齐 enabled 字段，避免不同版本 scheduler.list_test_plan 未返回该字段
        enabled_map = {d.id: bool(getattr(d, "enabled", True)) for d, _ in data}
        for item in ans:
            if "enabled" not in item:
                item["enabled"] = enabled_map.get(item.get("id"), True)
        return PityResponse.success_with_size(ans, total=total)
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/plan/insert")
async def insert_test_plan(form: PityTestPlanForm, user_info=Depends(Permission(Config.MANAGER))):
    try:
        await ensure_plan_enabled_column()
        form.cron = normalize_plan_cron(form.cron)
        plan = await PityTestPlanDao.insert_test_plan(form, user_info['id'])
        # 添加定时任务
        Scheduler.add_test_plan(plan.id, plan.name, plan.cron)
        # 按计划开关控制启停
        if not bool(getattr(plan, "enabled", True)):
            Scheduler.pause_resume_test_plan(plan.id, False)
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(str(e))


@router.post("/plan/update")
async def update_test_plan(form: PityTestPlanForm, user_info=Depends(Permission(Config.MANAGER))):
    try:
        await ensure_plan_enabled_column()
        form.cron = normalize_plan_cron(form.cron)
        await PityTestPlanDao.update_test_plan(form, user_info['id'], True)
        Scheduler.edit_test_plan(form.id, form.name, form.cron)
        Scheduler.pause_resume_test_plan(form.id, bool(form.enabled))
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(str(e))


@router.get("/plan/delete")
async def delete_test_plan(id: int, user_info=Depends(Permission(Config.MANAGER)), session=Depends(get_session)):
    try:
        await PityTestPlanDao.delete_record_by_id(session, user_info['id'], id, log=True)
        Scheduler.remove(id)
    except JobLookupError:
        # 说明没找到job
        pass
    except Exception as e:
        return PityResponse.failed(str(e))
    return PityResponse.success()


@router.get("/plan/switch")
async def switch_test_plan(id: int, status: bool, user_info=Depends(Permission(Config.MANAGER))):
    try:
        await ensure_plan_enabled_column()
        await PityTestPlanDao.update_test_plan_enabled(id, status, user_info['id'], log=True)
        Scheduler.pause_resume_test_plan(id, status)
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(str(e))


@router.get("/plan/execute")
async def run_test_plan(id: int, user_info=Depends(Permission(Config.MEMBER))):
    try:
        log_model = SimpleNamespace(
            id=id,
            action="执行测试计划",
            __fields__=[SimpleNamespace(name="id"), SimpleNamespace(name="action")],
            __tag__="测试计划",
            __alias__={"id": "计划ID", "action": "执行动作"},
            __show__=1,
        )
        async with async_session() as session:
            async with session.begin():
                await PityOperationDao.insert_log(
                    session,
                    user_info["id"],
                    OperationType.EXECUTE,
                    log_model,
                    key=id,
                    changed=["action"],
                )
        asyncio.create_task(Executor.run_test_plan(id, user_info['id']))
        return PityResponse.success("开始执行，请耐心等待")
    except Exception as e:
        return PityResponse.failed(str(e))


@router.get("/plan/follow", description="关注测试计划")
async def follow_test_plan(id: int, user_info=Depends(Permission(Config.MEMBER))):
    try:
        await PityTestPlanDao.follow_test_plan(id, user_info['id'])
        return PityResponse.success(msg="关注成功")
    except Exception as e:
        return PityResponse.failed(str(e))


@router.get("/plan/unfollow", description="取消关注测试计划")
async def unfollow_test_plan(id: int, user_info=Depends(Permission(Config.MEMBER))):
    try:
        await PityTestPlanDao.unfollow_test_plan(id, user_info['id'])
        return PityResponse.success(msg="取关成功")
    except Exception as e:
        return PityResponse.failed(str(e))
