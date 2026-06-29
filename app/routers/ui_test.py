import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import bindparam, text

from app.core.platform_task import PlatformTaskService
from app.crud.config.GConfigDao import GConfigDao
from app.enums.platform_task import PlatformTaskType
from app.handler.fatcory import ArgusResponse
from app.middleware.oss import OssClient, get_default_bucket_name, normalize_oss_upload_result
from app.middleware.Jwt import UserToken
from app.models import async_session
from app.routers import Permission, get_session
from app.utils.logger import Log
from app.utils.scheduler import Scheduler
from config import Config

router = APIRouter(prefix="/ui-test")
ui_test_log = Log("UITestRouter")

UI_CASE_NODE_NAME = "UI自动化用例"
UI_CASE_STEP_NODE_NAME = "测试步骤"
UI_CASE_CONFIG_NODE_NAME = "场景配置"
UI_CASE_ASSERT_NODE_NAME = "执行断言"
UI_BUCKET_NAME = get_default_bucket_name() or "argus-end"
UI_OBJECT_PREFIX = "autowebcase"
UI_SCHEMA_READY = False
UI_RUNNER_BOOTSTRAP_FILE = Path(__file__).resolve().parents[2] / "ui_runner" / ".runner-bootstrap.json"
UI_RUN_ACTIVE_STATUSES = {"queued", "claimed", "running", "uploading"}
UI_RUN_TERMINAL_STATUSES = {"success", "failed", "cancelled", "skipped", "partial_success"}
UI_PRIORITY_MARKER_SQL = "source_snapshot LIKE '%priority_%'"
FUNCTIONAL_CASE_TYPE_UI = "ui"
UI_FUNCTIONAL_CASE_ITEM_SCHEMA_READY = False
UI_CASE_PATH_JOIN_SQL = (
    "CONVERT(i.case_path USING utf8mb4) COLLATE utf8mb4_general_ci="
    "CONVERT(r.node_path USING utf8mb4) COLLATE utf8mb4_general_ci"
)
UI_STREAM_POLL_INTERVAL = 1.0
UI_STREAM_KEEPALIVE_INTERVAL = 15.0
UI_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _node_text(node):
    if not isinstance(node, dict):
        return ""
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return str(
        data.get("text")
        or data.get("title")
        or node.get("label")
        or node.get("title")
        or node.get("text")
        or ""
    ).strip()


def _normalize_node_marker(value):
    return re.sub(r"[\s:：]+", "", str(value or "")).strip().lower()


def _is_named_node(value, expected):
    return _normalize_node_marker(value) == _normalize_node_marker(expected)


def _is_ui_case_content_node(value):
    return any((
        _is_named_node(value, UI_CASE_CONFIG_NODE_NAME),
        _is_named_node(value, UI_CASE_STEP_NODE_NAME),
        _is_named_node(value, UI_CASE_ASSERT_NODE_NAME),
    ))


def _node_icons(node):
    if not isinstance(node, dict):
        return []
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    raw_icons = (
        data.get("icon")
        or data.get("icons")
        or data.get("marker")
        or data.get("markers")
        or node.get("icon")
        or node.get("icons")
        or []
    )
    if isinstance(raw_icons, (str, int, float)):
        raw_icons = [raw_icons]
    if not isinstance(raw_icons, list):
        return []

    icons = []
    for item in raw_icons:
        if isinstance(item, dict):
            item = item.get("value") or item.get("name") or item.get("type") or item.get("icon")
        text_value = str(item or "").strip()
        if text_value:
            icons.append(text_value)
    return icons


def _has_priority_marker(node):
    return any(re.fullmatch(r"priority_\d+", icon) for icon in _node_icons(node))


def _looks_like_ui_case_node(node):
    if not _has_priority_marker(node):
        return False
    children = _node_children(node)
    if not children:
        return False
    has_step_or_assert = False
    has_config = False
    for child in children:
        child_text = _node_text(child)
        if _is_named_node(child_text, UI_CASE_STEP_NODE_NAME) or _is_named_node(child_text, UI_CASE_ASSERT_NODE_NAME):
            has_step_or_assert = True
        elif _is_named_node(child_text, UI_CASE_CONFIG_NODE_NAME):
            has_config = True
    if has_step_or_assert:
        return True
    if not has_config:
        return False
    return not any(_looks_like_ui_case_node(child) for child in children if not _is_ui_case_content_node(_node_text(child)))


def _normalize_page_url(value):
    text_value = str(value or "").strip()
    if not text_value:
        return ""
    if text_value.startswith(("http://", "https://")):
        return text_value.rstrip("/")
    normalized = "/" + text_value.lstrip("/")
    return normalized.rstrip("/") or "/"


def _compose_plan_base_url(gateway, page_url=""):
    gateway_value = str(gateway or "").strip().rstrip("/")
    page_value = _normalize_page_url(page_url)
    if not gateway_value:
        return page_value
    if not page_value or page_value == "/":
        return gateway_value
    if page_value.startswith(("http://", "https://")):
        return page_value.rstrip("/")
    return f"{gateway_value}{page_value}"


def _node_children(node):
    if not isinstance(node, dict):
        return []
    children = node.get("children")
    if isinstance(children, list):
        return children
    if isinstance(children, dict):
        attached = children.get("attached")
        if isinstance(attached, list):
            return attached
    return []


def _parse_json_text(value):
    text_value = str(value or "").strip()
    if not text_value:
        return None
    try:
        return json.loads(text_value)
    except Exception:
        return None


def _normalize_runner_server(request: Request):
    internal_server = str(getattr(Config, "UI_RUNNER_INTERNAL_SERVER", "") or "").strip().rstrip("/")
    if internal_server:
        return internal_server if internal_server.endswith("/argus") else f"{internal_server}/argus"
    origin = str(getattr(request, "base_url", "") or "").strip().rstrip("/")
    if not origin:
        origin = "http://127.0.0.1:7777"
    return origin if origin.endswith("/argus") else f"{origin}/argus"


def _build_runner_bootstrap_payload(request: Request, user_info: dict, project_id: int, run_ids, plan_id: int = 0,
                                    ai_model: dict = None):
    expire_ts, token = UserToken.get_token({
        "id": int(user_info["id"]),
        "role": int(user_info.get("role") or 0),
    })
    primary_run_id = int(run_ids[0]) if run_ids else 0
    return {
        "server": _normalize_runner_server(request),
        "project_id": int(project_id or 0),
        "plan_id": int(plan_id or 0),
        "run_id": primary_run_id,
        "run_ids": [int(item) for item in (run_ids or []) if int(item or 0) > 0],
        "any_project": True,
        "token": token,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": datetime.fromtimestamp(float(expire_ts)).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "argus-ui-test",
        "ai_model": ai_model or {},
    }


def _write_runner_bootstrap_file(payload: dict):
    UI_RUNNER_BOOTSTRAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    UI_RUNNER_BOOTSTRAP_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _ensure_table_index(session, table_name: str, index_name: str, columns: str):
    try:
        await session.execute(text(f"ALTER TABLE {table_name} ADD INDEX {index_name} ({columns})"))
    except Exception:
        pass


async def _ensure_table_column(session, table_name: str, column_name: str, alter_sql: str):
    try:
        result = await session.execute(text(f"SHOW COLUMNS FROM {table_name} LIKE :column_name"), {"column_name": column_name})
        if result.first() is None:
            await session.execute(text(alter_sql))
    except Exception:
        pass


def _clamp_pagination(page=1, size=20, max_size=200):
    page_value = max(int(page or 1), 1)
    size_value = min(max(int(size or 20), 1), max_size)
    return page_value, size_value, (page_value - 1) * size_value


def _paged_payload(items, total: int, page: int, size: int):
    return {
        "list": items,
        "total": int(total or 0),
        "page": int(page or 1),
        "size": int(size or 20),
    }


def _resolve_tree_roots(root):
    if isinstance(root, list):
        return [item for item in root if isinstance(item, dict)]
    if not isinstance(root, dict):
        return []
    if isinstance(root.get("root"), dict):
        return [root["root"]]
    return [root]


def _find_ui_nodes(root):
    results = []

    def append_result(node, path_parts, shared_config=None):
        current_text = _node_text(node)
        if not current_text:
            return
        results.append({
            "node": node,
            "path": " / ".join(path_parts + [current_text]),
            "title": current_text,
            "shared_config": dict(shared_config or {}),
        })

    def collect_cases_under_ui_root(node, path_parts, inherited_config=None):
        # 兼容两种结构：
        # 1. UI自动化用例 -> 场景配置/测试步骤/执行断言
        # 2. UI自动化用例 -> 功能节点 -> 场景配置/测试步骤/执行断言
        current_text = _node_text(node)
        next_path = path_parts + [current_text] if current_text else list(path_parts)
        merged_config = dict(inherited_config or {})
        for child in _node_children(node):
            if _is_named_node(_node_text(child), UI_CASE_CONFIG_NODE_NAME):
                merged_config.update(_parse_key_value_nodes(child))
        if _looks_like_ui_case_node(node):
            append_result(node, path_parts, merged_config)
            return
        for child in _node_children(node):
            if _is_ui_case_content_node(_node_text(child)):
                continue
            if _looks_like_ui_case_node(child):
                append_result(child, next_path, merged_config)
        for child in _node_children(node):
            if _is_ui_case_content_node(_node_text(child)):
                continue
            if _looks_like_ui_case_node(child):
                continue
            collect_cases_under_ui_root(child, next_path, merged_config)

    def walk(node, path_parts):
        current_text = _node_text(node)
        next_path = path_parts + [current_text] if current_text else list(path_parts)
        if _is_named_node(current_text, UI_CASE_NODE_NAME):
            collect_cases_under_ui_root(node, path_parts, {})
            return
        for child in _node_children(node):
            walk(child, next_path)

    for item in _resolve_tree_roots(root):
        walk(item, [])
    return results


def _parse_key_value_nodes(node):
    values = {}
    for child in _node_children(node):
        text_value = _node_text(child)
        if not text_value:
            continue
        if "：" in text_value:
            key, value = text_value.split("：", 1)
            values[str(key).strip()] = str(value).strip()
        elif ":" in text_value:
            key, value = text_value.split(":", 1)
            values[str(key).strip()] = str(value).strip()
    return values


def _parse_ui_step(text_value):
    raw_text = str(text_value or "").strip()
    if not raw_text:
        return None, "步骤内容为空"
    normalized = re.sub(r"^\d+[\s\.\、\)\）\-]*", "", raw_text).strip()

    if normalized.startswith("打开 "):
        return {"type": "open", "value": normalized[3:].strip(), "raw": raw_text}, None
    if normalized.startswith("点击 "):
        return {"type": "click", "target": normalized[3:].strip(), "raw": raw_text}, None
    if normalized.startswith("输入 "):
        body = normalized[3:].strip()
        if " " not in body:
            return None, f"输入步骤缺少目标或值: {raw_text}"
        target, value = body.split(" ", 1)
        return {"type": "input", "target": target.strip(), "value": value.strip(), "raw": raw_text}, None
    if normalized.startswith("选择 "):
        body = normalized[3:].strip()
        if " " not in body:
            return None, f"选择步骤缺少目标或值: {raw_text}"
        target, value = body.split(" ", 1)
        return {"type": "select", "target": target.strip(), "value": value.strip(), "raw": raw_text}, None
    if normalized.startswith("等待出现 "):
        return {"type": "wait_exists", "target": normalized[5:].strip(), "raw": raw_text}, None
    if normalized.startswith("等待消失 "):
        return {"type": "wait_not_exists", "target": normalized[5:].strip(), "raw": raw_text}, None
    if normalized.startswith("断言出现 "):
        return {"type": "assert_exists", "target": normalized[5:].strip(), "raw": raw_text}, None
    if normalized.startswith("断言不存在 "):
        return {"type": "assert_not_exists", "target": normalized[6:].strip(), "raw": raw_text}, None
    if normalized.startswith("断言文本 "):
        body = normalized[5:].strip()
        if " " not in body:
            return None, f"断言文本步骤缺少目标或值: {raw_text}"
        target, expected = body.split(" ", 1)
        return {"type": "assert_text", "target": target.strip(), "expected": expected.strip(), "raw": raw_text}, None
    if normalized.startswith("截图 "):
        return {"type": "screenshot", "name": normalized[3:].strip() or "step", "raw": raw_text}, None
    if normalized.startswith("提取 "):
        body = normalized[3:].strip()
        if "=>" not in body:
            return None, f"提取步骤缺少保存变量: {raw_text}"
        target, save_as = body.split("=>", 1)
        return {
            "type": "extract_text",
            "target": target.strip(),
            "save_as": save_as.strip(),
            "raw": raw_text,
        }, None
    return None, f"不支持的步骤动作: {raw_text}"


def _compile_ui_case(node_wrapper, project_id, file_id, file_title):
    node = node_wrapper["node"]
    ui_path = node_wrapper["path"]
    config_node = None
    step_node = None
    assertion_node = None
    for child in _node_children(node):
        child_text = _node_text(child)
        if _is_named_node(child_text, UI_CASE_CONFIG_NODE_NAME):
            config_node = child
        elif _is_named_node(child_text, UI_CASE_STEP_NODE_NAME):
            step_node = child
        elif _is_named_node(child_text, UI_CASE_ASSERT_NODE_NAME):
            assertion_node = child

    if step_node is None:
        return {
            "status": "empty_ui_node",
            "message": "缺少“测试步骤”节点",
            "dsl": None,
            "step_count": 0,
            "assert_count": 0,
        }

    step_errors = []
    steps = []
    for child in _node_children(step_node):
        parsed, error = _parse_ui_step(_node_text(child))
        if error:
            step_errors.append(error)
            continue
        steps.append(parsed)

    if not steps:
        return {
            "status": "empty_ui_node",
            "message": "测试步骤为空",
            "dsl": None,
            "step_count": 0,
            "assert_count": 0,
        }

    if step_errors:
        return {
            "status": "invalid_ui_node",
            "message": "；".join(step_errors[:5]),
            "dsl": {
                "mode": "ui_web_midscene",
                "project_id": project_id,
                "file_id": file_id,
                "file_title": file_title,
                "ui_case_path": ui_path,
                "steps": steps,
            },
            "step_count": len(steps),
            "assert_count": 0,
        }

    config_map = dict(node_wrapper.get("shared_config") or {})
    if config_node:
        config_map.update(_parse_key_value_nodes(config_node))
    assertions = []
    if assertion_node:
        for child in _node_children(assertion_node):
            child_text = _node_text(child)
            if not child_text:
                continue
            if "：" in child_text:
                key, value = child_text.split("：", 1)
            elif ":" in child_text:
                key, value = child_text.split(":", 1)
            else:
                assertions.append({"type": "raw", "value": child_text})
                continue
            assertions.append({"type": str(key).strip(), "value": str(value).strip()})

    dsl = {
        "mode": "ui_web_midscene",
        "project_id": project_id,
        "file_id": file_id,
        "file_title": file_title,
        "ui_case_path": ui_path,
        "ui_case_title": str(node_wrapper.get("title") or UI_CASE_NODE_NAME),
        "channel": config_map.get("渠道", "web"),
        "entry_url": config_map.get("页面入口", ""),
        "browser": config_map.get("浏览器", "chromium"),
        "headless": True,
        "scene_config": config_map,
        "steps": steps,
        "assertions": assertions,
    }
    return {
        "status": "valid",
        "message": "校验通过",
        "dsl": dsl,
        "step_count": len(steps),
        "assert_count": len(assertions),
    }


