from fastapi import APIRouter, Query
from sqlalchemy import select

from app.crud.test_case.TestReport import TestReportDao
from app.handler.fatcory import PityResponse
from app.models import async_session
from app.models.report import PityReport
from app.models.test_case import TestCase
from app.models.test_plan import PityTestPlan
from app.models.project import Project
from app.models.user import User
from app.models.environment import Environment

router = APIRouter(prefix="/share")


@router.get("/report")
async def query_shared_report(id: int, status: int = Query(default=None)):
    report, case_list, plan_name = await TestReportDao.query(id)

    # case_name 兜底补全
    case_ids = [int(getattr(item, "case_id", 0) or 0) for item in case_list if int(getattr(item, "case_id", 0) or 0) > 0]
    env_name = None
    executor_name = None
    project_name = None
    if case_ids:
        async with async_session() as session:
            name_rows = await session.execute(
                select(TestCase.id, TestCase.name).where(
                    TestCase.id.in_(case_ids),
                    TestCase.deleted_at == 0,
                )
            )
            case_name_map = {int(case_id): case_name for case_id, case_name in name_rows.all()}
        for item in case_list:
            if not getattr(item, "case_name", None):
                item.case_name = case_name_map.get(int(getattr(item, "case_id", 0) or 0), "")
    if report is not None:
        async with async_session() as session:
            env_row = await session.execute(
                select(Environment.name).where(Environment.id == report.env, Environment.deleted_at == 0)
            )
            env_result = env_row.first()
            env_name = env_result[0] if env_result else None
            if report.executor != 0:
                user_row = await session.execute(
                    select(User.name).where(User.id == report.executor)
                )
                user_result = user_row.first()
                executor_name = user_result[0] if user_result else None
            if report.plan_id:
                plan_row = await session.execute(
                    select(PityTestPlan.project_id).where(PityTestPlan.id == report.plan_id, PityTestPlan.deleted_at == 0)
                )
                plan_result = plan_row.first()
                if plan_result:
                    proj_row = await session.execute(
                        select(Project.name).where(Project.id == plan_result[0], Project.deleted_at == 0)
                    )
                    proj_result = proj_row.first()
                    project_name = proj_result[0] if proj_result else None

    if status is not None:
        case_list = [item for item in case_list if getattr(item, "status", None) == status]
    return PityResponse.success(dict(
        report=report, plan_name=plan_name, case_list=case_list,
        env_name=env_name, executor_name=executor_name, project_name=project_name,
    ))
