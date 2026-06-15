from types import SimpleNamespace

from apscheduler.jobstores.base import JobLookupError
from fastapi import Depends
from sqlalchemy import text, select

from app.core.platform_task import PlatformTaskService
from app.crud.operation.ArgusOperationDao import ArgusOperationDao
from app.crud.test_case.TestPlan import ArgusTestPlanDao
from app.enums.OperationEnum import OperationType
from app.enums.platform_task import PlatformTaskType
from app.handler.fatcory import ArgusResponse
from app.models import async_session
from app.models.test_case import TestCase
from app.schema.test_plan import ArgusTestPlanForm
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


def parse_case_id_list(raw_value):
    result = []
    for item in str(raw_value or "").split(","):
        value = str(item).strip()
        if not value:
            continue
        try:
            result.append(int(value))
        except Exception:
            continue
    return result


async def ensure_plan_enabled_column():
    async with async_session() as session:
        result = await session.execute(text("SHOW COLUMNS FROM argus_test_plan LIKE 'enabled'"))
        row = result.first()
        if row is None:
            await session.execute(text(
                "ALTER TABLE argus_test_plan "
                "ADD COLUMN enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否开启计划调度'"
            ))
            await session.commit()


@router.get("/plan/list")
async def list_test_plan(page: int, size: int, project_id: int = None, name: str = "", priority: str = '',
                         create_user: int = None, follow: bool = None, user_info=Depends(Permission())):
    try:
        await ensure_plan_enabled_column()
        data, total = await ArgusTestPlanDao.list_test_plan(page, size, project_id=project_id, name=name,
                                                           follow=follow, priority=priority, role=user_info['role'],
                                                           create_user=create_user, user_id=user_info['id'])

        ans = Scheduler.list_test_plan(data)
        # 兜底补齐 enabled 字段，避免不同版本 scheduler.list_test_plan 未返回该字段
        enabled_map = {d.id: bool(getattr(d, "enabled", True)) for d, _ in data}
        plan_case_map = {}
        case_ids = []
        for item in ans:
            current_case_ids = parse_case_id_list(item.get("case_list"))
            plan_case_map[int(item.get("id") or 0)] = current_case_ids
            case_ids.extend(current_case_ids)
        pending_case_ids = set()
        if case_ids:
            async with async_session() as session:
                rows = await session.execute(
                    select(TestCase.id).where(
                        TestCase.id.in_(list(set(case_ids))),
                        TestCase.deleted_at == 0,
                        TestCase.api_pending_update == 1,
                    )
                )
                pending_case_ids = {int(case_id) for case_id, in rows.all()}
        for item in ans:
            if "enabled" not in item:
                item["enabled"] = enabled_map.get(item.get("id"), True)
            current_case_ids = plan_case_map.get(int(item.get("id") or 0), [])
            item["pending_review"] = 1 if any(case_id in pending_case_ids for case_id in current_case_ids) else 0
        return ArgusResponse.success_with_size(ans, total=total)
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/plan/insert")
async def insert_test_plan(form: ArgusTestPlanForm, user_info=Depends(Permission(Config.MANAGER))):
    try:
        await ensure_plan_enabled_column()
        form.cron = normalize_plan_cron(form.cron)
        plan = await ArgusTestPlanDao.insert_test_plan(form, user_info['id'])
        # 添加定时任务
        Scheduler.add_test_plan(plan.id, plan.name, plan.cron)
        # 按计划开关控制启停
        if not bool(getattr(plan, "enabled", True)):
            Scheduler.pause_resume_test_plan(plan.id, False)
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(str(e))


@router.post("/plan/update")
async def update_test_plan(form: ArgusTestPlanForm, user_info=Depends(Permission(Config.MANAGER))):
    try:
        await ensure_plan_enabled_column()
        form.cron = normalize_plan_cron(form.cron)
        await ArgusTestPlanDao.update_test_plan(form, user_info['id'], True)
        Scheduler.edit_test_plan(form.id, form.name, form.cron)
        Scheduler.pause_resume_test_plan(form.id, bool(form.enabled))
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(str(e))


@router.get("/plan/delete")
async def delete_test_plan(id: int, user_info=Depends(Permission(Config.MANAGER)), session=Depends(get_session)):
    try:
        await ArgusTestPlanDao.delete_record_by_id(session, user_info['id'], id, log=True)
        Scheduler.remove(id)
    except JobLookupError:
        # 说明没找到job
        pass
    except Exception as e:
        return ArgusResponse.failed(str(e))
    return ArgusResponse.success()


@router.get("/plan/switch")
async def switch_test_plan(id: int, status: bool, user_info=Depends(Permission(Config.MANAGER))):
    try:
        await ensure_plan_enabled_column()
        await ArgusTestPlanDao.update_test_plan_enabled(id, status, user_info['id'], log=True)
        Scheduler.pause_resume_test_plan(id, status)
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(str(e))


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
                await ArgusOperationDao.insert_log(
                    session,
                    user_info["id"],
                    OperationType.EXECUTE,
                    log_model,
                    key=id,
                    changed=["action"],
                )
        platform_task = await PlatformTaskService.create_task(
            task_type=PlatformTaskType.API_TEST_RUN.value,
            user_id=user_info["id"],
            biz_id=id,
            biz_type="test_plan",
            plan_id=id,
            resource_key=f"api_plan_{id}",
            payload={"plan_id": id, "executor": user_info["id"]},
        )
        return ArgusResponse.success({"message": "任务已入队，请耐心等待", "platform_task_id": platform_task.id})
    except Exception as e:
        return ArgusResponse.failed(str(e))


@router.get("/plan/follow", description="关注测试计划")
async def follow_test_plan(id: int, user_info=Depends(Permission(Config.MEMBER))):
    try:
        await ArgusTestPlanDao.follow_test_plan(id, user_info['id'])
        return ArgusResponse.success(msg="关注成功")
    except Exception as e:
        return ArgusResponse.failed(str(e))


@router.get("/plan/unfollow", description="取消关注测试计划")
async def unfollow_test_plan(id: int, user_info=Depends(Permission(Config.MEMBER))):
    try:
        await ArgusTestPlanDao.unfollow_test_plan(id, user_info['id'])
        return ArgusResponse.success(msg="取关成功")
    except Exception as e:
        return ArgusResponse.failed(str(e))