async def ensure_ui_test_schema(session):
    global UI_SCHEMA_READY
    if UI_SCHEMA_READY:
        return
    if not Config.RUNTIME_SCHEMA_MIGRATION_ENABLED:
        UI_SCHEMA_READY = True
        return
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS argus_ui_test_case_ref ("
        "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL DEFAULT 0,"
        "update_user INT NOT NULL DEFAULT 0,"
        "project_id INT NOT NULL DEFAULT 0,"
        "file_id INT NOT NULL DEFAULT 0,"
        "file_title VARCHAR(128) NOT NULL DEFAULT '',"
        "node_uid VARCHAR(64) NOT NULL DEFAULT '',"
        "node_title VARCHAR(128) NOT NULL DEFAULT '',"
        "node_path TEXT NULL,"
        "status VARCHAR(32) NOT NULL DEFAULT 'empty_ui_node',"
        "step_count INT NOT NULL DEFAULT 0,"
        "assert_count INT NOT NULL DEFAULT 0,"
        "dsl_json LONGTEXT NULL,"
        "validation_result LONGTEXT NULL,"
        "source_snapshot LONGTEXT NULL,"
        "last_scanned_at DATETIME NULL,"
        "KEY idx_ui_case_project_deleted (project_id, deleted_at),"
        "KEY idx_ui_case_file_deleted (file_id, deleted_at),"
        "KEY idx_ui_case_node_uid_deleted (node_uid, deleted_at)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UI测试用例引用表'"
    ))
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS argus_ui_test_plan ("
        "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL DEFAULT 0,"
        "update_user INT NOT NULL DEFAULT 0,"
        "project_id INT NOT NULL DEFAULT 0,"
        "name VARCHAR(128) NOT NULL DEFAULT '',"
        "description VARCHAR(500) NULL,"
        "env_name VARCHAR(64) NULL,"
        "base_url VARCHAR(255) NULL,"
        "browser VARCHAR(32) NOT NULL DEFAULT 'chromium',"
        "headless TINYINT(1) NOT NULL DEFAULT 1,"
        "ordered TINYINT(1) NOT NULL DEFAULT 0,"
        "cron VARCHAR(64) NULL,"
        "retry_times INT NOT NULL DEFAULT 0,"
        "status VARCHAR(32) NOT NULL DEFAULT 'enabled',"
        "runner_config LONGTEXT NULL,"
        "receiver TEXT NULL,"
        "msg_type VARCHAR(64) NULL,"
        "pass_rate SMALLINT NOT NULL DEFAULT 0,"
        "notification_config_id BIGINT NULL,"
        "KEY idx_ui_plan_project_deleted (project_id, deleted_at)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UI测试计划表'"
    ))
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS argus_ui_test_plan_case ("
        "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL DEFAULT 0,"
        "update_user INT NOT NULL DEFAULT 0,"
        "plan_id BIGINT NOT NULL DEFAULT 0,"
        "case_ref_id BIGINT NOT NULL DEFAULT 0,"
        "sort_index INT NOT NULL DEFAULT 0,"
        "enabled TINYINT(1) NOT NULL DEFAULT 1,"
        "KEY idx_ui_plan_case_plan_deleted (plan_id, deleted_at)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UI测试计划用例关系表'"
    ))
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS argus_ui_test_run ("
        "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL DEFAULT 0,"
        "update_user INT NOT NULL DEFAULT 0,"
        "project_id INT NOT NULL DEFAULT 0,"
        "plan_id BIGINT NOT NULL DEFAULT 0,"
        "case_ref_id BIGINT NOT NULL DEFAULT 0,"
        "run_name VARCHAR(128) NOT NULL DEFAULT '',"
        "status VARCHAR(32) NOT NULL DEFAULT 'queued',"
        "trigger_mode VARCHAR(32) NOT NULL DEFAULT 'manual',"
        "browser VARCHAR(32) NOT NULL DEFAULT 'chromium',"
        "headless TINYINT(1) NOT NULL DEFAULT 1,"
        "artifact_bucket VARCHAR(128) NOT NULL DEFAULT '',"
        "artifact_prefix VARCHAR(255) NOT NULL DEFAULT '',"
        "screenshot_dir VARCHAR(255) NOT NULL DEFAULT '',"
        "video_path VARCHAR(255) NOT NULL DEFAULT '',"
        "trace_path VARCHAR(255) NOT NULL DEFAULT '',"
        "report_path VARCHAR(255) NOT NULL DEFAULT '',"
        "result_json_path VARCHAR(255) NOT NULL DEFAULT '',"
        "runner_payload LONGTEXT NULL,"
        "result_payload LONGTEXT NULL,"
        "error_message LONGTEXT NULL,"
        "started_at DATETIME NULL,"
        "finished_at DATETIME NULL,"
        "KEY idx_ui_run_project_deleted (project_id, deleted_at),"
        "KEY idx_ui_run_plan_deleted (plan_id, deleted_at)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UI测试执行记录表'"
    ))
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS argus_ui_test_step_result ("
        "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL DEFAULT 0,"
        "update_user INT NOT NULL DEFAULT 0,"
        "run_id BIGINT NOT NULL DEFAULT 0,"
        "step_index INT NOT NULL DEFAULT 0,"
        "step_name VARCHAR(255) NOT NULL DEFAULT '',"
        "step_type VARCHAR(64) NOT NULL DEFAULT '',"
        "status VARCHAR(32) NOT NULL DEFAULT 'queued',"
        "screenshot_path VARCHAR(255) NOT NULL DEFAULT '',"
        "request_payload LONGTEXT NULL,"
        "result_payload LONGTEXT NULL,"
        "error_message LONGTEXT NULL,"
        "duration_ms INT NOT NULL DEFAULT 0,"
        "KEY idx_ui_step_run_deleted (run_id, deleted_at)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UI测试步骤结果表'"
    ))
    await _ensure_table_index(
        session,
        "argus_ui_test_run",
        "idx_ui_run_queue_claim",
        "status, deleted_at, project_id, plan_id, id",
    )
    await _ensure_table_index(
        session,
        "argus_ui_test_run",
        "idx_ui_run_debug_owner",
        "trigger_mode, create_user, project_id, case_ref_id, deleted_at, id",
    )
    await _ensure_table_index(
        session,
        "argus_ui_test_run",
        "idx_ui_run_report_scope",
        "project_id, trigger_mode, deleted_at, id",
    )
    await _ensure_table_index(
        session,
        "argus_ui_test_step_result",
        "idx_ui_step_run_order",
        "run_id, deleted_at, step_index, id",
    )
    await _ensure_table_index(
        session,
        "argus_ui_test_case_ref",
        "idx_ui_case_project_status_file",
        "project_id, status, deleted_at, file_id",
    )
    await _ensure_table_column(
        session,
        "argus_ui_test_case_ref",
        "assert_count",
        "ALTER TABLE argus_ui_test_case_ref ADD COLUMN assert_count INT NOT NULL DEFAULT 0 AFTER step_count",
    )
    await _ensure_table_index(
        session,
        "argus_ui_test_plan_case",
        "idx_ui_plan_case_enabled_order",
        "plan_id, enabled, deleted_at, sort_index, id",
    )
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS argus_ui_test_plan_follow_user_rel ("
        "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL DEFAULT 0,"
        "update_user INT NOT NULL DEFAULT 0,"
        "user_id INT NOT NULL DEFAULT 0,"
        "plan_id BIGINT NOT NULL DEFAULT 0,"
        "UNIQUE KEY uniq_ui_test_plan_follow_user (user_id, plan_id, deleted_at),"
        "KEY idx_ui_test_plan_follow_user (user_id, deleted_at, plan_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UI测试计划关注用户关系表'"
    ))
    # add notification columns if not exist
    existing_cols = set()
    try:
        result = await session.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='argus_ui_test_plan'"
        ))
        existing_cols = {row[0] for row in result.fetchall()}
    except Exception:
        pass
    for col, alter_sql in [
        ('receiver', "ALTER TABLE argus_ui_test_plan ADD COLUMN receiver TEXT NULL COMMENT '推送用户ID，逗号分隔'"),
        ('msg_type', "ALTER TABLE argus_ui_test_plan ADD COLUMN msg_type VARCHAR(64) NULL COMMENT '推送方式 0=邮件 1=钉钉 2=企业微信 3=飞书'"),
        ('pass_rate', "ALTER TABLE argus_ui_test_plan ADD COLUMN pass_rate SMALLINT NOT NULL DEFAULT 0 COMMENT '成功率阈值，0表示未配置'"),
        ('notification_config_id', "ALTER TABLE argus_ui_test_plan ADD COLUMN notification_config_id BIGINT NULL COMMENT '通知配置ID，关联argus_notification_config'"),
    ]:
        if col not in existing_cols:
            await session.execute(text(alter_sql))
    await session.commit()
    UI_SCHEMA_READY = True


async def ensure_ui_functional_case_item_schema(session):
    global UI_FUNCTIONAL_CASE_ITEM_SCHEMA_READY
    if UI_FUNCTIONAL_CASE_ITEM_SCHEMA_READY:
        return
    if not Config.RUNTIME_SCHEMA_MIGRATION_ENABLED:
        UI_FUNCTIONAL_CASE_ITEM_SCHEMA_READY = True
        return
    try:
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
        index_result = await session.execute(
            text("SHOW INDEX FROM argus_functional_case_item WHERE Key_name='idx_fc_item_type_created'")
        )
        if index_result.first() is None:
            await session.execute(
                text(
                    "ALTER TABLE argus_functional_case_item "
                    "ADD KEY idx_fc_item_type_created (case_type, deleted_at, created_at)"
                )
            )
        await session.execute(
            text(
                "UPDATE argus_functional_case_item "
                "SET case_type=:ui_type "
                "WHERE deleted_at=0 AND case_type<>:ui_type "
                "AND (COALESCE(case_path, '') LIKE :ui_root_like OR case_name=:ui_root_name)"
            ),
            {
                "ui_type": FUNCTIONAL_CASE_TYPE_UI,
                "ui_root_like": f"%{UI_CASE_NODE_NAME}%",
                "ui_root_name": UI_CASE_NODE_NAME,
            },
        )
        await session.commit()
        UI_FUNCTIONAL_CASE_ITEM_SCHEMA_READY = True
    except Exception:
        await session.rollback()
        raise


async def ensure_ui_test_gateway_schema(session):
    if not Config.RUNTIME_SCHEMA_MIGRATION_ENABLED:
        return
    result = await session.execute(text("SHOW COLUMNS FROM argus_gateway LIKE 'page_url'"))
    if result.first() is None:
        await session.execute(text(
            "ALTER TABLE argus_gateway "
            "ADD COLUMN page_url VARCHAR(255) NULL DEFAULT '' COMMENT '页面地址'"
        ))
        await session.commit()


async def _scan_project_cases(session, project_id, operator_user_id):
    await ensure_ui_test_schema(session)
    file_rows = await session.execute(
        text(
            "SELECT id, title, case_data FROM argus_functional_case_file "
            "WHERE deleted_at=0 AND project_id=:project_id ORDER BY updated_at DESC, id DESC"
        ),
        {"project_id": project_id},
    )
    files = file_rows.mappings().all()
    now_dt = datetime.now()

    existing_rows = await session.execute(
        text(
            "SELECT id, file_id, node_path, deleted_at "
            "FROM argus_ui_test_case_ref "
            "WHERE project_id=:project_id "
            "ORDER BY CASE WHEN deleted_at=0 THEN 0 ELSE 1 END ASC, id ASC"
        ),
        {"project_id": project_id},
    )
    existing_by_path = {}
    for row in existing_rows.mappings().all():
        file_id = int(row.get("file_id") or 0)
        node_path = str(row.get("node_path") or "").strip()
        if not file_id or not node_path:
            continue
        path_key = f"{file_id}:{node_path}"
        if path_key not in existing_by_path:
            existing_by_path[path_key] = dict(row)

    processed_case_ids = []
    update_rows = []
    insert_rows = []
    for file_item in files:
        file_id = int(file_item["id"])
        file_title = str(file_item["title"] or "")
        case_data = _parse_json_text(file_item.get("case_data"))
        ui_nodes = _find_ui_nodes(case_data)
        for index, node_wrapper in enumerate(ui_nodes):
            compiled = _compile_ui_case(node_wrapper, project_id, file_id, file_title)
            node_path = str(node_wrapper["path"] or "").strip()
            node_uid = hashlib.md5(f"{file_id}:{node_path}".encode("utf-8")).hexdigest()
            path_key = f"{file_id}:{node_path}"
            matched_row = existing_by_path.get(path_key)
            payload = {
                "project_id": project_id,
                "file_id": file_id,
                "file_title": file_title,
                "node_uid": node_uid,
                "node_title": node_wrapper["title"],
                "node_path": node_path,
                "status": compiled["status"],
                "step_count": int(compiled["step_count"] or 0),
                "assert_count": int(compiled.get("assert_count") or 0),
                "dsl_json": json.dumps(compiled.get("dsl"), ensure_ascii=False) if compiled.get("dsl") else "",
                "validation_result": json.dumps({"message": compiled["message"]}, ensure_ascii=False),
                "source_snapshot": json.dumps(node_wrapper["node"], ensure_ascii=False),
                "last_scanned_at": now_dt,
            }
            if matched_row:
                processed_case_ids.append(int(matched_row["id"]))
                update_rows.append({
                    "id": int(matched_row["id"]),
                    "deleted_at": 0,
                    "update_user": operator_user_id,
                    "updated_at": now_dt,
                    **payload,
                })
            else:
                insert_rows.append({
                    "deleted_at": 0,
                    "create_user": operator_user_id,
                    "update_user": operator_user_id,
                    "created_at": now_dt,
                    "updated_at": now_dt,
                    **payload,
                })
    if update_rows:
        await session.execute(
            text(
                "UPDATE argus_ui_test_case_ref SET "
                "deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at, "
                "project_id=:project_id, file_id=:file_id, file_title=:file_title, node_uid=:node_uid, "
                "node_title=:node_title, node_path=:node_path, status=:status, step_count=:step_count, "
                "assert_count=:assert_count, dsl_json=:dsl_json, validation_result=:validation_result, source_snapshot=:source_snapshot, "
                "last_scanned_at=:last_scanned_at "
                "WHERE id=:id"
            ),
            update_rows,
        )
    if insert_rows:
        await session.execute(
            text(
                "INSERT INTO argus_ui_test_case_ref "
                "(deleted_at, create_user, update_user, created_at, updated_at, project_id, file_id, file_title, node_uid, node_title, node_path, status, step_count, assert_count, dsl_json, validation_result, source_snapshot, last_scanned_at) "
                "VALUES "
                "(:deleted_at, :create_user, :update_user, :created_at, :updated_at, :project_id, :file_id, :file_title, :node_uid, :node_title, :node_path, :status, :step_count, :assert_count, :dsl_json, :validation_result, :source_snapshot, :last_scanned_at)"
            ),
            insert_rows,
        )
    if processed_case_ids:
        await session.execute(
            text(
                "UPDATE argus_ui_test_case_ref SET deleted_at=:deleted_at, update_user=:user_id, updated_at=:updated_at "
                "WHERE project_id=:project_id AND deleted_at=0 AND id NOT IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {
                "deleted_at": int(now_dt.timestamp()),
                "user_id": operator_user_id,
                "updated_at": now_dt,
                "project_id": project_id,
                "ids": processed_case_ids,
            },
        )
    else:
        await session.execute(
            text(
                "UPDATE argus_ui_test_case_ref SET deleted_at=:deleted_at, update_user=:user_id, updated_at=:updated_at "
                "WHERE project_id=:project_id AND deleted_at=0"
            ),
            {
                "deleted_at": int(now_dt.timestamp()),
                "user_id": operator_user_id,
                "updated_at": now_dt,
                "project_id": project_id,
            },
        )
    await session.commit()
    return {"file_count": len(files), "ui_case_count": len(update_rows) + len(insert_rows)}


async def _sync_ui_case_refs_by_project(session, project_id, operator_user_id=0):
    normalized_project_id = int(project_id or 0)
    if normalized_project_id <= 0:
        return None
    return await _scan_project_cases(session, normalized_project_id, int(operator_user_id or 0))


async def _sync_ui_case_refs_by_file(session, file_id, operator_user_id=0):
    row = await session.execute(
        text("SELECT project_id FROM argus_functional_case_file WHERE deleted_at=0 AND id=:id"),
        {"id": int(file_id or 0)},
    )
    data = row.mappings().first()
    if not data:
        return None
    return await _sync_ui_case_refs_by_project(session, int(data["project_id"] or 0), operator_user_id)


async def _sync_ui_case_refs_by_case_ids(session, case_ref_ids, operator_user_id=0):
    normalized_ids = [int(item or 0) for item in case_ref_ids if int(item or 0) > 0]
    if not normalized_ids:
        return
    rows = await session.execute(
        text(
            "SELECT DISTINCT project_id FROM argus_ui_test_case_ref "
            "WHERE deleted_at=0 AND id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": normalized_ids},
    )
    for row in rows.mappings().all():
        await _sync_ui_case_refs_by_project(session, int(row["project_id"] or 0), operator_user_id)


def _normalize_bool(value, default=False):
    if value in (True, False):
        return value
    if value is None:
        return default
    text_value = str(value).strip().lower()
    return text_value in {"1", "true", "yes", "on"}


def _normalize_ui_run_status(value, default="queued"):
    status = str(value or "").strip().lower()
    allowed = {
        "queued", "claimed", "running", "uploading", "success", "failed", "cancelled", "skipped", "partial_success"
    }
    return status if status in allowed else default


def _normalize_ui_step_status(value, default="queued"):
    status = str(value or "").strip().lower()
    allowed = {"queued", "running", "success", "failed", "skipped"}
    return status if status in allowed else default


def _normalize_plan_cron(cron):
    fields = [x.strip() for x in str(cron or "").split() if x.strip()]
    if not fields:
        return ""
    return " ".join("*" if field == "?" else field for field in fields)


def _normalize_optional_pass_rate(value, default=0):
    if value in (None, ""):
        return int(default or 0)
    try:
        rate = int(value)
    except Exception:
        return int(default or 0)
    if rate <= 0:
        return 0
    return min(rate, 100)


def _build_ui_plan_runner_cases(cases):
    runner_cases = []
    for index, item in enumerate(cases or [], start=1):
        dsl = _parse_json_text(item.get("dsl_json")) or {}
        steps = dsl.get("steps") if isinstance(dsl.get("steps"), list) else []
        if not steps:
            continue
        runner_cases.append({
            "case_index": index,
            "case_ref_id": int(item.get("case_ref_id") or 0),
            "file_title": str(item.get("file_title") or ""),
            "node_title": str(item.get("node_title") or ""),
            "node_path": str(item.get("node_path") or ""),
            "dsl": dsl,
        })
    return runner_cases


