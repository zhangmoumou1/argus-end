import asyncio
import csv
import io
import json
import math
import os
import random
import re
import time
import uuid
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import desc, func, select, text

from app.core.executor import Executor
from app.core.platform_task import PlatformTaskService
from app.crud.operation.ArgusOperationDao import ArgusOperationDao
from app.enums.OperationEnum import OperationType
from app.enums.platform_task import PlatformTaskType
from app.handler.fatcory import ArgusResponse
from app.middleware.AsyncHttpClient import AsyncRequest
from app.middleware.oss import OssClient, get_public_bucket_name
from app.models import async_session
from app.models.interface_manage import ArgusApiEndpointSample, ArgusApiEndpointVersion
from app.models.performance import (
    ArgusPerformanceParameterFile,
    ArgusPerformancePlan,
    ArgusPerformanceReport,
    ArgusPerformanceRunLog,
)
from app.models.test_case import TestCase
from app.models.out_parameters import ArgusTestCaseOutParameters
from app.models.testcase_asserts import TestCaseAsserts
from app.routers import Permission
from app.schema.performance import ArgusPerformanceParameterValidateForm, ArgusPerformancePlanForm
from app.utils.logger import Log
from config import Config

router = APIRouter(prefix="/performance")
log = Log("PerformanceRouter")
performance_schema_checked = False
SUPPORTED_SOURCE_TYPES = {"single", "link", "api_asset", "api_scenario", "manual"}
SUPPORTED_LOAD_MODES = {"concurrency", "qps"}
VARIABLE_PATTERN = re.compile(r"\$\{([^{}]+)\}")
PERFORMANCE_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads" / "performance_params"
MAX_REPORT_REQUEST_RECORDS = 10
MAX_REPORT_RESPONSE_SAMPLE_LENGTH = 1500
MAX_REPORT_REQUEST_BODY_LENGTH = 600
MAX_REPORT_ASSERTION_RESULTS = 5
PERFORMANCE_RUN_SEMAPHORE = asyncio.Semaphore(int(getattr(Config, "PERFORMANCE_MAX_CONCURRENT_RUNS", 1) or 1))


def resolve_grafana_url():
    env_value = os.getenv("GRAFANA_URL")
    if env_value:
        return env_value.strip().strip('"').strip("'")

    env_name = "pro.env" if str(os.getenv("ARGUS_ENV") or "").lower() == "pro" else "dev.env"
    env_path = Path(__file__).resolve().parents[2] / "conf" / env_name
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "GRAFANA_URL":
                return value.strip().strip('"').strip("'")
    return ""


async def ensure_performance_schema(session):
    """
    兼容已经创建过性能计划表的环境，避免新增字段需要手工迁移后才能使用。
    """
    global performance_schema_checked
    if not Config.RUNTIME_SCHEMA_MIGRATION_ENABLED:
        performance_schema_checked = True
        return
    if performance_schema_checked:
        return
    try:
        rows = await session.execute(text("SHOW COLUMNS FROM argus_performance_plan"))
        columns = {row[0] for row in rows.fetchall()}
        if "source_type" not in columns:
            await session.execute(text("ALTER TABLE argus_performance_plan ADD COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'single'"))
        if "case_list" not in columns:
            await session.execute(text("ALTER TABLE argus_performance_plan ADD COLUMN case_list TEXT NULL"))
        if "load_mode" not in columns:
            await session.execute(text("ALTER TABLE argus_performance_plan ADD COLUMN load_mode VARCHAR(16) NOT NULL DEFAULT 'concurrency'"))
        if "load_config" not in columns:
            await session.execute(text("ALTER TABLE argus_performance_plan ADD COLUMN load_config TEXT NULL"))
        if "threshold_config" not in columns:
            await session.execute(text("ALTER TABLE argus_performance_plan ADD COLUMN threshold_config TEXT NULL"))
        if "parameter_config" not in columns:
            await session.execute(text("ALTER TABLE argus_performance_plan ADD COLUMN parameter_config TEXT NULL"))
        if "assertions_config" not in columns:
            await session.execute(text("ALTER TABLE argus_performance_plan ADD COLUMN assertions_config TEXT NULL"))
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS argus_performance_parameter_file ("
            "id INT PRIMARY KEY AUTO_INCREMENT,"
            "project_id INT NOT NULL,"
            "name VARCHAR(128) NOT NULL,"
            "file_name VARCHAR(255) NOT NULL,"
            "file_path VARCHAR(512) NOT NULL,"
            "file_type VARCHAR(16) NOT NULL DEFAULT 'csv',"
            "columns TEXT NULL,"
            "row_count INT NOT NULL DEFAULT 0,"
            "encoding VARCHAR(32) NOT NULL DEFAULT 'utf-8',"
            "delimiter VARCHAR(8) NOT NULL DEFAULT ',',"
            "create_user INT NOT NULL,"
            "update_user INT NOT NULL,"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "deleted_at BIGINT NOT NULL DEFAULT 0"
            ")"
        ))
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS argus_performance_run_log ("
            "id INT PRIMARY KEY AUTO_INCREMENT,"
            "run_id INT NOT NULL,"
            "level VARCHAR(16) NOT NULL DEFAULT 'INFO',"
            "message VARCHAR(255) NOT NULL,"
            "detail TEXT NULL,"
            "create_user INT NOT NULL,"
            "update_user INT NOT NULL,"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "deleted_at BIGINT NOT NULL DEFAULT 0"
            ")"
        ))
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS argus_performance_plan_follow_user_rel ("
            "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
            "deleted_at BIGINT NOT NULL DEFAULT 0,"
            "create_user INT NOT NULL DEFAULT 0,"
            "update_user INT NOT NULL DEFAULT 0,"
            "user_id INT NOT NULL DEFAULT 0,"
            "plan_id BIGINT NOT NULL DEFAULT 0,"
            "UNIQUE KEY uniq_performance_plan_follow_user (user_id, plan_id, deleted_at),"
            "KEY idx_performance_plan_follow_user (user_id, deleted_at, plan_id)"
            ")"
        ))
        report_rows = await session.execute(text("SHOW COLUMNS FROM argus_performance_report"))
        report_columns = {row[0]: str(row[1]).lower() for row in report_rows.fetchall()}
        if report_columns.get("summary_json") == "text":
            await session.execute(text("ALTER TABLE argus_performance_report MODIFY COLUMN summary_json LONGTEXT NULL"))
        if report_columns.get("timeline_json") == "text":
            await session.execute(text("ALTER TABLE argus_performance_report MODIFY COLUMN timeline_json LONGTEXT NULL"))
        if report_columns.get("errors_json") == "text":
            await session.execute(text("ALTER TABLE argus_performance_report MODIFY COLUMN errors_json LONGTEXT NULL"))
        performance_schema_checked = True
    except Exception as exc:
        log.warning(f"检查性能测试表结构失败: {exc}")

