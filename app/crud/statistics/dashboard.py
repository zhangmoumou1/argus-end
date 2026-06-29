from datetime import datetime, timedelta

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import Mapper, connect
from app.models.functional_case import ArgusFunctionalCaseItem
from app.models.report import ArgusReport
from app.models.test_case import TestCase
from app.models.user import User

UI_FUNCTIONAL_ROOT_NAME = "UI自动化用例"
FUNCTIONAL_CASE_TYPE_FUNCTIONAL = "functional"
FUNCTIONAL_CASE_TYPE_UI = "ui"


class DashboardDao(Mapper):

    @classmethod
    async def _ensure_functional_case_type_column(cls, session: AsyncSession):
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

    @classmethod
    def _case_type_filter(cls, model, case_type: str):
        return model.case_type == case_type

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
                "ui_case_count": 0,
            })
            cursor += timedelta(days=1)
        return axis, index

    @classmethod
    async def _count_by_day(cls, session: AsyncSession, model, start: datetime, end: datetime, label: str,
                            case_type: str = None):
        conditions = [
            model.deleted_at == 0,
            model.created_at >= start,
            model.created_at <= end,
        ]
        if case_type:
            conditions.append(cls._case_type_filter(model, case_type))
        query = await session.execute(
            select(func.date(model.created_at).label("created_date"), func.count(model.id).label("total"))
            .where(*conditions)
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
    async def _count_created_between(cls, session: AsyncSession, model, start: datetime, end: datetime,
                                     case_type: str = None):
        conditions = [
            model.deleted_at == 0,
            model.created_at >= start,
            model.created_at <= end,
        ]
        if case_type:
            conditions.append(cls._case_type_filter(model, case_type))
        query = await session.execute(
            select(func.count(model.id)).where(*conditions)
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
            select(func.count(ArgusFunctionalCaseItem.id)).where(
                ArgusFunctionalCaseItem.deleted_at == 0,
                cls._case_type_filter(ArgusFunctionalCaseItem, FUNCTIONAL_CASE_TYPE_FUNCTIONAL),
                ArgusFunctionalCaseItem.case_priority.in_(["1", "2", 1, 2, "priority_1", "priority_2"]),
            )
        )
        return int(query.scalar() or 0)

    @classmethod
    async def _report_pass_rate(cls, session: AsyncSession, start: datetime, end: datetime):
        total_query = await session.execute(
            select(func.count(ArgusReport.id)).where(
                ArgusReport.deleted_at == 0,
                ArgusReport.status == 3,
                ArgusReport.start_at >= start,
                ArgusReport.start_at <= end,
            )
        )
        passed_query = await session.execute(
            select(func.count(ArgusReport.id)).where(
                ArgusReport.deleted_at == 0,
                ArgusReport.status == 3,
                ArgusReport.error_count == 0,
                ArgusReport.failed_count == 0,
                ArgusReport.start_at >= start,
                ArgusReport.start_at <= end,
            )
        )
        total = int(total_query.scalar() or 0)
        passed = int(passed_query.scalar() or 0)
        return round(passed / total * 100, 2) if total > 0 else 0.0

    @classmethod
    async def _leaderboard(cls, session: AsyncSession, model, start: datetime, end: datetime,
                           case_type: str = None):
        join_conditions = [
            User.id == model.create_user,
            model.deleted_at == 0,
            model.created_at >= start,
            model.created_at <= end,
        ]
        if case_type:
            join_conditions.append(cls._case_type_filter(model, case_type))
        query = await session.execute(
            select(
                User.id.label("user_id"),
                User.name.label("name"),
                User.username.label("username"),
                User.avatar.label("avatar"),
                User.email.label("email"),
                func.count(model.id).label("count"),
            )
            .select_from(User)
            .outerjoin(
                model,
                and_(*join_conditions),
            )
            .where(User.deleted_at == 0)
            .group_by(User.id, User.name, User.username, User.avatar, User.email)
            .order_by(func.count(model.id).desc(), User.id.asc())
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
        await cls._ensure_functional_case_type_column(session)
        trend_axis, trend_index = cls.build_daily_axis(start_time, end_time)

        api_case_total = await cls._count_created_between(session, TestCase, start_time, end_time)
        functional_case_total = await cls._count_created_between(
            session,
            ArgusFunctionalCaseItem,
            start_time,
            end_time,
            case_type=FUNCTIONAL_CASE_TYPE_FUNCTIONAL,
        )
        ui_case_total = await cls._count_created_between(
            session,
            ArgusFunctionalCaseItem,
            start_time,
            end_time,
            case_type=FUNCTIONAL_CASE_TYPE_UI,
        )
        api_case_total_all = await cls._count_total(session, TestCase)
        functional_priority_total = await cls._count_functional_priority_covered(session)
        api_pass_rate = await cls._report_pass_rate(session, start_time, end_time)

        api_label, api_daily = await cls._count_by_day(session, TestCase, start_time, end_time, "api")
        functional_label, functional_daily = await cls._count_by_day(
            session,
            ArgusFunctionalCaseItem,
            start_time,
            end_time,
            "functional",
            case_type=FUNCTIONAL_CASE_TYPE_FUNCTIONAL,
        )
        ui_label, ui_daily = await cls._count_by_day(
            session,
            ArgusFunctionalCaseItem,
            start_time,
            end_time,
            "ui",
            case_type=FUNCTIONAL_CASE_TYPE_UI,
        )

        for date_key, value in api_daily.items():
            if date_key in trend_index:
                trend_axis[trend_index[date_key]]["api_case_count"] = int(value or 0)
        for date_key, value in functional_daily.items():
            if date_key in trend_index:
                trend_axis[trend_index[date_key]]["functional_case_count"] = int(value or 0)
        for date_key, value in ui_daily.items():
            if date_key in trend_index:
                trend_axis[trend_index[date_key]]["ui_case_count"] = int(value or 0)

        api_case_ranking = await cls._leaderboard(session, TestCase, start_time, end_time)
        functional_case_ranking = await cls._leaderboard(
            session,
            ArgusFunctionalCaseItem,
            start_time,
            end_time,
            case_type=FUNCTIONAL_CASE_TYPE_FUNCTIONAL,
        )
        ui_case_ranking = await cls._leaderboard(
            session,
            ArgusFunctionalCaseItem,
            start_time,
            end_time,
            case_type=FUNCTIONAL_CASE_TYPE_UI,
        )

        coverage_rate = round(api_case_total_all / functional_priority_total * 100, 2) \
            if functional_priority_total > 0 else 0.0

        return {
            "overview": {
                "api_case_total": api_case_total,
                "functional_case_total": functional_case_total,
                "ui_case_total": ui_case_total,
                "api_coverage_rate": coverage_rate,
                "api_pass_rate": api_pass_rate,
            },
            "trend": trend_axis,
            "ranking": {
                "api_case": api_case_ranking,
                "functional_case": functional_case_ranking,
                "ui_case": ui_case_ranking,
            },
        }