async def _create_ui_plan_run(session, plan: dict, cases, create_user_id=0, trigger_mode="manual"):
    runner_cases = _build_ui_plan_runner_cases(cases)
    if not runner_cases:
        return 0

    plan_id = int(plan.get("id") or 0)
    project_id = int(plan.get("project_id") or 0)
    now_dt = datetime.now()
    run_name = str(plan.get("name") or "").strip() or f"UI计划#{plan_id}"
    if len(runner_cases) > 1:
        run_name = f"{run_name} ({len(runner_cases)}用例)"

    insert_result = await session.execute(
        text(
            "INSERT INTO argus_ui_test_run "
            "(created_at, updated_at, deleted_at, create_user, update_user, project_id, plan_id, case_ref_id, run_name, status, trigger_mode, browser, headless, artifact_bucket, artifact_prefix, screenshot_dir, video_path, trace_path, report_path, result_json_path, runner_payload, started_at) "
            "VALUES "
            "(:created_at, :updated_at, 0, :create_user, :update_user, :project_id, :plan_id, 0, :run_name, 'queued', :trigger_mode, :browser, :headless, :artifact_bucket, '', '', '', '', '', '', :runner_payload, :started_at)"
        ),
        {
            "created_at": now_dt,
            "updated_at": now_dt,
            "create_user": int(create_user_id or 0),
            "update_user": int(create_user_id or 0),
            "project_id": project_id,
            "plan_id": plan_id,
            "run_name": run_name,
            "trigger_mode": str(trigger_mode or "manual"),
            "browser": str(plan.get("browser") or "chromium"),
            "headless": int(plan.get("headless") or 1),
            "artifact_bucket": UI_BUCKET_NAME,
            "runner_payload": json.dumps({
                "source": "ui_test_plan",
                "plan_id": plan_id,
                "plan_name": str(plan.get("name") or ""),
                "env_name": str(plan.get("env_name") or ""),
                "base_url": str(plan.get("base_url") or ""),
                "case_count": len(runner_cases),
                "ordered": bool(plan.get("ordered")),
                "runner_config": _parse_json_text(plan.get("runner_config")) or {},
                "cases": runner_cases,
                "bucket": UI_BUCKET_NAME,
                "prefix": UI_OBJECT_PREFIX,
            }, ensure_ascii=False),
            "started_at": now_dt,
        },
    )
    run_id = int(insert_result.lastrowid or 0)
    artifact_prefix = f"{UI_OBJECT_PREFIX}/{project_id}/{plan_id}/{run_id}"
    await session.execute(
        text(
            "UPDATE argus_ui_test_run SET artifact_prefix=:artifact_prefix, screenshot_dir=:screenshot_dir, "
            "video_path=:video_path, trace_path=:trace_path, report_path=:report_path, result_json_path=:result_json_path "
            "WHERE id=:id"
        ),
        {
            "id": run_id,
            "artifact_prefix": artifact_prefix,
            "screenshot_dir": f"{artifact_prefix}/screenshots/",
            "video_path": f"{artifact_prefix}/videos/run.mp4",
            "trace_path": f"{artifact_prefix}/traces/trace.zip",
            "report_path": f"{artifact_prefix}/reports/report.html",
            "result_json_path": f"{artifact_prefix}/logs/result.json",
        },
    )
    return run_id


def _parse_cron_trigger(cron):
    fields = [x.strip() for x in str(cron or "").split() if x.strip()]
    if len(fields) == 5:
        return CronTrigger.from_crontab(" ".join(fields))
    if len(fields) == 6:
        second, minute, hour, day, month, day_of_week = fields
        return CronTrigger(second=second, minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)
    if len(fields) == 7:
        second, minute, hour, day, month, day_of_week, year = fields
        return CronTrigger(second=second, minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week,
                           year=year)
    raise ValueError("cron表达式不合法")


async def _enqueue_ui_plan_run(plan_id, user_id=0, trigger_mode="scheduler"):
    async with async_session() as session:
        await ensure_ui_functional_case_item_schema(session)
        plan_row = await session.execute(
            text("SELECT * FROM argus_ui_test_plan WHERE deleted_at=0 AND id=:id AND status='enabled'"),
            {"id": int(plan_id)},
        )
        plan = plan_row.mappings().first()
        if not plan:
            return
        await _sync_ui_case_refs_by_project(session, int(plan["project_id"] or 0), user_id)
        case_rows = await session.execute(
            text(
                "SELECT p.project_id, p.name, p.browser, p.headless, pc.case_ref_id, r.file_title, r.node_title, r.node_path, r.dsl_json "
                "FROM argus_ui_test_plan p "
                "LEFT JOIN argus_ui_test_plan_case pc ON p.id=pc.plan_id "
                "LEFT JOIN argus_ui_test_case_ref r ON pc.case_ref_id=r.id "
                f"INNER JOIN argus_functional_case_item i ON i.file_id=r.file_id AND {UI_CASE_PATH_JOIN_SQL} "
                f"WHERE p.deleted_at=0 AND pc.deleted_at=0 AND pc.enabled=1 AND r.deleted_at=0 AND i.deleted_at=0 AND i.case_type=:ui_case_type AND {UI_PRIORITY_MARKER_SQL} AND r.status='valid' "
                "AND p.id=:plan_id ORDER BY pc.sort_index ASC, pc.id ASC"
            ),
            {"plan_id": int(plan_id), "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
        )
        cases = case_rows.mappings().all()
        run_id = await _create_ui_plan_run(session, dict(plan), cases, create_user_id=user_id, trigger_mode=trigger_mode)
        await session.commit()
        return [run_id] if run_id else []


def _sync_ui_plan_scheduler(plan_id, plan_name, cron, enabled):
    normalized_cron = _normalize_plan_cron(cron)
    if not normalized_cron:
        try:
            Scheduler.scheduler.remove_job(f"ui_test_plan_{plan_id}")
        except Exception:
            pass
        return
    trigger = _parse_cron_trigger(normalized_cron)
    job_id = f"ui_test_plan_{plan_id}"
    try:
        Scheduler.scheduler.add_job(
            func=_enqueue_ui_plan_run,
            args=(int(plan_id), 0, "scheduler"),
            id=job_id,
            name=f"UI测试计划:{plan_name}",
            trigger=trigger,
            replace_existing=True,
        )
    except Exception:
        Scheduler.scheduler.modify_job(job_id=job_id, trigger=trigger, name=f"UI测试计划:{plan_name}")
    try:
        if enabled:
            Scheduler.scheduler.resume_job(job_id)
        else:
            Scheduler.scheduler.pause_job(job_id)
    except Exception:
        pass


async def restore_ui_test_scheduler_jobs():
    async with async_session() as session:
        rows = await session.execute(
            text(
                "SELECT id, name, cron, status "
                "FROM argus_ui_test_plan WHERE deleted_at=0"
            )
        )
        for item in rows.mappings().all():
            try:
                _sync_ui_plan_scheduler(
                    int(item["id"] or 0),
                    str(item.get("name") or ""),
                    str(item.get("cron") or ""),
                    str(item.get("status") or "enabled") == "enabled",
                )
            except Exception:
                continue


def _build_run_analysis(run_data, steps):
    status = str(run_data.get("status") or "")
    error_message = str(run_data.get("error_message") or "").strip()
    failed_steps = [item for item in steps if str(item.get("status") or "") == "failed"]
    if status == "cancelled":
        return {
            "status": "cancelled",
            "summary": "本次UI执行已被手动停止。",
            "reason_type": "cancelled",
            "failed_step_count": len(failed_steps),
            "suggestion": "如需继续验证，请重新试运行或重新执行计划。",
        }
    if status in {"queued", "claimed", "running", "uploading"}:
        summary_map = {
            "queued": "任务已入队，等待 Runner 领取执行。",
            "claimed": "任务已被 Runner 领取，等待开始执行步骤。",
            "running": "任务执行中，可稍后刷新查看步骤结果。",
            "uploading": "步骤已执行完成，正在生成并上传截图、录屏和报告产物。",
        }
        return {
            "status": status,
            "summary": summary_map.get(status) or "任务正在处理中。",
            "reason_type": "artifact_uploading" if status == "uploading" else "pending",
            "failed_step_count": len(failed_steps),
            "suggestion": "等待对象存储产物上传完成后再查看截图、录屏和报告。" if status == "uploading" else "等待 Runner 执行完成后再查看截图、录屏和报告产物。",
        }
    if status == "success":
        if not steps:
            return {
                "status": "failed",
                "summary": "Runner 标记成功，但没有回写任何步骤结果，判定为空执行。",
                "reason_type": "empty_execution",
                "failed_step_count": 0,
                "suggestion": "检查 Runner 是否消费到了 runner_payload.cases，并确认当前运行的 Runner 已更新到最新版本。",
            }
        return {
            "status": "success",
            "summary": "本次UI执行成功，未发现失败步骤。",
            "reason_type": "none",
            "suggestion": "无需处理。",
        }

    text_value = f"{error_message}\n" + "\n".join(str(item.get("error_message") or "") for item in failed_steps)
    lowered = text_value.lower()
    if any(key in lowered for key in ("timeout", "timed out", "waitfor")):
        reason_type = "timeout"
        summary = "高概率为页面加载或元素等待超时。"
        suggestion = "优先检查页面响应速度、等待条件和步骤描述是否过于模糊。"
    elif any(key in lowered for key in ("not found", "locator", "element", "不存在")):
        reason_type = "locator"
        summary = "高概率为元素定位失败或页面结构变化。"
        suggestion = "检查页面文案、布局变化，必要时改写步骤目标描述。"
    elif any(key in lowered for key in ("assert", "期望", "expected")):
        reason_type = "assertion"
        summary = "高概率为断言不满足。"
        suggestion = "检查业务预期、测试数据以及页面实际渲染结果。"
    elif any(key in lowered for key in ("net::", "network", "http", "https", "dns")):
        reason_type = "environment"
        summary = "高概率为环境连通性或网络异常。"
        suggestion = "检查目标环境、DNS、代理和登录态配置。"
    else:
        reason_type = "unknown"
        summary = "存在失败步骤，但当前无法从错误文本中稳定归因。"
        suggestion = "优先查看失败步骤截图、trace 和 report.html。"

    return {
        "status": status or "failed",
        "summary": summary,
        "reason_type": reason_type,
        "failed_step_count": len(failed_steps),
        "suggestion": suggestion,
    }


def _extract_ui_run_counts(result_payload, run_status=""):
    payload = result_payload if isinstance(result_payload, dict) else _parse_json_text(result_payload) or {}
    success_count = failed_count = skipped_count = error_count = 0
    total_count = 0
    report_status = ""

    if isinstance(payload, dict):
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
        base = summary or stats or payload
        success_count = int(base.get("success_count") or base.get("success_case_count") or base.get("passed") or 0)
        failed_count = int(base.get("failed_count") or base.get("failed_case_count") or base.get("failed") or 0)
        skipped_count = int(base.get("skipped_count") or base.get("skipped_case_count") or base.get("skipped") or 0)
        error_count = int(base.get("error_count") or base.get("error") or 0)
        total_count = int(base.get("total_count") or base.get("total") or base.get("case_count") or 0)
        report_status = str(base.get("report_status") or payload.get("report_status") or "").strip().lower()

        if not any((success_count, failed_count, skipped_count, error_count)) and isinstance(payload.get("case_results"), list):
            for case_item in payload.get("case_results") or []:
                case_status = str((case_item or {}).get("status") or "").strip().lower()
                if case_status == "success":
                    success_count += 1
                elif case_status == "failed":
                    failed_count += 1
                elif case_status == "skipped":
                    skipped_count += 1
                elif case_status:
                    error_count += 1

    if total_count <= 0:
        total_count = success_count + failed_count + skipped_count + error_count

    normalized_run_status = str(run_status or "").strip().lower()
    if not report_status:
        if normalized_run_status in {"queued", "claimed", "running", "uploading", "cancelled"}:
            report_status = normalized_run_status
        elif failed_count > 0 or error_count > 0:
            report_status = "failed"
        elif success_count > 0 and failed_count == 0 and error_count == 0:
            report_status = "success"
        elif skipped_count > 0 and total_count == skipped_count:
            report_status = "skipped"

    return {
        "total_count": int(total_count or 0),
        "success_count": int(success_count or 0),
        "failed_count": int(failed_count or 0),
        "skipped_count": int(skipped_count or 0),
        "error_count": int(error_count or 0),
        "report_status": report_status,
    }


def _guess_artifact_preview_type(path_value: str):
    suffix = Path(str(path_value or "").lower()).suffix
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}:
        return "image"
    if suffix in {".mp4", ".webm", ".ogg", ".mov", ".m4v", ".avi", ".mkv"}:
        return "video"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".json", ".log", ".txt", ".md", ".xml", ".yaml", ".yml"}:
        return "text"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".zip":
        return "archive"
    return "file"


def _normalize_ui_artifact_server(request: Request):
    origin = str(getattr(request, "base_url", "") or "").strip().rstrip("/")
    if not origin:
        origin = "http://127.0.0.1:7777"
    return origin if origin.endswith("/argus") else f"{origin}/argus"


def _build_ui_artifact_proxy_url(object_key: str, bucket_name: str = "", request: Request = None):
    normalized_key = str(object_key or "").replace("\\", "/").strip().strip("/")
    if not normalized_key:
        return ""
    query = {
        "object_key": normalized_key,
    }
    if str(bucket_name or "").strip():
        query["bucket_name"] = str(bucket_name or "").strip()
    path = f"/ui-test/run/share-artifact/view?{urlencode(query)}"
    if request is None:
        return f"/argus{path}"
    return f"{_normalize_ui_artifact_server(request)}{path}"


async def _build_artifact_descriptor(client, bucket_name: str, object_key: str, label: str = "", proxy_url: str = ""):
    normalized_key = str(object_key or "").replace("\\", "/").strip().strip("/")
    if not normalized_key:
        return None
    item = {
        "label": label or Path(normalized_key).name,
        "object_key": normalized_key,
        "preview_type": _guess_artifact_preview_type(normalized_key),
        "name": Path(normalized_key).name,
        "view_url": "",
        "content_type": "",
        "bucket_name": str(bucket_name or ""),
        "size": 0,
        "file_size": "",
        "available": False,
    }
    try:
        detail = await client.get_object_detail(normalized_key, bucket_name=bucket_name or None)
        item.update({
            "view_url": str(proxy_url or detail.get("view_url") or "").strip(),
            "content_type": detail.get("content_type") or "",
            "bucket_name": detail.get("bucket") or item["bucket_name"],
            "size": int(detail.get("size") or 0),
            "file_size": detail.get("file_size") or "",
            "available": True,
        })
    except Exception:
        pass
    return item


async def _upload_artifact_with_retry(client, object_key: str, content: bytes, bucket_name: str = None,
                                      content_type: str = "application/octet-stream", attempts: int = 3):
    last_error = None
    normalized_attempts = max(1, int(attempts or 1))
    for attempt in range(1, normalized_attempts + 1):
        try:
            upload_result, file_size = await client.create_file(
                object_key,
                content,
                bucket_name=bucket_name,
                content_type=content_type or "application/octet-stream",
            )
            return upload_result, file_size, attempt
        except Exception as exc:
            last_error = exc
            if attempt < normalized_attempts:
                await asyncio.sleep(min(1.2 * attempt, 5))
    raise RuntimeError(f"对象存储上传失败，已重试{normalized_attempts}次：{last_error}") from last_error