def safe_json_loads(value, default=None):
    if default is None:
        default = {}
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def serialize_json(value, default):
    if value in (None, ""):
        return json.dumps(default, ensure_ascii=False)
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except Exception:
            return json.dumps(default, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def stringify_json(value, fallback="{}"):
    if value in (None, ""):
        return fallback
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return fallback


def _normalize_bool_query(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_csv_file(file_path: Path, encoding="utf-8", delimiter=","):
    rows = []
    columns = []
    with file_path.open("r", encoding=encoding, newline="") as fp:
        reader = csv.DictReader(fp, delimiter=delimiter)
        columns = reader.fieldnames or []
        for row in reader:
            rows.append({str(k): row.get(k) for k in columns if k})
    return columns, rows


def parse_csv_content(content: bytes, encoding="utf-8", delimiter=","):
    rows = []
    columns = []
    text = content.decode(encoding or "utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter or ",")
    columns = reader.fieldnames or []
    for row in reader:
        rows.append({str(k): row.get(k) for k in columns if k})
    return columns, rows


def build_bucket_parameter_file_id(file_path: str) -> str:
    return f"bucket:{str(file_path or '').strip()}"


def parse_bucket_parameter_file_id(file_id) -> str:
    text = str(file_id or "").strip()
    if text.startswith("bucket:"):
        return text.split(":", 1)[1].strip()
    return ""


async def load_public_bucket_csv(file_path: str, encoding="utf-8", delimiter=","):
    client = OssClient.get_oss_client()
    content = await client.get_file_object(file_path, bucket_name=get_public_bucket_name() or None)
    return parse_csv_content(content, encoding=encoding or "utf-8", delimiter=delimiter or ",")


def normalize_plan_payload(payload: dict):
    case_list = payload.get("case_list", "")
    if isinstance(case_list, list):
        case_list = ",".join(str(item).replace("testcase_", "") for item in case_list if str(item).startswith("testcase_"))
    payload["case_list"] = case_list or ""

    source_type = payload.get("source_type") or "api_asset"
    if source_type not in SUPPORTED_SOURCE_TYPES:
        source_type = "api_asset"
    payload["source_type"] = source_type

    load_mode = payload.get("load_mode") or "concurrency"
    if load_mode not in SUPPORTED_LOAD_MODES:
        load_mode = "concurrency"
    payload["load_mode"] = load_mode

    load_config = safe_json_loads(payload.get("load_config"), {})
    threshold_config = safe_json_loads(payload.get("threshold_config"), [])
    parameter_config = safe_json_loads(payload.get("parameter_config"), {})
    assertions_config = safe_json_loads(payload.get("assertions_config"), [])

    if not isinstance(load_config, dict):
        load_config = {}
    if not isinstance(threshold_config, list):
        threshold_config = []
    if not isinstance(parameter_config, dict):
        parameter_config = {}
    parameter_config = normalize_parameter_config(parameter_config)
    if not isinstance(assertions_config, list):
        assertions_config = []

    request_timeout_ms = int(load_config.get("request_timeout_ms") or payload.get("request_timeout_ms") or 10000)
    duration_seconds = int(load_config.get("duration_seconds") or payload.get("duration_seconds") or 60)
    iterations = int(load_config.get("iterations") or payload.get("iterations") or 0)

    if load_mode == "qps":
        payload["concurrency"] = int(load_config.get("max_concurrency") or payload.get("concurrency") or 10)
        payload["think_time_ms"] = 0
        payload["ramp_up_seconds"] = 0
    else:
        payload["concurrency"] = int(load_config.get("concurrency") or payload.get("concurrency") or 10)
        payload["think_time_ms"] = int(load_config.get("think_time_ms") or payload.get("think_time_ms") or 0)
        payload["ramp_up_seconds"] = int(load_config.get("ramp_up_seconds") or payload.get("ramp_up_seconds") or 0)

    payload["duration_seconds"] = duration_seconds
    payload["iterations"] = iterations
    payload["request_timeout_ms"] = request_timeout_ms
    payload["load_config"] = serialize_json(load_config, {})
    payload["threshold_config"] = serialize_json(threshold_config, [])
    payload["parameter_config"] = serialize_json(parameter_config, {})
    payload["assertions_config"] = serialize_json(assertions_config, [])

    if source_type in {"link", "api_scenario"}:
        count = len([item for item in str(payload["case_list"]).split(",") if item])
        payload["request_method"] = "LINK"
        payload["request_url"] = payload.get("request_url") or f"接口用例链路（{count}个用例）"
        payload["service_id"] = 0
        payload["endpoint_id"] = 0
        payload["api_version_id"] = 0
    return payload


def normalize_headers(value):
    raw = safe_json_loads(value, {})
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if k and v is not None}
    if isinstance(raw, list):
        headers = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("key")
            val = item.get("value") or item.get("example") or item.get("default")
            if name and val is not None:
                headers[str(name)] = str(val)
        return headers
    return {}


def normalize_parameter_config(parameter_config):
    if not isinstance(parameter_config, dict):
        parameter_config = {}

    setup_config = parameter_config.get("setup_config") or {}
    if not isinstance(setup_config, dict):
        setup_config = {}
    enabled = bool(setup_config.get("enabled"))
    source = str(setup_config.get("source") or "case").lower()
    if source not in {"case", "chain"}:
        source = "case"
    scope = str(setup_config.get("scope") or "per_run").lower()
    if scope not in {"per_run", "per_worker"}:
        scope = "per_run"
    case_list = setup_config.get("case_list")
    case_ids = parse_case_ids(case_list)
    case_id = setup_config.get("case_id")
    try:
        case_id = int(case_id or 0)
    except (TypeError, ValueError):
        case_id = 0
    if source == "case":
        if case_id and not case_ids:
            case_ids = [case_id]
        else:
            case_ids = [int(item) for item in case_ids]
        enabled = enabled and bool(case_ids)
    parameter_config["setup_config"] = {
        "enabled": enabled,
        "source": source,
        "scope": scope,
        "case_id": case_ids[0] if case_ids else 0,
        "case_list": ",".join(str(item) for item in case_ids) if case_ids else "",
    }

    normalized_headers = []
    for item in parameter_config.get("global_headers") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("key") or "").strip()
        if not name:
            continue
        normalized_headers.append({
            "name": name,
            "value": item.get("value", ""),
            "description": item.get("description", ""),
            "enabled": item.get("enabled", True) is not False,
        })
    parameter_config["global_headers"] = normalized_headers
    parameter_config["manual_variables"] = parameter_config.get("manual_variables") or []
    parameter_config["file_variables"] = parameter_config.get("file_variables") or []
    parameter_config["builtin_functions_enabled"] = parameter_config.get("builtin_functions_enabled", True)
    return parameter_config


def normalize_query(value):
    raw = safe_json_loads(value, {})
    if isinstance(raw, dict):
        return raw
    return {}


def normalize_body(value):
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return json.loads(text_value)
    except Exception:
        return text_value


def resolve_builtin_expression(expr: str):
    now = datetime.now()
    if expr == "__timestamp()":
        return str(int(time.time() * 1000))
    if expr == "__datetime()":
        return now.strftime("%Y-%m-%d %H:%M:%S")
    if expr == "__date()":
        return now.strftime("%Y-%m-%d")
    if expr == "__uuid()":
        return str(uuid.uuid4())
    if expr == "__phone()":
        return f"1{random.randint(3, 9)}{random.randint(100000000, 999999999)}"
    matched = re.match(r"__randomInt\((\-?\d+)\s*,\s*(\-?\d+)\)", expr)
    if matched:
        start = int(matched.group(1))
        end = int(matched.group(2))
        if start > end:
            start, end = end, start
        return str(random.randint(start, end))
    matched = re.match(r"__randomString\((\d+)\)", expr)
    if matched:
        length = max(int(matched.group(1)), 1)
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join(random.choice(chars) for _ in range(length))
    return None


def replace_variables_in_string(text_value, variables):
    if text_value in (None, ""):
        return text_value
    raw = str(text_value)

    def _replace(match):
        expr = (match.group(1) or "").strip()
        builtin = resolve_builtin_expression(expr)
        if builtin is not None:
            return builtin
        return str(variables.get(expr, match.group(0)))

    replaced = VARIABLE_PATTERN.sub(_replace, raw)
    if replaced != raw and replaced.startswith("{") and replaced.endswith("}"):
        try:
            return json.loads(replaced)
        except Exception:
            return replaced
    return replaced


def replace_variables(value, variables):
    if isinstance(value, dict):
        return {k: replace_variables(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_variables(item, variables) for item in value]
    if isinstance(value, str):
        return replace_variables_in_string(value, variables)
    return value


def extract_json_path_value(payload, path):
    if payload is None or not path:
        return None
    current = payload
    normalized = str(path).strip()
    if normalized.startswith("$."):
        normalized = normalized[2:]
    elif normalized.startswith("$"):
        normalized = normalized[1:]
    parts = [part for part in normalized.split(".") if part]
    for part in parts:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except Exception:
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def evaluate_assertions(result, assertions):
    if not assertions:
        return True, []
    body_payload = result.get("response")
    if isinstance(body_payload, str):
        try:
            body_payload = json.loads(body_payload)
        except Exception:
            pass
    response_headers = result.get("response_headers") or {}
    assertion_results = []
    for item in assertions:
        if not isinstance(item, dict):
            continue
        assertion_type = str(item.get("type") or "").strip()
        name = item.get("name") or assertion_type or "断言"
        operator = item.get("operator") or "="
        expected = item.get("expected")
        actual = None
        passed = False
        if assertion_type == "status_code":
            actual = int(result.get("status_code") or 0)
            expected_num = int(expected or 0)
            passed = compare_value(actual, operator, expected_num)
        elif assertion_type == "body_contains":
            actual = result.get("response")
            actual_text = actual if isinstance(actual, str) else json.dumps(actual, ensure_ascii=False)
            passed = str(expected or "") in actual_text
        elif assertion_type == "json_path":
            actual = extract_json_path_value(body_payload, item.get("path"))
            if operator in {"contains", "includes"}:
                passed = str(expected or "") in str(actual or "")
            else:
                passed = compare_value(str(actual), operator, str(expected))
        elif assertion_type == "header_contains":
            header_name = str(item.get("path") or item.get("header") or "").strip()
            actual = response_headers.get(header_name)
            passed = str(expected or "") in str(actual or "")
        else:
            continue
        assertion_results.append({
            "name": name,
            "type": assertion_type,
            "path": item.get("path"),
            "operator": operator,
            "expected": expected,
            "actual": actual,
            "passed": passed,
        })
    failed = [item for item in assertion_results if not item.get("passed")]
    return len(failed) == 0, assertion_results


async def load_parameter_variables(session, parameter_config, state):
    parameter_config = parameter_config or {}
    manual_variables = {}
    for item in parameter_config.get("manual_variables") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        manual_variables[name] = item.get("value", "")

    file_variables = {}
    for item in parameter_config.get("file_variables") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        file_id = item.get("file_id")
        bucket_file_path = parse_bucket_parameter_file_id(file_id) or str(item.get("bucket_file_path") or "").strip()
        if not file_id and not bucket_file_path:
            continue
        if bucket_file_path:
            columns, rows = await load_public_bucket_csv(
                bucket_file_path,
                encoding=str(item.get("encoding") or "utf-8"),
                delimiter=str(item.get("delimiter") or ","),
            )
            state_file_key = build_bucket_parameter_file_id(bucket_file_path)
        else:
            row = await session.execute(
                select(ArgusPerformanceParameterFile).where(
                    ArgusPerformanceParameterFile.id == int(file_id),
                    ArgusPerformanceParameterFile.deleted_at == 0,
                )
            )
            file_record = row.scalars().first()
            if file_record is None:
                continue
            file_path = Path(file_record.file_path)
            if not file_path.exists():
                continue
            columns, rows = parse_csv_file(file_path, file_record.encoding or "utf-8", file_record.delimiter or ",")
            state_file_key = str(file_id)
        if not rows:
            continue
        read_mode = str(item.get("read_mode") or "CIRCULAR").upper()
        state_key = f"file_{state_file_key}_index"
        index = int(state.get(state_key, 0))
        if read_mode == "RANDOM":
            row_data = random.choice(rows)
        elif read_mode == "SEQUENTIAL":
            row_data = rows[min(index, len(rows) - 1)]
            state[state_key] = min(index + 1, len(rows) - 1)
        else:
            row_data = rows[index % len(rows)]
            state[state_key] = (index + 1) % len(rows)
        for col in columns:
            file_variables[col] = row_data.get(col)

    return manual_variables, file_variables


async def preload_parameter_sources(session, parameter_config):
    parameter_config = parameter_config or {}
    manual_variables = {}
    for item in parameter_config.get("manual_variables") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        manual_variables[name] = item.get("value", "")

    file_sources = []
    for item in parameter_config.get("file_variables") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        file_id = item.get("file_id")
        bucket_file_path = parse_bucket_parameter_file_id(file_id) or str(item.get("bucket_file_path") or "").strip()
        if not file_id and not bucket_file_path:
            continue
        if bucket_file_path:
            columns, rows = await load_public_bucket_csv(
                bucket_file_path,
                encoding=str(item.get("encoding") or "utf-8"),
                delimiter=str(item.get("delimiter") or ","),
            )
            state_file_key = build_bucket_parameter_file_id(bucket_file_path)
        else:
            row = await session.execute(
                select(ArgusPerformanceParameterFile).where(
                    ArgusPerformanceParameterFile.id == int(file_id),
                    ArgusPerformanceParameterFile.deleted_at == 0,
                )
            )
            file_record = row.scalars().first()
            if file_record is None:
                continue
            file_path = Path(file_record.file_path)
            if not file_path.exists():
                continue
            columns, rows = parse_csv_file(file_path, file_record.encoding or "utf-8", file_record.delimiter or ",")
            state_file_key = str(file_id)
        if not rows:
            continue
        file_sources.append({
            "columns": columns,
            "rows": rows,
            "read_mode": str(item.get("read_mode") or "CIRCULAR").upper(),
            "state_key": f"file_{state_file_key}_index",
        })

    return {
        "manual_variables": manual_variables,
        "file_sources": file_sources,
    }


async def resolve_parameter_variables(parameter_sources, state, state_lock=None):
    parameter_sources = parameter_sources or {}
    manual_variables = dict(parameter_sources.get("manual_variables") or {})
    file_variables = {}
    file_sources = parameter_sources.get("file_sources") or []
    if not file_sources:
        return manual_variables, file_variables

    if state_lock is None:
        state_lock = asyncio.Lock()

    async with state_lock:
        for source in file_sources:
            rows = source.get("rows") or []
            columns = source.get("columns") or []
            if not rows:
                continue
            read_mode = str(source.get("read_mode") or "CIRCULAR").upper()
            state_key = str(source.get("state_key") or "")
            index = int(state.get(state_key, 0))
            if read_mode == "RANDOM":
                row_data = random.choice(rows)
            elif read_mode == "SEQUENTIAL":
                row_data = rows[min(index, len(rows) - 1)]
                state[state_key] = min(index + 1, len(rows) - 1)
            else:
                row_data = rows[index % len(rows)]
                state[state_key] = (index + 1) % len(rows)
            for col in columns:
                file_variables[col] = row_data.get(col)

    return manual_variables, file_variables


def build_runtime_global_headers(parameter_config, variables=None):
    parameter_config = normalize_parameter_config(parameter_config)
    variables = variables or {}
    headers = {}
    for item in parameter_config.get("global_headers") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        value = item.get("value", "")
        if isinstance(value, str):
            value = replace_variables_in_string(value, variables)
        headers[name] = "" if value is None else str(value)
    return headers


async def collect_setup_output_names(session, setup_config):
    setup_config = (setup_config or {}) if isinstance(setup_config, dict) else {}
    if not setup_config.get("enabled"):
        return set()
    case_ids = parse_case_ids(setup_config.get("case_list") or setup_config.get("case_id"))
    if not case_ids:
        return set()
    rows = await session.execute(
        select(ArgusTestCaseOutParameters.name).where(
            ArgusTestCaseOutParameters.case_id.in_(case_ids),
            ArgusTestCaseOutParameters.deleted_at == 0,
        )
    )
    return {
        str(item[0]).strip()
        for item in rows.fetchall()
        if item and str(item[0] or "").strip()
    }


async def execute_setup_sequence(plan, executor, setup_config, variables=None, request_headers=None):
    setup_config = normalize_parameter_config({"setup_config": setup_config}).get("setup_config") or {}
    setup_variables = dict(variables or {})
    if not setup_config.get("enabled"):
        return {"variables": setup_variables, "records": []}
    case_ids = parse_case_ids(setup_config.get("case_list") or setup_config.get("case_id"))
    if not case_ids:
        return {"variables": setup_variables, "records": []}
    setup_records = []

    for index, case_id in enumerate(case_ids, 1):
        runner = Executor(runtime_user_id=executor)
        request_param = {}
        if request_headers:
            request_param["request_headers"] = dict(request_headers)
        step_start = time.perf_counter()
        result, err = await runner.run(
            plan.env,
            case_id,
            params_pool=setup_variables,
            request_param=request_param,
            path=f"性能预置-{index}",
        )
        latency_ms = round((time.perf_counter() - step_start) * 1000, 2)
        setup_status = "success"
        setup_message = result.get("msg") or "请求成功"
        if err is not None or not result.get("status"):
            setup_status = "error"
            setup_message = err or f"全局预置接口#{case_id}执行失败"
        setup_records.append({
            "case_id": case_id,
            "name": result.get("name") or f"预置接口#{case_id}",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": setup_status,
            "status_code": int(result.get("status_code") or 0),
            "response_time_ms": latency_ms,
            "message": setup_message,
            "request": {
                "method": result.get("request_method"),
                "url": result.get("request_url") or result.get("url"),
                "headers": result.get("request_headers") or {},
                "query": result.get("request_query") or {},
                "body": result.get("request_body"),
            },
            "response_sample": result.get("response"),
        })
        if err is not None or not result.get("status"):
            raise Exception(setup_message)
    return {"variables": setup_variables, "records": setup_records}


def collect_variable_references(raw_values):
    references = set()
    for value in raw_values:
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False)
            except Exception:
                value = str(value)
        for matched in VARIABLE_PATTERN.findall(value):
            key = str(matched or "").strip()
            if key and not key.startswith("__"):
                references.add(key)
    return references


async def validate_parameter_payload(session, form: ArgusPerformanceParameterValidateForm):
    parameter_config = form.parameter_config if isinstance(form.parameter_config, dict) else safe_json_loads(form.parameter_config, {})
    parameter_config = normalize_parameter_config(parameter_config)
    manual_names = {
        str(item.get("name")).strip()
        for item in (parameter_config.get("manual_variables") or [])
        if isinstance(item, dict) and item.get("enabled", True) and str(item.get("name") or "").strip()
    }
    file_columns = set()
    for item in parameter_config.get("file_variables") or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        file_id = item.get("file_id")
        bucket_file_path = parse_bucket_parameter_file_id(file_id) or str(item.get("bucket_file_path") or "").strip()
        if not file_id and not bucket_file_path:
            continue
        if bucket_file_path:
            columns, _ = await load_public_bucket_csv(
                bucket_file_path,
                encoding=str(item.get("encoding") or "utf-8"),
                delimiter=str(item.get("delimiter") or ","),
            )
            file_columns.update(columns)
        else:
            row = await session.execute(
                select(ArgusPerformanceParameterFile).where(
                    ArgusPerformanceParameterFile.id == int(file_id),
                    ArgusPerformanceParameterFile.deleted_at == 0,
                )
            )
            file_record = row.scalars().first()
            if file_record is None:
                continue
            file_columns.update(safe_json_loads(file_record.columns, []))

    setup_output_names = await collect_setup_output_names(session, parameter_config.get("setup_config"))
    global_header_values = [
        item.get("value")
        for item in (parameter_config.get("global_headers") or [])
        if isinstance(item, dict) and item.get("enabled", True)
    ]
    references = collect_variable_references([
        form.request_url,
        form.request_headers,
        form.request_query,
        form.request_body,
        *global_header_values,
    ])
    errors = []
    warnings = []
    for name in sorted(references):
        if name not in manual_names and name not in file_columns and name not in setup_output_names:
            errors.append(f"变量 ${{{name}}} 未定义")
    if form.source_type in {"api_scenario", "link"} and references:
        warnings.append("接口场景中的动态变量可能会被链路提取变量覆盖，请确认变量命名。")
    if setup_output_names:
        warnings.append(f"全局预置可提供变量：{'、'.join(sorted(setup_output_names))}")
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "references": sorted(references),
        "manual_variables": sorted(manual_names),
        "file_columns": sorted(file_columns),
        "setup_variables": sorted(setup_output_names),
    }


