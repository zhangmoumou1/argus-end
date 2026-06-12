from enum import Enum


class PlatformTaskType(str, Enum):
    API_TEST_RUN = "api_test_run"
    UI_TEST_RUN = "ui_test_run"
    PERFORMANCE_TEST_RUN = "performance_test_run"
    AI_FUNCTIONAL_CASE = "ai_functional_case"
    INTERFACE_RECORD_ASSOCIATE = "interface_record_associate"
    NOTIFICATION = "notification"


class PlatformTaskStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    PARTIAL_SUCCESS = "partial_success"


class PlatformResultStatus(str, Enum):
    NONE = "none"
    TEST_SUCCESS = "test_success"
    TEST_FAILED = "test_failed"
    PARTIAL_SUCCESS = "partial_success"
    SKIPPED = "skipped"


TASK_TERMINAL_STATUSES = {
    PlatformTaskStatus.SUCCESS.value,
    PlatformTaskStatus.FAILED.value,
    PlatformTaskStatus.CANCELLED.value,
    PlatformTaskStatus.SKIPPED.value,
    PlatformTaskStatus.PARTIAL_SUCCESS.value,
}

TASK_ACTIVE_STATUSES = {
    PlatformTaskStatus.QUEUED.value,
    PlatformTaskStatus.CLAIMED.value,
    PlatformTaskStatus.RUNNING.value,
    PlatformTaskStatus.CANCELLING.value,
}
