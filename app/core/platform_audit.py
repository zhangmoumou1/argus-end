import json

from app.models import async_session
from app.models.platform_task import ArgusPlatformAuditLog
from app.utils.logger import Log

logger = Log("platform_audit")


def _safe_json(value):
    try:
        return json.dumps(value or {}, ensure_ascii=False)
    except Exception:
        return str(value or "")


class PlatformAuditService:
    @staticmethod
    async def record(
        user_id=0,
        event_type="operation",
        module="",
        action="",
        biz_type="",
        biz_id=0,
        project_id=0,
        summary="",
        detail=None,
        request_id="",
        trace_id="",
        ip="",
        user_agent="",
    ):
        try:
            async with async_session() as session:
                model = ArgusPlatformAuditLog(
                    user=int(user_id or 0),
                    event_type=event_type,
                    module=module,
                    action=action,
                    biz_type=biz_type,
                    biz_id=int(biz_id or 0),
                    project_id=int(project_id or 0),
                    summary=summary,
                    detail=_safe_json(detail),
                )
                model.request_id = request_id or ""
                model.trace_id = trace_id or ""
                model.ip = ip or ""
                model.user_agent = user_agent or ""
                session.add(model)
                await session.commit()
                return model
        except Exception as exc:
            logger.warning(f"record platform audit failed: {exc}")
            return None

    @staticmethod
    async def record_task_event(action, task, summary="", detail=None):
        return await PlatformAuditService.record(
            user_id=int(getattr(task, "update_user", 0) or getattr(task, "create_user", 0) or getattr(task, "user", 0) or 0),
            event_type="task",
            module="platform_task",
            action=str(action or ""),
            biz_type=str(getattr(task, "biz_type", "") or getattr(task, "task_type", "") or ""),
            biz_id=int(getattr(task, "biz_id", 0) or 0),
            project_id=int(getattr(task, "project_id", 0) or 0),
            summary=summary or f"平台任务事件: {action}",
            detail=detail or {},
        )

    @staticmethod
    async def record_ai_event(user_id=0, biz_id=0, project_id=0, action="", summary="", detail=None):
        return await PlatformAuditService.record(
            user_id=int(user_id or 0),
            event_type="ai",
            module="functional_case_skill",
            action=str(action or ""),
            biz_type="functional_case_skill_task",
            biz_id=int(biz_id or 0),
            project_id=int(project_id or 0),
            summary=summary or f"AI事件: {action}",
            detail=detail or {},
        )

    @staticmethod
    async def record_storage_event(user_id=0, biz_type="", biz_id=0, project_id=0, action="", summary="", detail=None):
        return await PlatformAuditService.record(
            user_id=int(user_id or 0),
            event_type="storage",
            module="oss",
            action=str(action or ""),
            biz_type=str(biz_type or ""),
            biz_id=int(biz_id or 0),
            project_id=int(project_id or 0),
            summary=summary or f"对象存储事件: {action}",
            detail=detail or {},
        )

    @staticmethod
    async def record_notification_event(user_id=0, biz_type="", biz_id=0, project_id=0, action="", summary="", detail=None):
        return await PlatformAuditService.record(
            user_id=int(user_id or 0),
            event_type="notification",
            module="notification",
            action=str(action or ""),
            biz_type=str(biz_type or ""),
            biz_id=int(biz_id or 0),
            project_id=int(project_id or 0),
            summary=summary or f"通知事件: {action}",
            detail=detail or {},
        )