def build_request_sample(plan):
    if getattr(plan, "source_type", "single") in {"link", "api_scenario"}:
        return {
            "source_type": getattr(plan, "source_type", "single"),
            "case_ids": parse_case_ids(getattr(plan, "case_list", "")),
        }
    return {
        "method": str(getattr(plan, "request_method", "GET") or "GET").upper(),
        "url": getattr(plan, "request_url", ""),
        "headers": normalize_headers(getattr(plan, "request_headers", "")),
        "query": normalize_query(getattr(plan, "request_query", "")),
        "body": normalize_body(getattr(plan, "request_body", "")),
    }


def build_text_preview(value, limit=5000):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, indent=2)
    else:
        text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... 已截断 {len(text) - limit} 个字符"


def normalize_executor_asserts(value):
    raw_value = safe_json_loads(value, default=[])
    if isinstance(raw_value, list):
        return raw_value
    if not isinstance(raw_value, dict):
        return []
    rows = []
    for key, item in raw_value.items():
        if not isinstance(item, dict):
            item = {"msg": str(item)}
        rows.append({
            "name": str(key),
            "type": "assert",
            "passed": bool(item.get("status")),
            "actual": item.get("actually") or item.get("actual") or "-",
            "expected": item.get("expected") or "-",
            "message": item.get("msg") or ("断言通过" if item.get("status") else "断言失败"),
        })
    return rows


def compact_step_record(step):
    if not isinstance(step, dict):
        return step
    request_block = step.get("request") or {}
    return {
        "case_id": step.get("case_id"),
        "name": step.get("name"),
        "timestamp": step.get("timestamp"),
        "status": step.get("status"),
        "status_code": step.get("status_code"),
        "response_time_ms": step.get("response_time_ms"),
        "message": step.get("message"),
        "request": {
            "method": request_block.get("method"),
            "url": request_block.get("url"),
            "headers": request_block.get("headers") or {},
            "query": request_block.get("query") or {},
            "body": build_text_preview(request_block.get("body"), limit=MAX_REPORT_REQUEST_BODY_LENGTH),
        },
        "response_sample": build_text_preview(step.get("response_sample"), limit=MAX_REPORT_RESPONSE_SAMPLE_LENGTH),
        "assertion_results": normalize_executor_asserts(step.get("assertion_results") or step.get("asserts")),
    }


def format_exception_message(exc):
    text = str(exc).strip()
    if text:
        return text
    return repr(exc)


def build_request_record(status, latency_ms, result=None, plan=None, variables=None, message="", assertion_results=None):
    result = result or {}
    request_sample = {
        "variables": variables or {},
        "request": {
            "method": result.get("request_method") or getattr(plan, "request_method", "GET"),
            "url": result.get("request_url") or getattr(plan, "request_url", ""),
            "headers": result.get("request_headers") or normalize_headers(getattr(plan, "request_headers", "")),
            "query": result.get("request_query") or normalize_query(getattr(plan, "request_query", "")),
            "body": result.get("request_body") if result.get("request_body") is not None else normalize_body(getattr(plan, "request_body", "")),
        },
    }
    response_sample = build_text_preview(result.get("response"), limit=MAX_REPORT_RESPONSE_SAMPLE_LENGTH)
    status_code = int(result.get("status_code") or 0)
    record = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "method": request_sample["request"]["method"],
        "url": request_sample["request"]["url"],
        "status_code": status_code,
        "response_time_ms": latency_ms,
        "error_type": result.get("error_type") or (infer_error_type(status_code, message) if status != "success" else ""),
        "message": message or ("请求成功" if status == "success" else "请求失败"),
        "request_sample": request_sample,
        "response_sample": response_sample,
        "assertion_results": assertion_results or [],
        "assertion_failed": status == "assertion_failed",
    }
    for extra_key in ("sample_scope", "setup_scope", "steps", "step_count"):
        if result.get(extra_key) is not None:
            record[extra_key] = result.get(extra_key)
    return record


def compact_request_record(record):
    request_sample = record.get("request_sample") or {}
    request_block = request_sample.get("request") or {}
    compact_request_sample = {
        "request": {
            "method": request_block.get("method"),
            "url": request_block.get("url"),
            "headers": request_block.get("headers") or {},
            "query": request_block.get("query") or {},
            "body": build_text_preview(request_block.get("body"), limit=MAX_REPORT_REQUEST_BODY_LENGTH),
        },
    }
    if request_sample.get("variables"):
        compact_request_sample["variables"] = request_sample.get("variables")
    compact_record = {
        "timestamp": record.get("timestamp"),
        "status": record.get("status"),
        "method": record.get("method"),
        "url": record.get("url"),
        "status_code": record.get("status_code"),
        "response_time_ms": record.get("response_time_ms"),
        "error_type": record.get("error_type"),
        "message": record.get("message"),
        "request_sample": compact_request_sample,
        "response_sample": build_text_preview(record.get("response_sample"), limit=MAX_REPORT_RESPONSE_SAMPLE_LENGTH),
        "assertion_results": (record.get("assertion_results") or [])[:MAX_REPORT_ASSERTION_RESULTS],
        "assertion_failed": record.get("assertion_failed", False),
    }
    for extra_key in ("sample_scope", "setup_scope", "step_count"):
        if record.get(extra_key) is not None:
            compact_record[extra_key] = record.get(extra_key)
    if record.get("steps"):
        compact_record["steps"] = [compact_step_record(item) for item in (record.get("steps") or [])]
    return compact_record


def infer_error_type(status_code, message=""):
    text = str(message or "").lower()
    if isinstance(status_code, str) and not status_code.isdigit():
        status_code = 0
    if status_code and int(status_code) >= 400:
        return "HTTP_ERROR"
    if "timeout" in text or "超时" in text:
        return "TIMEOUT"
    if "assert" in text or "断言" in text:
        return "ASSERT_FAILED"
    if "network" in text or "connect" in text or "connection" in text:
        return "NETWORK_ERROR"
    return "SCRIPT_ERROR"


