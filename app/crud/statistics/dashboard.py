from datetime import datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import Mapper, connect
from app.models.functional_case import PityFunctionalCaseItem
from app.models.report import PityReport
from app.models.test_case import TestCase
from app.models.user import User


class DashboardDao(Mapper):

    @classmethod
    def normalize_range(cls, start: datetime, end: datetime):
        start_time = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start_time, end_time

    @classmethod
    def build_daily_axis(cls, start: datetime, end: datetime):
        axis = []
        index = {}
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_cursor = end.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor <= end_cursor:
            date_str = cursor.strftime("%Y-%m-%d")
            index[date_str] = len(axis)
            axis.append({
                "date": date_str,
                "api_case_count": 0,
                "functional_case_count": 0,
            })
            cursor += timedelta(days=1)
        return axis, index

    @classmethod
    async def _count_by_day(cls, session: AsyncSession, model, start: datetime, end: datetime, label: str):
        query = await session.execute(
            select(func.date(model.created_at).label("created_date"), func.count(model.id).label("total"))
            .where(
                model.deleted_at == 0,
                model.created_at >= start,
                model.created_at <= end,
            )
            .group_by(func.date(model.created_at))
            .order_by(func.date(model.created_at).asc())
        )
        result = {}
        for item in query:
            date_value = item.created_date
            date_key = date_value.strftime("%Y-%m-%d") if hasattr(date_value, "strftime") else str(date_value)
            result[date_key] = int(item.total or 0)
        return label, result

    @classmethod
    async def _count_created_between(cls, session: AsyncSession, model, start: datetime, end: datetime):
        query = await session.execute(
            select(func.count(model.id)).where(
                model.deleted_at == 0,
                model.created_at >= start,
                model.created_at <= end,
            )
        )
        return int(query.scalar() or 0)

    @classmethod
    async def _count_total(cls, session: AsyncSession, model):
        query = await session.execute(
            select(func.count(model.id)).where(model.deleted_at == 0)
        )
        return int(query.scalar() or 0)

    @classmethod
    async def _count_functional_priority_covered(cls, session: AsyncSession):
        query = await session.execute(
            select(func.count(PityFunctionalCaseItem.id)).where(
                PityFunctionalCaseItem.deleted_at == 0,
                PityFunctionalCaseItem.case_priority.in_(["1", "2", 1, 2, "priority_1", "priority_2"]),
            )
        )
        return int(query.scalar() or 0)

    @classmethod
    async def _report_pass_rate(cls, session: AsyncSession, start: datetime, end: datetime):
        total_query = await session.execute(
            select(func.count(PityReport.id)).where(
                PityReport.deleted_at == 0,
                PityReport.status == 3,
                PityReport.start_at >= start,
                PityReport.start_at <= end,
            )
        )
        passed_query = await session.execute(
            select(func.count(PityReport.id)).where(
                PityReport.deleted_at == 0,
                PityReport.status == 3,
                PityReport.error_count == 0,
                PityReport.failed_count == 0,
                PityReport.start_at >= start,
                PityReport.start_at <= end,
            )
        )
        total = int(total_query.scalar() or 0)
        passed = int(passed_query.scalar() or 0)
        return round(passed / total * 100, 2) if total > 0 else 0.0

    @classmethod
    async def _leaderboard(cls, session: AsyncSession, model, start: datetime, end: datetime):
        query = await session.execute(
            select(
                model.create_user.label("user_id"),
                User.name.label("name"),
                User.username.label("username"),
                User.avatar.label("avatar"),
                User.email.label("email"),
                func.count(model.id).label("count"),
            )
            .outerjoin(User, and_(User.id == model.create_user, User.deleted_at == 0))
            .where(
                model.deleted_at == 0,
                model.created_at >= start,
                model.created_at <= end,
            )
            .group_by(model.create_user, User.name, User.username, User.avatar, User.email)
            .order_by(func.count(model.id).desc(), model.create_user.asc())
            .limit(20)
        )
        data = []
        rank = 1
        for item in query:
            data.append({
                "rank": rank,
                "user_id": item.user_id,
                "name": item.name or item.username or f"用户{item.user_id}",
                "avatar": item.avatar,
                "email": item.email,
                "count": int(item.count or 0),
            })
            rank += 1
        return data

    @classmethod
    @connect
    async def get_case_dashboard(cls, start: datetime, end: datetime, session: AsyncSession = None):
        start_time, end_time = cls.normalize_range(start, end)
        trend_axis, trend_index = cls.build_daily_axis(start_time, end_time)

        api_case_total = await cls._count_created_between(session, TestCase, start_time, end_time)
        functional_case_total = await cls._count_created_between(session, PityFunctionalCaseItem, start_time, end_time)
        api_case_total_all = await cls._count_total(session, TestCase)
        functional_priority_total = await cls._count_functional_priority_covered(session)
        api_pass_rate = await cls._report_pass_rate(session, start_time, end_time)

        api_label, api_daily = await cls._count_by_day(session, TestCase, start_time, end_time, "api")
        functional_label, functional_daily = await cls._count_by_day(
            session,
            PityFunctionalCaseItem,
            start_time,
            end_time,
            "functional",
        )

        for date_key, value in api_daily.items():
            if date_key in trend_index:
                trend_axis[trend_index[date_key]]["api_case_count"] = int(value or 0)
        for date_key, value in functional_daily.items():
            if date_key in trend_index:
                trend_axis[trend_index[date_key]]["functional_case_count"] = int(value or 0)

        api_case_ranking = await cls._leaderboard(session, TestCase, start_time, end_time)
        functional_case_ranking = await cls._leaderboard(session, PityFunctionalCaseItem, start_time, end_time)

        coverage_rate = round(api_case_total_all / functional_priority_total * 100, 2) \
            if functional_priority_total > 0 else 0.0

        return {
            "overview": {
                "api_case_total": api_case_total,
                "functional_case_total": functional_case_total,
                "api_coverage_rate": coverage_rate,
                "api_pass_rate": api_pass_rate,
            },
            "trend": trend_axis,
            "ranking": {
                "api_case": api_case_ranking,
                "functional_case": functional_case_ranking,
            },
        }
