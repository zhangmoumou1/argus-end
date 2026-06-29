import json
import asyncio
from datetime import datetime

from sqlalchemy import select, update

from app.core.platform_mq import (
    build_dead_queue_name,
    build_task_queue_name,
    publish_platform_dead_message,
    publish_platform_retry_message,
    publish_platform_task,
)
from app.core.platform_audit import PlatformAuditService
from app.enums.platform_task import PlatformResultStatus, PlatformTaskStatus
from app.models import async_session
from app.models.platform_task import ArgusPlatformTask
from app.utils.logger import Log

logger = Log("platform_task")


def encode_payload(payload):
    try:
        return json.dumps(payload or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def decode_payload(payload):
    try:
        value = json.loads(payload or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


class PlatformTaskService:
    @staticmethod
    async def get_task(task_id):
        async with async_session() as session:
            result = await session.execute(
                select(ArgusPlatformTask).where(
                    ArgusPlatformTask.id == int(task_id or 0),
                    ArgusPlatformTask.deleted_at == 0,
                )
            )
            return result.scalars().first()

    @staticmethod
    async def create_task(
        task_type,
        user_id=0,
        biz_id=0,
        biz_type="",
        project_id=0,
        plan_id=0,
        resource_key="default",
        payload=None,
        max_retries=0,
        priority=0,
        publish=True,
    ):
        queue_name = build_task_queue_name(task_type, resource_key)
        async with async_session() as session:
            model = ArgusPlatformTask(
                user=int(user_id or 0),
                task_type=str(task_type or ""),
                biz_id=int(biz_id or 0),
                biz_type=str(biz_type or ""),
                project_id=int(project_id or 0),
                plan_id=int(plan_id or 0),
                resource_key=str(resource_key or "default"),
                payload=encode_payload(payload),
            )
            model.queue_name = queue_name
            model.max_retries = int(max_retries or 0)
            model.priority = int(priority or 0)
            session.add(model)
            await session.commit()
            await session.refresh(model)
        if publish:
            model.published = False
            try:
                publish_platform_task(
                    task_id=model.id,
                    task_type=model.task_type,
                    payload={
                        "biz_id": model.biz_id,
                        "biz_type": model.biz_type,
                        "project_id": int(model.project_id or 0),
                        "plan_id": int(model.plan_id or 0),
                        "task_id": int(model.id or 0),
                    },
                    resource_key=model.resource_key,
                    queue_name=queue_name,
                )
                model.published = True
            except Exception as exc:
                logger.warning(f"publish platform task failed, task_id={model.id}, error={exc}")
        else:
            model.published = False
        try:
            from app.core.platform_worker import platform_task_worker

            loop = asyncio.get_running_loop()
            if model.published:
                loop.create_task(platform_task_worker.ensure_queue_consumer(queue_name))
            loop.create_task(platform_task_worker.kickoff_task_if_stuck(int(model.id or 0), delay_seconds=5))
        except RuntimeError:
            pass
        except Exception as wake_exc:
            logger.warning(f"wake platform task executor failed, queue={queue_name}, task_id={model.id}, error={wake_exc}")
        await PlatformAuditService.record(
            user_id=user_id,
            module="platform_task",
            action="create",
            event_type="task",
            biz_type=biz_type or task_type,
            biz_id=model.biz_id,
            project_id=model.project_id,
            summary=f"创建平台任务: {task_type}",
            detail={
                "task_id": model.id,
                "task_type": model.task_type,
                "queue_name": model.queue_name,
                "resource_key": model.resource_key,
            },
        )
        return model

    @staticmethod
    async def claim_queued_task(task_id, stage="running", stage_text="任务执行中"):
        async with async_session() as session:
            now = datetime.now()
            claim_stmt = (
                update(ArgusPlatformTask)
                .where(
                    ArgusPlatformTask.id == int(task_id or 0),
                    ArgusPlatformTask.deleted_at == 0,
                    ArgusPlatformTask.status == PlatformTaskStatus.QUEUED.value,
                )
                .values(
                    status=PlatformTaskStatus.RUNNING.value,
                    stage=stage,
                    stage_text=stage_text,
                    progress=1,
                    started_at=now,
                    updated_at=now,
                )
            )
            result = await session.execute(claim_stmt)
            if int(getattr(result, "rowcount", 0) or 0) <= 0:
                await session.rollback()
                return None
            await session.commit()
            task_result = await session.execute(
                select(ArgusPlatformTask).where(
                    ArgusPlatformTask.id == int(task_id or 0),
                    ArgusPlatformTask.deleted_at == 0,
                )
            )
            task = task_result.scalars().first()
        await PlatformAuditService.record_task_event(
            "claim",
            task,
            summary="平台任务被Worker领取",
            detail={"task_id": int(task.id or 0), "queue_name": str(task.queue_name or "")},
        )
        return task

    @staticmethod
    async def update_task(task_id, **fields):
        async with async_session() as session:
            result = await session.execute(
                select(ArgusPlatformTask).where(
                    ArgusPlatformTask.id == int(task_id or 0),
                    ArgusPlatformTask.deleted_at == 0,
                )
            )
            task = result.scalars().first()
            if task is None:
                return None
            now = datetime.now()
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            if fields.get("status") == PlatformTaskStatus.RUNNING.value and not task.started_at:
                task.started_at = now
            if fields.get("status") in {
                PlatformTaskStatus.SUCCESS.value,
                PlatformTaskStatus.FAILED.value,
                PlatformTaskStatus.CANCELLED.value,
                PlatformTaskStatus.SKIPPED.value,
                PlatformTaskStatus.PARTIAL_SUCCESS.value,
            }:
                task.finished_at = now
            task.updated_at = now
            await session.commit()
            await session.refresh(task)
            if "status" in fields:
                await PlatformAuditService.record(
                    user_id=int(getattr(task, "update_user", 0) or getattr(task, "user", 0) or 0),
                    module="platform_task",
                    action="status_change",
                    event_type="task",
                    biz_type=task.biz_type or task.task_type,
                    biz_id=int(task.biz_id or 0),
                    project_id=int(task.project_id or 0),
                    summary=f"平台任务状态变更: {fields.get('status')}",
                    detail={
                        "task_id": task.id,
                        "task_type": task.task_type,
                        "status": fields.get("status"),
                        "stage": fields.get("stage"),
                    },
                )
            return task

    @staticmethod
    async def mark_running(task_id, stage="running", stage_text="任务执行中"):
        task = await PlatformTaskService.claim_queued_task(task_id, stage=stage, stage_text=stage_text)
        if task is not None:
            return task
        return await PlatformTaskService.update_task(
            task_id,
            status=PlatformTaskStatus.RUNNING.value,
            stage=stage,
            stage_text=stage_text,
            progress=max(1, int(0)),
        )

    @staticmethod
    async def mark_success(task_id, result_payload=None, result_status=PlatformResultStatus.NONE.value):
        return await PlatformTaskService.update_task(
            task_id,
            status=PlatformTaskStatus.SUCCESS.value,
            result_status=result_status,
            stage="finished",
            stage_text="任务执行完成",
            progress=100,
            result_payload=encode_payload(result_payload),
            error_message="",
        )

    @staticmethod
    async def mark_failed(task_id, error_message, result_payload=None):
        return await PlatformTaskService.update_task(
            task_id,
            status=PlatformTaskStatus.FAILED.value,
            result_status=PlatformResultStatus.TEST_FAILED.value,
            stage="failed",
            stage_text="任务执行失败",
            progress=100,
            result_payload=encode_payload(result_payload),
            error_message=str(error_message or ""),
        )

    @staticmethod
    async def mark_retry(task_id, error_message="", stage_text="任务执行失败，等待重试"):
        async with async_session() as session:
            result = await session.execute(
                select(ArgusPlatformTask).where(
                    ArgusPlatformTask.id == int(task_id or 0),
                    ArgusPlatformTask.deleted_at == 0,
                )
            )
            task = result.scalars().first()
            if task is None:
                return None
            retry_count = int(task.retry_count or 0) + 1
            task.retry_count = retry_count
            task.status = PlatformTaskStatus.QUEUED.value
            task.stage = "retrying"
            task.stage_text = stage_text
            task.progress = min(max(int(task.progress or 0), 1), 99)
            task.error_message = str(error_message or "")
            task.updated_at = datetime.now()
            await session.commit()
            await session.refresh(task)
        await PlatformAuditService.record_task_event(
            "retry",
            task,
            summary="平台任务进入重试队列",
            detail={
                "task_id": int(task.id or 0),
                "retry_count": int(task.retry_count or 0),
                "max_retries": int(task.max_retries or 0),
                "queue_name": str(task.queue_name or ""),
            },
        )
        return task

    @staticmethod
    async def can_retry(task_id):
        task = await PlatformTaskService.get_task(task_id)
        if task is None:
            return False, None
        return int(task.retry_count or 0) < int(task.max_retries or 0), task

    @staticmethod
    async def publish_retry(task_id, error_message="", extra_payload=None):
        task = await PlatformTaskService.mark_retry(task_id, error_message=error_message)
        if task is None:
            return None
        publish_platform_retry_message(
            str(task.queue_name or build_task_queue_name(task.task_type, task.resource_key)),
            {
                "task_id": int(task.id or 0),
                "task_type": str(task.task_type or ""),
                "plan_id": int(task.plan_id or 0),
                "project_id": int(task.project_id or 0),
                "resource_key": str(task.resource_key or ""),
                "retry_count": int(task.retry_count or 0),
                "payload": extra_payload or {},
            },
        )
        return task

    @staticmethod
    async def publish_dead_letter(task_id, error_message="", detail=None):
        task = await PlatformTaskService.get_task(task_id)
        if task is None:
            return None
        queue_name = str(task.queue_name or build_task_queue_name(task.task_type, task.resource_key))
        publish_platform_dead_message(
            queue_name,
            {
                "task_id": int(task.id or 0),
                "task_type": str(task.task_type or ""),
                "plan_id": int(task.plan_id or 0),
                "project_id": int(task.project_id or 0),
                "resource_key": str(task.resource_key or ""),
                "retry_count": int(task.retry_count or 0),
                "max_retries": int(task.max_retries or 0),
                "dead_queue": build_dead_queue_name(queue_name),
                "error_message": str(error_message or ""),
                "detail": detail or {},
            },
        )
        await PlatformAuditService.record_task_event(
            "dead_letter",
            task,
            summary="平台任务进入死信队列",
            detail={
                "task_id": int(task.id or 0),
                "queue_name": queue_name,
                "dead_queue": build_dead_queue_name(queue_name),
                "error_message": str(error_message or ""),
            },
        )
        return task