def normalize_report_errors(report):
    raw_errors = safe_json_loads(getattr(report, "errors_json", None), [])
    if not isinstance(raw_errors, list):
        raw_errors = []
    normalized = []
    for item in raw_errors:
        if not isinstance(item, dict):
            item = {"message": str(item)}
        message = item.get("message") or item.get("error") or item.get("detail") or ""
        raw_status_code = item.get("status_code")
        if raw_status_code in (None, "", 0):
            raw_status_code = item.get("status")
        try:
            status_code = int(raw_status_code or 0)
        except (TypeError, ValueError):
            status_code = 0
        request_sample = item.get("request_sample")
        if request_sample in (None, "", {}):
            request_sample = item.get("request") or {}
        response_sample = item.get("response_sample")
        if response_sample in (None, ""):
            response_sample = item.get("response") or item.get("response_body") or ""
        normalized.append({
            "timestamp": item.get("timestamp") or item.get("time") or str(getattr(report, "created_at", "")),
            "method": item.get("method") or getattr(report, "request_method", "GET"),
            "url": item.get("url") or item.get("request_url") or getattr(report, "request_url", ""),
            "status_code": status_code,
            "response_time_ms": item.get("response_time_ms") or item.get("latency_ms") or 0,
            "error_type": item.get("error_type") or item.get("type") or infer_error_type(status_code, message),
            "message": message,
            "request_sample": request_sample,
            "response_sample": response_sample,
        })
    if normalized:
        return normalized
    try:
        failed_count = int(getattr(report, "failed_count", 0) or 0)
    except (TypeError, ValueError):
        failed_count = 0
    try:
        error_rate = float(getattr(report, "error_rate", 0) or 0)
    except (TypeError, ValueError):
        error_rate = 0
    if failed_count > 0 or error_rate > 0:
        return [{
            "timestamp": str(getattr(report, "created_at", "")),
            "method": getattr(report, "request_method", "GET"),
            "url": getattr(report, "request_url", ""),
            "status_code": 0,
            "response_time_ms": 0,
            "error_type": "UNSAMPLED_FAILURE",
            "message": "本次压测存在失败请求，但当前报告没有保留错误样本，请重新执行以采集完整失败明细。",
            "request_sample": build_request_sample(report),
            "response_sample": "",
        }]
    return []


def percentile(values, p):
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * p) - 1))
    return ordered[idx]


def get_effective_load_config(plan):
    mode = getattr(plan, "load_mode", "concurrency") or "concurrency"
    config = safe_json_loads(getattr(plan, "load_config", None), {})
    if not isinstance(config, dict):
        config = {}
    if mode == "qps":
        target_qps = float(config.get("target_qps") or 1)
        max_concurrency = max(int(config.get("max_concurrency") or getattr(plan, "concurrency", 1) or 1), 1)
        return {
            "mode": mode,
            "concurrency": max_concurrency,
            "duration_seconds": max(int(config.get("duration_seconds") or getattr(plan, "duration_seconds", 60) or 60), 1),
            "iterations": max(int(config.get("iterations") or getattr(plan, "iterations", 0) or 0), 0),
            "ramp_up_seconds": 0,
            "think_time_ms": 0,
            "request_timeout_ms": max(int(config.get("request_timeout_ms") or getattr(plan, "request_timeout_ms", 10000) or 10000), 1000),
            "target_qps": target_qps,
            "per_worker_interval": max(max_concurrency / target_qps, 0) if target_qps > 0 else 0,
        }
    return {
        "mode": "concurrency",
        "concurrency": max(int(config.get("concurrency") or getattr(plan, "concurrency", 1) or 1), 1),
        "duration_seconds": max(int(config.get("duration_seconds") or getattr(plan, "duration_seconds", 60) or 60), 1),
        "iterations": max(int(config.get("iterations") or getattr(plan, "iterations", 0) or 0), 0),
        "ramp_up_seconds": max(int(config.get("ramp_up_seconds") or getattr(plan, "ramp_up_seconds", 0) or 0), 0),
        "think_time_ms": max(int(config.get("think_time_ms") or getattr(plan, "think_time_ms", 0) or 0), 0),
        "request_timeout_ms": max(int(config.get("request_timeout_ms") or getattr(plan, "request_timeout_ms", 10000) or 10000), 1000),
        "target_qps": 0,
        "per_worker_interval": 0,
    }


def compare_value(actual, operator, expected):
    if operator == "<":
        return actual < expected
    if operator == "<=":
        return actual <= expected
    if operator == ">":
        return actual > expected
    if operator == ">=":
        return actual >= expected
    if operator == "=":
        return actual == expected
    return False


def summarize_threshold(report):
    checks = []
    if getattr(report, "summary_json", None):
        summary = safe_json_loads(report.summary_json, {})
        threshold_config = summary.get("threshold_config") or []
        metric_map = {
            "avg_rt_ms": float(report.avg_rt_ms or 0),
            "p90_rt_ms": float(report.p90_rt_ms or 0),
            "p95_rt_ms": float(report.p95_rt_ms or 0),
            "p99_rt_ms": float(report.p99_rt_ms or 0),
            "max_rt_ms": float(report.max_rt_ms or 0),
            "avg_rps": float(report.avg_rps or 0),
            "error_rate": float(report.error_rate or 0),
            "success_rate": round(100 - float(report.error_rate or 0), 2),
            "assertion_failed_count": float(summary.get("assertion_failed_count") or 0),
        }
        unit_map = {
            "avg_rt_ms": "ms",
            "p90_rt_ms": "ms",
            "p95_rt_ms": "ms",
            "p99_rt_ms": "ms",
            "max_rt_ms": "ms",
            "avg_rps": "",
            "error_rate": "%",
            "success_rate": "%",
            "assertion_failed_count": "",
        }
        if threshold_config:
            for item in threshold_config:
                metric = item.get("metric")
                if metric not in metric_map:
                    continue
                expected = float(item.get("value") or 0)
                operator = item.get("operator") or "<="
                unit = unit_map.get(metric, "")
                label = item.get("label") or metric
                actual = metric_map[metric]
                checks.append({
                    "name": label,
                    "expected": f"{operator} {expected}{unit}",
                    "actual": f"{actual}{unit}",
                    "passed": compare_value(actual, operator, expected),
                })
        else:
            expect_p95_ms = summary.get("expect_p95_ms")
            if expect_p95_ms is not None:
                checks.append({
                    "name": "P95",
                    "expected": f"<= {expect_p95_ms}ms",
                    "actual": f"{report.p95_rt_ms}ms",
                    "passed": float(report.p95_rt_ms or 0) <= float(expect_p95_ms),
                })
            expect_error_rate = summary.get("expect_error_rate")
            if expect_error_rate is not None:
                checks.append({
                    "name": "错误率",
                    "expected": f"<= {expect_error_rate}%",
                    "actual": f"{report.error_rate}%",
                    "passed": float(report.error_rate or 0) <= float(expect_error_rate),
                })
    return checks


async def append_run_log(run_id, executor, level, message, detail=None):
    async with async_session() as session:
        async with session.begin():
            log_model = ArgusPerformanceRunLog(
                executor,
                run_id=run_id,
                level=level,
                message=message,
                detail=stringify_json(detail, fallback="") if isinstance(detail, (dict, list)) else (detail or ""),
            )
            session.add(log_model)


async def build_case_chain_preview(case_ids):
    if not case_ids:
        return []
    async with async_session() as session:
        rows = await session.execute(
            select(TestCase).where(
                TestCase.id.in_(case_ids),
                TestCase.deleted_at == 0,
            )
        )
        cases = {item.id: item for item in rows.scalars().all()}
        assert_rows = await session.execute(
            select(TestCaseAsserts).where(
                TestCaseAsserts.case_id.in_(case_ids),
                TestCaseAsserts.deleted_at == 0,
            ).order_by(TestCaseAsserts.case_id.asc(), TestCaseAsserts.id.asc())
        )
        assert_map = defaultdict(list)
        for item in assert_rows.scalars().all():
            assert_type = str(item.assert_type or "")
            assert_map[int(item.case_id or 0)].append({
                "name": item.name,
                "type": assert_type,
                "operator": "=" if assert_type == "equal" else "contains" if assert_type == "contain" else assert_type,
                "path": item.actually,
                "expected": item.expected,
            })
        preview = []
        for index, case_id in enumerate(case_ids, 1):
            case = cases.get(case_id)
            if case is None:
                continue
            raw_url = str(case.url or "")
            parsed_url = urlsplit(raw_url)
            request_url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", parsed_url.fragment)) or raw_url
            request_query = {}
            for key, value in parse_qsl(parsed_url.query, keep_blank_values=True):
                if key in request_query:
                    current = request_query[key]
                    if isinstance(current, list):
                        current.append(value)
                    else:
                        request_query[key] = [current, value]
                else:
                    request_query[key] = value
            out_params = await session.execute(
                select(ArgusTestCaseOutParameters).where(
                    ArgusTestCaseOutParameters.case_id == case_id,
                    ArgusTestCaseOutParameters.deleted_at == 0,
                ).order_by(ArgusTestCaseOutParameters.id)
            )
            extractors = []
            for item in out_params.scalars().all():
                extractors.append({
                    "name": item.name,
                    "expression": item.expression,
                    "source": item.source,
                    "match_index": item.match_index,
                })
            preview.append({
                "step_order": index,
                "case_id": case.id,
                "name": case.name,
                "method": case.request_method,
                "url": request_url,
                "headers": safe_json_loads(case.request_headers, {}),
                "query": request_query,
                "body": safe_json_loads(case.body, case.body or ""),
                "assertions": assert_map.get(int(case.id or 0), []),
                "extractors": extractors,
                "enabled": True,
            })
        return preview


async def create_report(plan, executor, status=0):
    async with async_session() as session:
        async with session.begin():
            initial_summary = {
                "source_type": getattr(plan, "source_type", "single"),
                "load_mode": getattr(plan, "load_mode", "concurrency"),
            }
            report = ArgusPerformanceReport(
                executor,
                plan_id=plan.id,
                env=plan.env,
                plan_name=plan.name,
                request_method=plan.request_method,
                request_url=plan.request_url,
                concurrency=plan.concurrency,
                duration_seconds=plan.duration_seconds,
                status=status,
                summary_json=json.dumps(initial_summary, ensure_ascii=False),
            )
            report.executor = executor
            session.add(report)
            await session.flush()
            return report.id


async def update_report_status(report_id, status):
    async with async_session() as session:
        async with session.begin():
            row = await session.execute(
                select(ArgusPerformanceReport).where(
                    ArgusPerformanceReport.id == report_id,
                    ArgusPerformanceReport.deleted_at == 0,
                )
            )
            report = row.scalars().first()
            if report is None:
                return
            report.status = status


async def finalize_report(report_id, summary):
    async with async_session() as session:
        async with session.begin():
            row = await session.execute(
                select(ArgusPerformanceReport).where(
                    ArgusPerformanceReport.id == report_id,
                    ArgusPerformanceReport.deleted_at == 0,
                )
            )
            report = row.scalars().first()
            if report is None:
                raise Exception("性能报告不存在")
            report.status = 3
            report.updated_at = datetime.now()
            report.cost = summary.get("cost")
            report.total_requests = summary.get("total_requests", 0)
            report.success_count = summary.get("success_count", 0)
            report.failed_count = summary.get("failed_count", 0)
            report.avg_rt_ms = summary.get("avg_rt_ms")
            report.min_rt_ms = summary.get("min_rt_ms")
            report.max_rt_ms = summary.get("max_rt_ms")
            report.p50_rt_ms = summary.get("p50_rt_ms")
            report.p90_rt_ms = summary.get("p90_rt_ms")
            report.p95_rt_ms = summary.get("p95_rt_ms")
            report.p99_rt_ms = summary.get("p99_rt_ms")
            report.avg_rps = summary.get("avg_rps")
            report.error_rate = summary.get("error_rate")
            report.summary_json = json.dumps(summary.get("summary", {}), ensure_ascii=False)
            report.timeline_json = json.dumps(summary.get("timeline", []), ensure_ascii=False)
            report.errors_json = json.dumps(summary.get("errors", []), ensure_ascii=False)


