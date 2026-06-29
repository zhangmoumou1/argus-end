import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text

from app.crud.project.ProjectDao import ProjectDao
from app.models.interface_manage import ArgusApiService
from app.crud.test_case.TestCaseDao import TestCaseDao
from app.crud.test_case.TestPlan import ArgusTestPlanDao
from app.handler.fatcory import ArgusResponse
from app.models import async_session
from app.models.functional_case import ArgusFunctionalCaseItem
from app.models.project import Project
from app.models.test_case import TestCase
from app.routers import Permission

router = APIRouter(prefix="/workspace")
UI_FUNCTIONAL_ROOT_NAME = "UI自动化用例"
FUNCTIONAL_CASE_TYPE_FUNCTIONAL = "functional"
FUNCTIONAL_CASE_TYPE_UI = "ui"


async def _ensure_functional_case_type_column(session):
    column_result = await session.execute(
        text("SHOW COLUMNS FROM argus_functional_case_item LIKE 'case_type'")
    )
    if column_result.first() is None:
        await session.execute(
            text(
                "ALTER TABLE argus_functional_case_item "
                "ADD COLUMN case_type VARCHAR(32) NOT NULL DEFAULT 'functional' COMMENT '用例类型(functional/ui)'"
            )
        )
    await session.execute(
        text(
            "UPDATE argus_functional_case_item "
            "SET case_type=:ui_type "
            "WHERE deleted_at=0 AND case_type<>:ui_type "
            "AND (COALESCE(case_path, '') LIKE :ui_marker OR case_name=:ui_root)"
        ),
        {
            "ui_type": FUNCTIONAL_CASE_TYPE_UI,
            "ui_marker": f"%{UI_FUNCTIONAL_ROOT_NAME}%",
            "ui_root": UI_FUNCTIONAL_ROOT_NAME,
        },
    )
    await session.commit()


def _case_type_filter(model, case_type):
    return model.case_type == case_type


async def _get_top3_distribution(session, model, join_field, user_id: int, case_type: str = None):
    conditions = [
        model.create_user == user_id,
        model.deleted_at == 0,
        join_field > 0,
    ]
    if case_type:
        conditions.append(_case_type_filter(model, case_type))
    query = await session.execute(
        select(
            Project.name.label("label"),
            func.count(model.id).label("count"),
        ).where(*conditions).join(
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
            ArgusApiService,
            ArgusApiService.id == TestCase.api_service_id,
        ).join(
            Project,
            Project.id == ArgusApiService.project_id,
        ).group_by(Project.id, Project.name).order_by(func.count(TestCase.id).desc()).limit(3)
    )
    return [
        {"label": str(row.label or "未设置"), "value": int(row.count or 0)}
        for row in query.all()
    ]


async def _get_ui_case_distribution(session, user_id: int):
    return await _get_top3_distribution(
        session,
        ArgusFunctionalCaseItem,
        ArgusFunctionalCaseItem.project_id,
        user_id,
        case_type=FUNCTIONAL_CASE_TYPE_UI,
    )


def _extract_workspace_ui_run_counts(result_payload, run_status=""):
    if isinstance(result_payload, dict):
        payload = result_payload
    elif isinstance(result_payload, str) and result_payload.strip():
        try:
            payload = json.loads(result_payload)
        except Exception:
            payload = {}
    else:
        payload = {}

    success_count = failed_count = skipped_count = error_count = 0
    total_count = 0
    report_status = ""

    if isinstance(payload, dict):
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
        base = summary or stats or payload
        success_count = int(base.get("success_count") or base.get("success_case_count") or base.get("passed") or 0)
        failed_count = int(base.get("failed_count") or base.get("failed_case_count") or base.get("failed") or 0)
        skipped_count = int(base.get("skipped_count") or base.get("skipped_case_count") or base.get("skipped") or 0)
        error_count = int(base.get("error_count") or base.get("error") or 0)
        total_count = int(base.get("total_count") or base.get("total") or base.get("case_count") or 0)
        report_status = str(base.get("report_status") or payload.get("report_status") or "").strip().lower()

        if not any((success_count, failed_count, skipped_count, error_count)) and isinstance(payload.get("case_results"), list):
            for case_item in payload.get("case_results") or []:
                case_status = str((case_item or {}).get("status") or "").strip().lower()
                if case_status == "success":
                    success_count += 1
                elif case_status == "failed":
                    failed_count += 1
                elif case_status == "skipped":
                    skipped_count += 1
                elif case_status:
                    error_count += 1

    if total_count <= 0:
        total_count = success_count + failed_count + skipped_count + error_count

    normalized_run_status = str(run_status or "").strip().lower()
    if not report_status:
        if normalized_run_status in {"queued", "claimed", "running", "uploading", "cancelled"}:
            report_status = normalized_run_status
        elif failed_count > 0 or error_count > 0:
            report_status = "failed"
        elif success_count > 0 and failed_count == 0 and error_count == 0:
            report_status = "success"
        elif skipped_count > 0 and total_count == skipped_count:
            report_status = "skipped"

    return {
        "success_count": int(success_count or 0),
        "failed_count": int(failed_count or 0),
        "skipped_count": int(skipped_count or 0),
        "error_count": int(error_count or 0),
        "total_count": int(total_count or 0),
        "report_status": report_status,
    }


