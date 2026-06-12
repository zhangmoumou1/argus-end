from sqlalchemy import BIGINT, Column, DateTime, Index, INT, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT

from app.models.basic import PityBase


class PityPlatformTask(PityBase):
    __tablename__ = "pity_platform_task"

    task_type = Column(String(64), nullable=False, default="", comment="任务类型")
    biz_id = Column(BIGINT, nullable=False, default=0, comment="业务ID")
    biz_type = Column(String(64), nullable=False, default="", comment="业务类型")
    project_id = Column(INT, nullable=False, default=0, comment="项目ID")
    plan_id = Column(BIGINT, nullable=False, default=0, comment="计划ID")
    resource_key = Column(String(128), nullable=False, default="", comment="顺序执行资源键")
    status = Column(String(32), nullable=False, default="queued", comment="执行状态")
    result_status = Column(String(32), nullable=False, default="none", comment="结果状态")
    stage = Column(String(64), nullable=False, default="queued", comment="执行阶段")
    stage_text = Column(String(255), nullable=True, comment="阶段说明")
    progress = Column(INT, nullable=False, default=0, comment="进度")
    priority = Column(INT, nullable=False, default=0, comment="优先级")
    retry_count = Column(INT, nullable=False, default=0, comment="重试次数")
    max_retries = Column(INT, nullable=False, default=0, comment="最大重试次数")
    queue_name = Column(String(128), nullable=False, default="", comment="队列名称")
    payload = Column(LONGTEXT, nullable=True, comment="任务载荷")
    result_payload = Column(LONGTEXT, nullable=True, comment="任务结果")
    error_message = Column(LONGTEXT, nullable=True, comment="错误信息")
    started_at = Column(DateTime, nullable=True, comment="开始时间")
    finished_at = Column(DateTime, nullable=True, comment="结束时间")

    __table_args__ = (
        Index("idx_platform_task_queue", "task_type", "status", "resource_key", "priority", "id"),
        Index("idx_platform_task_biz", "biz_type", "biz_id", "deleted_at"),
        Index("idx_platform_task_project", "project_id", "task_type", "deleted_at", "id"),
    )

    __fields__ = [task_type, biz_id, status]
    __tag__ = "平台任务"
    __alias__ = dict(task_type="任务类型", biz_id="业务ID", status="状态")
    __show__ = 1

    def __init__(self, user, task_type, biz_id=0, biz_type="", project_id=0, plan_id=0, resource_key="", payload=""):
        super().__init__(user)
        self.task_type = task_type
        self.biz_id = int(biz_id or 0)
        self.biz_type = biz_type or ""
        self.project_id = int(project_id or 0)
        self.plan_id = int(plan_id or 0)
        self.resource_key = resource_key or ""
        self.status = "queued"
        self.result_status = "none"
        self.stage = "queued"
        self.stage_text = "任务已入队"
        self.progress = 0
        self.priority = 0
        self.retry_count = 0
        self.max_retries = 0
        self.queue_name = ""
        self.payload = payload


class PityPlatformAuditLog(PityBase):
    __tablename__ = "pity_platform_audit_log"

    event_type = Column(String(64), nullable=False, default="", comment="事件类型")
    module = Column(String(64), nullable=False, default="", comment="模块")
    action = Column(String(64), nullable=False, default="", comment="动作")
    biz_type = Column(String(64), nullable=False, default="", comment="业务类型")
    biz_id = Column(BIGINT, nullable=False, default=0, comment="业务ID")
    project_id = Column(INT, nullable=False, default=0, comment="项目ID")
    request_id = Column(String(64), nullable=False, default="", comment="请求ID")
    trace_id = Column(String(64), nullable=False, default="", comment="链路ID")
    summary = Column(String(255), nullable=True, comment="摘要")
    detail = Column(LONGTEXT, nullable=True, comment="详情")
    ip = Column(String(64), nullable=False, default="", comment="IP")
    user_agent = Column(Text, nullable=True, comment="User-Agent")

    __table_args__ = (
        Index("idx_platform_audit_biz", "biz_type", "biz_id", "deleted_at"),
        Index("idx_platform_audit_project", "project_id", "event_type", "deleted_at", "id"),
        Index("idx_platform_audit_user", "create_user", "deleted_at", "id"),
    )

    __fields__ = [event_type, module, action]
    __tag__ = "平台审计日志"
    __alias__ = dict(event_type="事件类型", module="模块", action="动作")
    __show__ = 1

    def __init__(self, user, event_type, module, action, biz_type="", biz_id=0, project_id=0, summary="", detail=""):
        super().__init__(user)
        self.event_type = event_type or ""
        self.module = module or ""
        self.action = action or ""
        self.biz_type = biz_type or ""
        self.biz_id = int(biz_id or 0)
        self.project_id = int(project_id or 0)
        self.summary = summary or ""
        self.detail = detail or ""
