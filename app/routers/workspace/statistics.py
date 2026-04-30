from datetime import datetime, timedelta

from fastapi import Depends

from app.crud.statistics.dashboard import DashboardDao
from app.handler.fatcory import PityResponse
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
            return PityResponse.failed("开始时间不能大于结束时间")
        data = await DashboardDao.get_case_dashboard(start, end)
        data["range"] = {
            "period": period_key,
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
        }
        return PityResponse.success(data)
    except ValueError as exc:
        return PityResponse.failed(str(exc))