def _extract_workspace_performance_report_counts(report):
    summary = safe_json = {}
    raw_summary = report.get("summary_json") if hasattr(report, "get") else None
    if isinstance(raw_summary, str) and raw_summary.strip():
        try:
            safe_json = json.loads(raw_summary)
        except Exception:
            safe_json = {}
    elif isinstance(raw_summary, dict):
        safe_json = raw_summary
    if isinstance(safe_json.get("summary"), dict):
        summary = safe_json.get("summary") or {}
    elif isinstance(safe_json, dict):
        summary = safe_json

    request_success_count = int(report.get("success_count") or 0)
    request_failed_count = int(report.get("failed_count") or 0)
    failed_reasons = summary.get("failed_reasons") if isinstance(summary, dict) else []
    if not isinstance(failed_reasons, list):
        failed_reasons = [str(failed_reasons)] if failed_reasons else []
    threshold_failed = any(str(item or "").strip() for item in failed_reasons)
    status = int(report.get("status") or 0)

    if status in {0, 1}:
        success_count = failed_count = error_count = 0
    elif request_failed_count > 0 or threshold_failed:
        success_count = 0
        failed_count = 1
        error_count = 0
    elif request_success_count > 0:
        success_count = 1
        failed_count = 0
        error_count = 0
    else:
        success_count = 0
        failed_count = 0
        error_count = 1 if status not in {0, 1, 3} else 0

    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "error_count": error_count,
        "request_success_count": request_success_count,
        "request_failed_count": request_failed_count,
        "failed_reasons": [str(item) for item in failed_reasons if str(item or "").strip()],
        "status": status,
    }


async def _query_ui_follow_test_plan(session, user_id: int):
    rows = await session.execute(
        text(
            "SELECT p.id, p.project_id, p.name, p.description, p.env_name, p.base_url, p.browser, p.status "
            "FROM argus_ui_test_plan_follow_user_rel f "
            "INNER JOIN argus_ui_test_plan p ON f.plan_id=p.id "
            "WHERE f.deleted_at=0 AND p.deleted_at=0 AND f.user_id=:user_id "
            "ORDER BY p.updated_at DESC, p.id DESC"
        ),
        {"user_id": int(user_id)},
    )
    items = []
    for row in rows.mappings().all():
        plan = dict(row)
        report_rows = await session.execute(
            text(
                "SELECT id, run_name AS plan_name, status, started_at AS start_at, result_payload "
                "FROM argus_ui_test_run "
                "WHERE deleted_at=0 AND trigger_mode<>'trial' AND plan_id=:plan_id "
                "ORDER BY id DESC LIMIT 7"
            ),
            {"plan_id": int(plan["id"] or 0)},
        )
        reports = []
        for report in report_rows.mappings().all():
            counts = _extract_workspace_ui_run_counts(report.get("result_payload"), report.get("status"))
            reports.append({
                "id": int(report["id"] or 0),
                "start_at": report.get("start_at"),
                "success_count": int(counts.get("success_count") or 0),
                "failed_count": int(counts.get("failed_count") or 0),
                "error_count": int(counts.get("error_count") or 0),
                "status": report.get("status"),
            })
        items.append({
            "plan_type": "ui",
            "plan": plan,
            "report": reports,
        })
    return items