async def safe_finalize_report(report_id, summary, retries=3):
    last_error = None
    for _ in range(retries):
        try:
            await finalize_report(report_id, summary)
            return True
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    raise last_error


async def mark_report_terminal(report_id, status=3):
    async with async_session() as session:
        async with session.begin():
            row = await session.execute(
                select(ArgusPerformanceReport).where(
                    ArgusPerformanceReport.id == report_id,
                    ArgusPerformanceReport.deleted_at == 0,
                )
            )
            report = row.scalars().first()
            if report is None:
                return
            report.status = status
            report.updated_at = datetime.now()


async def execute_one_request(plan, variables=None, global_headers=None):
    variables = variables or {}
    headers = replace_variables(normalize_headers(plan.request_headers), variables)
    if global_headers:
        headers = {**dict(global_headers), **headers}
    query = replace_variables(normalize_query(plan.request_query), variables)
    body = replace_variables(normalize_body(plan.request_body), variables)
    request_url = replace_variables(getattr(plan, "request_url", ""), variables)
    timeout_seconds = max(int(plan.request_timeout_ms or 10000) / 1000, 1)

    kwargs = {"headers": headers, "timeout": timeout_seconds}
    if query:
        kwargs["params"] = query
    if body is not None:
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["data"] = body
    request_client = AsyncRequest(request_url, **kwargs)
    response = await request_client.invoke(str(plan.request_method or "GET").upper())
    response["request_url"] = request_url
    response["request_headers"] = headers
    response["request_query"] = query
    response["request_body"] = body
    return response


def parse_case_ids(case_list):
    if not case_list:
        return []
    if isinstance(case_list, list):
        values = case_list
    else:
        values = str(case_list).split(",")
    result = []
    for item in values:
        value = str(item).replace("testcase_", "").strip()
        if value:
            result.append(int(value))
    return result


async def execute_case_link(plan, executor, variables=None, global_headers=None):
    case_ids = parse_case_ids(plan.case_list)
    if not case_ids:
        return {"status_code": 400, "msg": "未选择接口用例", "response": ""}
    shared_params = dict(variables or {})
    responses = []
    step_metrics = []
    step_records = []
    for index, case_id in enumerate(case_ids, 1):
        runner = Executor(runtime_user_id=executor)
        step_start = time.perf_counter()
        request_param = {}
        if global_headers:
            request_param["request_headers"] = dict(global_headers)
        result, err = await runner.run(
            plan.env,
            case_id,
            params_pool=shared_params,
            request_param=request_param,
            path=f"性能链路-{index}",
        )
        step_cost_ms = round((time.perf_counter() - step_start) * 1000, 2)
        step_record = {
            "case_id": case_id,
            "name": result.get("name") or f"接口用例#{case_id}",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "success",
            "status_code": int(result.get("status_code") or 0),
            "response_time_ms": step_cost_ms,
            "message": result.get("msg") or "请求成功",
            "request": {
                "method": result.get("request_method"),
                "url": result.get("request_url") or result.get("url"),
                "headers": result.get("request_headers") or {},
                "query": result.get("request_query") or {},
                "body": result.get("request_body"),
            },
            "response_sample": result.get("response"),
            "assertion_results": normalize_executor_asserts(result.get("assertion_results") or result.get("asserts")),
        }
        if err is not None or not result.get("status"):
            step_record["status"] = "error"
            step_record["status_code"] = int(result.get("status_code") or 500)
            step_record["message"] = err or f"接口用例#{case_id}断言失败"
            step_records.append(step_record)
            return {
                "status_code": 500,
                "msg": err or f"接口用例#{case_id}断言失败",
                "error_type": infer_error_type(500, err or "assert failed"),
                "case_id": case_id,
                "step_metrics": step_metrics,
                "steps": step_records,
                "step_count": len(step_records),
                "response": result,
            }
        step_records.append(step_record)
        responses.append({
            "case_id": case_id,
            "status_code": result.get("status_code"),
            "url": result.get("url"),
        })
        step_metrics.append({
            "case_id": case_id,
            "name": result.get("name") or f"接口用例#{case_id}",
            "url": result.get("url"),
            "request_method": result.get("request_method"),
            "status_code": result.get("status_code"),
            "cost_ms": step_cost_ms,
            "success": True,
        })
    return {
        "status_code": 200,
        "msg": "success",
        "response": responses,
        "step_metrics": step_metrics,
        "steps": step_records,
        "step_count": len(step_records),
    }


async def execute_plan_once(plan, executor, variables=None, global_headers=None):
    if getattr(plan, "source_type", "single") in {"link", "api_scenario"}:
        return await execute_case_link(plan, executor, variables=variables, global_headers=global_headers)
    return await execute_one_request(plan, variables=variables, global_headers=global_headers)


