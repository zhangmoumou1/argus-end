from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select, text

from app.core.platform_audit import PlatformAuditService
from app.core.platform_mq import publish_platform_task
from app.core.platform_task import PlatformTaskService, decode_payload
from app.enums.platform_task import PlatformTaskStatus, TASK_TERMINAL_STATUSES
from app.handler.fatcory import PityResponse
from app.models.platform_task import PityPlatformTask
from app.routers import Permission, get_session

router = APIRouter(prefix="/platform/task")


def serialize_platform_task(task: PityPlatformTask, include_payload=False):
    data = PityResponse.model_to_dict(task)
    if include_payload:
        data["payload"] = decode_payload(task.payload)
    else:
        data.pop("payload", None)
    return data


@router.get("/detail")
async def get_platform_task_detail(id: int, include_payload: bool = False, session=Depends(get_session), _=Depends(Permission())):
    result = await session.execute(
        select(PityPlatformTask).where(
            PityPlatformTask.id == int(id or 0),
            PityPlatformTask.deleted_at == 0,
        )
    )
    task = result.scalars().first()
    if task is None:
        return PityResponse.failed("任务不存在")
    return PityResponse.success(serialize_platform_task(task, include_payload=include_payload))


@router.get("/list")
async def list_platform_tasks(
    task_type: str = "",
    status: str = "",
    project_id: int = 0,
    biz_type: str = "",
    biz_id: int = 0,
    page: int = 1,
    size: int = 20,
    session=Depends(get_session),
    _=Depends(Permission()),
):
    page = max(int(page or 1), 1)
    size = min(max(int(size or 20), 1), 200)
    offset = (page - 1) * size
    filters = [PityPlatformTask.deleted_at == 0]
    if task_type:
        filters.append(PityPlatformTask.task_type == task_type)
    if status:
        filters.append(PityPlatformTask.status == status)
    if int(project_id or 0) > 0:
        filters.append(PityPlatformTask.project_id == int(project_id))
    if biz_type:
        filters.append(PityPlatformTask.biz_type == biz_type)
    if int(biz_id or 0) > 0:
        filters.append(PityPlatformTask.biz_id == int(biz_id))
    total_row = await session.execute(
        text(
            "SELECT COUNT(1) AS total FROM pity_platform_task WHERE deleted_at=0 "
            + ("AND task_type=:task_type " if task_type else "")
            + ("AND status=:status " if status else "")
            + ("AND project_id=:project_id " if int(project_id or 0) > 0 else "")
            + ("AND biz_type=:biz_type " if biz_type else "")
            + ("AND biz_id=:biz_id " if int(biz_id or 0) > 0 else "")
        ),
        {
            "task_type": task_type,
            "status": status,
            "project_id": int(project_id or 0),
            "biz_type": biz_type,
            "biz_id": int(biz_id or 0),
        },
    )
    total = int((total_row.mappings().first() or {}).get("total") or 0)
    rows = await session.execute(
        select(PityPlatformTask)
        .where(*filters)
        .order_by(desc(PityPlatformTask.id))
        .limit(size)
        .offset(offset)
    )
    return PityResponse.page(
        [serialize_platform_task(item) for item in rows.scalars().all()],
        total=total,
        page=page,
        size=size,
    )


@router.post("/cancel")
async def cancel_platform_task(id: int, session=Depends(get_session), user_info=Depends(Permission())):
    result = await session.execute(
        select(PityPlatformTask).where(
            PityPlatformTask.id == int(id or 0),
            PityPlatformTask.deleted_at == 0,
        )
    )
    task = result.scalars().first()
    if task is None:
        return PityResponse.failed("任务不存在")
    if str(task.status or "") in TASK_TERMINAL_STATUSES:
        return PityResponse.success(serialize_platform_task(task))
    task.status = PlatformTaskStatus.CANCELLING.value
    task.stage = "cancelling"
    task.stage_text = "任务正在取消"
    task.update_user = int(user_info["id"])
    task.updated_at = datetime.now()
    await session.commit()
    await session.refresh(task)
    await PlatformAuditService.record_task_event(
        "cancel",
        task,
        summary="平台任务被手动取消",
        detail={"task_id": int(task.id or 0), "status": str(task.status or "")},
    )
    return PityResponse.success(serialize_platform_task(task))


@router.post("/retry")
async def retry_platform_task(id: int, session=Depends(get_session), user_info=Depends(Permission())):
    result = await session.execute(
        select(PityPlatformTask).where(
            PityPlatformTask.id == int(id or 0),
            PityPlatformTask.deleted_at == 0,
        )
    )
    task = result.scalars().first()
    if task is None:
        return PityResponse.failed("任务不存在")
    if str(task.status or "") not in TASK_TERMINAL_STATUSES:
        return PityResponse.failed("仅已结束任务允许重试")
    if not str(task.queue_name or "").strip():
        return PityResponse.failed("当前任务缺少队列信息，无法重试")
    task.retry_count = 0
    task.status = PlatformTaskStatus.QUEUED.value
    task.stage = "queued"
    task.stage_text = "任务已重新入队"
    task.progress = 0
    task.error_message = ""
    task.result_payload = ""
    task.started_at = None
    task.finished_at = None
    task.update_user = int(user_info["id"])
    task.updated_at = datetime.now()
    await session.commit()
    await session.refresh(task)
    publish_platform_task(
        task_id=int(task.id or 0),
        task_type=str(task.task_type or ""),
        payload={"biz_id": int(task.biz_id or 0), "biz_type": str(task.biz_type or "")},
        resource_key=str(task.resource_key or "default"),
        queue_name=str(task.queue_name or ""),
    )
    await PlatformAuditService.record_task_event(
        "manual_retry",
        task,
        summary="平台任务被手动重试",
        detail={"task_id": int(task.id or 0), "queue_name": str(task.queue_name or "")},
    )
    return PityResponse.success(serialize_platform_task(task))
