from datetime import datetime, timedelta

from fastapi import Depends

from app.crud.statistics.dashboard import DashboardDao
from app.handler.fatcory import ArgusResponse
from app.routers import Permission
from app.routers.workspace.workspace import router


def _get_period_range(period: str = "week"):
    now = datetime.today()
    period_key = str(period or "week").strip().lower()
    if period_key == "month":
        start = now.replace(day=1)
    elif period_key == "quarter":
        quarter_month = ((now.month - 1) // 3) * 3 + 1
        start = now.replace(month=quarter_month, day=1)
    elif period_key == "year":
        start = now.replace(month=1, day=1)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    return start, now, period_key


def _parse_date(value: str, field_name: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except Exception as exc:
        raise ValueError(f"{field_name}格式错误，应为YYYY-MM-DD") from exc


def _get_previous_period_range(start: datetime, end: datetime):
    current_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    current_end = end.replace(hour=0, minute=0, second=0, microsecond=0)
    days = (current_end - current_start).days + 1
    prev_end = current_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return prev_start, prev_end


def _calc_change(current_value, previous_value):
    current_num = float(current_value or 0)
    previous_num = float(previous_value or 0)

    if previous_num == 0:
        if current_num == 0:
            return {"direction": "flat", "percent": 0.0, "previous_value": previous_num}
        return {"direction": "up", "percent": 100.0, "previous_value": previous_num}

    percent = round(abs((current_num - previous_num) / previous_num) * 100, 2)
    if current_num > previous_num:
        direction = "up"
    elif current_num < previous_num:
        direction = "down"
    else:
        direction = "flat"
    return {"direction": direction, "percent": percent, "previous_value": previous_num}


def _build_overview_change(current_overview: dict, previous_overview: dict):
    return {
        "api_case_total": _calc_change(
            current_overview.get("api_case_total"),
            previous_overview.get("api_case_total"),
        ),
        "functional_case_total": _calc_change(
            current_overview.get("functional_case_total"),
            previous_overview.get("functional_case_total"),
        ),
        "ui_case_total": _calc_change(
            current_overview.get("ui_case_total"),
            previous_overview.get("ui_case_total"),
        ),
        "api_coverage_rate": _calc_change(
            current_overview.get("api_coverage_rate"),
            previous_overview.get("api_coverage_rate"),
        ),
        "api_pass_rate": _calc_change(
            current_overview.get("api_pass_rate"),
            previous_overview.get("api_pass_rate"),
        ),
    }


@router.get("/statistics", description="获取看板统计数据", summary="获取看板统计数据")
async def query_statistics(
    period: str = "week",
    start_date: str = None,
    end_date: str = None,
    _=Depends(Permission()),
):
    try:
        if start_date and end_date:
            start = _parse_date(start_date, "start_date")
            end = _parse_date(end_date, "end_date")
            period_key = "custom"
        else:
            start, end, period_key = _get_period_range(period)
        if start > end:
            return ArgusResponse.failed("开始时间不能大于结束时间")
        data = await DashboardDao.get_case_dashboard(start, end)
        previous_start, previous_end = _get_previous_period_range(start, end)
        previous_data = await DashboardDao.get_case_dashboard(previous_start, previous_end)
        data["range"] = {
            "period": period_key,
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
        }
        data["overview_change"] = _build_overview_change(
            data.get("overview", {}),
            previous_data.get("overview", {}),
        )
        return ArgusResponse.success(data)
    except ValueError as exc:
        return ArgusResponse.failed(str(exc))