@router.get("/case/list")
async def list_ui_test_cases(project_id: int, keyword: str = "", status: str = "", auto_scan: bool = False,
                             page: int = 1, size: int = 20, paged: bool = False,
                             session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    page, size, offset = _clamp_pagination(page, size, 200)
    await _sync_ui_case_refs_by_project(session, int(project_id), int(user_info["id"]))
    normalized_status = str(status or "").strip()
    status_expr = (
        "CASE "
        "WHEN COUNT(i.id)=0 THEN 'no_ui_node' "
        "WHEN SUM(CASE WHEN r.status='valid' THEN 1 ELSE 0 END)>0 THEN 'valid' "
        "WHEN SUM(CASE WHEN r.status='invalid_ui_node' THEN 1 ELSE 0 END)>0 THEN 'invalid_ui_node' "
        "ELSE 'empty_ui_node' END"
    )
    params = {
        "project_id": project_id,
        "keyword": keyword or "",
        "like_keyword": f"%{keyword or ''}%",
        "ui_case_type": FUNCTIONAL_CASE_TYPE_UI,
    }
    sql = (
        "SELECT MIN(r.id) AS id, f.id AS file_id, f.title AS file_title, "
        "COUNT(DISTINCT i.id) AS ui_case_count, "
        "SUM(CASE WHEN r.status='valid' THEN 1 ELSE 0 END) AS valid_ui_case_count, "
        "SUM(CASE WHEN r.status='invalid_ui_node' THEN 1 ELSE 0 END) AS invalid_ui_case_count, "
        "SUM(CASE WHEN r.status='empty_ui_node' THEN 1 ELSE 0 END) AS empty_ui_case_count, "
        "MAX(r.last_scanned_at) AS last_scanned_at "
        "FROM argus_functional_case_file f "
        "INNER JOIN argus_functional_case_item i ON i.file_id=f.id AND i.deleted_at=0 AND i.case_type=:ui_case_type "
        f"LEFT JOIN argus_ui_test_case_ref r ON r.file_id=i.file_id AND {UI_CASE_PATH_JOIN_SQL} AND r.deleted_at=0 AND {UI_PRIORITY_MARKER_SQL} "
        "WHERE f.deleted_at=0 AND f.project_id=:project_id "
        "AND (:keyword='' OR f.title LIKE :like_keyword OR i.case_name LIKE :like_keyword OR i.case_path LIKE :like_keyword OR CAST(f.id AS CHAR) LIKE :like_keyword) "
        "GROUP BY f.id, f.title "
    )
    if normalized_status:
        sql += f"HAVING {status_expr}=:status "
        params["status"] = normalized_status
    sql += "ORDER BY f.updated_at DESC, f.id DESC"
    if paged:
        sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = size
        params["offset"] = offset
    rows = await session.execute(text(sql), params)
    result = []
    for row in rows.mappings().all():
        item = dict(row)
        item["follow"] = bool(item.get("follow"))
        item["ui_case_count"] = int(item.get("ui_case_count") or 0)
        item["valid_ui_case_count"] = int(item.get("valid_ui_case_count") or 0)
        item["invalid_ui_case_count"] = int(item.get("invalid_ui_case_count") or 0)
        item["empty_ui_case_count"] = int(item.get("empty_ui_case_count") or 0)
        item["has_valid_data"] = item["valid_ui_case_count"] > 0
        if item["ui_case_count"] == 0:
            item["status"] = "no_ui_node"
        elif item["valid_ui_case_count"] > 0:
            item["status"] = "valid"
        elif item["invalid_ui_case_count"] > 0:
            item["status"] = "invalid_ui_node"
        else:
            item["status"] = "empty_ui_node"
        result.append(item)
    if not paged:
        return ArgusResponse.success(result)
    count_sql = (
        "SELECT COUNT(1) AS total FROM ("
        "SELECT f.id "
        "FROM argus_functional_case_file f "
        "INNER JOIN argus_functional_case_item i ON i.file_id=f.id AND i.deleted_at=0 AND i.case_type=:ui_case_type "
        f"LEFT JOIN argus_ui_test_case_ref r ON r.file_id=i.file_id AND {UI_CASE_PATH_JOIN_SQL} AND r.deleted_at=0 AND {UI_PRIORITY_MARKER_SQL} "
        "WHERE f.deleted_at=0 AND f.project_id=:project_id "
        "AND (:keyword='' OR f.title LIKE :like_keyword OR i.case_name LIKE :like_keyword OR i.case_path LIKE :like_keyword OR CAST(f.id AS CHAR) LIKE :like_keyword) "
        "GROUP BY f.id, f.title "
    )
    count_params = {
        "project_id": project_id,
        "keyword": keyword or "",
        "like_keyword": f"%{keyword or ''}%",
        "ui_case_type": FUNCTIONAL_CASE_TYPE_UI,
    }
    if normalized_status:
        count_sql += f"HAVING {status_expr}=:status "
        count_params["status"] = normalized_status
    count_sql += ") t"
    count_row = await session.execute(text(count_sql), count_params)
    total = int((count_row.mappings().first() or {}).get("total") or 0)
    return ArgusResponse.success(_paged_payload(result, total, page, size))


@router.get("/case/nodes")
async def list_ui_test_case_nodes(file_id: int, include_dsl: bool = False,
                                  session=Depends(get_session), _=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    await _sync_ui_case_refs_by_file(session, file_id)
    dsl_column = ", dsl_json" if include_dsl else ""
    rows = await session.execute(
        text(
            "SELECT r.id, r.file_id, r.file_title, r.node_uid, r.node_title, r.node_path, r.status, "
            "r.step_count, r.assert_count, r.validation_result, r.source_snapshot, r.last_scanned_at, "
            "i.id AS functional_case_item_id, i.case_uid, i.case_type "
            f"{dsl_column} "
            "FROM argus_functional_case_item i "
            f"INNER JOIN argus_ui_test_case_ref r ON r.file_id=i.file_id AND {UI_CASE_PATH_JOIN_SQL} AND r.deleted_at=0 AND {UI_PRIORITY_MARKER_SQL} "
            "WHERE i.deleted_at=0 AND i.case_type=:ui_case_type AND i.file_id=:file_id "
            "ORDER BY i.id ASC, r.id ASC"
        ),
        {"file_id": file_id, "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    data = []
    for row in rows.mappings().all():
        item = dict(row)
        item["case_ref_id"] = int(item.get("id") or 0)
        item["step_count"] = int(item.get("step_count") or 0)
        item["assert_count"] = int(item.get("assert_count") or 0)
        item["validation_result"] = _parse_json_text(item.get("validation_result")) or {}
        if not _has_priority_marker(_parse_json_text(item.get("source_snapshot")) or {}):
            continue
        item.pop("source_snapshot", None)
        item["dsl_json"] = (_parse_json_text(item.get("dsl_json")) or {}) if include_dsl else {}
        data.append(item)
    return ArgusResponse.success(data)


@router.get("/case/detail")
async def get_ui_test_case_detail(id: int, session=Depends(get_session), _=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    await _sync_ui_case_refs_by_case_ids(session, [id])
    row = await session.execute(
        text(
            "SELECT r.id, r.project_id, r.file_id, r.file_title, r.node_uid, r.node_title, r.node_path, r.status, "
            "r.step_count, r.assert_count, r.dsl_json, r.validation_result, r.source_snapshot, r.last_scanned_at, "
            "i.id AS functional_case_item_id, i.case_uid, i.case_type "
            "FROM argus_ui_test_case_ref r "
            f"INNER JOIN argus_functional_case_item i ON i.file_id=r.file_id AND {UI_CASE_PATH_JOIN_SQL} "
            f"WHERE r.deleted_at=0 AND i.deleted_at=0 AND i.case_type=:ui_case_type AND {UI_PRIORITY_MARKER_SQL} AND r.id=:id"
        ),
        {"id": id, "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    item = row.mappings().first()
    if not item:
        return ArgusResponse.failed("UI测试用例不存在")
    data = dict(item)
    data["dsl_json"] = _parse_json_text(data.get("dsl_json")) or {}
    data["validation_result"] = _parse_json_text(data.get("validation_result")) or {}
    data["source_snapshot"] = _parse_json_text(data.get("source_snapshot")) or {}
    return ArgusResponse.success(data)


@router.post("/case/validate")
async def validate_ui_test_case(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    payload = await request.json()
    case_ref_id = int(payload.get("id") or 0)
    if case_ref_id <= 0:
        return ArgusResponse.failed("id不能为空")
    await _sync_ui_case_refs_by_case_ids(session, [case_ref_id], int(user_info["id"]))
    row = await session.execute(
        text(
            "SELECT r.id, r.project_id, r.file_id, r.node_uid, r.node_path "
            "FROM argus_ui_test_case_ref r "
            f"INNER JOIN argus_functional_case_item i ON i.file_id=r.file_id AND {UI_CASE_PATH_JOIN_SQL} "
            f"WHERE r.deleted_at=0 AND i.deleted_at=0 AND i.case_type=:ui_case_type AND {UI_PRIORITY_MARKER_SQL} AND r.id=:id"
        ),
        {"id": case_ref_id, "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    record = row.mappings().first()
    if not record:
        return ArgusResponse.failed("UI测试用例不存在")
    refreshed = await session.execute(
        text(
            "SELECT id, status, step_count, assert_count, validation_result, dsl_json, last_scanned_at "
            "FROM argus_ui_test_case_ref "
            f"WHERE deleted_at=0 AND {UI_PRIORITY_MARKER_SQL} AND project_id=:project_id AND file_id=:file_id AND node_uid=:node_uid"
        ),
        {
            "project_id": int(record["project_id"] or 0),
            "file_id": int(record["file_id"] or 0),
            "node_uid": str(record.get("node_uid") or ""),
        },
    )
    item = refreshed.mappings().first()
    if not item:
        return ArgusResponse.failed(f"UI测试用例已失效: {record.get('node_path') or case_ref_id}")
    data = dict(item)
    data["validation_result"] = _parse_json_text(data.get("validation_result")) or {}
    data["dsl_json"] = _parse_json_text(data.get("dsl_json")) or {}
    return ArgusResponse.success(data)


@router.post("/case/preview-dsl")
async def preview_ui_test_case_dsl(request: Request, session=Depends(get_session), _=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    payload = await request.json()
    case_ref_id = int(payload.get("id") or 0)
    if case_ref_id <= 0:
        return ArgusResponse.failed("id不能为空")
    await _sync_ui_case_refs_by_case_ids(session, [case_ref_id])
    row = await session.execute(
        text(
            "SELECT r.id, r.status, r.dsl_json, r.validation_result, i.id AS functional_case_item_id "
            "FROM argus_ui_test_case_ref r "
            f"INNER JOIN argus_functional_case_item i ON i.file_id=r.file_id AND {UI_CASE_PATH_JOIN_SQL} "
            f"WHERE r.deleted_at=0 AND i.deleted_at=0 AND i.case_type=:ui_case_type AND {UI_PRIORITY_MARKER_SQL} AND r.id=:id"
        ),
        {"id": case_ref_id, "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    record = row.mappings().first()
    if not record:
        return ArgusResponse.failed("UI测试用例不存在")
    return ArgusResponse.success({
        "id": int(record["id"]),
        "status": record["status"],
        "dsl": _parse_json_text(record.get("dsl_json")) or {},
        "validation_result": _parse_json_text(record.get("validation_result")) or {},
    })


async def _resolve_ui_trial_context(session, payload):
    env_id = int(payload.get("env_id") or 0)
    address_id = int(payload.get("address_id") or 0)
    if env_id <= 0:
        return None, "env_id不能为空"

    env_row = await session.execute(
        text("SELECT id, name FROM argus_environment WHERE deleted_at=0 AND id=:id"),
        {"id": env_id},
    )
    env_data = env_row.mappings().first()
    if not env_data:
        return None, "所选环境不存在"

    context = {
        "env_id": env_id,
        "env_name": str(env_data.get("name") or "").strip(),
        "address_id": address_id,
        "address_name": "",
        "page_url": "",
        "base_url": "",
    }
    if address_id > 0:
        gateway_row = await session.execute(
            text("SELECT id, env, name, gateway, page_url FROM argus_gateway WHERE deleted_at=0 AND id=:id"),
            {"id": address_id},
        )
        gateway_data = gateway_row.mappings().first()
        if not gateway_data:
            return None, "所选地址前缀不存在"
        if int(gateway_data.get("env") or 0) != env_id:
            return None, "地址前缀与所选环境不匹配"
        context.update({
            "address_name": str(gateway_data.get("name") or "").strip(),
            "page_url": str(gateway_data.get("page_url") or "").strip(),
            "base_url": _compose_plan_base_url(gateway_data.get("gateway"), gateway_data.get("page_url")),
        })
    return context, ""


async def _load_valid_ui_case_refs(session, case_ref_ids):
    rows = await session.execute(
        text(
            "SELECT r.id, r.project_id, r.file_title, r.node_title, r.node_path, r.status, r.dsl_json, r.source_snapshot "
            "FROM argus_ui_test_case_ref r "
            f"INNER JOIN argus_functional_case_item i ON i.file_id=r.file_id AND {UI_CASE_PATH_JOIN_SQL} "
            f"WHERE r.deleted_at=0 AND i.deleted_at=0 AND i.case_type=:ui_case_type AND {UI_PRIORITY_MARKER_SQL} AND r.id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": case_ref_ids, "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    case_refs = rows.mappings().all()
    case_ref_map = {
        int(item["id"]): item
        for item in case_refs
        if _has_priority_marker(_parse_json_text(item.get("source_snapshot")) or {})
    }
    missing_ids = [case_id for case_id in case_ref_ids if case_id not in case_ref_map]
    if missing_ids:
        return [], f"UI测试用例不存在: {', '.join(map(str, missing_ids))}"

    invalid_refs = [item for item in case_refs if str(item["status"]) != "valid"]
    if invalid_refs:
        invalid_names = [str(item.get("node_title") or item.get("node_path") or item["id"]) for item in invalid_refs]
        return [], f"存在不可试运行的UI测试用例: {', '.join(invalid_names)}"
    return [case_ref_map[case_id] for case_id in case_ref_ids], ""


async def _create_ui_trial_run(session, payload, user_id, case_ref, context):
    now_dt = datetime.now()
    case_ref_id = int(case_ref["id"] or 0)
    insert_result = await session.execute(
        text(
            "INSERT INTO argus_ui_test_run "
            "(created_at, updated_at, deleted_at, create_user, update_user, project_id, plan_id, case_ref_id, run_name, status, trigger_mode, browser, headless, artifact_bucket, artifact_prefix, screenshot_dir, video_path, trace_path, report_path, result_json_path, runner_payload, started_at) "
            "VALUES "
            "(:created_at, :updated_at, 0, :create_user, :update_user, :project_id, 0, :case_ref_id, :run_name, :status, :trigger_mode, :browser, :headless, :artifact_bucket, :artifact_prefix, :screenshot_dir, :video_path, :trace_path, :report_path, :result_json_path, :runner_payload, :started_at)"
        ),
        {
            "created_at": now_dt,
            "updated_at": now_dt,
            "create_user": user_id,
            "update_user": user_id,
            "project_id": int(case_ref["project_id"] or 0),
            "case_ref_id": case_ref_id,
            "run_name": f"Trial Run #{case_ref_id}",
            "status": "queued",
            "trigger_mode": "trial",
            "browser": str(payload.get("browser") or "chromium"),
            "headless": 1 if _normalize_bool(payload.get("headless"), True) else 0,
            "artifact_bucket": UI_BUCKET_NAME,
            "artifact_prefix": "",
            "screenshot_dir": "",
            "video_path": "",
            "trace_path": "",
            "report_path": "",
            "result_json_path": "",
            "runner_payload": json.dumps({
                "source": "ui_test_trial_run",
                "debug": True,
                "debug_user_id": user_id,
                "env_id": context["env_id"],
                "env_name": context["env_name"],
                "address_id": context["address_id"],
                "address_name": context["address_name"],
                "page_url": context["page_url"],
                "base_url": context["base_url"],
                "file_title": case_ref["file_title"],
                "node_title": case_ref["node_title"],
                "node_path": case_ref["node_path"],
                "dsl": _parse_json_text(case_ref.get("dsl_json")) or {},
                "bucket": UI_BUCKET_NAME,
                "prefix": UI_OBJECT_PREFIX,
            }, ensure_ascii=False),
            "started_at": now_dt,
        },
    )
    run_id = int(insert_result.lastrowid or 0)
    artifact_prefix = f"{UI_OBJECT_PREFIX}/{int(case_ref['project_id'] or 0)}/0/{run_id}"
    await session.execute(
        text(
            "UPDATE argus_ui_test_run SET artifact_prefix=:artifact_prefix, screenshot_dir=:screenshot_dir, "
            "video_path=:video_path, trace_path=:trace_path, report_path=:report_path, result_json_path=:result_json_path "
            "WHERE id=:id"
        ),
        {
            "id": run_id,
            "artifact_prefix": artifact_prefix,
            "screenshot_dir": f"{artifact_prefix}/screenshots/",
            "video_path": f"{artifact_prefix}/videos/run.mp4",
            "trace_path": f"{artifact_prefix}/traces/trace.zip",
            "report_path": f"{artifact_prefix}/reports/report.html",
            "result_json_path": f"{artifact_prefix}/logs/result.json",
        },
    )
    return run_id


@router.post("/case/trial-run")
async def trial_run_ui_test_case(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    await ensure_ui_test_gateway_schema(session)
    payload = await request.json()
    case_ref_id = int(payload.get("id") or 0)
    env_id = int(payload.get("env_id") or 0)
    address_id = int(payload.get("address_id") or 0)
    if case_ref_id <= 0:
        return ArgusResponse.failed("id不能为空")
    if env_id <= 0:
        return ArgusResponse.failed("env_id不能为空")
    await _sync_ui_case_refs_by_case_ids(session, [case_ref_id], int(user_info["id"]))

    row = await session.execute(
        text(
            "SELECT r.id, r.project_id, r.file_title, r.node_title, r.node_path, r.status, r.dsl_json, r.source_snapshot "
            "FROM argus_ui_test_case_ref r "
            f"INNER JOIN argus_functional_case_item i ON i.file_id=r.file_id AND {UI_CASE_PATH_JOIN_SQL} "
            f"WHERE r.deleted_at=0 AND i.deleted_at=0 AND i.case_type=:ui_case_type AND {UI_PRIORITY_MARKER_SQL} AND r.id=:id"
        ),
        {"id": case_ref_id, "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    case_ref = row.mappings().first()
    if not case_ref:
        return ArgusResponse.failed("UI测试用例不存在")
    if not _has_priority_marker(_parse_json_text(case_ref.get("source_snapshot")) or {}):
        return ArgusResponse.failed("该节点未标记优先级，不能作为UI自动化用例执行")
    if str(case_ref["status"]) != "valid":
        return ArgusResponse.failed("该UI测试用例当前不可试运行")

    env_row = await session.execute(
        text("SELECT id, name FROM argus_environment WHERE deleted_at=0 AND id=:id"),
        {"id": env_id},
    )
    env_data = env_row.mappings().first()
    if not env_data:
        return ArgusResponse.failed("所选环境不存在")
    env_name = str(env_data.get("name") or "").strip()
    address_name = ""
    page_url = ""
    resolved_base_url = ""
    if address_id > 0:
        gateway_row = await session.execute(
            text("SELECT id, env, name, gateway, page_url FROM argus_gateway WHERE deleted_at=0 AND id=:id"),
            {"id": address_id},
        )
        gateway_data = gateway_row.mappings().first()
        if not gateway_data:
            return ArgusResponse.failed("所选地址前缀不存在")
        if int(gateway_data.get("env") or 0) != env_id:
            return ArgusResponse.failed("地址前缀与所选环境不匹配")
        address_name = str(gateway_data.get("name") or "").strip()
        page_url = str(gateway_data.get("page_url") or "").strip()
        resolved_base_url = _compose_plan_base_url(gateway_data.get("gateway"), page_url)

    now_dt = datetime.now()
    insert_result = await session.execute(
        text(
            "INSERT INTO argus_ui_test_run "
            "(created_at, updated_at, deleted_at, create_user, update_user, project_id, plan_id, case_ref_id, run_name, status, trigger_mode, browser, headless, artifact_bucket, artifact_prefix, screenshot_dir, video_path, trace_path, report_path, result_json_path, runner_payload, started_at) "
            "VALUES "
            "(:created_at, :updated_at, 0, :create_user, :update_user, :project_id, 0, :case_ref_id, :run_name, :status, :trigger_mode, :browser, :headless, :artifact_bucket, :artifact_prefix, :screenshot_dir, :video_path, :trace_path, :report_path, :result_json_path, :runner_payload, :started_at)"
        ),
        {
            "created_at": now_dt,
            "updated_at": now_dt,
            "create_user": int(user_info["id"]),
            "update_user": int(user_info["id"]),
            "project_id": int(case_ref["project_id"] or 0),
            "case_ref_id": case_ref_id,
            "run_name": f"Trial Run #{case_ref_id}",
            "status": "queued",
            "trigger_mode": "trial",
            "browser": str(payload.get("browser") or "chromium"),
            "headless": 1 if _normalize_bool(payload.get("headless"), True) else 0,
            "artifact_bucket": UI_BUCKET_NAME,
            "artifact_prefix": "",
            "screenshot_dir": "",
            "video_path": "",
            "trace_path": "",
            "report_path": "",
            "result_json_path": "",
            "runner_payload": json.dumps({
                "source": "ui_test_trial_run",
                "debug": True,
                "debug_user_id": int(user_info["id"]),
                "env_id": env_id,
                "env_name": env_name,
                "address_id": address_id,
                "address_name": address_name,
                "page_url": page_url,
                "base_url": resolved_base_url,
                "file_title": case_ref["file_title"],
                "node_title": case_ref["node_title"],
                "node_path": case_ref["node_path"],
                "dsl": _parse_json_text(case_ref.get("dsl_json")) or {},
                "bucket": UI_BUCKET_NAME,
                "prefix": UI_OBJECT_PREFIX,
            }, ensure_ascii=False),
            "started_at": now_dt,
        },
    )
    run_id = int(insert_result.lastrowid or 0)
    artifact_prefix = f"{UI_OBJECT_PREFIX}/{int(case_ref['project_id'] or 0)}/0/{run_id}"
    await session.execute(
        text(
            "UPDATE argus_ui_test_run SET artifact_prefix=:artifact_prefix, screenshot_dir=:screenshot_dir, "
            "video_path=:video_path, trace_path=:trace_path, report_path=:report_path, result_json_path=:result_json_path "
            "WHERE id=:id"
        ),
        {
            "id": run_id,
            "artifact_prefix": artifact_prefix,
            "screenshot_dir": f"{artifact_prefix}/screenshots/",
            "video_path": f"{artifact_prefix}/videos/run.mp4",
            "trace_path": f"{artifact_prefix}/traces/trace.zip",
            "report_path": f"{artifact_prefix}/reports/report.html",
            "result_json_path": f"{artifact_prefix}/logs/result.json",
        },
    )
    await session.commit()
    ai_model = await GConfigDao.get_active_ai_model_config()
    bootstrap = _build_runner_bootstrap_payload(request, user_info, int(case_ref["project_id"] or 0), [run_id], 0, ai_model)
    _write_runner_bootstrap_file(bootstrap)
    return ArgusResponse.success({"run_id": run_id, "trigger_mode": "trial", "runner_bootstrap": bootstrap})


@router.post("/case/trial-run-batch")
async def trial_run_ui_test_cases(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    await ensure_ui_test_gateway_schema(session)
    payload = await request.json()
    raw_ids = payload.get("ids") or payload.get("case_ref_ids") or []
    if isinstance(raw_ids, (str, int)):
        raw_ids = [raw_ids]

    case_ref_ids = []
    for item in raw_ids:
        case_id = int(item or 0)
        if case_id > 0 and case_id not in case_ref_ids:
            case_ref_ids.append(case_id)
    if not case_ref_ids:
        return ArgusResponse.failed("ids不能为空")

    await _sync_ui_case_refs_by_case_ids(session, case_ref_ids, int(user_info["id"]))
    context, error_message = await _resolve_ui_trial_context(session, payload)
    if error_message:
        return ArgusResponse.failed(error_message)
    case_refs, error_message = await _load_valid_ui_case_refs(session, case_ref_ids)
    if error_message:
        return ArgusResponse.failed(error_message)

    user_id = int(user_info["id"])
    run_ids = []
    for case_ref in case_refs:
        run_ids.append(await _create_ui_trial_run(session, payload, user_id, case_ref, context))
    await session.commit()

    project_id = int(case_refs[0]["project_id"] or 0)
    ai_model = await GConfigDao.get_active_ai_model_config()
    bootstrap = _build_runner_bootstrap_payload(request, user_info, project_id, run_ids, 0, ai_model)
    _write_runner_bootstrap_file(bootstrap)
    return ArgusResponse.success({"run_ids": run_ids, "trigger_mode": "trial", "runner_bootstrap": bootstrap})


@router.get("/plan/candidates")
async def list_ui_plan_candidates(project_id: int, session=Depends(get_session), _=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    await _sync_ui_case_refs_by_project(session, project_id)
    rows = await session.execute(
        text(
            "SELECT r.file_id, r.file_title, r.id, r.node_title, r.node_path, r.step_count, r.assert_count, "
            "i.id AS functional_case_item_id, i.case_uid, i.case_type "
            "FROM argus_functional_case_item i "
            f"INNER JOIN argus_ui_test_case_ref r ON r.file_id=i.file_id AND {UI_CASE_PATH_JOIN_SQL} AND r.deleted_at=0 AND {UI_PRIORITY_MARKER_SQL} "
            "WHERE i.deleted_at=0 AND i.case_type=:ui_case_type AND r.project_id=:project_id AND r.status='valid' "
            "ORDER BY r.file_title ASC, i.id ASC, r.id ASC"
        ),
        {"project_id": project_id, "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    grouped = {}
    for row in rows.mappings().all():
        file_id = int(row["file_id"])
        grouped.setdefault(file_id, {
            "file_id": file_id,
            "file_title": row["file_title"],
            "ui_case_count": 0,
            "nodes": [],
        })
        grouped[file_id]["ui_case_count"] += 1
        grouped[file_id]["nodes"].append({
            "id": int(row["id"]),
            "case_ref_id": int(row["id"]),
            "functional_case_item_id": int(row.get("functional_case_item_id") or 0),
            "case_uid": row.get("case_uid"),
            "case_type": row.get("case_type") or FUNCTIONAL_CASE_TYPE_UI,
            "node_title": row["node_title"],
            "node_path": row["node_path"],
            "step_count": int(row["step_count"] or 0),
            "assert_count": int(row["assert_count"] or 0),
        })
    return ArgusResponse.success(list(grouped.values()))


@router.get("/plan/list")
async def list_ui_test_plans(project_id: int = 0, keyword: str = "", status: str = "", follow: bool = None,
                             page: int = 1, size: int = 20, paged: bool = False,
                             session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    page, size, offset = _clamp_pagination(page, size, 200)
    sql = (
        "SELECT p.id, p.project_id, p.name, p.description, p.env_name, p.base_url, p.browser, p.headless, "
        "p.ordered, p.cron, p.retry_times, p.status, p.created_at, "
        "CASE WHEN MAX(CASE WHEN f.user_id IS NOT NULL AND f.deleted_at=0 THEN 1 ELSE 0 END) > 0 THEN 1 ELSE 0 END AS follow, "
        "COUNT(pc.id) AS case_count "
        "FROM argus_ui_test_plan p "
        "LEFT JOIN argus_ui_test_plan_case pc ON p.id=pc.plan_id AND pc.deleted_at=0 "
        "LEFT JOIN argus_ui_test_plan_follow_user_rel f ON p.id=f.plan_id AND f.user_id=:user_id "
        "WHERE p.deleted_at=0 "
    )
    params = {"user_id": int(user_info["id"])}
    if int(project_id or 0) > 0:
        sql += "AND p.project_id=:project_id "
        params["project_id"] = int(project_id)
    normalized_status = str(status or "").strip()
    if normalized_status:
        sql += "AND p.status=:status "
        params["status"] = normalized_status
    normalized_keyword = str(keyword or "").strip()
    if normalized_keyword:
        sql += "AND (p.name LIKE :like_keyword OR p.description LIKE :like_keyword OR p.env_name LIKE :like_keyword) "
        params["like_keyword"] = f"%{normalized_keyword}%"
    sql += "GROUP BY p.id "
    if follow is True:
        sql += "HAVING follow=1 "
    elif follow is False:
        sql += "HAVING follow=0 "
    sql += "ORDER BY p.updated_at DESC, p.id DESC"
    if paged:
        sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = size
        params["offset"] = offset
    rows = await session.execute(text(sql), params)
    items = [dict(row) for row in rows.mappings().all()]
    # enrich with scheduler state
    try:
        for item in items:
            job = Scheduler.scheduler.get_job(f"ui_test_plan_{item['id']}")
            if job is None:
                item['state'] = 2
            elif job.next_run_time is None:
                item['state'] = 3
            else:
                item['state'] = 1
                item['next_run'] = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
    if not paged:
        return ArgusResponse.success(items)
    count_sql = (
        "SELECT COUNT(1) AS total FROM ("
        "SELECT p.id, CASE WHEN MAX(CASE WHEN f.user_id IS NOT NULL AND f.deleted_at=0 THEN 1 ELSE 0 END) > 0 THEN 1 ELSE 0 END AS follow "
        "FROM argus_ui_test_plan p "
        "LEFT JOIN argus_ui_test_plan_follow_user_rel f ON p.id=f.plan_id AND f.user_id=:user_id "
        "WHERE p.deleted_at=0 "
    )
    count_params = {"user_id": int(user_info["id"])}
    if int(project_id or 0) > 0:
        count_sql += "AND p.project_id=:project_id "
        count_params["project_id"] = int(project_id)
    if normalized_status:
        count_sql += "AND p.status=:status "
        count_params["status"] = normalized_status
    if normalized_keyword:
        count_sql += "AND (p.name LIKE :like_keyword OR p.description LIKE :like_keyword OR p.env_name LIKE :like_keyword) "
        count_params["like_keyword"] = f"%{normalized_keyword}%"
    count_sql += "GROUP BY p.id "
    if follow is True:
        count_sql += "HAVING follow=1 "
    elif follow is False:
        count_sql += "HAVING follow=0 "
    count_sql += ") t"
    count_row = await session.execute(text(count_sql), count_params)
    total = int((count_row.mappings().first() or {}).get("total") or 0)
    return ArgusResponse.success(_paged_payload(items, total, page, size))


@router.get("/plan/detail")
async def get_ui_test_plan_detail(id: int, session=Depends(get_session), _=Depends(Permission())):
    await ensure_ui_test_schema(session)
    plan_row = await session.execute(
        text("SELECT * FROM argus_ui_test_plan WHERE deleted_at=0 AND id=:id"),
        {"id": id},
    )
    plan = plan_row.mappings().first()
    if not plan:
        return ArgusResponse.failed("UI测试计划不存在")
    case_rows = await session.execute(
        text(
            "SELECT pc.id, pc.case_ref_id, pc.sort_index, pc.enabled, r.file_title, r.node_title, r.node_path, r.status "
            "FROM argus_ui_test_plan_case pc "
            "LEFT JOIN argus_ui_test_case_ref r ON pc.case_ref_id=r.id "
            "WHERE pc.deleted_at=0 AND pc.plan_id=:plan_id ORDER BY pc.sort_index ASC, pc.id ASC"
        ),
        {"plan_id": id},
    )
    data = dict(plan)
    data["runner_config"] = _parse_json_text(data.get("runner_config")) or {}
    data["cases"] = [dict(row) for row in case_rows.mappings().all()]
    # enrich with scheduler state
    try:
        job = Scheduler.scheduler.get_job(f"ui_test_plan_{data['id']}")
        if job is None:
            data["state"] = 2
        elif job.next_run_time is None:
            data["state"] = 3
        else:
            data["state"] = 1
            data["next_run"] = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return ArgusResponse.success(data)


@router.post("/plan/save")
async def save_ui_test_plan(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    await ensure_ui_test_gateway_schema(session)
    payload = await request.json()
    plan_id = int(payload.get("id") or 0)
    project_id = int(payload.get("project_id") or 0)
    name = str(payload.get("name") or "").strip()
    if project_id <= 0:
        return ArgusResponse.failed("project_id不能为空")
    if not name:
        return ArgusResponse.failed("计划名称不能为空")
    selected_case_ref_ids = [int(item) for item in (payload.get("selected_case_ref_ids") or []) if int(item or 0) > 0]
    if not selected_case_ref_ids:
        return ArgusResponse.failed("请至少选择一个UI自动化用例")
    await _sync_ui_case_refs_by_project(session, project_id, int(user_info["id"]))
    valid_rows = await session.execute(
        text(
            "SELECT r.id FROM argus_ui_test_case_ref r "
            f"INNER JOIN argus_functional_case_item i ON i.file_id=r.file_id AND {UI_CASE_PATH_JOIN_SQL} "
            f"WHERE r.deleted_at=0 AND i.deleted_at=0 AND i.case_type=:ui_case_type AND r.project_id=:project_id AND {UI_PRIORITY_MARKER_SQL} AND r.status='valid' AND r.id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"project_id": project_id, "ids": list(set(selected_case_ref_ids)), "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    valid_ids = {int(row["id"]) for row in valid_rows.mappings().all()}
    if len(valid_ids) != len(set(selected_case_ref_ids)):
        return ArgusResponse.failed("包含不可执行或已失效的UI自动化用例")

    now_dt = datetime.now()
    env_id = int(payload.get("env_id") or 0)
    address_id = int(payload.get("address_id") or 0)
    env_name = str(payload.get("env_name") or "").strip()
    address_name = ""
    page_url = ""
    resolved_base_url = str(payload.get("base_url") or "").strip()
    if env_id > 0:
        env_row = await session.execute(
            text("SELECT id, name FROM argus_environment WHERE deleted_at=0 AND id=:id"),
            {"id": env_id},
        )
        env_data = env_row.mappings().first()
        if not env_data:
            return ArgusResponse.failed("所选环境不存在")
        env_name = str(env_data.get("name") or "").strip()
    if address_id > 0:
        gateway_row = await session.execute(
            text("SELECT id, env, name, gateway, page_url FROM argus_gateway WHERE deleted_at=0 AND id=:id"),
            {"id": address_id},
        )
        gateway_data = gateway_row.mappings().first()
        if not gateway_data:
            return ArgusResponse.failed("所选地址前缀不存在")
        if env_id > 0 and int(gateway_data.get("env") or 0) != env_id:
            return ArgusResponse.failed("地址前缀与所选环境不匹配")
        if env_id <= 0:
            env_id = int(gateway_data.get("env") or 0)
            env_row = await session.execute(
                text("SELECT id, name FROM argus_environment WHERE deleted_at=0 AND id=:id"),
                {"id": env_id},
            )
            env_data = env_row.mappings().first()
            env_name = str((env_data or {}).get("name") or "").strip()
        address_name = str(gateway_data.get("name") or "").strip()
        page_url = str(gateway_data.get("page_url") or "").strip()
        resolved_base_url = _compose_plan_base_url(gateway_data.get("gateway"), page_url)

    ai_model_id = str(payload.get("ai_model_id") or "").strip()
    ai_model_config = await GConfigDao.get_ai_model_config()
    providers = ai_model_config.get("providers") if isinstance(ai_model_config, dict) else []
    selected_model = next(
        (item for item in (providers or []) if str(item.get("id") or "").strip() == ai_model_id),
        None,
    )
    if ai_model_id and not selected_model:
        return ArgusResponse.failed("所选AI模型不存在或未启用")
    runner_config = json.dumps({
        "ai_model_id": ai_model_id,
        "env_id": env_id,
        "env_name": env_name,
        "address_id": address_id,
        "address_name": address_name,
        "page_url": page_url,
        "base_url": resolved_base_url,
        "midscene_provider": str((selected_model or {}).get("provider_type") or payload.get("midscene_provider") or "").strip(),
        "analysis_provider": str((selected_model or {}).get("provider_type") or payload.get("analysis_provider") or "").strip(),
        "record_video": _normalize_bool(payload.get("record_video"), True),
        "record_trace": _normalize_bool(payload.get("record_trace"), True),
        "capture_screenshot": _normalize_bool(payload.get("capture_screenshot"), True),
    }, ensure_ascii=False)

    if plan_id > 0:
        plan_row = await session.execute(
            text("SELECT id FROM argus_ui_test_plan WHERE deleted_at=0 AND id=:id"),
            {"id": plan_id},
        )
        if not plan_row.first():
            return ArgusResponse.failed("UI测试计划不存在")
        await session.execute(
            text(
                "UPDATE argus_ui_test_plan SET project_id=:project_id, name=:name, description=:description, "
                "env_name=:env_name, base_url=:base_url, browser=:browser, headless=:headless, ordered=:ordered, "
                "cron=:cron, retry_times=:retry_times, status=:status, runner_config=:runner_config, "
                "receiver=:receiver, msg_type=:msg_type, pass_rate=:pass_rate, notification_config_id=:notification_config_id, "
                "update_user=:update_user, updated_at=:updated_at WHERE id=:id"
            ),
            {
                "id": plan_id,
                "project_id": project_id,
                "name": name,
                "description": str(payload.get("description") or "").strip(),
                "env_name": env_name,
                "base_url": resolved_base_url,
                "browser": str(payload.get("browser") or "chromium").strip() or "chromium",
                "headless": 1 if _normalize_bool(payload.get("headless"), True) else 0,
                "ordered": 1 if _normalize_bool(payload.get("ordered"), False) else 0,
                "cron": str(payload.get("cron") or "").strip(),
                "retry_times": int(payload.get("retry_times") or 0),
                "status": str(payload.get("status") or "enabled").strip() or "enabled",
                "runner_config": runner_config,
                "receiver": ",".join(str(x) for x in (payload.get("receiver") or []) if str(x).strip().isdigit()),
                "msg_type": ",".join(str(x) for x in (payload.get("msg_type") or []) if str(x).strip().isdigit()),
                "pass_rate": _normalize_optional_pass_rate(payload.get("pass_rate"), 0),
                "notification_config_id": int(payload.get("notification_config_id") or 0) or None,
                "update_user": int(user_info["id"]),
                "updated_at": now_dt,
            },
        )
        await session.execute(
            text(
                "UPDATE argus_ui_test_plan_case SET deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at "
                "WHERE plan_id=:plan_id AND deleted_at=0"
            ),
            {
                "deleted_at": int(now_dt.timestamp()),
                "update_user": int(user_info["id"]),
                "updated_at": now_dt,
                "plan_id": plan_id,
            },
        )
    else:
        insert_result = await session.execute(
            text(
                "INSERT INTO argus_ui_test_plan "
                "(created_at, updated_at, deleted_at, create_user, update_user, project_id, name, description, env_name, base_url, browser, headless, ordered, cron, retry_times, status, runner_config, receiver, msg_type, pass_rate, notification_config_id) "
                "VALUES "
                "(:created_at, :updated_at, 0, :create_user, :update_user, :project_id, :name, :description, :env_name, :base_url, :browser, :headless, :ordered, :cron, :retry_times, :status, :runner_config, :receiver, :msg_type, :pass_rate, :notification_config_id)"
            ),
            {
                "created_at": now_dt,
                "updated_at": now_dt,
                "create_user": int(user_info["id"]),
                "update_user": int(user_info["id"]),
                "project_id": project_id,
                "name": name,
                "description": str(payload.get("description") or "").strip(),
                "env_name": env_name,
                "base_url": resolved_base_url,
                "browser": str(payload.get("browser") or "chromium").strip() or "chromium",
                "headless": 1 if _normalize_bool(payload.get("headless"), True) else 0,
                "ordered": 1 if _normalize_bool(payload.get("ordered"), False) else 0,
                "cron": str(payload.get("cron") or "").strip(),
                "retry_times": int(payload.get("retry_times") or 0),
                "status": str(payload.get("status") or "enabled").strip() or "enabled",
                "runner_config": runner_config,
                "receiver": ",".join(str(x) for x in (payload.get("receiver") or []) if str(x).strip().isdigit()),
                "msg_type": ",".join(str(x) for x in (payload.get("msg_type") or []) if str(x).strip().isdigit()),
                "pass_rate": _normalize_optional_pass_rate(payload.get("pass_rate"), 0),
                "notification_config_id": int(payload.get("notification_config_id") or 0) or None,
            },
        )
        plan_id = int(insert_result.lastrowid or 0)

    relation_rows = []
    for index, case_ref_id in enumerate(selected_case_ref_ids):
        relation_rows.append({
            "created_at": now_dt,
            "updated_at": now_dt,
            "create_user": int(user_info["id"]),
            "update_user": int(user_info["id"]),
            "plan_id": plan_id,
            "case_ref_id": case_ref_id,
            "sort_index": index,
        })
    await session.execute(
        text(
            "INSERT INTO argus_ui_test_plan_case "
            "(created_at, updated_at, deleted_at, create_user, update_user, plan_id, case_ref_id, sort_index, enabled) "
            "VALUES "
            "(:created_at, :updated_at, 0, :create_user, :update_user, :plan_id, :case_ref_id, :sort_index, 1)"
        ),
        relation_rows,
    )
    await session.commit()
    _sync_ui_plan_scheduler(
        plan_id,
        name,
        str(payload.get("cron") or "").strip(),
        str(payload.get("status") or "enabled").strip() == "enabled",
    )
    return ArgusResponse.success({"id": plan_id})


@router.post("/plan/run")
async def run_ui_test_plan(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    await ensure_ui_functional_case_item_schema(session)
    payload = await request.json()
    plan_id = int(payload.get("id") or 0)
    if plan_id <= 0:
        return ArgusResponse.failed("计划ID不能为空")
    plan_row = await session.execute(
        text("SELECT * FROM argus_ui_test_plan WHERE deleted_at=0 AND id=:id"),
        {"id": plan_id},
    )
    plan = plan_row.mappings().first()
    if not plan:
        return ArgusResponse.failed("UI测试计划不存在")
    await _sync_ui_case_refs_by_project(session, int(plan["project_id"] or 0), int(user_info["id"]))
    case_rows = await session.execute(
        text(
            "SELECT pc.case_ref_id, r.file_title, r.node_title, r.node_path, r.dsl_json "
            "FROM argus_ui_test_plan_case pc "
            "LEFT JOIN argus_ui_test_case_ref r ON pc.case_ref_id=r.id "
            f"INNER JOIN argus_functional_case_item i ON i.file_id=r.file_id AND {UI_CASE_PATH_JOIN_SQL} "
            f"WHERE pc.deleted_at=0 AND pc.plan_id=:plan_id AND pc.enabled=1 AND r.deleted_at=0 AND i.deleted_at=0 AND i.case_type=:ui_case_type AND {UI_PRIORITY_MARKER_SQL} AND r.status='valid' "
            "ORDER BY pc.sort_index ASC, pc.id ASC"
        ),
        {"plan_id": plan_id, "ui_case_type": FUNCTIONAL_CASE_TYPE_UI},
    )
    cases = case_rows.mappings().all()
    if not cases:
        return ArgusResponse.failed("该计划没有可执行的UI自动化用例")

    run_id = await _create_ui_plan_run(
        session,
        dict(plan),
        cases,
        create_user_id=int(user_info["id"]),
        trigger_mode=str(payload.get("trigger_mode") or "manual"),
    )
    if not run_id:
        await session.rollback()
        return ArgusResponse.failed("该计划的UI自动化用例缺少可执行步骤，请重新扫描或校验用例")
    await session.commit()
    ai_model = await GConfigDao.get_active_ai_model_config()
    bootstrap = _build_runner_bootstrap_payload(
        request,
        user_info,
        int(plan["project_id"] or 0),
        [run_id] if run_id else [],
        plan_id,
        ai_model,
    )
    _write_runner_bootstrap_file(bootstrap)
    platform_task = await PlatformTaskService.create_task(
        task_type=PlatformTaskType.UI_TEST_RUN.value,
        user_id=int(user_info["id"]),
        biz_id=run_id,
        biz_type="ui_test_run",
        project_id=int(plan["project_id"] or 0),
        plan_id=plan_id,
        resource_key=f"ui_plan_{plan_id}",
        payload={"plan_id": plan_id, "run_id": run_id, "executor": int(user_info["id"])},
    )
    return ArgusResponse.success({
        "run_ids": [run_id] if run_id else [],
        "platform_task_id": int(platform_task.id or 0),
        "bucket": UI_BUCKET_NAME,
        "object_prefix": UI_OBJECT_PREFIX,
        "note": "已生成计划级 UI 测试执行记录与对象存储路径，Runner 接入后可直接消费该批次任务。",
        "runner_bootstrap": bootstrap,
    })


def _serialize_sse_event(event, data, event_id=None):
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event:
        lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False, default=str)
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def _serialize_sse_comment(comment="keepalive"):
    return f": {comment}\n\n"


async def _query_ui_test_runs_payload(
        session,
        user_info,
        project_id: int = 0,
        plan_id: int = 0,
        case_ref_id: int = 0,
        executor_id: int = 0,
        env_name: str = "",
        scope: str = "report",
        source: str = "",
        status: str = "",
        keyword: str = "",
        started_at_start: str = "",
        started_at_end: str = "",
        page: int = 1,
        size: int = 20,
        paged: bool = False,
):
    normalized_scope = str(scope or "report").strip().lower()
    page, size, offset = _clamp_pagination(page, size, 200)
    base_from = (
        "FROM argus_ui_test_run r "
        "LEFT JOIN argus_ui_test_plan p ON r.plan_id=p.id "
        "LEFT JOIN argus_ui_test_case_ref c ON r.case_ref_id=c.id "
        "LEFT JOIN argus_user u ON r.create_user=u.id "
    )
    where_sql = "WHERE r.deleted_at=0 "
    params = {}
    if int(project_id or 0) > 0:
        where_sql += "AND r.project_id=:project_id "
        params["project_id"] = int(project_id)
    if int(plan_id or 0) > 0:
        where_sql += "AND r.plan_id=:plan_id "
        params["plan_id"] = int(plan_id)
    if int(case_ref_id or 0) > 0:
        where_sql += "AND r.case_ref_id=:case_ref_id "
        params["case_ref_id"] = int(case_ref_id)
    if int(executor_id or 0) > 0:
        where_sql += "AND r.create_user=:executor_id "
        params["executor_id"] = int(executor_id)
    normalized_source = str(source or "").strip().lower()
    if normalized_scope == "debug":
        where_sql += "AND r.trigger_mode='trial' AND r.create_user=:create_user "
        params["create_user"] = int(user_info["id"])
    elif normalized_scope == "all":
        pass
    elif normalized_source != "trial":
        where_sql += "AND r.trigger_mode<>'trial' "
    if normalized_source == "trial":
        where_sql += "AND r.trigger_mode='trial' "
    elif normalized_source == "formal":
        where_sql += "AND r.trigger_mode<>'trial' "
    normalized_env_name = str(env_name or "").strip()
    if normalized_env_name:
        where_sql += "AND (p.env_name=:env_name OR r.runner_payload LIKE :env_name_like) "
        params["env_name"] = normalized_env_name
        params["env_name_like"] = f'%"env_name": "{normalized_env_name}"%'
    normalized_status = str(status or "").strip().lower()
    if normalized_status:
        if normalized_status == "running":
            where_sql += "AND r.status IN ('claimed', 'running', 'uploading') "
        else:
            where_sql += "AND r.status=:status "
            params["status"] = normalized_status
    normalized_started_at_start = str(started_at_start or "").strip()
    if normalized_started_at_start:
        where_sql += "AND COALESCE(r.started_at, r.created_at) >= :started_at_start "
        params["started_at_start"] = normalized_started_at_start
    normalized_started_at_end = str(started_at_end or "").strip()
    if normalized_started_at_end:
        where_sql += "AND COALESCE(r.started_at, r.created_at) <= :started_at_end "
        params["started_at_end"] = normalized_started_at_end
    normalized_keyword = str(keyword or "").strip()
    if normalized_keyword:
        where_sql += (
            "AND (CAST(r.id AS CHAR) LIKE :like_keyword OR r.run_name LIKE :like_keyword "
            "OR p.name LIKE :like_keyword OR c.file_title LIKE :like_keyword "
            "OR c.node_title LIKE :like_keyword OR c.node_path LIKE :like_keyword) "
        )
        params["like_keyword"] = f"%{normalized_keyword}%"

    select_sql = (
        "SELECT r.id, r.project_id, r.plan_id, r.case_ref_id, r.create_user, r.run_name, r.status, r.trigger_mode, "
        "r.browser, r.headless, r.error_message, r.created_at, r.started_at, r.finished_at, "
        "p.name AS plan_name, p.env_name AS plan_env_name, c.file_title, c.node_title, c.node_path, "
        "u.name AS executor_name, r.runner_payload, r.result_payload "
    )
    sql = f"{select_sql}{base_from}{where_sql}ORDER BY r.id DESC"
    if paged:
        sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = size
        params["offset"] = offset
    rows = await session.execute(text(sql), params)
    items = []
    for row in rows.mappings().all():
        item = dict(row)
        runner_payload = _parse_json_text(item.get("runner_payload")) or {}
        result_payload = _parse_json_text(item.get("result_payload")) or {}
        item["env_name"] = str(item.get("plan_env_name") or runner_payload.get("env_name") or "").strip()
        item["address_name"] = str(runner_payload.get("address_name") or "").strip()
        item.update(_extract_ui_run_counts(result_payload, item.get("status")))
        item.pop("runner_payload", None)
        item.pop("result_payload", None)
        item.pop("plan_env_name", None)
        items.append(item)
    if not paged:
        return items

    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    count_row = await session.execute(text(f"SELECT COUNT(1) AS total {base_from}{where_sql}"), count_params)
    total = int((count_row.mappings().first() or {}).get("total") or 0)
    return _paged_payload(items, total, page, size)


async def _query_ui_test_run_detail_payload(
        session,
        request: Request,
        id: int,
        include_payload: bool = True,
        include_artifacts: bool = True,
        include_step_payload: bool = True,
        include_step_artifacts: bool = True,
):
    run_payload_columns = ", r.runner_payload, r.result_payload" if include_payload else ""
    run_row = await session.execute(
        text(
            "SELECT r.id, r.created_at, r.updated_at, r.create_user, r.project_id, r.plan_id, r.case_ref_id, "
            "r.run_name, r.status, r.trigger_mode, r.browser, r.headless, r.artifact_bucket, r.artifact_prefix, "
            "r.screenshot_dir, r.video_path, r.trace_path, r.report_path, r.result_json_path, "
            "r.error_message, r.started_at, r.finished_at, "
            "p.name AS plan_name, p.env_name AS plan_env_name, pr.name AS project_name, "
            "u.name AS executor_name, c.file_title, c.node_title, c.node_path "
            f"{run_payload_columns} "
            "FROM argus_ui_test_run r "
            "LEFT JOIN argus_ui_test_plan p ON r.plan_id=p.id "
            "LEFT JOIN argus_project pr ON r.project_id=pr.id "
            "LEFT JOIN argus_ui_test_case_ref c ON r.case_ref_id=c.id "
            "LEFT JOIN argus_user u ON r.create_user=u.id "
            "WHERE r.deleted_at=0 AND r.id=:id"
        ),
        {"id": id},
    )
    run = run_row.mappings().first()
    if not run:
        return None
    step_payload_columns = ", request_payload, result_payload" if include_step_payload else ""
    step_rows = await session.execute(
        text(
            "SELECT id, step_index, step_name, step_type, status, screenshot_path, error_message, duration_ms "
            f"{step_payload_columns} "
            "FROM argus_ui_test_step_result WHERE deleted_at=0 AND run_id=:run_id ORDER BY step_index ASC, id ASC"
        ),
        {"run_id": id},
    )
    data = dict(run)
    if include_payload:
        data["runner_payload"] = _parse_json_text(data.get("runner_payload")) or {}
        data["result_payload"] = _parse_json_text(data.get("result_payload")) or {}
    else:
        data["runner_payload"] = {}
        data["result_payload"] = {}
    data["env_name"] = str(data.get("plan_env_name") or data["runner_payload"].get("env_name") or "").strip()
    data["address_name"] = str(data["runner_payload"].get("address_name") or "").strip()
    data.pop("plan_env_name", None)
    client = None
    if include_artifacts or include_step_artifacts:
        try:
            client = OssClient.get_oss_client()
        except Exception:
            client = None
    steps = []
    for row in step_rows.mappings().all():
        item = dict(row)
        if include_step_payload:
            item["request_payload"] = _parse_json_text(item.get("request_payload")) or item.get("request_payload") or ""
            item["result_payload"] = _parse_json_text(item.get("result_payload")) or item.get("result_payload") or ""
        else:
            item["request_payload"] = ""
            item["result_payload"] = ""
        if include_step_artifacts and client and item.get("screenshot_path"):
            item["screenshot_artifact"] = await _build_artifact_descriptor(
                client,
                str(data.get("artifact_bucket") or UI_BUCKET_NAME or ""),
                str(item.get("screenshot_path") or ""),
                f"步骤{int(item.get('step_index') or 0)}截图",
                proxy_url=_build_ui_artifact_proxy_url(
                    str(item.get("screenshot_path") or ""),
                    str(data.get("artifact_bucket") or UI_BUCKET_NAME or ""),
                    request=request,
                ),
            )
        else:
            item["screenshot_artifact"] = None
        steps.append(item)
    data["steps"] = steps
    data["analysis_summary"] = _build_run_analysis(data, steps)
    artifact_bucket = str(data.get("artifact_bucket") or UI_BUCKET_NAME or "")
    if include_artifacts and client:
        data["artifacts"] = [item for item in [
            await _build_artifact_descriptor(
                client,
                artifact_bucket,
                str(data.get("report_path") or ""),
                "执行报告",
                proxy_url=_build_ui_artifact_proxy_url(
                    str(data.get("report_path") or ""),
                    artifact_bucket,
                    request=request,
                ),
            ),
            await _build_artifact_descriptor(
                client,
                artifact_bucket,
                str(data.get("video_path") or ""),
                "录屏",
                proxy_url=_build_ui_artifact_proxy_url(
                    str(data.get("video_path") or ""),
                    artifact_bucket,
                    request=request,
                ),
            ),
            await _build_artifact_descriptor(
                client,
                artifact_bucket,
                str(data.get("result_json_path") or ""),
                "结果JSON",
                proxy_url=_build_ui_artifact_proxy_url(
                    str(data.get("result_json_path") or ""),
                    artifact_bucket,
                    request=request,
                ),
            ),
        ] if item]
    else:
        data["artifacts"] = []
    return data


def _resolve_ui_debug_focus_run_id(runs, focus_run_id=0):
    expected_id = int(focus_run_id or 0)
    if expected_id > 0 and any(int(item.get("id") or 0) == expected_id for item in runs):
        return expected_id
    for item in runs:
        if str(item.get("status") or "").strip().lower() in UI_RUN_ACTIVE_STATUSES:
            return int(item.get("id") or 0)
    if runs:
        return int(runs[0].get("id") or 0)
    return 0


async def _build_ui_debug_stream_payload(
        session,
        request: Request,
        user_info,
        project_id: int,
        case_ref_id: int,
        focus_run_id: int = 0,
        include_payload: bool = True,
        include_artifacts: bool = True,
        include_step_payload: bool = False,
        include_step_artifacts: bool = True,
):
    runs = await _query_ui_test_runs_payload(
        session=session,
        user_info=user_info,
        project_id=project_id,
        case_ref_id=case_ref_id,
        scope="debug",
        page=1,
        size=100,
        paged=False,
    )
    active_run_id = _resolve_ui_debug_focus_run_id(runs, focus_run_id=focus_run_id)
    detail = None
    if active_run_id > 0:
        detail = await _query_ui_test_run_detail_payload(
            session=session,
            request=request,
            id=active_run_id,
            include_payload=include_payload,
            include_artifacts=include_artifacts,
            include_step_payload=include_step_payload,
            include_step_artifacts=include_step_artifacts,
        )
    has_active_run = any(str(item.get("status") or "").strip().lower() in UI_RUN_ACTIVE_STATUSES for item in runs)
    return {
        "runs": runs,
        "detail": detail,
        "active_run_id": active_run_id or (detail or {}).get("id") or 0,
        "done": bool(runs) and not has_active_run,
    }


@router.get("/run/list")
async def list_ui_test_runs(
        project_id: int = 0,
        plan_id: int = 0,
        case_ref_id: int = 0,
        executor_id: int = 0,
        env_name: str = "",
        scope: str = "report",
        source: str = "",
        status: str = "",
        keyword: str = "",
        started_at_start: str = "",
        started_at_end: str = "",
        page: int = 1,
        size: int = 20,
        paged: bool = False,
        session=Depends(get_session),
        user_info=Depends(Permission()),
):
    await ensure_ui_test_schema(session)
    data = await _query_ui_test_runs_payload(
        session=session,
        user_info=user_info,
        project_id=project_id,
        plan_id=plan_id,
        case_ref_id=case_ref_id,
        executor_id=executor_id,
        env_name=env_name,
        scope=scope,
        source=source,
        status=status,
        keyword=keyword,
        started_at_start=started_at_start,
        started_at_end=started_at_end,
        page=page,
        size=size,
        paged=paged,
    )
    return ArgusResponse.success(data)


@router.get("/run/detail")
async def get_ui_test_run_detail(
        id: int,
        include_payload: bool = True,
        include_artifacts: bool = True,
        include_step_payload: bool = True,
        include_step_artifacts: bool = True,
        request: Request = None,
        session=Depends(get_session),
        _=Depends(Permission()),
):
    await ensure_ui_test_schema(session)
    data = await _query_ui_test_run_detail_payload(
        session=session,
        request=request,
        id=id,
        include_payload=include_payload,
        include_artifacts=include_artifacts,
        include_step_payload=include_step_payload,
        include_step_artifacts=include_step_artifacts,
    )
    if not data:
        return ArgusResponse.failed("UI测试执行记录不存在")
    return ArgusResponse.success(data)


@router.get("/run/share-detail")
async def get_ui_test_shared_run_detail(
    id: int,
    include_payload: bool = True,
    include_artifacts: bool = True,
    include_step_payload: bool = True,
    include_step_artifacts: bool = True,
    request: Request = None,
    session=Depends(get_session),
):
    """公开分享接口，无需鉴权"""
    await ensure_ui_test_schema(session)
    data = await _query_ui_test_run_detail_payload(
        session=session,
        request=request,
        id=id,
        include_payload=include_payload,
        include_artifacts=include_artifacts,
        include_step_payload=include_step_payload,
        include_step_artifacts=include_step_artifacts,
    )
    if not data:
        return ArgusResponse.failed("UI测试执行记录不存在")
    return ArgusResponse.success(data)


@router.get("/run/stream")
async def stream_ui_test_run_detail(
        id: int,
        include_payload: bool = True,
        include_artifacts: bool = True,
        include_step_payload: bool = True,
        include_step_artifacts: bool = True,
        request: Request = None,
        _=Depends(Permission()),
):
    async def event_generator():
        event_id = 0
        last_payload = ""
        last_keepalive_at = 0.0
        done_sent = False
        while True:
            if await request.is_disconnected():
                break
            async with async_session() as stream_session:
                await ensure_ui_test_schema(stream_session)
                data = await _query_ui_test_run_detail_payload(
                    session=stream_session,
                    request=request,
                    id=id,
                    include_payload=include_payload,
                    include_artifacts=include_artifacts,
                    include_step_payload=include_step_payload,
                    include_step_artifacts=include_step_artifacts,
                )
            if not data:
                event_id += 1
                yield _serialize_sse_event("error", {"message": "UI测试执行记录不存在", "done": True}, event_id)
                break
            done = str(data.get("status") or "").strip().lower() in UI_RUN_TERMINAL_STATUSES
            payload = {
                "run": data,
                "run_id": int(data.get("id") or 0),
                "done": done,
            }
            payload_text = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
            now = time.monotonic()
            if payload_text != last_payload:
                event_id += 1
                last_payload = payload_text
                last_keepalive_at = now
                yield _serialize_sse_event("snapshot", payload, event_id)
            elif now - last_keepalive_at >= UI_STREAM_KEEPALIVE_INTERVAL:
                last_keepalive_at = now
                yield _serialize_sse_comment("run-stream-keepalive")
            if done and not done_sent:
                done_sent = True
                event_id += 1
                yield _serialize_sse_event("done", {"run_id": int(data.get("id") or 0), "done": True}, event_id)
                break
            await asyncio.sleep(UI_STREAM_POLL_INTERVAL)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=UI_STREAM_HEADERS)


@router.get("/run/share-stream")
async def stream_ui_test_shared_run_detail(
        id: int,
        include_payload: bool = True,
        include_artifacts: bool = True,
        include_step_payload: bool = True,
        include_step_artifacts: bool = True,
        request: Request = None,
):
    async def event_generator():
        event_id = 0
        last_payload = ""
        last_keepalive_at = 0.0
        done_sent = False
        while True:
            if await request.is_disconnected():
                break
            async with async_session() as stream_session:
                await ensure_ui_test_schema(stream_session)
                data = await _query_ui_test_run_detail_payload(
                    session=stream_session,
                    request=request,
                    id=id,
                    include_payload=include_payload,
                    include_artifacts=include_artifacts,
                    include_step_payload=include_step_payload,
                    include_step_artifacts=include_step_artifacts,
                )
            if not data:
                event_id += 1
                yield _serialize_sse_event("error", {"message": "UI测试执行记录不存在", "done": True}, event_id)
                break
            done = str(data.get("status") or "").strip().lower() in UI_RUN_TERMINAL_STATUSES
            payload = {
                "run": data,
                "run_id": int(data.get("id") or 0),
                "done": done,
            }
            payload_text = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
            now = time.monotonic()
            if payload_text != last_payload:
                event_id += 1
                last_payload = payload_text
                last_keepalive_at = now
                yield _serialize_sse_event("snapshot", payload, event_id)
            elif now - last_keepalive_at >= UI_STREAM_KEEPALIVE_INTERVAL:
                last_keepalive_at = now
                yield _serialize_sse_comment("shared-run-stream-keepalive")
            if done and not done_sent:
                done_sent = True
                event_id += 1
                yield _serialize_sse_event("done", {"run_id": int(data.get("id") or 0), "done": True}, event_id)
                break
            await asyncio.sleep(UI_STREAM_POLL_INTERVAL)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=UI_STREAM_HEADERS)


@router.get("/run/debug-stream")
async def stream_ui_test_debug_runs(
        project_id: int,
        case_ref_id: int,
        focus_run_id: int = 0,
        include_payload: bool = True,
        include_artifacts: bool = True,
        include_step_payload: bool = False,
        include_step_artifacts: bool = True,
        request: Request = None,
        user_info=Depends(Permission()),
):
    async def event_generator():
        event_id = 0
        last_payload = ""
        last_keepalive_at = 0.0
        while True:
            if await request.is_disconnected():
                break
            async with async_session() as stream_session:
                await ensure_ui_test_schema(stream_session)
                payload = await _build_ui_debug_stream_payload(
                    session=stream_session,
                    request=request,
                    user_info=user_info,
                    project_id=int(project_id or 0),
                    case_ref_id=int(case_ref_id or 0),
                    focus_run_id=int(focus_run_id or 0),
                    include_payload=include_payload,
                    include_artifacts=include_artifacts,
                    include_step_payload=include_step_payload,
                    include_step_artifacts=include_step_artifacts,
                )
            payload_text = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
            now = time.monotonic()
            if payload_text != last_payload:
                event_id += 1
                last_payload = payload_text
                last_keepalive_at = now
                yield _serialize_sse_event("snapshot", payload, event_id)
            elif now - last_keepalive_at >= UI_STREAM_KEEPALIVE_INTERVAL:
                last_keepalive_at = now
                yield _serialize_sse_comment("debug-stream-keepalive")
            await asyncio.sleep(UI_STREAM_POLL_INTERVAL)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=UI_STREAM_HEADERS)


@router.get("/run/step-detail")
async def get_ui_test_run_step_detail(id: int, request: Request, session=Depends(get_session)):
    """步骤详情对分享报告页开放，避免公开链接中的截图预览再次触发鉴权。"""
    await ensure_ui_test_schema(session)
    row = await session.execute(
        text(
            "SELECT id, run_id, step_index, step_name, step_type, status, screenshot_path, request_payload, "
            "result_payload, error_message, duration_ms, created_at, updated_at "
            "FROM argus_ui_test_step_result WHERE deleted_at=0 AND id=:id"
        ),
        {"id": id},
    )
    item = row.mappings().first()
    if not item:
        return ArgusResponse.failed("UI测试步骤结果不存在")
    data = dict(item)
    data["request_payload"] = _parse_json_text(data.get("request_payload")) or data.get("request_payload") or ""
    data["result_payload"] = _parse_json_text(data.get("result_payload")) or data.get("result_payload") or ""
    try:
        client = OssClient.get_oss_client()
    except Exception:
        client = None
    if client and data.get("screenshot_path"):
        run_row = await session.execute(
            text("SELECT artifact_bucket FROM argus_ui_test_run WHERE deleted_at=0 AND id=:id"),
            {"id": int(data.get("run_id") or 0)},
        )
        run = run_row.mappings().first() or {}
        data["screenshot_artifact"] = await _build_artifact_descriptor(
            client,
            str(run.get("artifact_bucket") or UI_BUCKET_NAME or ""),
            str(data.get("screenshot_path") or ""),
            f"步骤{int(data.get('step_index') or 0)}截图",
            proxy_url=_build_ui_artifact_proxy_url(
                str(data.get("screenshot_path") or ""),
                str(run.get("artifact_bucket") or UI_BUCKET_NAME or ""),
                request=request,
            ),
        )
    else:
        data["screenshot_artifact"] = None
    return ArgusResponse.success(data)


@router.get("/run/share-artifact/view")
async def view_ui_test_shared_artifact(object_key: str, bucket_name: str = "", session=Depends(get_session)):
    await ensure_ui_test_schema(session)
    normalized_key = str(object_key or "").replace("\\", "/").strip().strip("/")
    if not normalized_key:
        return ArgusResponse.failed("object_key不能为空")
    bucket_value = str(bucket_name or UI_BUCKET_NAME or "").strip()
    verify_row = await session.execute(
        text(
            "SELECT 1 AS matched FROM argus_ui_test_run "
            "WHERE deleted_at=0 AND (report_path=:object_key OR video_path=:object_key OR result_json_path=:object_key) "
            "UNION ALL "
            "SELECT 1 AS matched FROM argus_ui_test_step_result "
            "WHERE deleted_at=0 AND screenshot_path=:object_key "
            "LIMIT 1"
        ),
        {"object_key": normalized_key},
    )
    if not verify_row.mappings().first():
        return ArgusResponse.failed("文件不存在")
    client = OssClient.get_oss_client()
    detail = await client.get_object_detail(normalized_key, bucket_name=bucket_value or None)
    content = await client.get_file_object(normalized_key, bucket_name=bucket_value or None)
    media_type = str(detail.get("content_type") or "").strip() or "application/octet-stream"
    return Response(content=content, media_type=media_type)


@router.post("/run/stop")
async def stop_ui_test_run(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    payload = await request.json()
    run_id = int(payload.get("id") or payload.get("run_id") or 0)
    if run_id <= 0:
        return ArgusResponse.failed("id不能为空")

    row = await session.execute(
        text("SELECT id, status FROM argus_ui_test_run WHERE deleted_at=0 AND id=:id"),
        {"id": run_id},
    )
    run = row.mappings().first()
    if not run:
        return ArgusResponse.failed("UI测试执行记录不存在")

    current_status = str(run.get("status") or "").strip().lower()
    if current_status in UI_RUN_TERMINAL_STATUSES:
        return ArgusResponse.success({
            "run_id": run_id,
            "status": current_status,
            "stopped": current_status == "cancelled",
            "message": "当前执行记录已结束，无需停止。",
        })
    if current_status not in UI_RUN_ACTIVE_STATUSES:
        return ArgusResponse.failed(f"当前状态不允许停止：{current_status or '-'}")

    now_dt = datetime.now()
    message = str(payload.get("reason") or "用户手动停止UI测试执行").strip()
    result_payload = {
        "status": "cancelled",
        "message": message,
        "stopped_by": int(user_info["id"]),
        "stopped_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
    }
    updated = await session.execute(
        text(
            "UPDATE argus_ui_test_run SET status='cancelled', result_payload=:result_payload, "
            "error_message=:error_message, finished_at=:finished_at, update_user=:update_user, updated_at=:updated_at "
            "WHERE id=:id AND deleted_at=0 AND status IN ('queued', 'claimed', 'running', 'uploading')"
        ),
        {
            "id": run_id,
            "result_payload": json.dumps(result_payload, ensure_ascii=False),
            "error_message": message,
            "finished_at": now_dt,
            "update_user": int(user_info["id"]),
            "updated_at": now_dt,
        },
    )
    if int(updated.rowcount or 0) == 0:
        await session.rollback()
        return ArgusResponse.failed("执行记录状态已变化，请刷新后重试")
    await session.commit()
    return ArgusResponse.success({"run_id": run_id, "status": "cancelled", "stopped": True})


@router.post("/run/retry")
async def retry_ui_test_run(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    payload = await request.json()
    run_id = int(payload.get("id") or 0)
    if run_id <= 0:
        return ArgusResponse.failed("id不能为空")

    row = await session.execute(
        text(
            "SELECT id, project_id, plan_id, case_ref_id, browser, headless, run_name, runner_payload "
            "FROM argus_ui_test_run WHERE deleted_at=0 AND id=:id"
        ),
        {"id": run_id},
    )
    source_run = row.mappings().first()
    if not source_run:
        return ArgusResponse.failed("UI测试执行记录不存在")

    now_dt = datetime.now()
    insert_result = await session.execute(
        text(
            "INSERT INTO argus_ui_test_run "
            "(created_at, updated_at, deleted_at, create_user, update_user, project_id, plan_id, case_ref_id, run_name, status, trigger_mode, browser, headless, artifact_bucket, artifact_prefix, screenshot_dir, video_path, trace_path, report_path, result_json_path, runner_payload, started_at) "
            "VALUES "
            "(:created_at, :updated_at, 0, :create_user, :update_user, :project_id, :plan_id, :case_ref_id, :run_name, 'queued', 'retry', :browser, :headless, :artifact_bucket, '', '', '', '', '', '', :runner_payload, :started_at)"
        ),
        {
            "created_at": now_dt,
            "updated_at": now_dt,
            "create_user": int(user_info["id"]),
            "update_user": int(user_info["id"]),
            "project_id": int(source_run["project_id"] or 0),
            "plan_id": int(source_run["plan_id"] or 0),
            "case_ref_id": int(source_run["case_ref_id"] or 0),
            "run_name": f"{source_run['run_name']} Retry",
            "browser": str(source_run.get("browser") or "chromium"),
            "headless": int(source_run.get("headless") or 1),
            "artifact_bucket": UI_BUCKET_NAME,
            "runner_payload": str(source_run.get("runner_payload") or ""),
            "started_at": now_dt,
        },
    )
    new_run_id = int(insert_result.lastrowid or 0)
    artifact_prefix = f"{UI_OBJECT_PREFIX}/{int(source_run['project_id'] or 0)}/{int(source_run['plan_id'] or 0)}/{new_run_id}"
    await session.execute(
        text(
            "UPDATE argus_ui_test_run SET artifact_prefix=:artifact_prefix, screenshot_dir=:screenshot_dir, "
            "video_path=:video_path, trace_path=:trace_path, report_path=:report_path, result_json_path=:result_json_path "
            "WHERE id=:id"
        ),
        {
            "id": new_run_id,
            "artifact_prefix": artifact_prefix,
            "screenshot_dir": f"{artifact_prefix}/screenshots/",
            "video_path": f"{artifact_prefix}/videos/run.mp4",
            "trace_path": f"{artifact_prefix}/traces/trace.zip",
            "report_path": f"{artifact_prefix}/reports/report.html",
            "result_json_path": f"{artifact_prefix}/logs/result.json",
        },
    )
    await session.commit()
    ai_model = await GConfigDao.get_active_ai_model_config()
    bootstrap = _build_runner_bootstrap_payload(
        request,
        user_info,
        int(source_run["project_id"] or 0),
        [new_run_id],
        int(source_run["plan_id"] or 0),
        ai_model,
    )
    _write_runner_bootstrap_file(bootstrap)
    return ArgusResponse.success({"run_id": new_run_id, "trigger_mode": "retry", "runner_bootstrap": bootstrap})


@router.get("/resource/status")
async def get_ui_test_resource_status(project_id: int = 0, session=Depends(get_session), _=Depends(Permission())):
    await ensure_ui_test_schema(session)
    sql = (
        "SELECT "
        "SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued_count, "
        "SUM(CASE WHEN status IN ('claimed', 'running', 'uploading') THEN 1 ELSE 0 END) AS running_count, "
        "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count, "
        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count "
        "FROM argus_ui_test_run WHERE deleted_at=0 AND trigger_mode<>'trial' "
    )
    params = {}
    if int(project_id or 0) > 0:
        sql += "AND project_id=:project_id "
        params["project_id"] = int(project_id)
    row = await session.execute(text(sql), params)
    stats = dict(row.mappings().first() or {})
    for key in ("queued_count", "running_count", "success_count", "failed_count"):
        stats[key] = int(stats.get(key) or 0)
    stats["bucket"] = UI_BUCKET_NAME
    stats["object_prefix"] = UI_OBJECT_PREFIX
    stats["runner_status"] = "waiting_runner"
    stats["oss_status"] = "configured" if UI_BUCKET_NAME else "missing_bucket"
    return ArgusResponse.success(stats)


@router.get("/plan/switch")
async def switch_ui_test_plan(id: int, status: bool, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    now_dt = datetime.now()
    row = await session.execute(
        text("SELECT id, name, cron FROM argus_ui_test_plan WHERE deleted_at=0 AND id=:id"),
        {"id": int(id)},
    )
    plan = row.mappings().first()
    if not plan:
        return ArgusResponse.failed("UI测试计划不存在")
    await session.execute(
        text("UPDATE argus_ui_test_plan SET status=:status, update_user=:update_user, updated_at=:updated_at WHERE id=:id"),
        {
            "id": int(id),
            "status": "enabled" if bool(status) else "disabled",
            "update_user": int(user_info["id"]),
            "updated_at": now_dt,
        },
    )
    await session.commit()
    _sync_ui_plan_scheduler(int(id), str(plan.get("name") or ""), str(plan.get("cron") or ""), bool(status))
    return ArgusResponse.success()


@router.get("/plan/delete")
async def delete_ui_test_plan(id: int, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    now_dt = datetime.now()
    deleted_at = int(now_dt.timestamp())
    row = await session.execute(
        text("SELECT id FROM argus_ui_test_plan WHERE deleted_at=0 AND id=:id"),
        {"id": int(id)},
    )
    plan = row.mappings().first()
    if not plan:
        return ArgusResponse.failed("UI测试计划不存在")
    await session.execute(
        text("UPDATE argus_ui_test_plan SET deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at WHERE id=:id"),
        {
            "id": int(id),
            "deleted_at": deleted_at,
            "update_user": int(user_info["id"]),
            "updated_at": now_dt,
        },
    )
    await session.execute(
        text("UPDATE argus_ui_test_plan_case SET deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at WHERE plan_id=:plan_id AND deleted_at=0"),
        {
            "plan_id": int(id),
            "deleted_at": deleted_at,
            "update_user": int(user_info["id"]),
            "updated_at": now_dt,
        },
    )
    await session.commit()
    try:
        Scheduler.scheduler.remove_job(f"ui_test_plan_{int(id)}")
    except JobLookupError:
        pass
    except Exception:
        pass
    return ArgusResponse.success()


@router.get("/plan/follow")
async def follow_ui_test_plan(id: int, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    plan_row = await session.execute(
        text("SELECT id FROM argus_ui_test_plan WHERE deleted_at=0 AND id=:id"),
        {"id": int(id or 0)},
    )
    if not plan_row.first():
        return ArgusResponse.failed("UI测试计划不存在")
    follow_row = await session.execute(
        text(
            "SELECT id FROM argus_ui_test_plan_follow_user_rel "
            "WHERE deleted_at=0 AND plan_id=:plan_id AND user_id=:user_id"
        ),
        {"plan_id": int(id or 0), "user_id": int(user_info["id"])},
    )
    if follow_row.first():
        return ArgusResponse.failed("已关注过此UI测试计划")
    now_dt = datetime.now()
    await session.execute(
        text(
            "INSERT INTO argus_ui_test_plan_follow_user_rel "
            "(created_at, updated_at, deleted_at, create_user, update_user, user_id, plan_id) "
            "VALUES (:created_at, :updated_at, 0, :create_user, :update_user, :user_id, :plan_id)"
        ),
        {
            "created_at": now_dt,
            "updated_at": now_dt,
            "create_user": int(user_info["id"]),
            "update_user": int(user_info["id"]),
            "user_id": int(user_info["id"]),
            "plan_id": int(id or 0),
        },
    )
    await session.commit()
    return ArgusResponse.success(msg="关注成功")


@router.get("/plan/unfollow")
async def unfollow_ui_test_plan(id: int, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    follow_row = await session.execute(
        text(
            "SELECT id FROM argus_ui_test_plan_follow_user_rel "
            "WHERE deleted_at=0 AND plan_id=:plan_id AND user_id=:user_id"
        ),
        {"plan_id": int(id or 0), "user_id": int(user_info["id"])},
    )
    row = follow_row.mappings().first()
    if not row:
        return ArgusResponse.failed("已取关过此UI测试计划")
    now_dt = datetime.now()
    await session.execute(
        text(
            "UPDATE argus_ui_test_plan_follow_user_rel "
            "SET deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at "
            "WHERE id=:id"
        ),
        {
            "id": int(row["id"] or 0),
            "deleted_at": int(time.time() * 1000),
            "update_user": int(user_info["id"]),
            "updated_at": now_dt,
        },
    )
    await session.commit()
    return ArgusResponse.success(msg="取关成功")


@router.post("/runner/claim")
async def claim_ui_test_run(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    payload = await request.json()
    project_id = int(payload.get("project_id") or 0)
    plan_id = int(payload.get("plan_id") or 0)
    run_id = int(payload.get("run_id") or 0)
    any_project = _normalize_bool(payload.get("any_project"), False)
    if project_id <= 0 and run_id <= 0 and not any_project:
        ui_test_log.warning(
            f"runner claim end invalid payload project_id={project_id}, plan_id={plan_id}, "
            f"run_id={run_id}, any_project={int(any_project)}"
        )
        return ArgusResponse.failed("project_id或run_id不能为空")

    sql = (
        "SELECT id, project_id, plan_id, case_ref_id, run_name, status, trigger_mode, browser, headless, "
        "artifact_bucket, artifact_prefix, screenshot_dir, video_path, trace_path, report_path, result_json_path, "
        "runner_payload, created_at, started_at "
        "FROM argus_ui_test_run WHERE deleted_at=0 AND status='queued' "
    )
    params = {}
    if run_id > 0:
        sql += "AND id=:run_id "
        params["run_id"] = run_id
    else:
        if not any_project:
            sql += "AND project_id=:project_id "
            params["project_id"] = project_id
        if plan_id > 0:
            sql += "AND plan_id=:plan_id "
            params["plan_id"] = plan_id
    sql += "ORDER BY id ASC LIMIT 1"

    row = await session.execute(text(sql), params)
    task = row.mappings().first()
    if not task:
        return ArgusResponse.success(None, msg="当前没有可领取的UI任务")

    now_dt = datetime.now()
    updated = await session.execute(
        text(
            "UPDATE argus_ui_test_run SET status='claimed', update_user=:update_user, updated_at=:updated_at, "
            "started_at=COALESCE(started_at, :started_at) WHERE id=:id AND deleted_at=0 AND status='queued'"
        ),
        {
            "id": int(task["id"]),
            "update_user": int(user_info["id"]),
            "updated_at": now_dt,
            "started_at": now_dt,
        },
    )
    if int(updated.rowcount or 0) == 0:
        await session.rollback()
        return ArgusResponse.success(None, msg="任务已被其他Runner领取")
    await session.commit()

    claimed = dict(task)
    claimed["status"] = "claimed"
    claimed["runner_payload"] = _parse_json_text(claimed.get("runner_payload")) or {}
    ui_test_log.info(
        f"runner claim claimed task_id={int(claimed['id'] or 0)}, "
        f"project_id={int(claimed.get('project_id') or 0)}, plan_id={int(claimed.get('plan_id') or 0)}, "
        f"run_id={run_id}, any_project={int(any_project)}, "
        f"trigger_mode={str(claimed.get('trigger_mode') or '')}, user_id={int(user_info['id'])}"
    )
    return ArgusResponse.success(claimed)


@router.get("/runner/run/status")
async def get_runner_ui_test_run_status(run_id: int, session=Depends(get_session), _=Depends(Permission())):
    await ensure_ui_test_schema(session)
    row = await session.execute(
        text("SELECT id, status, error_message, updated_at FROM argus_ui_test_run WHERE deleted_at=0 AND id=:id"),
        {"id": int(run_id or 0)},
    )
    run = row.mappings().first()
    if not run:
        return ArgusResponse.failed("UI测试执行记录不存在")
    status = str(run.get("status") or "").strip().lower()
    return ArgusResponse.success({
        "run_id": int(run["id"]),
        "status": status,
        "cancelled": status == "cancelled",
        "active": status in UI_RUN_ACTIVE_STATUSES,
        "error_message": str(run.get("error_message") or ""),
        "updated_at": run.get("updated_at"),
    })


@router.post("/runner/step/save")
async def save_ui_test_step_result(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    payload = await request.json()
    run_id = int(payload.get("run_id") or 0)
    step_index = int(payload.get("step_index") or 0)
    if run_id <= 0:
        return ArgusResponse.failed("run_id不能为空")

    run_row = await session.execute(
        text("SELECT id, status FROM argus_ui_test_run WHERE deleted_at=0 AND id=:id"),
        {"id": run_id},
    )
    run = run_row.mappings().first()
    if not run:
        return ArgusResponse.failed("UI测试执行记录不存在")

    step_name = str(payload.get("step_name") or f"Step {step_index}").strip()
    step_type = str(payload.get("step_type") or "").strip()
    status = _normalize_ui_step_status(payload.get("status"), "running")
    screenshot_path = str(payload.get("screenshot_path") or "").strip()
    request_payload = json.dumps(payload.get("request_payload"), ensure_ascii=False) if "request_payload" in payload else None
    result_payload = json.dumps(payload.get("result_payload"), ensure_ascii=False) if "result_payload" in payload else None
    error_message = str(payload.get("error_message") or "").strip()
    duration_ms = int(payload.get("duration_ms") or 0)
    now_dt = datetime.now()

    existing_row = await session.execute(
        text("SELECT id FROM argus_ui_test_step_result WHERE deleted_at=0 AND run_id=:run_id AND step_index=:step_index LIMIT 1"),
        {"run_id": run_id, "step_index": step_index},
    )
    existing = existing_row.mappings().first()
    if existing:
        await session.execute(
            text(
                "UPDATE argus_ui_test_step_result SET step_name=:step_name, step_type=:step_type, status=:status, "
                "screenshot_path=:screenshot_path, request_payload=:request_payload, result_payload=:result_payload, "
                "error_message=:error_message, duration_ms=:duration_ms, update_user=:update_user, updated_at=:updated_at "
                "WHERE id=:id"
            ),
            {
                "id": int(existing["id"]),
                "step_name": step_name,
                "step_type": step_type,
                "status": status,
                "screenshot_path": screenshot_path,
                "request_payload": request_payload,
                "result_payload": result_payload,
                "error_message": error_message,
                "duration_ms": duration_ms,
                "update_user": int(user_info["id"]),
                "updated_at": now_dt,
            },
        )
    else:
        await session.execute(
            text(
                "INSERT INTO argus_ui_test_step_result "
                "(created_at, updated_at, deleted_at, create_user, update_user, run_id, step_index, step_name, step_type, status, screenshot_path, request_payload, result_payload, error_message, duration_ms) "
                "VALUES "
                "(:created_at, :updated_at, 0, :create_user, :update_user, :run_id, :step_index, :step_name, :step_type, :status, :screenshot_path, :request_payload, :result_payload, :error_message, :duration_ms)"
            ),
            {
                "created_at": now_dt,
                "updated_at": now_dt,
                "create_user": int(user_info["id"]),
                "update_user": int(user_info["id"]),
                "run_id": run_id,
                "step_index": step_index,
                "step_name": step_name,
                "step_type": step_type,
                "status": status,
                "screenshot_path": screenshot_path,
                "request_payload": request_payload,
                "result_payload": result_payload,
                "error_message": error_message,
                "duration_ms": duration_ms,
            },
        )

    if str(run.get("status") or "") in {"queued", "claimed"} and status in {"running", "success", "failed"}:
        await session.execute(
            text("UPDATE argus_ui_test_run SET status='running', update_user=:update_user, updated_at=:updated_at WHERE id=:id"),
            {"id": run_id, "update_user": int(user_info["id"]), "updated_at": now_dt},
        )
    await session.commit()
    return ArgusResponse.success({"run_id": run_id, "step_index": step_index, "status": status})


@router.post("/runner/run/save")
async def save_ui_test_run_result(request: Request, session=Depends(get_session), user_info=Depends(Permission())):
    await ensure_ui_test_schema(session)
    payload = await request.json()
    run_id = int(payload.get("run_id") or 0)
    if run_id <= 0:
        return ArgusResponse.failed("run_id不能为空")

    run_row = await session.execute(
        text("SELECT id, status FROM argus_ui_test_run WHERE deleted_at=0 AND id=:id"),
        {"id": run_id},
    )
    run = run_row.mappings().first()
    if not run:
        return ArgusResponse.failed("UI测试执行记录不存在")

    now_dt = datetime.now()
    status = _normalize_ui_run_status(payload.get("status"), "success")
    current_status = str(run.get("status") or "").strip().lower()
    if current_status == "cancelled" and status != "cancelled":
        return ArgusResponse.success({
            "run_id": run_id,
            "status": "cancelled",
            "ignored": True,
            "message": "执行记录已被手动停止，忽略Runner后续状态回写。",
        })
    result_payload = json.dumps(payload.get("result_payload"), ensure_ascii=False) if "result_payload" in payload else None
    error_message = str(payload.get("error_message") or "").strip()
    step_count_row = await session.execute(
        text("SELECT COUNT(1) AS step_count FROM argus_ui_test_step_result WHERE deleted_at=0 AND run_id=:run_id"),
        {"run_id": run_id},
    )
    saved_step_count = int((step_count_row.mappings().first() or {}).get("step_count") or 0)
    if status == "success" and saved_step_count <= 0:
        status = "failed"
        error_message = error_message or "Runner回写成功但没有任何步骤结果，已拦截为空执行"
        parsed_result = payload.get("result_payload") if isinstance(payload.get("result_payload"), dict) else {}
        parsed_result.update({
            "status": "failed",
            "guard_reason": "no_step_results",
            "step_count": 0,
            "message": error_message,
        })
        result_payload = json.dumps(parsed_result, ensure_ascii=False)

    update_fields = {
        "id": run_id,
        "status": status,
        "result_payload": result_payload,
        "error_message": error_message,
        "finished_at": now_dt if status in UI_RUN_TERMINAL_STATUSES else None,
        "updated_at": now_dt,
        "update_user": int(user_info["id"]),
        "artifact_prefix": str(payload.get("artifact_prefix") or "").strip(),
        "screenshot_dir": str(payload.get("screenshot_dir") or "").strip(),
        "video_path": str(payload.get("video_path") or "").strip(),
        "trace_path": str(payload.get("trace_path") or "").strip(),
        "report_path": str(payload.get("report_path") or "").strip(),
        "result_json_path": str(payload.get("result_json_path") or "").strip(),
    }
    await session.execute(
        text(
            "UPDATE argus_ui_test_run SET status=:status, result_payload=:result_payload, error_message=:error_message, "
            "finished_at=CASE WHEN :finished_at IS NULL THEN finished_at ELSE :finished_at END, "
            "updated_at=:updated_at, update_user=:update_user, "
            "artifact_prefix=CASE WHEN :artifact_prefix='' THEN artifact_prefix ELSE :artifact_prefix END, "
            "screenshot_dir=CASE WHEN :screenshot_dir='' THEN screenshot_dir ELSE :screenshot_dir END, "
            "video_path=CASE WHEN :video_path='' THEN video_path ELSE :video_path END, "
            "trace_path=CASE WHEN :trace_path='' THEN trace_path ELSE :trace_path END, "
            "report_path=CASE WHEN :report_path='' THEN report_path ELSE :report_path END, "
            "result_json_path=CASE WHEN :result_json_path='' THEN result_json_path ELSE :result_json_path END "
            "WHERE id=:id"
        ),
        update_fields,
    )
    await session.commit()
    # trigger notification (only for terminal statuses)
    if status in UI_RUN_TERMINAL_STATUSES:
        try:
            from app.core.ui_notice import UiNotice
            import asyncio
            asyncio.create_task(UiNotice.notify(0, run_id))
        except Exception:
            pass
    return ArgusResponse.success({"run_id": run_id, "status": status})


@router.post("/runner/artifact/upload")
async def upload_ui_test_artifact(run_id: int = Form(...), object_key: str = Form(...), file: UploadFile = File(...),
                                  session=Depends(get_session), _=Depends(Permission())):
    await ensure_ui_test_schema(session)
    run_row = await session.execute(
        text("SELECT id, status, artifact_prefix, artifact_bucket FROM argus_ui_test_run WHERE deleted_at=0 AND id=:id"),
        {"id": int(run_id)},
    )
    run = run_row.mappings().first()
    if not run:
        return ArgusResponse.failed("UI测试执行记录不存在")
    if str(run.get("status") or "").strip().lower() == "cancelled":
        return ArgusResponse.failed("UI测试执行已停止，跳过产物上传")

    normalized_key = str(object_key or "").replace("\\", "/").strip().strip("/")
    artifact_prefix = str(run.get("artifact_prefix") or "").strip().strip("/")
    if not normalized_key:
        return ArgusResponse.failed("object_key不能为空")
    if artifact_prefix and not normalized_key.startswith(f"{artifact_prefix}/") and normalized_key != artifact_prefix:
        return ArgusResponse.failed("object_key不在当前run的artifact_prefix下")

    client = OssClient.get_oss_client()
    content = await file.read()
    bucket_name = str(run.get("artifact_bucket") or UI_BUCKET_NAME or "").strip() or None
    try:
        upload_result, file_size, upload_attempts = await _upload_artifact_with_retry(
            client,
            normalized_key,
            content,
            bucket_name=bucket_name,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as exc:
        return ArgusResponse.failed(str(exc))
    upload_meta = normalize_oss_upload_result(client, upload_result, normalized_key, bucket_name=bucket_name)
    return ArgusResponse.success({
        "run_id": int(run_id),
        "bucket_name": upload_meta.get("bucket_name") or bucket_name or "",
        "object_key": upload_meta.get("object_key") or normalized_key,
        "file_url": upload_meta.get("file_url") or "",
        "file_size": int(file_size or 0),
        "upload_attempts": upload_attempts,
    })