async def _run_plan_task_impl(plan_id: int, executor: int, report_id: int = None):
    async with async_session() as session:
        row = await session.execute(
            select(ArgusPerformancePlan).where(
                ArgusPerformancePlan.id == plan_id,
                ArgusPerformancePlan.deleted_at == 0,
            )
        )
        plan = row.scalars().first()
        if plan is None:
            log.error(f"性能计划不存在, id={plan_id}")
            return
        if report_id is None:
            report_id = await create_report(plan, executor, status=0)
        runtime_config = get_effective_load_config(plan)
        parameter_config = normalize_parameter_config(safe_json_loads(getattr(plan, "parameter_config", None), {}))
        assertions_config = safe_json_loads(getattr(plan, "assertions_config", None), [])
        chain_preview = await build_case_chain_preview(parse_case_ids(getattr(plan, "case_list", "")))
        parameter_sources = await preload_parameter_sources(session, parameter_config)

    await update_report_status(report_id, 1)
    await append_run_log(report_id, executor, "INFO", "启动压测任务", {
        "plan_id": plan_id,
        "source_type": getattr(plan, "source_type", "single"),
        "load_mode": getattr(plan, "load_mode", "concurrency"),
    })
    await append_run_log(report_id, executor, "INFO", "加载压测配置", {
        "concurrency": runtime_config.get("concurrency"),
        "duration_seconds": runtime_config.get("duration_seconds"),
        "iterations": runtime_config.get("iterations"),
        "request_timeout_ms": runtime_config.get("request_timeout_ms"),
        "request_url": getattr(plan, "request_url", ""),
    })

    start = time.perf_counter()
    start_at = datetime.now()
    duration_seconds = runtime_config["duration_seconds"]
    deadline = start + duration_seconds
    concurrency = runtime_config["concurrency"]
    think_seconds = max(int(runtime_config["think_time_ms"] or 0), 0) / 1000
    ramp_up_seconds = max(int(runtime_config["ramp_up_seconds"] or 0), 0)
    per_worker_interval = float(runtime_config.get("per_worker_interval") or 0)
    remaining = max(int(runtime_config["iterations"] or 0), 0)
    counters = dict(total=0, success=0, failed=0)
    latencies = []
    timeline = defaultdict(lambda: {"success": 0, "failed": 0, "latencies": []})
    error_records = []
    success_records = []
    assertion_failures = []
    step_stats = defaultdict(lambda: {"name": "", "url": "", "method": "", "count": 0, "failed": 0, "costs": []})
    lock = asyncio.Lock()
    parameter_state_lock = asyncio.Lock()
    execution_state = {}
    final_summary = None
    runtime_error_log_count = 0
    request_failure_log_count = 0
    setup_config = parameter_config.get("setup_config") or {}
    global_setup_variables = {}
    setup_records = []

    if setup_config.get("enabled") and setup_config.get("scope") == "per_run":
        try:
            manual_variables, file_variables = await resolve_parameter_variables(
                parameter_sources,
                execution_state,
                parameter_state_lock,
            )
            setup_seed_variables = {}
            setup_seed_variables.update(manual_variables)
            setup_seed_variables.update(file_variables)
            setup_headers = build_runtime_global_headers(parameter_config, setup_seed_variables)
            setup_result = await execute_setup_sequence(
                plan,
                executor,
                setup_config,
                variables=setup_seed_variables,
                request_headers=setup_headers,
            )
            global_setup_variables = setup_result.get("variables") or {}
            setup_records.extend(setup_result.get("records") or [])
            await append_run_log(report_id, executor, "INFO", "执行全局预置完成", {
                "scope": "per_run",
                "source": setup_config.get("source"),
                "case_list": setup_config.get("case_list"),
                "exported_variables": sorted(global_setup_variables.keys()),
            })
        except Exception as exc:
            await append_run_log(report_id, executor, "ERROR", "执行全局预置失败", {
                "scope": "per_run",
                "source": setup_config.get("source"),
                "case_list": setup_config.get("case_list"),
                "message": format_exception_message(exc),
            })
            raise

    async def worker(index: int):
        nonlocal remaining, runtime_error_log_count, request_failure_log_count
        worker_setup_variables = dict(global_setup_variables)
        if ramp_up_seconds > 0 and concurrency > 1:
            await asyncio.sleep((ramp_up_seconds / concurrency) * index)
        if setup_config.get("enabled") and setup_config.get("scope") == "per_worker":
            try:
                manual_variables, file_variables = await resolve_parameter_variables(
                    parameter_sources,
                    execution_state,
                    parameter_state_lock,
                )
                setup_seed_variables = {}
                setup_seed_variables.update(manual_variables)
                setup_seed_variables.update(file_variables)
                setup_headers = build_runtime_global_headers(parameter_config, setup_seed_variables)
                setup_result = await execute_setup_sequence(
                    plan,
                    executor,
                    setup_config,
                    variables=setup_seed_variables,
                    request_headers=setup_headers,
                )
                worker_setup_variables = setup_result.get("variables") or {}
                async with lock:
                    setup_records.extend(setup_result.get("records") or [])
                await append_run_log(report_id, executor, "INFO", "执行全局预置完成", {
                    "scope": "per_worker",
                    "worker": index + 1,
                    "source": setup_config.get("source"),
                    "case_list": setup_config.get("case_list"),
                    "exported_variables": sorted(worker_setup_variables.keys()),
                })
            except Exception as exc:
                async with lock:
                    counters["total"] += 1
                    counters["failed"] += 1
                    timeline[0]["failed"] += 1
                    error_records.append({
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "error",
                        "method": "SETUP",
                        "url": getattr(plan, "request_url", ""),
                        "status_code": 500,
                        "response_time_ms": 0,
                        "error_type": "SETUP_FAILED",
                        "message": format_exception_message(exc),
                        "request_sample": {"setup_config": setup_config, "worker": index + 1},
                        "response_sample": "",
                        "assertion_results": [],
                        "assertion_failed": False,
                        "sample_scope": "setup",
                        "setup_scope": "per_worker",
                    })
                await append_run_log(report_id, executor, "ERROR", "执行全局预置失败", {
                    "scope": "per_worker",
                    "worker": index + 1,
                    "source": setup_config.get("source"),
                    "case_list": setup_config.get("case_list"),
                    "message": format_exception_message(exc),
                })
                return
        while True:
            now = time.perf_counter()
            if remaining == 0 and int(runtime_config["iterations"] or 0) > 0:
                return
            if int(runtime_config["iterations"] or 0) == 0 and now >= deadline:
                return
            if int(runtime_config["iterations"] or 0) > 0:
                async with lock:
                    if remaining <= 0:
                        return
                    remaining -= 1
            request_start = time.perf_counter()
            bucket = int(max(request_start - start, 0))
            try:
                failure_log_detail = None
                manual_variables, file_variables = await resolve_parameter_variables(
                    parameter_sources,
                    execution_state,
                    parameter_state_lock,
                )
                variables = {}
                variables.update(manual_variables)
                variables.update(file_variables)
                variables.update(worker_setup_variables)
                runtime_global_headers = build_runtime_global_headers(parameter_config, variables)
                result = await execute_plan_once(
                    plan,
                    executor,
                    variables=variables,
                    global_headers=runtime_global_headers,
                )
                latency_ms = round((time.perf_counter() - request_start) * 1000, 2)
                status_code = int(result.get("status_code") or 0)
                success = 200 <= status_code < 400
                assertion_passed = True
                assertion_results = []
                if success and getattr(plan, "source_type", "single") not in {"link", "api_scenario"}:
                    assertion_passed, assertion_results = evaluate_assertions(result, assertions_config)
                    if not assertion_passed:
                        success = False
                        result["error_type"] = "ASSERT_FAILED"
                        result["msg"] = "断言校验失败"
                async with lock:
                    counters["total"] += 1
                    if success:
                        counters["success"] += 1
                        timeline[bucket]["success"] += 1
                        if isinstance(result.get("steps"), list):
                            result["sample_scope"] = "runtime_chain"
                        else:
                            result["sample_scope"] = "runtime"
                        success_records.append(build_request_record(
                            "success",
                            latency_ms,
                            result=result,
                            plan=plan,
                            variables=variables,
                            message=result.get("msg") or "请求成功",
                            assertion_results=assertion_results,
                        ))
                    else:
                        counters["failed"] += 1
                        timeline[bucket]["failed"] += 1
                        if assertion_results and not assertion_passed:
                            assertion_failures.extend([item for item in assertion_results if not item.get("passed")])
                        message = result.get("msg") or "请求失败"
                        if isinstance(result.get("steps"), list):
                            result["sample_scope"] = "runtime_chain"
                        else:
                            result["sample_scope"] = "runtime"
                        current_error = build_request_record(
                            "assertion_failed" if assertion_results and not assertion_passed else "error",
                            latency_ms,
                            result=result,
                            plan=plan,
                            variables=variables,
                            message=message,
                            assertion_results=assertion_results,
                        )
                        error_records.append(current_error)
                        if request_failure_log_count < 20:
                            request_failure_log_count += 1
                            failure_log_detail = {
                                "message": message,
                                "status_code": status_code,
                                "url": result.get("request_url") or getattr(plan, "request_url", ""),
                                "assertion_results": assertion_results,
                            }
                    latencies.append(latency_ms)
                    timeline[bucket]["latencies"].append(latency_ms)
                    step_metrics = result.get("step_metrics") or []
                    if step_metrics:
                        for metric in step_metrics:
                            key = str(metric.get("case_id") or metric.get("url") or metric.get("name"))
                            stat = step_stats[key]
                            stat["name"] = metric.get("name") or stat["name"]
                            stat["url"] = metric.get("url") or stat["url"]
                            stat["method"] = metric.get("request_method") or stat["method"]
                            stat["count"] += 1
                            stat["failed"] += 0 if metric.get("success", True) else 1
                            stat["costs"].append(float(metric.get("cost_ms") or latency_ms))
                    else:
                        key = getattr(plan, "request_url", "")
                        stat = step_stats[key]
                        stat["name"] = getattr(plan, "name", "压测接口")
                        stat["url"] = result.get("request_url") or getattr(plan, "request_url", "")
                        stat["method"] = getattr(plan, "request_method", "GET")
                        stat["count"] += 1
                        stat["failed"] += 0 if success else 1
                        stat["costs"].append(float(latency_ms))
                if failure_log_detail:
                    await append_run_log(report_id, executor, "ERROR", "请求执行失败", failure_log_detail)
            except Exception as exc:
                latency_ms = round((time.perf_counter() - request_start) * 1000, 2)
                runtime_log_detail = None
                async with lock:
                    counters["total"] += 1
                    counters["failed"] += 1
                    latencies.append(latency_ms)
                    timeline[bucket]["failed"] += 1
                    timeline[bucket]["latencies"].append(latency_ms)
                    message = format_exception_message(exc)
                    current_error = build_request_record(
                        "error",
                        latency_ms,
                        result={},
                        plan=plan,
                        variables=variables if 'variables' in locals() else {},
                        message=message,
                        assertion_results=[],
                    )
                    error_records.append(current_error)
                    if runtime_error_log_count < 5:
                        runtime_error_log_count += 1
                        runtime_log_detail = {
                            "message": message,
                            "worker": index,
                            "bucket_second": bucket,
                            "response_time_ms": latency_ms,
                            "total_requests": counters["total"],
                            "failed_count": counters["failed"],
                            "request_url": getattr(plan, "request_url", ""),
                        }
                if runtime_log_detail:
                    await append_run_log(report_id, executor, "ERROR", "压测执行异常", runtime_log_detail)
            request_cost = time.perf_counter() - request_start
            if per_worker_interval > 0:
                await asyncio.sleep(max(per_worker_interval - request_cost, 0))
            if think_seconds > 0:
                await asyncio.sleep(think_seconds)

    def build_runtime_summary(extra_error=None, elapsed_override=None):
        elapsed = max(elapsed_override if elapsed_override is not None else (time.perf_counter() - start), 0.001)
        total = counters["total"]
        success_count = counters["success"]
        failed_count = counters["failed"]
        avg_rt = round(sum(latencies) / len(latencies), 2) if latencies else 0
        min_rt = round(min(latencies), 2) if latencies else 0
        max_rt = round(max(latencies), 2) if latencies else 0
        timeline_rows = []
        for second in sorted(timeline.keys()):
            item = timeline[second]
            avg_bucket = round(sum(item["latencies"]) / len(item["latencies"]), 2) if item["latencies"] else 0
            timeline_rows.append({
                "second": second,
                "label": f"{second + 1}s",
                "rps": item["success"] + item["failed"],
                "success": item["success"],
                "failed": item["failed"],
                "avg_rt_ms": avg_bucket,
            })
        api_rankings = []
        for key, item in step_stats.items():
            if not item["count"]:
                continue
            avg_cost = round(sum(item["costs"]) / len(item["costs"]), 2) if item["costs"] else 0
            p95_cost = round(percentile(item["costs"], 0.95), 2) if item["costs"] else 0
            error_rate = round((item["failed"] / item["count"]) * 100, 2) if item["count"] else 0
            api_rankings.append({
                "key": key,
                "name": item["name"] or key,
                "url": item["url"],
                "method": item["method"],
                "count": item["count"],
                "avg_rt_ms": avg_cost,
                "p95_rt_ms": p95_cost,
                "error_rate": error_rate,
            })
        api_rankings.sort(key=lambda item: (-item["p95_rt_ms"], -item["avg_rt_ms"], item["error_rate"]))
        failed_reasons = [
            f"{item.get('name')} P95={item.get('p95_rt_ms')}ms，错误率={item.get('error_rate')}%"
            for item in api_rankings[:3] if item.get("error_rate", 0) > 0 or item.get("p95_rt_ms", 0) > float(plan.expect_p95_ms or 0)
        ]
        if assertion_failures:
            failed_reasons.insert(0, f"断言失败 {len(assertion_failures)} 次，请优先检查响应内容、状态码和业务字段是否符合预期。")
        if extra_error:
            failed_reasons.insert(0, f"报告收尾异常: {extra_error}")
            error_records.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "method": getattr(plan, "request_method", "GET"),
                "url": getattr(plan, "request_url", ""),
                "status_code": 0,
                "response_time_ms": 0,
                "error_type": infer_error_type(0, str(extra_error)),
                "message": str(extra_error),
                "request_sample": build_request_sample(plan),
                "response_sample": "",
                "sample_scope": "report_finalize",
            })
        persisted_request_records = [compact_request_record(item) for item in success_records[:MAX_REPORT_REQUEST_RECORDS]]
        persisted_errors = [compact_request_record(item) for item in error_records]
        persisted_setup_records = [
            compact_request_record({
                "timestamp": item.get("timestamp"),
                "status": item.get("status"),
                "method": item.get("request", {}).get("method"),
                "url": item.get("request", {}).get("url"),
                "status_code": item.get("status_code"),
                "response_time_ms": item.get("response_time_ms"),
                "error_type": "SETUP_FAILED" if item.get("status") != "success" else "",
                "message": item.get("message"),
                "request_sample": {
                    "request": item.get("request") or {},
                    "steps": [compact_step_record(item)],
                },
                "response_sample": item.get("response_sample"),
                "assertion_results": [],
                "assertion_failed": False,
                "sample_scope": "setup",
                "setup_scope": setup_config.get("scope"),
                "step_count": 1,
            })
            for item in setup_records
        ]
        return {
            "total_requests": total,
            "success_count": success_count,
            "failed_count": failed_count,
            "avg_rt_ms": f"{avg_rt:.2f}",
            "min_rt_ms": f"{min_rt:.2f}",
            "max_rt_ms": f"{max_rt:.2f}",
            "p50_rt_ms": f"{percentile(latencies, 0.50):.2f}" if latencies else "0.00",
            "p90_rt_ms": f"{percentile(latencies, 0.90):.2f}" if latencies else "0.00",
            "p95_rt_ms": f"{percentile(latencies, 0.95):.2f}" if latencies else "0.00",
            "p99_rt_ms": f"{percentile(latencies, 0.99):.2f}" if latencies else "0.00",
            "avg_rps": f"{(total / elapsed):.2f}",
            "error_rate": f"{((failed_count / total) * 100 if total else 0):.2f}",
            "cost": f"{elapsed:.2f}s",
            "timeline": timeline_rows,
            "errors": persisted_errors,
            "summary": {
                "plan_id": plan.id,
                "plan_name": plan.name,
                "env": plan.env,
                "started_at": start_at.strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "concurrency": concurrency,
                "duration_seconds": duration_seconds,
                "iterations": int(runtime_config["iterations"] or 0),
                "expect_p95_ms": plan.expect_p95_ms,
                "expect_error_rate": plan.expect_error_rate,
                "source_type": getattr(plan, "source_type", "single"),
                "case_list": getattr(plan, "case_list", ""),
                "chain_preview": chain_preview,
                "load_mode": getattr(plan, "load_mode", "concurrency"),
                "load_config": safe_json_loads(getattr(plan, "load_config", None), {}),
                "threshold_config": safe_json_loads(getattr(plan, "threshold_config", None), []),
                "parameter_config": parameter_config,
                "parameter_snapshot": {
                    "manual_variables": parameter_config.get("manual_variables") or [],
                    "file_variables": parameter_config.get("file_variables") or [],
                    "last_file_variables": file_variables if 'file_variables' in locals() else {},
                },
                "request_records": persisted_request_records,
                "request_records_total": len(success_records),
                "request_records_sampled": len(persisted_request_records),
                "request_records_truncated": len(success_records) > len(persisted_request_records),
                "setup_records": persisted_setup_records,
                "setup_records_total": len(setup_records),
                "error_records_total": len(error_records),
                "assertions_config": assertions_config,
                "assertion_failed_count": len(assertion_failures),
                "assertion_failures": assertion_failures[:20],
                "request_snapshot": build_request_sample(plan),
                "setup_snapshot": {
                    "enabled": bool(setup_config.get("enabled")),
                    "scope": setup_config.get("scope"),
                    "case_list": setup_config.get("case_list"),
                },
                "api_rankings": api_rankings[:10],
                "failed_reasons": failed_reasons,
            },
        }

    try:
        await asyncio.gather(*(worker(i) for i in range(concurrency)))
        total = counters["total"]
        success_count = counters["success"]
        failed_count = counters["failed"]
        final_summary = build_runtime_summary()
        await append_run_log(report_id, executor, "INFO", "压测执行完成", {
            "total_requests": total,
            "success_count": success_count,
            "failed_count": failed_count,
        })
        await append_run_log(report_id, executor, "INFO", "写入性能报告", {
            "timeline_points": len(final_summary.get("timeline") or []),
            "error_samples": len(final_summary.get("errors") or []),
            "request_records": len(final_summary.get("summary", {}).get("request_records") or []),
            "request_records_total": final_summary.get("summary", {}).get("request_records_total", 0),
            "assertion_failed_count": final_summary.get("summary", {}).get("assertion_failed_count", 0),
        })
        await safe_finalize_report(report_id, final_summary)
    except Exception as exc:
        error_message = format_exception_message(exc)
        log.exception(f"执行性能计划失败, id={plan_id}, error={error_message}")
        await append_run_log(report_id, executor, "ERROR", "压测任务执行失败", {
            "message": error_message,
            "total_requests": counters["total"],
            "success_count": counters["success"],
            "failed_count": counters["failed"],
            "timeline_points": len(timeline.keys()),
            "error_samples": len(error_records),
            "request_records": len(success_records),
        })
        recovery_summary = final_summary or build_runtime_summary(extra_error=error_message)
        if final_summary:
            recovery_summary["summary"]["failed_reasons"] = [
                f"报告收尾异常: {error_message}",
                *(recovery_summary["summary"].get("failed_reasons") or []),
            ]
            recovery_summary["errors"].append(compact_request_record({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "method": getattr(plan, "request_method", "GET"),
                "url": getattr(plan, "request_url", ""),
                "status_code": 0,
                "response_time_ms": 0,
                "error_type": infer_error_type(0, error_message),
                "message": error_message,
                "request_sample": build_request_sample(plan),
                "response_sample": "",
            }))
        try:
            await safe_finalize_report(report_id, recovery_summary)
        except Exception as finalize_exc:
            finalize_message = format_exception_message(finalize_exc)
            log.exception(f"性能报告收尾失败, id={report_id}, error={finalize_message}")
            try:
                await append_run_log(report_id, executor, "ERROR", "性能报告收尾失败", {
                    "message": finalize_message,
                    "total_requests": counters["total"],
                    "success_count": counters["success"],
                    "failed_count": counters["failed"],
                })
            except Exception:
                pass
            try:
                await mark_report_terminal(report_id, status=3)
            except Exception as mark_exc:
                log.exception(f"性能报告状态兜底失败, id={report_id}, error={format_exception_message(mark_exc)}")


