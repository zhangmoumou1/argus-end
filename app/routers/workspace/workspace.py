from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.crud.project.ProjectDao import ProjectDao
from app.models.interface_manage import PityApiService
from app.crud.test_case.TestCaseDao import TestCaseDao
from app.crud.test_case.TestPlan import PityTestPlanDao
from app.handler.fatcory import PityResponse
from app.models import async_session
from app.models.functional_case import PityFunctionalCaseItem
from app.models.project import Project
from app.models.test_case import TestCase
from app.routers import Permission

router = APIRouter(prefix="/workspace")


async def _get_top3_distribution(session, model, join_field, user_id: int):
    query = await session.execute(
        select(
            Project.name.label("label"),
            func.count(model.id).label("count"),
        ).where(
            model.create_user == user_id,
            model.deleted_at == 0,
            join_field > 0,
        ).join(
            Project,
            Project.id == join_field,
        ).group_by(Project.id, Project.name).order_by(func.count(model.id).desc()).limit(3)
    )
    return [
        {"label": str(row.label or "未设置"), "value": int(row.count or 0)}
        for row in query.all()
    ]


async def _get_api_case_distribution(session, user_id: int):
    query = await session.execute(
        select(
            Project.name.label("label"),
            func.count(TestCase.id).label("count"),
        ).where(
            TestCase.create_user == user_id,
            TestCase.deleted_at == 0,
            TestCase.api_service_id > 0,
        ).join(
            PityApiService,
            PityApiService.id == TestCase.api_service_id,
        ).join(
            Project,
            Project.id == PityApiService.project_id,
        ).group_by(Project.id, Project.name).order_by(func.count(TestCase.id).desc()).limit(3)
    )
    return [
        {"label": str(row.label or "未设置"), "value": int(row.count or 0)}
        for row in query.all()
    ]


@router.get("/", description="获取工作台用户统计数据")
async def query_user_statistics(user_info=Depends(Permission())):
    user_id = user_info['id']
    count = await ProjectDao.query_user_project(user_id)
    rank = await TestCaseDao.query_user_case_list()
    now = datetime.now()
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    # 保留旧排名逻辑字段，兼容已有依赖
    old_case_count, user_rank = rank.get(str(user_id), [0, 0])

    async with async_session() as session:
        api_case_count = (await session.execute(
            select(func.count(TestCase.id)).where(
                TestCase.create_user == user_id,
                TestCase.deleted_at == 0,
            )
        )).scalar() or 0

        functional_case_count = (await session.execute(
            select(func.count(PityFunctionalCaseItem.id)).where(
                PityFunctionalCaseItem.create_user == user_id,
                PityFunctionalCaseItem.deleted_at == 0,
            )
        )).scalar() or 0

        weekly_new_api_case = (await session.execute(
            select(func.count(TestCase.id)).where(
                TestCase.create_user == user_id,
                TestCase.deleted_at == 0,
                TestCase.created_at >= week_start,
                TestCase.created_at <= now,
            )
        )).scalar() or 0

        weekly_new_functional_case = (await session.execute(
            select(func.count(PityFunctionalCaseItem.id)).where(
                PityFunctionalCaseItem.create_user == user_id,
                PityFunctionalCaseItem.deleted_at == 0,
                PityFunctionalCaseItem.created_at >= week_start,
                PityFunctionalCaseItem.created_at <= now,
            )
        )).scalar() or 0

        api_case_distribution = await _get_api_case_distribution(session, user_id)
        functional_case_distribution = await _get_top3_distribution(
            session, PityFunctionalCaseItem, PityFunctionalCaseItem.project_id, user_id
        )

        api_daily_rows = (await session.execute(
            select(
                func.date_format(TestCase.created_at, "%Y-%m-%d").label("date"),
                func.count(TestCase.id).label("count"),
            ).where(
                TestCase.create_user == user_id,
                TestCase.deleted_at == 0,
                TestCase.created_at >= month_start,
                TestCase.created_at <= now,
            ).group_by(func.date_format(TestCase.created_at, "%Y-%m-%d"))
        )).all()

        functional_daily_rows = (await session.execute(
            select(
                func.date_format(PityFunctionalCaseItem.created_at, "%Y-%m-%d").label("date"),
                func.count(PityFunctionalCaseItem.id).label("count"),
            ).where(
                PityFunctionalCaseItem.create_user == user_id,
                PityFunctionalCaseItem.deleted_at == 0,
                PityFunctionalCaseItem.created_at >= month_start,
                PityFunctionalCaseItem.created_at <= now,
            ).group_by(func.date_format(PityFunctionalCaseItem.created_at, "%Y-%m-%d"))
        )).all()

    api_daily_map = {str(item.date): int(item.count or 0) for item in api_daily_rows}
    functional_daily_map = {str(item.date): int(item.count or 0) for item in functional_daily_rows}

    month_case = []
    weekly_case = []
    cursor = month_start
    while cursor.date() <= now.date():
        day_key = cursor.strftime("%Y-%m-%d")
        api_value = api_daily_map.get(day_key, 0)
        functional_value = functional_daily_map.get(day_key, 0)

        day_item = {
            "date": day_key,
            "api_case_count": api_value,
            "functional_case_count": functional_value,
            # backward compatibility for old frontend parser keys
            "api_count": api_value,
            "functional_count": functional_value,
            "count": api_value + functional_value,
        }

        month_case.append(day_item)

        if cursor >= week_start:
            weekly_case.append(day_item)

        cursor += timedelta(days=1)

    case_count = int(api_case_count) + int(functional_case_count)
    if not case_count and old_case_count:
        case_count = int(old_case_count)

    return PityResponse.success(dict(
        project_count=count,
        case_count=case_count,
        api_case_count=int(api_case_count),
        functional_case_count=int(functional_case_count),
        weekly_new_api_case=int(weekly_new_api_case),
        weekly_new_functional_case=int(weekly_new_functional_case),
        api_case_distribution=api_case_distribution,
        functional_case_distribution=functional_case_distribution,
        month_case=month_case,
        weekly_case=weekly_case,
        user_rank=user_rank,
        total_user=len(rank),
    ))


@router.get("/testplan", description="获取用户关注的测试计划执行数据")
async def query_follow_testplan(user_info=Depends(Permission())):
    user_id = user_info['id']
    ans = await PityTestPlanDao.query_user_follow_test_plan(user_id)
    return PityResponse.success(ans)
