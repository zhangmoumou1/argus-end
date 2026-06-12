import asyncio
import concurrent.futures
import json
import traceback
from contextlib import suppress

from sqlalchemy import select, text

from app.core.executor import Executor
from app.core.platform_audit import PlatformAuditService
from app.core.platform_mq import declare_platform_task_topology, rabbit_connection
from app.core.platform_task import PlatformTaskService, decode_payload
from app.enums.platform_task import PlatformResultStatus, PlatformTaskStatus, PlatformTaskType, TASK_TERMINAL_STATUSES
from app.models import async_session
from app.models.platform_task import PityPlatformTask
from app.utils.logger import Log
from config import Config

logger = Log("platform_worker")


class PlatformTaskWorker:
    def __init__(self):
        self._running = False
        self._consumer_tasks = {}
        self._supported_task_types = [
            PlatformTaskType.API_TEST_RUN.value,
            PlatformTaskType.UI_TEST_RUN.value,
            PlatformTaskType.PERFORMANCE_TEST_RUN.value,
            PlatformTaskType.AI_FUNCTIONAL_CASE.value,
        ]

    async def start(self):
        if self._running:
            return
        self._running = True
        logger.success("platform task worker started.        ✔")
        while self._running:
            try:
                await self._ensure_queue_consumers()
            except Exception as exc:
                logger.warning(f"platform worker ensure queues failed: {exc}")
            await asyncio.sleep(5)

    async def stop(self):
        self._running = False
        for task in list(self._consumer_tasks.values()):
            task.cancel()
        for task in list(self._consumer_tasks.values()):
            with suppress(Exception):
                await task
        self._consumer_tasks.clear()

    async def _ensure_queue_consumers(self):
        async with async_session() as session:
            exists = await session.execute(text(
                "SELECT COUNT(1) AS total FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='pity_platform_task'"
            ))
            if int((exists.mappings().first() or {}).get("total") or 0) <= 0:
                logger.info("platform task table not found, skip worker polling until Alembic migration is applied")
                return
        queue_names = [str(f"{Config.RABBITMQ_QUEUE_PREFIX}.{task_type}") for task_type in self._supported_task_types]
        for queue_name in queue_names:
            await self.ensure_queue_consumer(queue_name)

    async def ensure_queue_consumer(self, queue_name):
        normalized_queue = str(queue_name or "").strip()
        if not normalized_queue:
            return
        if normalized_queue in self._consumer_tasks and not self._consumer_tasks[normalized_queue].done():
            return
        self._consumer_tasks[normalized_queue] = asyncio.create_task(self._consume_queue(normalized_queue))
        logger.success(f"platform task consumer attached.        ✔ queue={normalized_queue}")

    async def _consume_queue(self, queue_name):
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                consumed = await loop.run_in_executor(None, self._consume_one_message, queue_name, loop)
                if not consumed:
                    await asyncio.sleep(2)
                    continue
            except asyncio.CancelledError:
                raise
            except concurrent.futures.CancelledError:
                if self._running:
                    logger.info(f"consume platform queue cancelled queue={queue_name}")
                return
            except Exception as exc:
                logger.warning(
                    f"consume platform queue failed queue={queue_name}, "
                    f"error_type={type(exc).__name__}, error={repr(exc)}, "
                    f"trace={''.join(traceback.format_exception_only(type(exc), exc)).strip()}"
                )
            await asyncio.sleep(3)

    def _consume_one_message(self, queue_name, loop):
        with rabbit_connection() as conn:
            channel = conn.channel()
            declare_platform_task_topology(channel, queue_name)
            channel.basic_qos(prefetch_count=int(Config.PLATFORM_TASK_WORKER_PREFETCH or 1))
            method, _, body = channel.basic_get(queue=queue_name, auto_ack=False)
            if method is None:
                return False
            future = asyncio.run_coroutine_threadsafe(self._handle_message(body.decode("utf-8")), loop)
            try:
                ok = bool(future.result())
            except concurrent.futures.CancelledError:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                raise
            except Exception:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                raise
            if ok:
                channel.basic_ack(delivery_tag=method.delivery_tag)
            else:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return True

    async def _handle_message(self, body):
        try:
            message = json.loads(body or "{}")
        except Exception as exc:
            logger.warning(f"invalid platform task message: {exc}")
            return False
        task_id = int(message.get("task_id") or 0)
        task = await PlatformTaskService.get_task(task_id)
        if task is None:
            logger.warning(f"platform task not found: {task_id}")
            return True
        normalized_status = str(task.status or "").strip().lower()
        if normalized_status in TASK_TERMINAL_STATUSES:
            return True
        if normalized_status == PlatformTaskStatus.CANCELLING.value:
            await PlatformTaskService.update_task(
                task_id,
                status=PlatformTaskStatus.CANCELLED.value,
                stage="cancelled",
                stage_text="任务在执行前被取消",
                progress=100,
                error_message="任务在执行前被取消",
            )
            return True
        if normalized_status != PlatformTaskStatus.QUEUED.value:
            return True
        try:
            running_task = await PlatformTaskService.claim_queued_task(task_id)
            if running_task is None:
                return True
            logger.info(
                f"platform task claimed task_id={int(running_task.id or 0)}, "
                f"task_type={str(running_task.task_type or '')}, queue={str(running_task.queue_name or '')}"
            )
            await self._execute_task(running_task)
            return True
        except Exception as exc:
            logger.error(f"platform task failed task_id={task_id}, error={exc}")
            can_retry, latest_task = await PlatformTaskService.can_retry(task_id)
            if can_retry:
                await PlatformTaskService.publish_retry(
                    task_id,
                    error_message=str(exc),
                    extra_payload={"last_error": str(exc)},
                )
                return True
            await PlatformTaskService.mark_failed(task_id, str(exc))
            await PlatformTaskService.publish_dead_letter(
                task_id,
                error_message=str(exc),
                detail={"message": message, "task_type": str(getattr(latest_task, "task_type", "") or "")},
            )
            return True

    async def _execute_task(self, task: PityPlatformTask):
        task_type = str(task.task_type or "")
        payload = decode_payload(task.payload)
        if task_type == PlatformTaskType.API_TEST_RUN.value:
            await Executor.run_test_plan(int(payload.get("plan_id") or task.plan_id or task.biz_id), int(payload.get("executor") or task.user or 0))
            await PlatformTaskService.mark_success(task.id, result_status=PlatformResultStatus.TEST_SUCCESS.value)
            return
        if task_type == PlatformTaskType.UI_TEST_RUN.value:
            run_id = int(payload.get("run_id") or task.biz_id or 0)
            run_status = "queued"
            if run_id <= 0:
                raise RuntimeError("UI测试任务缺少run_id")
            async with async_session() as session:
                run_row = await session.execute(
                    text("SELECT id, status, plan_id, project_id FROM pity_ui_test_run WHERE deleted_at=0 AND id=:id"),
                    {"id": run_id},
                )
                run = run_row.mappings().first()
            if not run:
                raise RuntimeError(f"UI测试执行记录不存在: {run_id}")
            run_status = str(run.get("status") or "queued").strip().lower() or "queued"
            await PlatformTaskService.mark_success(
                task.id,
                result_status=PlatformResultStatus.NONE.value,
                result_payload={
                    "run_id": run_id,
                    "run_status": run_status,
                    "project_id": int(run.get("project_id") or task.project_id or 0),
                    "plan_id": int(run.get("plan_id") or task.plan_id or 0),
                    "dispatch_mode": "runner_claim",
                    "message": "UI测试任务已转交Runner领取执行",
                },
            )
            await PlatformAuditService.record_task_event(
                "dispatch_runner",
                task,
                summary="UI测试任务已转交Runner领取",
                detail={
                    "task_id": int(task.id or 0),
                    "run_id": run_id,
                    "run_status": run_status,
                },
            )
            return
        if task_type == PlatformTaskType.PERFORMANCE_TEST_RUN.value:
            from app.routers.performance import run_plan_task

            await run_plan_task(
                int(payload.get("plan_id") or task.plan_id or 0),
                int(payload.get("executor") or task.user or 0),
                report_id=int(payload.get("report_id") or task.biz_id or 0),
            )
            await PlatformTaskService.mark_success(task.id, result_status=PlatformResultStatus.TEST_SUCCESS.value)
            return
        if task_type == PlatformTaskType.AI_FUNCTIONAL_CASE.value:
            from app.routers.testcase.functional_case_skill import execute_skill_task

            await execute_skill_task(
                int(payload.get("skill_task_id") or task.biz_id or 0),
                task_payload=payload.get("task_payload") or {},
                docs=payload.get("docs") or [],
            )
            await PlatformTaskService.mark_success(task.id, result_status=PlatformResultStatus.TEST_SUCCESS.value)
            return
        await PlatformAuditService.record_task_event(
            "unsupported",
            task,
            summary="平台任务类型未接入Worker执行器",
            detail={"task_type": task_type},
        )
        raise RuntimeError(f"不支持的任务类型: {task_type}")


platform_task_worker = PlatformTaskWorker()