async def run_plan_task(plan_id: int, executor: int, report_id: int = None):
    await PERFORMANCE_RUN_SEMAPHORE.acquire()
    try:
        return await _run_plan_task_impl(plan_id, executor, report_id=report_id)
    finally:
        PERFORMANCE_RUN_SEMAPHORE.release()


@router.get("/plan/list")
async def list_performance_plan(page: int, size: int, project_id: int = None, name: str = "", env: int = None,
                                create_user: int = None, follow: bool = None, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_performance_schema(session)
        follow = _normalize_bool_query(follow)
        follow_expr = (
            "CASE WHEN f.user_id IS NULL OR f.deleted_at <> 0 THEN 0 ELSE 1 END AS follow"
        )
        base_sql = (
            "SELECT p.*, "
            f"{follow_expr} "
            "FROM argus_performance_plan p "
            "LEFT JOIN argus_performance_plan_follow_user_rel f ON p.id=f.plan_id AND f.user_id=:user_id "
            "WHERE p.deleted_at=0 "
        )
        params = {"user_id": int(user_info["id"])}
        if project_id:
            base_sql += "AND p.project_id=:project_id "
            params["project_id"] = project_id
        if name:
            base_sql += "AND p.name LIKE :name "
            params["name"] = f"%{name}%"
        if env:
            base_sql += "AND p.env=:env "
            params["env"] = env
        if create_user:
            base_sql += "AND p.create_user=:create_user "
            params["create_user"] = create_user
        if follow is True:
            base_sql += "AND f.user_id IS NOT NULL AND f.deleted_at=0 "
        elif follow is False:
            base_sql += "AND (f.user_id IS NULL OR f.deleted_at<>0) "
        count_row = await session.execute(
            text(f"SELECT COUNT(1) AS total FROM ({base_sql}) t"),
            params,
        )
        total = int((count_row.mappings().first() or {}).get("total") or 0)
        if total == 0:
            return ArgusResponse.success_with_size([], 0)
        page_sql = f"{base_sql} ORDER BY p.updated_at DESC LIMIT :offset, :size"
        page_data = await session.execute(
            text(page_sql),
            {**params, "offset": max(page - 1, 0) * size, "size": size},
        )
        items = []
        for row in page_data.mappings().all():
            item = dict(row)
            item["follow"] = bool(item.get("follow"))
            items.append(item)
        return ArgusResponse.success_with_size(items, total=total)


@router.post("/plan/insert")
async def insert_performance_plan(form: ArgusPerformancePlanForm, user_info=Depends(Permission())):
    async with async_session() as session:
        async with session.begin():
            await ensure_performance_schema(session)
            payload = normalize_plan_payload(form.dict(exclude={"id"}))
            model = ArgusPerformancePlan(user_info["id"], **payload)
            session.add(model)
            await session.flush()
            await ArgusOperationDao.insert_log(session, user_info["id"], OperationType.INSERT, model, key=model.id)
            plan_id = model.id
    return ArgusResponse.success({"id": plan_id})


@router.post("/plan/update")
async def update_performance_plan(form: ArgusPerformancePlanForm, user_info=Depends(Permission())):
    async with async_session() as session:
        async with session.begin():
            await ensure_performance_schema(session)
            row = await session.execute(
                select(ArgusPerformancePlan).where(
                    ArgusPerformancePlan.id == form.id,
                    ArgusPerformancePlan.deleted_at == 0,
                )
            )
            model = row.scalars().first()
            if model is None:
                return ArgusResponse.failed("性能计划不存在")
            old = deepcopy(model)
            payload = normalize_plan_payload(form.dict(exclude_unset=True))
            for key, value in payload.items():
                if key == "id":
                    continue
                setattr(model, key, value)
            model.update_user = user_info["id"]
            model.updated_at = datetime.now()
            await session.flush()
            await ArgusOperationDao.insert_log(session, user_info["id"], OperationType.UPDATE, model, old, model.id, changed=[k for k in payload.keys() if k != "id"])
    return ArgusResponse.success()


@router.get("/plan/delete")
async def delete_performance_plan(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        async with session.begin():
            row = await session.execute(
                select(ArgusPerformancePlan).where(
                    ArgusPerformancePlan.id == id,
                    ArgusPerformancePlan.deleted_at == 0,
                )
            )
            model = row.scalars().first()
            if model is None:
                return ArgusResponse.failed("性能计划不存在")
            model.deleted_at = int(time.time() * 1000)
            model.update_user = user_info["id"]
            model.updated_at = datetime.now()
            await session.flush()
            await ArgusOperationDao.insert_log(session, user_info["id"], OperationType.DELETE, model, key=id)
    return ArgusResponse.success()


@router.get("/plan/execute")
async def execute_performance_plan(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_performance_schema(session)
        row = await session.execute(
            select(ArgusPerformancePlan).where(
                ArgusPerformancePlan.id == id,
                ArgusPerformancePlan.deleted_at == 0,
            )
        )
        plan = row.scalars().first()
        if plan is None:
            return ArgusResponse.failed("性能计划不存在")
    log_model = SimpleNamespace(
        name=plan.name,
        action="执行性能测试计划",
        source_type=getattr(plan, "source_type", "single"),
        __fields__=[SimpleNamespace(name="name"), SimpleNamespace(name="action"), SimpleNamespace(name="source_type")],
        __tag__="性能测试",
        __alias__={
            "name": "计划名称",
            "action": "执行动作",
            "source_type": "来源类型",
        },
        __show__=1,
    )
    async with async_session() as log_session:
        async with log_session.begin():
            await ArgusOperationDao.insert_log(
                log_session,
                user_info["id"],
                OperationType.UPDATE,
                log_model,
                key=plan.id,
                changed=["action", "source_type"],
            )
    report_id = await create_report(plan, user_info["id"], status=0)
    await append_run_log(report_id, user_info["id"], "INFO", "创建执行记录", {
        "plan_id": id,
        "plan_name": plan.name,
        "source_type": getattr(plan, "source_type", "single"),
    })
    platform_task = await PlatformTaskService.create_task(
        task_type=PlatformTaskType.PERFORMANCE_TEST_RUN.value,
        user_id=user_info["id"],
        biz_id=report_id,
        biz_type="performance_report",
        project_id=int(getattr(plan, "project_id", 0) or 0),
        plan_id=id,
        resource_key=f"performance_plan_{id}",
        payload={"plan_id": id, "report_id": report_id, "executor": user_info["id"]},
    )
    try:
        from app.core.platform_worker import platform_task_worker

        asyncio.create_task(platform_task_worker.kickoff_task_if_stuck(int(platform_task.id or 0), delay_seconds=0))
    except Exception as exc:
        log.warning(f"性能任务本地调度唤醒失败, task_id={getattr(platform_task, 'id', 0)}, error={format_exception_message(exc)}")
    return ArgusResponse.success(
        {"report_id": report_id, "platform_task_id": platform_task.id},
        msg="性能计划已入队，请稍后查看执行记录",
    )


@router.get("/plan/follow")
async def follow_performance_plan(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        async with session.begin():
            await ensure_performance_schema(session)
            row = await session.execute(
                select(ArgusPerformancePlan).where(
                    ArgusPerformancePlan.id == id,
                    ArgusPerformancePlan.deleted_at == 0,
                )
            )
            plan = row.scalars().first()
            if plan is None:
                return ArgusResponse.failed("性能计划不存在")
            exists = await session.execute(
                text(
                    "SELECT id FROM argus_performance_plan_follow_user_rel "
                    "WHERE deleted_at=0 AND plan_id=:plan_id AND user_id=:user_id"
                ),
                {"plan_id": int(id or 0), "user_id": int(user_info["id"])},
            )
            if exists.first():
                return ArgusResponse.failed("已关注过此性能计划")
            now_dt = datetime.now()
            await session.execute(
                text(
                    "INSERT INTO argus_performance_plan_follow_user_rel "
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
    return ArgusResponse.success(msg="关注成功")


@router.get("/plan/unfollow")
async def unfollow_performance_plan(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        async with session.begin():
            await ensure_performance_schema(session)
            row = await session.execute(
                text(
                    "SELECT id FROM argus_performance_plan_follow_user_rel "
                    "WHERE deleted_at=0 AND plan_id=:plan_id AND user_id=:user_id"
                ),
                {"plan_id": int(id or 0), "user_id": int(user_info["id"])},
            )
            data = row.mappings().first()
            if not data:
                return ArgusResponse.failed("已取关过此性能计划")
            await session.execute(
                text(
                    "UPDATE argus_performance_plan_follow_user_rel "
                    "SET deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at "
                    "WHERE id=:id"
                ),
                {
                    "id": int(data["id"] or 0),
                    "deleted_at": int(time.time() * 1000),
                    "update_user": int(user_info["id"]),
                    "updated_at": datetime.now(),
                },
            )
    return ArgusResponse.success(msg="取关成功")


@router.get("/report/list")
async def list_performance_report(page: int, size: int, start_time: str, end_time: str, executor: int = None,
                                  status: int = None, plan_id: int = None, _=Depends(Permission())):
    start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    list_columns = [
        ArgusPerformanceReport.id,
        ArgusPerformanceReport.plan_id,
        ArgusPerformanceReport.env,
        ArgusPerformanceReport.plan_name,
        ArgusPerformanceReport.request_method,
        ArgusPerformanceReport.request_url,
        ArgusPerformanceReport.concurrency,
        ArgusPerformanceReport.duration_seconds,
        ArgusPerformanceReport.status,
        ArgusPerformanceReport.executor,
        ArgusPerformanceReport.total_requests,
        ArgusPerformanceReport.success_count,
        ArgusPerformanceReport.failed_count,
        ArgusPerformanceReport.avg_rt_ms,
        ArgusPerformanceReport.min_rt_ms,
        ArgusPerformanceReport.max_rt_ms,
        ArgusPerformanceReport.p50_rt_ms,
        ArgusPerformanceReport.p90_rt_ms,
        ArgusPerformanceReport.p95_rt_ms,
        ArgusPerformanceReport.p99_rt_ms,
        ArgusPerformanceReport.avg_rps,
        ArgusPerformanceReport.error_rate,
        ArgusPerformanceReport.cost,
        ArgusPerformanceReport.created_at,
        ArgusPerformanceReport.updated_at.label("finished_at"),
        ArgusPerformanceReport.summary_json,
    ]
    async with async_session() as session:
        base_sql = select(*list_columns).where(
            ArgusPerformanceReport.deleted_at == 0,
            ArgusPerformanceReport.created_at.between(start, end),
        )
        if executor is not None:
            base_sql = base_sql.where(ArgusPerformanceReport.executor == executor)
        if status is not None:
            base_sql = base_sql.where(ArgusPerformanceReport.status == status)
        if plan_id is not None:
            base_sql = base_sql.where(ArgusPerformanceReport.plan_id == plan_id)
        count_sql = select(func.count(ArgusPerformanceReport.id)).select_from(ArgusPerformanceReport).where(
            ArgusPerformanceReport.deleted_at == 0,
            ArgusPerformanceReport.created_at.between(start, end),
        )
        if executor is not None:
            count_sql = count_sql.where(ArgusPerformanceReport.executor == executor)
        if status is not None:
            count_sql = count_sql.where(ArgusPerformanceReport.status == status)
        if plan_id is not None:
            count_sql = count_sql.where(ArgusPerformanceReport.plan_id == plan_id)
        total = int((await session.execute(count_sql)).scalar() or 0)
        if total == 0:
            return ArgusResponse.success_with_size([], 0)
        page_sql = base_sql.order_by(desc(ArgusPerformanceReport.created_at)).offset((page - 1) * size).limit(size)
        page_data = await session.execute(page_sql)
        records = [dict(item) for item in page_data.mappings().all()]
        missing_summary_plan_ids = list({
            int(item.get("plan_id") or 0) for item in records
            if item.get("plan_id") and not item.get("summary_json")
        })
        plan_map = {}
        if missing_summary_plan_ids:
            plan_rows = await session.execute(
                select(ArgusPerformancePlan).where(
                    ArgusPerformancePlan.id.in_(missing_summary_plan_ids),
                    ArgusPerformancePlan.deleted_at == 0,
                )
            )
            plan_map = {item.id: item for item in plan_rows.scalars().all()}
        for item in records:
            if item.get("summary_json"):
                continue
            plan = plan_map.get(int(item.get("plan_id") or 0))
            if plan is None:
                continue
            item["summary_json"] = json.dumps({
                "source_type": getattr(plan, "source_type", "single"),
                "load_mode": getattr(plan, "load_mode", "concurrency"),
            }, ensure_ascii=False)
        return ArgusResponse.success_with_size(records, total=total)


@router.get("/monitor/config")
async def query_performance_monitor_config(_=Depends(Permission())):
    return ArgusResponse.success({
        "grafana_url": resolve_grafana_url(),
    })


@router.get("/report")
async def query_performance_report(id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_performance_schema(session)
        row = await session.execute(
            select(ArgusPerformanceReport).where(
                ArgusPerformanceReport.id == id,
                ArgusPerformanceReport.deleted_at == 0,
            )
        )
        report = row.scalars().first()
        if report is None:
            return ArgusResponse.failed("性能报告不存在")
        errors = normalize_report_errors(report)
        log_rows = await session.execute(
            select(ArgusPerformanceRunLog).where(
                ArgusPerformanceRunLog.run_id == id,
                ArgusPerformanceRunLog.deleted_at == 0,
            ).order_by(ArgusPerformanceRunLog.created_at.asc())
        )
        return ArgusResponse.success({
            "report": report,
            "summary": safe_json_loads(report.summary_json, {}),
            "timeline": safe_json_loads(report.timeline_json, []),
            "errors": errors,
            "thresholds": summarize_threshold(report),
            "logs": log_rows.scalars().all(),
        })


@router.get("/run/logs")
async def query_performance_run_logs(run_id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_performance_schema(session)
        rows = await session.execute(
            select(ArgusPerformanceRunLog).where(
                ArgusPerformanceRunLog.run_id == run_id,
                ArgusPerformanceRunLog.deleted_at == 0,
            ).order_by(ArgusPerformanceRunLog.created_at.asc())
        )
        return ArgusResponse.success(rows.scalars().all())


@router.get("/parameter-files")
async def list_performance_parameter_files(project_id: int = None, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_performance_schema(session)
        sql = select(ArgusPerformanceParameterFile).where(ArgusPerformanceParameterFile.deleted_at == 0).order_by(
            desc(ArgusPerformanceParameterFile.updated_at)
        )
        if project_id:
            sql = sql.where(ArgusPerformanceParameterFile.project_id == project_id)
        rows = await session.execute(sql)
        return ArgusResponse.success(rows.scalars().all())


@router.post("/parameter-files/upload")
async def upload_performance_parameter_file(
        project_id: int = Form(...),
        file: UploadFile = File(...),
        name: str = Form(""),
        user_info=Depends(Permission())):
    async with async_session() as session:
        async with session.begin():
            await ensure_performance_schema(session)
            suffix = Path(file.filename or "").suffix.lower()
            if suffix not in {".csv"}:
                return ArgusResponse.failed("当前仅支持 CSV 参数文件")
            PERFORMANCE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            store_name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex}{suffix}"
            file_path = PERFORMANCE_UPLOAD_DIR / store_name
            content = await file.read()
            file_path.write_bytes(content)
            columns, rows = parse_csv_file(file_path)
            model = ArgusPerformanceParameterFile(
                user_info["id"],
                project_id=int(project_id),
                name=name or Path(file.filename or "参数文件").stem,
                file_name=file.filename or store_name,
                file_path=str(file_path),
                file_type="csv",
                columns=json.dumps(columns, ensure_ascii=False),
                row_count=len(rows),
                encoding="utf-8",
                delimiter=",",
            )
            session.add(model)
            await session.flush()
            await ArgusOperationDao.insert_log(session, user_info["id"], OperationType.INSERT, model, key=model.id)
            return ArgusResponse.success({
                "id": model.id,
                "file_name": model.file_name,
                "columns": columns,
                "row_count": len(rows),
            })


@router.get("/parameter-files/preview")
async def preview_performance_parameter_file(id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_performance_schema(session)
        row = await session.execute(
            select(ArgusPerformanceParameterFile).where(
                ArgusPerformanceParameterFile.id == id,
                ArgusPerformanceParameterFile.deleted_at == 0,
            )
        )
        record = row.scalars().first()
        if record is None:
            return ArgusResponse.failed("参数文件不存在")
        file_path = Path(record.file_path)
        if not file_path.exists():
            return ArgusResponse.failed("参数文件已丢失")
        columns, rows = parse_csv_file(file_path, record.encoding or "utf-8", record.delimiter or ",")
        return ArgusResponse.success({
            "columns": columns,
            "rows": rows[:10],
            "row_count": len(rows),
        })


@router.get("/parameter-files/delete")
async def delete_performance_parameter_file(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        async with session.begin():
            await ensure_performance_schema(session)
            row = await session.execute(
                select(ArgusPerformanceParameterFile).where(
                    ArgusPerformanceParameterFile.id == id,
                    ArgusPerformanceParameterFile.deleted_at == 0,
                )
            )
            record = row.scalars().first()
            if record is None:
                return ArgusResponse.failed("参数文件不存在")
            record.deleted_at = int(time.time() * 1000)
            record.updated_at = datetime.now()
            record.update_user = user_info["id"]
            await session.flush()
            await ArgusOperationDao.insert_log(session, user_info["id"], OperationType.DELETE, record, key=id)
        return ArgusResponse.success()


@router.post("/plan/validate-parameters")
async def validate_performance_plan_parameters(form: ArgusPerformanceParameterValidateForm, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_performance_schema(session)
        result = await validate_parameter_payload(session, form)
        return ArgusResponse.success(result)


@router.get("/plan/case-preview")
async def query_performance_case_preview(case_ids: str, _=Depends(Permission())):
    ids = parse_case_ids(case_ids)
    preview = await build_case_chain_preview(ids)
    return ArgusResponse.success(preview)


@router.get("/plan/source")
async def query_performance_plan_source(api_version_id: int, _=Depends(Permission())):
    async with async_session() as session:
        version_row = await session.execute(
            select(ArgusApiEndpointVersion).where(
                ArgusApiEndpointVersion.id == api_version_id,
                ArgusApiEndpointVersion.deleted_at == 0,
            )
        )
        version = version_row.scalars().first()
        if version is None:
            return ArgusResponse.failed("接口版本不存在")
        sample_row = await session.execute(
            select(ArgusApiEndpointSample).where(
                ArgusApiEndpointSample.api_version_id == api_version_id,
                ArgusApiEndpointSample.deleted_at == 0,
            ).order_by(desc(ArgusApiEndpointSample.id))
        )
        sample = sample_row.scalars().first()
        request_url = version.full_url or ""
        request_headers = version.request_headers or "[]"
        request_query = "{}"
        request_body = ""
        if sample is not None:
            request_url = sample.request_url or request_url
            request_headers = sample.request_headers or request_headers
            request_query = sample.request_query or request_query
            request_body = sample.request_body or request_body
        return ArgusResponse.success({
            "request_method": version.method,
            "request_url": request_url,
            "request_headers": request_headers,
            "request_query": request_query,
            "request_body": request_body,
            "version_name": version.name,
            "path": version.path,
            "module_name": version.module_name,
        })