async def _query_performance_follow_test_plan(session, user_id: int):
    rows = await session.execute(
        text(
            "SELECT p.id, p.project_id, p.name, p.description, p.env, p.source_type, p.load_mode, p.enabled "
            "FROM argus_performance_plan_follow_user_rel f "
            "INNER JOIN argus_performance_plan p ON f.plan_id=p.id "
            "WHERE f.deleted_at=0 AND p.deleted_at=0 AND f.user_id=:user_id "
            "ORDER BY p.updated_at DESC, p.id DESC"
        ),
        {"user_id": int(user_id)},
    )
    items = []
    for row in rows.mappings().all():
        plan = dict(row)
        report_rows = await session.execute(
            text(
                "SELECT id, created_at AS start_at, status, success_count, failed_count, summary_json "
                "FROM argus_performance_report "
                "WHERE deleted_at=0 AND plan_id=:plan_id "
                "ORDER BY id DESC LIMIT 7"
            ),
            {"plan_id": int(plan["id"] or 0)},
        )
        reports = []
        for report in report_rows.mappings().all():
            counts = _extract_workspace_performance_report_counts(report)
            reports.append({
                "id": int(report["id"] or 0),
                "start_at": report.get("start_at"),
                "success_count": int(counts.get("success_count") or 0),
                "failed_count": int(counts.get("failed_count") or 0),
                "error_count": int(counts.get("error_count") or 0),
                "request_success_count": int(counts.get("request_success_count") or 0),
                "request_failed_count": int(counts.get("request_failed_count") or 0),
                "failed_reasons": counts.get("failed_reasons") or [],
                "status": counts.get("status"),
            })
        items.append({
            "plan_type": "performance",
            "plan": plan,
            "report": reports,
        })
    return items


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
        await _ensure_functional_case_type_column(session)
        api_case_count = (await session.execute(
            select(func.count(TestCase.id)).where(
                TestCase.create_user == user_id,
                TestCase.deleted_at == 0,
            )
        )).scalar() or 0

        functional_case_count = (await session.execute(
            select(func.count(ArgusFunctionalCaseItem.id)).where(
                ArgusFunctionalCaseItem.create_user == user_id,
                ArgusFunctionalCaseItem.deleted_at == 0,
                _case_type_filter(ArgusFunctionalCaseItem, FUNCTIONAL_CASE_TYPE_FUNCTIONAL),
            )
        )).scalar() or 0

        ui_case_count = (await session.execute(
            select(func.count(ArgusFunctionalCaseItem.id)).where(
                ArgusFunctionalCaseItem.create_user == user_id,
                ArgusFunctionalCaseItem.deleted_at == 0,
                _case_type_filter(ArgusFunctionalCaseItem, FUNCTIONAL_CASE_TYPE_UI),
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
            select(func.count(ArgusFunctionalCaseItem.id)).where(
                ArgusFunctionalCaseItem.create_user == user_id,
                ArgusFunctionalCaseItem.deleted_at == 0,
                _case_type_filter(ArgusFunctionalCaseItem, FUNCTIONAL_CASE_TYPE_FUNCTIONAL),
                ArgusFunctionalCaseItem.created_at >= week_start,
                ArgusFunctionalCaseItem.created_at <= now,
            )
        )).scalar() or 0

        weekly_new_ui_case = (await session.execute(
            select(func.count(ArgusFunctionalCaseItem.id)).where(
                ArgusFunctionalCaseItem.create_user == user_id,
                ArgusFunctionalCaseItem.deleted_at == 0,
                _case_type_filter(ArgusFunctionalCaseItem, FUNCTIONAL_CASE_TYPE_UI),
                ArgusFunctionalCaseItem.created_at >= week_start,
                ArgusFunctionalCaseItem.created_at <= now,
            )
        )).scalar() or 0

        api_case_distribution = await _get_api_case_distribution(session, user_id)
        functional_case_distribution = await _get_top3_distribution(
            session,
            ArgusFunctionalCaseItem,
            ArgusFunctionalCaseItem.project_id,
            user_id,
            case_type=FUNCTIONAL_CASE_TYPE_FUNCTIONAL,
        )
        ui_case_distribution = await _get_ui_case_distribution(session, user_id)

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
                func.date_format(ArgusFunctionalCaseItem.created_at, "%Y-%m-%d").label("date"),
                func.count(ArgusFunctionalCaseItem.id).label("count"),
            ).where(
                ArgusFunctionalCaseItem.create_user == user_id,
                ArgusFunctionalCaseItem.deleted_at == 0,
                _case_type_filter(ArgusFunctionalCaseItem, FUNCTIONAL_CASE_TYPE_FUNCTIONAL),
                ArgusFunctionalCaseItem.created_at >= month_start,
                ArgusFunctionalCaseItem.created_at <= now,
            ).group_by(func.date_format(ArgusFunctionalCaseItem.created_at, "%Y-%m-%d"))
        )).all()

        ui_daily_rows = (await session.execute(
            select(
                func.date_format(ArgusFunctionalCaseItem.created_at, "%Y-%m-%d").label("date"),
                func.count(ArgusFunctionalCaseItem.id).label("count"),
            ).where(
                ArgusFunctionalCaseItem.create_user == user_id,
                ArgusFunctionalCaseItem.deleted_at == 0,
                _case_type_filter(ArgusFunctionalCaseItem, FUNCTIONAL_CASE_TYPE_UI),
                ArgusFunctionalCaseItem.created_at >= month_start,
                ArgusFunctionalCaseItem.created_at <= now,
            ).group_by(func.date_format(ArgusFunctionalCaseItem.created_at, "%Y-%m-%d"))
        )).all()

    api_daily_map = {str(item.date): int(item.count or 0) for item in api_daily_rows}
    functional_daily_map = {str(item.date): int(item.count or 0) for item in functional_daily_rows}
    ui_daily_map = {str(item.date): int(item.count or 0) for item in ui_daily_rows}

    month_case = []
    weekly_case = []
    cursor = month_start
    while cursor.date() <= now.date():
        day_key = cursor.strftime("%Y-%m-%d")
        api_value = api_daily_map.get(day_key, 0)
        functional_value = functional_daily_map.get(day_key, 0)
        ui_value = ui_daily_map.get(day_key, 0)

        day_item = {
            "date": day_key,
            "api_case_count": api_value,
            "functional_case_count": functional_value,
            "ui_case_count": ui_value,
            # backward compatibility for old frontend parser keys
            "api_count": api_value,
            "functional_count": functional_value,
            "ui_count": ui_value,
            "count": api_value + functional_value + ui_value,
        }

        month_case.append(day_item)

        if cursor >= week_start:
            weekly_case.append(day_item)

        cursor += timedelta(days=1)

    case_count = int(api_case_count) + int(functional_case_count) + int(ui_case_count)
    if not case_count and old_case_count:
        case_count = int(old_case_count)

    return ArgusResponse.success(dict(
        project_count=count,
        case_count=case_count,
        api_case_count=int(api_case_count),
        functional_case_count=int(functional_case_count),
        ui_case_count=int(ui_case_count),
        weekly_new_api_case=int(weekly_new_api_case),
        weekly_new_functional_case=int(weekly_new_functional_case),
        weekly_new_ui_case=int(weekly_new_ui_case),
        api_case_distribution=api_case_distribution,
        functional_case_distribution=functional_case_distribution,
        ui_case_distribution=ui_case_distribution,
        month_case=month_case,
        weekly_case=weekly_case,
        user_rank=user_rank,
        total_user=len(rank),
    ))


@router.get("/testplan", description="获取用户关注的测试计划执行数据")
async def query_follow_testplan(user_info=Depends(Permission())):
    user_id = user_info['id']
    api_items = await ArgusTestPlanDao.query_user_follow_test_plan(user_id)
    async with async_session() as session:
        ui_items = await _query_ui_follow_test_plan(session, user_id)
        performance_items = await _query_performance_follow_test_plan(session, user_id)
    for item in api_items:
        item["plan_type"] = "api"
    return ArgusResponse.success(api_items + ui_items + performance_items)
