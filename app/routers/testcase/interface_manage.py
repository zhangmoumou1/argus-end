import asyncio
import json
import time
from copy import deepcopy
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urljoin

import requests
from fastapi import APIRouter, Depends
from sqlalchemy import select, text, func

from app import pity
from app.crud.operation.PityOperationDao import PityOperationDao
from app.enums.OperationEnum import OperationType
from app.handler.fatcory import PityResponse
from app.core.configuration import SystemConfiguration
from app.models import async_session
from app.models.interface_manage import PityApiService, PityApiEndpoint, PityApiEndpointVersion, PityApiEndpointSample
from app.core.interface_sample import ensure_interface_sample_schema, get_endpoint_sample as load_endpoint_sample, save_endpoint_sample
from app.routers import Permission
from app.utils.json_compare import JsonCompare
from app.models.test_case import TestCase
from app.models.testcase_directory import PityTestcaseDirectory
from app.models.user import User

router = APIRouter(prefix="/interface-management")
DEFAULT_SYNC_CRON = "0 0 * * *"
_INTERFACE_SCHEMA_READY = False
_INTERFACE_SCHEMA_LOCK = asyncio.Lock()


def normalize_path(path: str):
    value = str(path or "").strip()
    if not value.startswith("/"):
        value = "/" + value
    while "//" in value:
        value = value.replace("//", "/")
    return value


def endpoint_key(method: str, path: str):
    return f"{str(method or 'GET').upper()} {normalize_path(path)}"


def serialize_model(model):
    return PityResponse.model_to_dict(model)


def safe_json_dumps(value):
    try:
        return json.dumps(value or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def safe_json_loads(text_value):
    try:
        return json.loads(text_value or "{}")
    except Exception:
        return {}


def safe_json_loads_any(text_value, fallback=None):
    if fallback is None:
        fallback = {}
    if text_value is None:
        return fallback
    if isinstance(text_value, (dict, list)):
        return text_value
    try:
        return json.loads(text_value)
    except Exception:
        return fallback


def normalize_url_for_fill(base_url: str, path: str, query: dict = None):
    query = query or {}
    base_value = str(base_url or "").strip().rstrip("/")
    path_value = normalize_path(path or "")
    url = f"{base_value}{path_value}" if base_value else path_value
    pairs = []
    for key, value in (query or {}).items():
        k = str(key or "").strip()
        if not k:
            continue
        if value is None:
            pairs.append(f"{k}=")
        else:
            pairs.append(f"{k}={value}")
    if pairs:
        glue = "&" if "?" in url else "?"
        url = f"{url}{glue}{'&'.join(pairs)}"
    return url


def build_defaults_from_request_params(request_params):
    req = safe_json_loads_any(request_params, {})
    query_obj = {}
    body_value = ""

    parameters = req.get("parameters") if isinstance(req, dict) else []
    if not isinstance(parameters, list):
        parameters = req.get("req_query") if isinstance(req, dict) else []
    if isinstance(parameters, list):
        for item in parameters:
            if not isinstance(item, dict):
                continue
            in_value = str(item.get("in") or "").lower()
            if in_value and in_value != "query":
                continue
            key = str(item.get("name") or "").strip()
            if not key:
                continue
            schema = item.get("schema") if isinstance(item.get("schema"), dict) else {}
            t = str(schema.get("type") or "").lower()
            if t in ("integer", "number"):
                query_obj[key] = 0
            elif t == "boolean":
                query_obj[key] = False
            else:
                query_obj[key] = ""

    request_body = req.get("requestBody") if isinstance(req, dict) else {}
    content = request_body.get("content") if isinstance(request_body, dict) else {}
    if isinstance(content, dict) and content:
        content_node = content.get("application/json")
        if not isinstance(content_node, dict):
            first_key = next(iter(content.keys()))
            content_node = content.get(first_key) if isinstance(content.get(first_key), dict) else {}
        schema = content_node.get("schema") if isinstance(content_node, dict) else {}
        if isinstance(schema, dict):
            if schema.get("type") == "array":
                body_value = "[]"
            else:
                props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
                if props:
                    body_obj = {}
                    for k, v in props.items():
                        prop_schema = v if isinstance(v, dict) else {}
                        prop_type = str(prop_schema.get("type") or "").lower()
                        if prop_type in ("integer", "number"):
                            body_obj[k] = 0
                        elif prop_type == "boolean":
                            body_obj[k] = False
                        elif prop_type == "array":
                            body_obj[k] = []
                        elif prop_type == "object":
                            body_obj[k] = {}
                        else:
                            body_obj[k] = ""
                    body_value = json.dumps(body_obj, ensure_ascii=False, indent=2)
                else:
                    body_value = "{}"
    elif isinstance(req.get("req_body_other"), (dict, list)):
        body_value = json.dumps(req.get("req_body_other"), ensure_ascii=False, indent=2)
    elif isinstance(req.get("req_body_other"), str) and str(req.get("req_body_other")).strip():
        body_value = req.get("req_body_other")
    elif isinstance(req.get("req_body_form"), list) and req.get("req_body_form"):
        body_obj = {}
        for item in req.get("req_body_form"):
            if not isinstance(item, dict):
                continue
            key = str(item.get("name") or "").strip()
            if key:
                body_obj[key] = ""
        body_value = json.dumps(body_obj, ensure_ascii=False, indent=2)

    return query_obj, body_value


def simplify_request_params(request_params):
    req = safe_json_loads_any(request_params, {})
    result = {
        "request_kind": "none",  # none | params | body
        "params_type": "none",   # none | query
        "params_items": [],
        "body_type": "none",     # none | raw-json | raw-text | form-data | x-www-form-urlencoded
        "body_items": [],
        "body_raw_example": "",
    }
    if not isinstance(req, dict):
        return result

    # Swagger/OpenAPI query parameters
    parameters = req.get("parameters")
    if not isinstance(parameters, list):
        parameters = req.get("req_query")
    if isinstance(parameters, list):
        for item in parameters:
            if not isinstance(item, dict):
                continue
            in_value = str(item.get("in") or "").lower()
            if in_value and in_value != "query":
                continue
            key = str(item.get("name") or "").strip()
            if not key:
                continue
            schema = item.get("schema") if isinstance(item.get("schema"), dict) else {}
            item_type = str(schema.get("type") or item.get("type") or "").lower()
            result["params_items"].append({
                "key": key,
                "description": str(item.get("description") or ""),
                "required": bool(item.get("required")),
                "data_type": item_type or "string",
            })
    if result["params_items"]:
        result["request_kind"] = "params"
        result["params_type"] = "query"

    # OpenAPI requestBody
    request_body = req.get("requestBody") if isinstance(req.get("requestBody"), dict) else {}
    content = request_body.get("content") if isinstance(request_body.get("content"), dict) else {}
    if content:
        if "application/json" in content:
            node = content.get("application/json") if isinstance(content.get("application/json"), dict) else {}
            schema = node.get("schema") if isinstance(node.get("schema"), dict) else {}
            result["body_type"] = "raw-json"
            body_obj = {}
            props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            for k, v in props.items():
                prop_schema = v if isinstance(v, dict) else {}
                p_type = str(prop_schema.get("type") or "").lower()
                if p_type in ("integer", "number"):
                    body_obj[k] = 0
                elif p_type == "boolean":
                    body_obj[k] = False
                elif p_type == "array":
                    body_obj[k] = []
                elif p_type == "object":
                    body_obj[k] = {}
                else:
                    body_obj[k] = ""
                result["body_items"].append({
                    "key": k,
                    "description": str(prop_schema.get("description") or ""),
                    "required": False,
                    "data_type": p_type or "string",
                })
            result["body_raw_example"] = json.dumps(body_obj if body_obj else {}, ensure_ascii=False, indent=2)
        elif "multipart/form-data" in content:
            node = content.get("multipart/form-data") if isinstance(content.get("multipart/form-data"), dict) else {}
            schema = node.get("schema") if isinstance(node.get("schema"), dict) else {}
            result["body_type"] = "form-data"
            props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            for k, v in props.items():
                prop_schema = v if isinstance(v, dict) else {}
                p_type = str(prop_schema.get("type") or "").lower() or "string"
                result["body_items"].append({
                    "key": k,
                    "description": str(prop_schema.get("description") or ""),
                    "required": False,
                    "data_type": p_type,
                })
        elif "application/x-www-form-urlencoded" in content:
            node = content.get("application/x-www-form-urlencoded") if isinstance(content.get("application/x-www-form-urlencoded"), dict) else {}
            schema = node.get("schema") if isinstance(node.get("schema"), dict) else {}
            result["body_type"] = "x-www-form-urlencoded"
            props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            for k, v in props.items():
                prop_schema = v if isinstance(v, dict) else {}
                p_type = str(prop_schema.get("type") or "").lower() or "string"
                result["body_items"].append({
                    "key": k,
                    "description": str(prop_schema.get("description") or ""),
                    "required": False,
                    "data_type": p_type,
                })
        else:
            # fallback any other content type -> treat as raw text
            result["body_type"] = "raw-text"
            result["body_raw_example"] = ""

    # YAPI request body
    if result["body_type"] == "none":
        req_body_other = req.get("req_body_other")
        req_body_form = req.get("req_body_form")
        if isinstance(req_body_form, list) and req_body_form:
            result["body_type"] = "form-data"
            for item in req_body_form:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("name") or "").strip()
                if not key:
                    continue
                result["body_items"].append({
                    "key": key,
                    "description": str(item.get("desc") or item.get("description") or ""),
                    "required": str(item.get("required") or "") in ("1", "true", "True"),
                    "data_type": str(item.get("type") or "string"),
                })
        elif isinstance(req_body_other, (dict, list)):
            result["body_type"] = "raw-json"
            result["body_raw_example"] = json.dumps(req_body_other, ensure_ascii=False, indent=2)
        elif isinstance(req_body_other, str) and req_body_other.strip():
            parsed = safe_json_loads_any(req_body_other, None)
            if isinstance(parsed, (dict, list)):
                result["body_type"] = "raw-json"
                result["body_raw_example"] = json.dumps(parsed, ensure_ascii=False, indent=2)
            else:
                result["body_type"] = "raw-text"
                result["body_raw_example"] = req_body_other

    if result["body_type"] != "none":
        result["request_kind"] = "body"
    return result


def resolve_openapi_ref(payload: dict, ref: str):
    if not isinstance(payload, dict) or not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    current = payload
    for part in ref[2:].split("/"):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def expand_openapi_node(payload: dict, node, seen_refs=None):
    if seen_refs is None:
        seen_refs = set()

    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref in seen_refs:
                return {"$ref": ref}
            target = resolve_openapi_ref(payload, ref)
            if target is None:
                return {"$ref": ref}
            merged = dict(target) if isinstance(target, dict) else target
            if isinstance(merged, dict):
                for key, value in node.items():
                    if key != "$ref":
                        merged[key] = value
            return expand_openapi_node(payload, merged, seen_refs | {ref})
        return {key: expand_openapi_node(payload, value, seen_refs) for key, value in node.items()}

    if isinstance(node, list):
        return [expand_openapi_node(payload, item, seen_refs) for item in node]

    return node


def extract_change_points(compare_rows):
    ans = []
    for row in compare_rows or []:
        text_value = str(row or "").strip()
        if not text_value:
            continue
        point = text_value.split(" ", 1)[0].strip()
        if point and point not in ans:
            ans.append(point)
    return ans


def normalize_structured_text(value):
    if isinstance(value, (dict, list)):
        return value
    text_value = str(value or "").strip()
    if not text_value:
        return ""
    try:
        return json.loads(text_value)
    except Exception:
        return text_value


def parse_swagger_payload(payload):
    paths = payload.get("paths") or {}
    ans = []
    for raw_path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in ("get", "post", "put", "delete", "patch", "head", "options"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            resolved_parameters = expand_openapi_node(payload, op.get("parameters") or [])
            resolved_request_body = expand_openapi_node(payload, op.get("requestBody") or {})
            resolved_responses = expand_openapi_node(payload, op.get("responses") or {})
            ans.append({
                "name": op.get("summary") or op.get("operationId") or f"{method.upper()} {raw_path}",
                "method": method.upper(),
                "module_name": ((op.get("tags") or ["默认模块"])[0] if isinstance(op.get("tags"), list) else "默认模块"),
                "endpoint_status": "deprecated" if bool(op.get("deprecated")) else "available",
                "path": normalize_path(raw_path),
                "request_headers": [x for x in resolved_parameters if isinstance(x, dict) and str(x.get("in")) == "header"],
                "request_params": {
                    "parameters": resolved_parameters,
                    "requestBody": resolved_request_body,
                },
                "response_body": resolved_responses,
            })
    return ans


def resolve_swagger_base_url(payload: dict, source_url: str = ""):
    payload = payload or {}
    # OpenAPI 3.x: only keep path part
    servers = payload.get("servers") or []
    if isinstance(servers, list):
        for server in servers:
            if not isinstance(server, dict):
                continue
            server_url = str(server.get("url") or "").strip()
            if not server_url:
                continue
            parsed_server = urlparse(server_url)
            if parsed_server.path:
                return normalize_path(parsed_server.path)
            if server_url.startswith("/"):
                return normalize_path(server_url)

    # Swagger 2.0: basePath only
    base_path = normalize_path(payload.get("basePath") or "/")
    return base_path


def parse_yapi_payload(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        data = payload.get("data")
        interfaces = data.get("list") or []
    elif isinstance(payload, dict):
        interfaces = payload.get("list") or payload.get("data") or []
    else:
        interfaces = []
    ans = []
    for item in interfaces:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "GET").upper()
        raw_path = item.get("path") or item.get("url") or ""
        req_headers = item.get("req_headers") or []
        req_query = item.get("req_query") or []
        req_body_other = normalize_structured_text(item.get("req_body_other") or "")
        req_body_form = item.get("req_body_form") or []
        res_body = normalize_structured_text(item.get("res_body") or "")
        ans.append({
            "name": item.get("title") or f"{method} {raw_path}",
            "method": method,
            "module_name": item.get("cat_name") or "默认模块",
            "endpoint_status": "deprecated" if str(item.get("status") or "").lower() in ("deprecated", "disable", "disabled") else "available",
            "path": normalize_path(raw_path),
            "request_headers": req_headers,
            "request_params": {
                "req_query": req_query,
                "req_headers": req_headers,
                "req_body_other": req_body_other,
                "req_body_form": req_body_form,
            },
            "response_body": res_body,
        })
    return ans


def resolve_local_openapi_payload(source_url: str):
    parsed = urlparse(str(source_url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    path = normalize_path(parsed.path or "")
    if host not in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return None
    if path in {"/openapi.json", "/docs", "/redoc", "/swagger-ui.html", "/swagger-ui/index.html"}:
        payload = pity.openapi()
        if isinstance(payload, dict) and (payload.get("openapi") or payload.get("swagger")):
            return payload
    return None


def resolve_swagger_payload(source_url: str):
    source_url = str(source_url or "").strip()
    if not source_url:
        raise ValueError("source_url不能为空")
    local_payload = resolve_local_openapi_payload(source_url)
    if isinstance(local_payload, dict):
        return local_payload
    direct_resp = requests.get(source_url, timeout=120)
    if direct_resp.ok:
        try:
            direct_json = direct_resp.json()
            if isinstance(direct_json, dict) and (direct_json.get("openapi") or direct_json.get("swagger")):
                return direct_json
        except Exception:
            pass
    parsed = urlparse(source_url)
    qs = parse_qs(parsed.query or "")
    target_name = (qs.get("urls.primaryName") or [None])[0]
    base = f"{parsed.scheme}://{parsed.netloc}"
    config_candidates = [
        urljoin(base, "/v3/api-docs/swagger-config"),
        urljoin(base, "/swagger-ui/swagger-config"),
    ]
    config_data = None
    for conf_url in config_candidates:
        try:
            conf_resp = requests.get(conf_url, timeout=120)
            conf_resp.raise_for_status()
            config_data = conf_resp.json()
            break
        except Exception:
            continue
    if not isinstance(config_data, dict):
        raise ValueError("无法从swagger-ui地址解析到swagger-config")
    urls = config_data.get("urls") or []
    spec_url = config_data.get("url")
    if isinstance(urls, list) and urls:
        selected = None
        if target_name:
            for item in urls:
                if isinstance(item, dict) and str(item.get("name")) == str(target_name):
                    selected = item
                    break
        if selected is None and isinstance(urls[0], dict):
            selected = urls[0]
        if isinstance(selected, dict):
            spec_url = selected.get("url") or spec_url
    if not spec_url:
        raise ValueError("swagger-config未提供可用文档地址")
    final_url = urljoin(base, spec_url)
    spec_resp = requests.get(final_url, timeout=120)
    spec_resp.raise_for_status()
    payload = spec_resp.json()
    if not isinstance(payload, dict):
        raise ValueError("Swagger文档格式不正确")
    return payload


async def ensure_interface_schema(session):
    global _INTERFACE_SCHEMA_READY
    if _INTERFACE_SCHEMA_READY:
        return
    async with _INTERFACE_SCHEMA_LOCK:
        if _INTERFACE_SCHEMA_READY:
            return
        await session.execute(text(
        "CREATE TABLE IF NOT EXISTS pity_api_service ("
        "id INT PRIMARY KEY AUTO_INCREMENT,"
        "project_id INT NOT NULL DEFAULT 0,"
        "name VARCHAR(128) NOT NULL,"
        "base_url VARCHAR(255) NULL,"
        "owner TEXT NULL,"
        "developer VARCHAR(128) NULL,"
        "tester VARCHAR(128) NULL,"
        "source_type VARCHAR(32) NOT NULL DEFAULT 'manual',"
        "source_config TEXT NULL,"
        "sync_enabled INT NOT NULL DEFAULT 0,"
        "sync_cron VARCHAR(64) NULL,"
        "last_sync_status VARCHAR(32) NULL,"
        "last_sync_at VARCHAR(32) NULL,"
        "created_at TIMESTAMP NOT NULL,"
        "updated_at TIMESTAMP NOT NULL,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL,"
        "update_user INT NOT NULL"
        ")"
    ))
        await session.execute(text(
        "CREATE TABLE IF NOT EXISTS pity_api_endpoint ("
        "id INT PRIMARY KEY AUTO_INCREMENT,"
        "service_id INT NOT NULL DEFAULT 0,"
        "name VARCHAR(255) NOT NULL,"
        "method VARCHAR(16) NOT NULL DEFAULT 'GET',"
        "module_name VARCHAR(128) NOT NULL DEFAULT '默认模块',"
        "endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available',"
        "path VARCHAR(512) NOT NULL,"
        "full_url VARCHAR(1024) NULL,"
        "request_headers LONGTEXT NULL,"
        "request_params LONGTEXT NULL,"
        "response_body LONGTEXT NULL,"
        "endpoint_key VARCHAR(768) NOT NULL,"
        "current_version_id INT NOT NULL DEFAULT 0,"
        "current_version_no VARCHAR(32) NOT NULL DEFAULT 'v1',"
        "created_at TIMESTAMP NOT NULL,"
        "updated_at TIMESTAMP NOT NULL,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL,"
        "update_user INT NOT NULL"
        ")"
    ))
        await session.execute(text(
        "CREATE TABLE IF NOT EXISTS pity_api_endpoint_version ("
        "id INT PRIMARY KEY AUTO_INCREMENT,"
        "endpoint_id INT NOT NULL DEFAULT 0,"
        "version_no VARCHAR(32) NOT NULL DEFAULT 'v1',"
        "name VARCHAR(255) NOT NULL,"
        "method VARCHAR(16) NOT NULL DEFAULT 'GET',"
        "module_name VARCHAR(128) NOT NULL DEFAULT '默认模块',"
        "endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available',"
        "path VARCHAR(512) NOT NULL,"
        "full_url VARCHAR(1024) NULL,"
        "request_headers LONGTEXT NULL,"
        "request_params LONGTEXT NULL,"
        "response_body LONGTEXT NULL,"
        "created_at TIMESTAMP NOT NULL,"
        "updated_at TIMESTAMP NOT NULL,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL,"
        "update_user INT NOT NULL"
        ")"
    ))
        await ensure_interface_sample_schema(session)
        for column_sql in [
        "ALTER TABLE pity_api_endpoint ADD COLUMN module_name VARCHAR(128) NOT NULL DEFAULT '默认模块' COMMENT '功能模块'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN module_name VARCHAR(128) NOT NULL DEFAULT '默认模块' COMMENT '功能模块'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT '接口状态'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT '接口状态'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN request_headers LONGTEXT NULL COMMENT '请求头'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN request_headers LONGTEXT NULL COMMENT '请求头'",
        "ALTER TABLE pity_api_service ADD COLUMN owner TEXT NULL COMMENT '负责人'",
        "ALTER TABLE pity_api_service ADD COLUMN developer VARCHAR(128) NULL COMMENT '开发人员'",
        "ALTER TABLE pity_api_service ADD COLUMN tester VARCHAR(128) NULL COMMENT '测试人员'",
        "ALTER TABLE pity_testcase ADD COLUMN api_service_id INT NOT NULL DEFAULT 0 COMMENT '绑定服务ID'",
        "ALTER TABLE pity_testcase ADD COLUMN api_endpoint_id INT NOT NULL DEFAULT 0 COMMENT '绑定接口ID'",
        "ALTER TABLE pity_testcase ADD COLUMN api_version_id INT NOT NULL DEFAULT 0 COMMENT '绑定接口版本ID'",
        "ALTER TABLE pity_testcase ADD COLUMN api_version_no VARCHAR(32) NULL COMMENT '绑定接口版本号'",
        "ALTER TABLE pity_testcase ADD COLUMN api_bind_mode VARCHAR(16) NOT NULL DEFAULT 'pinned' COMMENT '绑定模式'",
        "ALTER TABLE pity_testcase ADD COLUMN api_pending_update INT NOT NULL DEFAULT 0 COMMENT '是否待更新'",
        "ALTER TABLE pity_api_endpoint ADD INDEX idx_service_deleted_module_status_updated(service_id, deleted_at, module_name, endpoint_status, updated_at)",
        "ALTER TABLE pity_api_endpoint_sample ADD INDEX idx_endpoint_deleted(endpoint_id, deleted_at)",
    ]:
            try:
                await session.execute(text(column_sql))
            except Exception:
                pass
        await session.commit()
        _INTERFACE_SCHEMA_READY = True


async def create_version(session, endpoint: PityApiEndpoint, user_id: int):
    version_count_sql = select(func.count(PityApiEndpointVersion.id)).where(
        PityApiEndpointVersion.endpoint_id == endpoint.id,
        PityApiEndpointVersion.deleted_at == 0,
    )
    version_count = (await session.execute(version_count_sql)).scalar() or 0
    version_no = f"v{int(version_count) + 1}"
    model = PityApiEndpointVersion(
        endpoint_id=endpoint.id,
        version_no=version_no,
        name=endpoint.name,
        method=endpoint.method,
        module_name=endpoint.module_name or "默认模块",
        endpoint_status=endpoint.endpoint_status or "available",
        path=endpoint.path,
        full_url=endpoint.full_url,
        request_headers=endpoint.request_headers,
        request_params=endpoint.request_params,
        response_body=endpoint.response_body,
        user=user_id,
    )
    session.add(model)
    await session.flush()
    endpoint.current_version_id = model.id
    endpoint.current_version_no = version_no
    endpoint.updated_at = datetime.now()
    endpoint.update_user = user_id


async def upsert_endpoints(session, service: PityApiService, user_id: int, endpoint_items, log_endpoints: bool = False):
    result = {"created": 0, "updated": 0, "unchanged": 0}
    existing_sql = await session.execute(
        select(PityApiEndpoint).where(
            PityApiEndpoint.service_id == service.id,
            PityApiEndpoint.deleted_at == 0,
        )
    )
    existing_list = existing_sql.scalars().all()
    existing_map = {item.endpoint_key: item for item in existing_list}

    for raw in endpoint_items:
        method = str(raw.get("method") or "GET").upper()
        module_name = str(raw.get("module_name") or "默认模块").strip() or "默认模块"
        endpoint_status = str(raw.get("endpoint_status") or "available").strip() or "available"
        path = normalize_path(raw.get("path") or "")
        key = endpoint_key(method, path)
        name = str(raw.get("name") or key).strip() or key
        request_params = safe_json_dumps(raw.get("request_params") or {})
        request_headers = safe_json_dumps(raw.get("request_headers") or [])
        response_body = safe_json_dumps(raw.get("response_body") or {})
        full_url = str((service.base_url or "").rstrip("/") + path)

        exists = existing_map.get(key)
        if exists is None:
            endpoint = PityApiEndpoint(
                service_id=service.id,
                name=name,
                method=method,
                module_name=module_name,
                endpoint_status=endpoint_status,
                path=path,
                full_url=full_url,
                endpoint_key=key,
                request_headers=request_headers,
                request_params=request_params,
                response_body=response_body,
                user=user_id,
            )
            session.add(endpoint)
            await session.flush()
            await create_version(session, endpoint, user_id)
            if log_endpoints:
                await PityOperationDao.insert_log(session, user_id, OperationType.INSERT, endpoint, key=endpoint.id)
            result["created"] += 1
            continue

        changed = False
        old = deepcopy(exists)
        if exists.name != name:
            exists.name = name
            changed = True
        if (exists.module_name or "默认模块") != module_name:
            exists.module_name = module_name
            changed = True
        if (exists.endpoint_status or "available") != endpoint_status:
            exists.endpoint_status = endpoint_status
            changed = True
        if exists.request_params != request_params:
            exists.request_params = request_params
            changed = True
        if (exists.request_headers or "[]") != request_headers:
            exists.request_headers = request_headers
            changed = True
        if exists.response_body != response_body:
            exists.response_body = response_body
            changed = True
        if exists.full_url != full_url:
            exists.full_url = full_url
            changed = True

        if changed:
            exists.updated_at = datetime.now()
            exists.update_user = user_id
            await create_version(session, exists, user_id)
            await session.execute(text(
                "UPDATE pity_testcase "
                "SET api_pending_update = 1, updated_at = NOW(), update_user = :user_id "
                "WHERE deleted_at = 0 AND api_endpoint_id = :endpoint_id "
                "AND api_version_id > 0 AND api_version_id <> :current_version_id"
            ), {
                "user_id": user_id,
                "endpoint_id": exists.id,
                "current_version_id": exists.current_version_id,
            })
            if log_endpoints:
                await PityOperationDao.insert_log(
                    session,
                    user_id,
                    OperationType.UPDATE,
                    exists,
                    old,
                    exists.id,
                    changed=["name", "module_name", "endpoint_status", "request_params", "request_headers", "response_body", "full_url", "current_version_no"],
                )
            result["updated"] += 1
        else:
            result["unchanged"] += 1

    await session.commit()
    return result


@router.get("/service/list")
async def list_services(project_id: int = None, keyword: str = "", _=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        filters = [PityApiService.deleted_at == 0]
        if project_id is not None:
            filters.append(PityApiService.project_id == project_id)
        if keyword:
            filters.append(PityApiService.name.like(f"%{keyword}%"))
        result = await session.execute(
            select(PityApiService).where(*filters).order_by(PityApiService.updated_at.desc(), PityApiService.id.desc())
        )
        rows = result.scalars().all()
        data = []
        for item in rows:
            endpoint_total_sql = select(func.count(PityApiEndpoint.id)).where(
                PityApiEndpoint.service_id == item.id,
                PityApiEndpoint.deleted_at == 0,
            )
            endpoint_total = (await session.execute(endpoint_total_sql)).scalar() or 0
            row = serialize_model(item)
            row["endpoint_total"] = int(endpoint_total)
            data.append(row)
    return PityResponse.success(data)


@router.post("/service/insert")
async def insert_service(form: dict, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        model = PityApiService(
            project_id=int(form.get("project_id") or 0),
            name=str(form.get("name") or "").strip(),
            base_url=str(form.get("base_url") or "").strip(),
            owner=safe_json_dumps(form.get("owner") or []),
            developer=str(form.get("developer") or "").strip(),
            tester=str(form.get("tester") or "").strip(),
            source_type=str(form.get("source_type") or "manual").strip() or "manual",
            source_config=safe_json_dumps(form.get("source_config") or {}),
            user=user_info["id"],
        )
        if not model.name:
            return PityResponse.failed("服务名称不能为空")
        source_type = (model.source_type or "manual").lower()
        model.sync_enabled = 0 if source_type == "manual" else int(form.get("sync_enabled") or 0)
        model.sync_cron = DEFAULT_SYNC_CRON if model.sync_enabled and not form.get("sync_cron") else str(form.get("sync_cron") or "").strip() or None
        session.add(model)
        await session.flush()
        await PityOperationDao.insert_log(session, user_info["id"], OperationType.INSERT, model, key=model.id)
        await session.commit()
        await session.refresh(model)
    return PityResponse.success(serialize_model(model))


@router.post("/service/update")
async def update_service(form: dict, user_info=Depends(Permission())):
    service_id = int(form.get("id") or 0)
    if not service_id:
        return PityResponse.failed("id不能为空")
    async with async_session() as session:
        await ensure_interface_schema(session)
        result = await session.execute(
            select(PityApiService).where(PityApiService.id == service_id, PityApiService.deleted_at == 0)
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("服务不存在")
        old = deepcopy(model)
        name = str(form.get("name") or model.name).strip()
        if not name:
            return PityResponse.failed("服务名称不能为空")
        if form.get("project_id") is not None:
            model.project_id = int(form.get("project_id") or 0)
        model.name = name
        if "base_url" in form:
            model.base_url = str(form.get("base_url") or "").strip()
        model.owner = safe_json_dumps(form.get("owner") or [])
        model.developer = str(form.get("developer") or "").strip()
        model.tester = str(form.get("tester") or "").strip()
        model.source_type = str(form.get("source_type") or model.source_type or "manual").strip() or "manual"
        if "source_config" in form:
            model.source_config = safe_json_dumps(form.get("source_config") or {})
        if (model.source_type or "manual").lower() == "manual":
            model.sync_enabled = 0
            model.sync_cron = None
        else:
            model.sync_enabled = int(form.get("sync_enabled") if form.get("sync_enabled") is not None else model.sync_enabled)
            model.sync_cron = DEFAULT_SYNC_CRON if model.sync_enabled and not form.get("sync_cron") else str(form.get("sync_cron") or model.sync_cron or "").strip() or None
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.flush()
        await PityOperationDao.insert_log(
            session,
            user_info["id"],
            OperationType.UPDATE,
            model,
            old,
            model.id,
            changed=["project_id", "name", "base_url", "owner", "developer", "tester", "source_type", "source_config", "sync_enabled", "sync_cron"],
        )
        await session.commit()
        await session.refresh(model)
    return PityResponse.success(serialize_model(model))


@router.get("/service/delete")
async def delete_service(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        result = await session.execute(
            select(PityApiService).where(PityApiService.id == id, PityApiService.deleted_at == 0)
        )
        service = result.scalars().first()
        if service is None:
            return PityResponse.failed("服务不存在")
        now_deleted = int(datetime.now().timestamp())
        service.deleted_at = now_deleted
        service.update_user = user_info["id"]
        service.updated_at = datetime.now()
        await PityOperationDao.insert_log(session, user_info["id"], OperationType.DELETE, service, key=service.id)
        endpoints = (await session.execute(
            select(PityApiEndpoint).where(PityApiEndpoint.service_id == id, PityApiEndpoint.deleted_at == 0)
        )).scalars().all()
        for endpoint in endpoints:
            endpoint.deleted_at = now_deleted
            endpoint.update_user = user_info["id"]
            endpoint.updated_at = datetime.now()
        await session.commit()
    return PityResponse.success()


@router.get("/endpoint/list")
async def list_endpoints(service_id: int, keyword: str = "", module_name: str = "", url: str = "", endpoint_status: str = "", _=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        filters = [
            PityApiEndpoint.service_id == service_id,
            PityApiEndpoint.deleted_at == 0,
        ]
        if keyword:
            filters.append(PityApiEndpoint.name.like(f"%{keyword}%"))
        if module_name:
            filters.append(PityApiEndpoint.module_name == module_name)
        if url:
            filters.append(PityApiEndpoint.path.like(f"%{url}%"))
        if endpoint_status:
            filters.append(PityApiEndpoint.endpoint_status == endpoint_status)
        result = await session.execute(
            select(
                PityApiEndpoint.id,
                PityApiEndpoint.name,
                PityApiEndpoint.method,
                PityApiEndpoint.module_name,
                PityApiEndpoint.endpoint_status,
                PityApiEndpoint.path,
                PityApiEndpoint.current_version_no,
                PityApiEndpoint.updated_at,
            )
            .where(*filters)
            .order_by(PityApiEndpoint.module_name.asc(), PityApiEndpoint.updated_at.desc(), PityApiEndpoint.id.desc())
        )
        rows = result.all()
        endpoint_ids = [item.id for item in rows]
        sample_map = {}
        if endpoint_ids:
            sample_rows = (await session.execute(
                select(
                    PityApiEndpointSample.endpoint_id,
                    PityApiEndpointSample.recorded_at,
                    PityApiEndpointSample.status_code,
                    PityApiEndpointSample.sample_source,
                ).where(
                    PityApiEndpointSample.endpoint_id.in_(endpoint_ids),
                    PityApiEndpointSample.deleted_at == 0,
                )
            )).all()
            sample_map = {item.endpoint_id: item for item in sample_rows}
        case_count_map = {}
        if endpoint_ids:
            case_rows = await session.execute(
                select(
                    TestCase.api_endpoint_id,
                    func.count(TestCase.id).label("case_total"),
                ).where(
                    TestCase.api_endpoint_id.in_(endpoint_ids),
                    TestCase.deleted_at == 0,
                ).group_by(TestCase.api_endpoint_id)
            )
            case_count_map = {item.api_endpoint_id: int(item.case_total or 0) for item in case_rows.all()}
        data = []
        for item in rows:
            sample = sample_map.get(item.id)
            data.append({
                "id": item.id,
                "name": item.name,
                "method": item.method,
                "module_name": item.module_name,
                "endpoint_status": item.endpoint_status,
                "path": item.path,
                "current_version_no": item.current_version_no,
                "updated_at": item.updated_at,
                "case_total": int(case_count_map.get(item.id, 0) or 0),
                "sample_available": 1 if sample else 0,
                "sample_recorded_at": sample.recorded_at if sample else None,
                "sample_status_code": sample.status_code if sample else None,
                "sample_source": sample.sample_source if sample else None,
            })
        module_result = await session.execute(
            select(PityApiEndpoint.module_name).where(
                PityApiEndpoint.service_id == service_id,
                PityApiEndpoint.deleted_at == 0,
            ).distinct()
        )
        modules = [str(item[0] or "默认模块") for item in module_result.all()]
    return PityResponse.success({"list": data, "modules": sorted(list(set(modules)))})


@router.get("/endpoint/lineage")
async def get_endpoint_lineage(endpoint_id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        endpoint = (await session.execute(
            select(PityApiEndpoint).where(
                PityApiEndpoint.id == endpoint_id,
                PityApiEndpoint.deleted_at == 0,
            )
        )).scalars().first()
        if endpoint is None:
            return PityResponse.failed("接口不存在")
        service = (await session.execute(
            select(PityApiService).where(
                PityApiService.id == endpoint.service_id,
                PityApiService.deleted_at == 0,
            )
        )).scalars().first()
        case_rows = (await session.execute(
            select(TestCase, User.name.label("create_user_name"))
            .outerjoin(User, User.id == TestCase.create_user)
            .where(
                TestCase.api_endpoint_id == endpoint_id,
                TestCase.deleted_at == 0,
            ).order_by(TestCase.updated_at.desc(), TestCase.id.desc())
        )).all()
        directory_ids = list({item.directory_id for item, _ in case_rows if item.directory_id})
        directory_map = {}
        if directory_ids:
            directory_rows = (await session.execute(
                select(PityTestcaseDirectory).where(
                    PityTestcaseDirectory.id.in_(directory_ids),
                    PityTestcaseDirectory.deleted_at == 0,
                )
            )).scalars().all()
            directory_lookup = {item.id: item for item in directory_rows}
            parent_ids = list({item.parent for item in directory_rows if item.parent})
            while parent_ids:
                parent_rows = (await session.execute(
                    select(PityTestcaseDirectory).where(
                        PityTestcaseDirectory.id.in_(parent_ids),
                        PityTestcaseDirectory.deleted_at == 0,
                    )
                )).scalars().all()
                next_parent_ids = []
                for item in parent_rows:
                    if item.id not in directory_lookup:
                        directory_lookup[item.id] = item
                    if item.parent and item.parent not in directory_lookup:
                        next_parent_ids.append(item.parent)
                parent_ids = next_parent_ids
            for directory_id in directory_ids:
                current = directory_lookup.get(directory_id)
                names = []
                while current is not None:
                    names.append(current.name)
                    current = directory_lookup.get(current.parent)
                directory_map[directory_id] = '/'.join(reversed(names)) if names else '-'
        cases = []
        for item, create_user_name in case_rows:
            cases.append({
                "id": item.id,
                "name": item.name,
                "directory_id": item.directory_id,
                "directory_path": directory_map.get(item.directory_id, '-'),
                "create_user": item.create_user,
                "create_user_name": create_user_name or '',
                "creator_name": create_user_name or '',
                "request_type": item.request_type,
                "request_method": item.request_method,
                "url": item.url,
                "api_service_id": item.api_service_id,
                "api_endpoint_id": item.api_endpoint_id,
                "api_version_id": item.api_version_id,
                "api_version_no": item.api_version_no,
                "priority": item.priority,
                "status": item.status,
                "updated_at": item.updated_at,
            })
        return PityResponse.success({
            "endpoint": {
                "id": endpoint.id,
                "name": endpoint.name,
                "method": endpoint.method,
                "path": endpoint.path,
                "full_url": endpoint.full_url,
                "service_id": endpoint.service_id,
                "service_name": service.name if service else "",
                "current_version_no": endpoint.current_version_no,
            },
            "case_total": len(cases),
            "cases": cases,
        })


@router.get("/endpoint/version/list")
async def list_endpoint_versions(endpoint_id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        result = await session.execute(
            select(PityApiEndpointVersion).where(
                PityApiEndpointVersion.endpoint_id == endpoint_id,
                PityApiEndpointVersion.deleted_at == 0,
            ).order_by(PityApiEndpointVersion.id.desc())
        )
        rows = result.scalars().all()
        data = [serialize_model(item) for item in rows]
    return PityResponse.success(data)


@router.get("/endpoint/version/detail")
async def get_endpoint_version_detail(version_id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        record = (await session.execute(
            select(PityApiEndpointVersion).where(
                PityApiEndpointVersion.id == version_id,
                PityApiEndpointVersion.deleted_at == 0,
            )
        )).scalars().first()
        if record is None:
            return PityResponse.failed("版本不存在")
        data = serialize_model(record)
        data["request_params_struct"] = simplify_request_params(record.request_params)
    return PityResponse.success(data)


@router.get("/endpoint/source/query")
async def query_endpoint_source(endpoint_id: int, version_id: int = 0, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        endpoint = (await session.execute(
            select(PityApiEndpoint).where(
                PityApiEndpoint.id == endpoint_id,
                PityApiEndpoint.deleted_at == 0,
            )
        )).scalars().first()
        if endpoint is None:
            return PityResponse.failed("接口不存在")
        service = (await session.execute(
            select(PityApiService).where(
                PityApiService.id == endpoint.service_id,
                PityApiService.deleted_at == 0,
            )
        )).scalars().first()
        if service is None:
            return PityResponse.failed("接口所属服务不存在")

        record_version = None
        target_version_id = int(version_id or 0) or int(endpoint.current_version_id or 0)
        if target_version_id > 0:
            record_version = (await session.execute(
                select(PityApiEndpointVersion).where(
                    PityApiEndpointVersion.id == target_version_id,
                    PityApiEndpointVersion.deleted_at == 0,
                )
            )).scalars().first()
        if record_version is None:
            record_version = endpoint

        sample = await load_endpoint_sample(session, endpoint_id)
        method = str(getattr(record_version, "method", None) or endpoint.method or "GET").upper()
        path = str(getattr(record_version, "path", None) or endpoint.path or "")
        request_params = safe_json_loads_any(getattr(record_version, "request_params", None), {})
        query_from_schema, body_from_schema = build_defaults_from_request_params(request_params)

        sample_query = safe_json_loads_any(getattr(sample, "request_query", None), {}) if sample else {}
        sample_body = getattr(sample, "request_body", "") if sample else ""

        merged_query = sample_query if isinstance(sample_query, dict) and sample_query else query_from_schema
        body_value = sample_body
        if body_value is None:
            body_value = ""
        if not str(body_value).strip():
            body_value = body_from_schema
        if isinstance(body_value, (dict, list)):
            body_value = json.dumps(body_value, ensure_ascii=False, indent=2)

        request_url = str(getattr(record_version, "full_url", "") or "").strip()
        if not request_url:
            request_url = normalize_url_for_fill(service.base_url or "", path, {})
        if merged_query:
            request_url = normalize_url_for_fill(request_url, "", merged_query)

        return PityResponse.success({
            "endpoint_id": endpoint.id,
            "version_id": int(getattr(record_version, "id", 0) or 0),
            "version_no": str(getattr(record_version, "version_no", "") or endpoint.current_version_no or ""),
            "request_method": method,
            "request_url": request_url,
            "request_path": path,
            "request_headers": {},
            "request_query": merged_query if isinstance(merged_query, dict) else {},
            "request_body": str(body_value or ""),
            "request_params": request_params if isinstance(request_params, dict) else {},
            "sample_used": 1 if sample else 0,
            "sample_id": int(getattr(sample, "id", 0) or 0) if sample else 0,
            "service_base_url": str(service.base_url or ""),
        })


@router.get("/endpoint/sample/query")
async def get_endpoint_sample(endpoint_id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        record = (await session.execute(
            select(PityApiEndpointSample).where(
                PityApiEndpointSample.endpoint_id == endpoint_id,
                PityApiEndpointSample.deleted_at == 0,
            )
        )).scalars().first()
        if record is None:
            return PityResponse.success(None)
        data = serialize_model(record)
    return PityResponse.success(data)


@router.post("/endpoint/sample/associate")
async def associate_endpoint_sample(form: dict, user_info=Depends(Permission())):
    endpoint_id = int(form.get("endpoint_id") or 0)
    if not endpoint_id:
        return PityResponse.failed("endpoint_id不能为空")
    async with async_session() as session:
        await ensure_interface_schema(session)
        endpoint = (
            await session.execute(
                select(PityApiEndpoint).where(
                    PityApiEndpoint.id == endpoint_id,
                    PityApiEndpoint.deleted_at == 0,
                )
            )
        ).scalars().first()
        if endpoint is None:
            return PityResponse.failed("接口不存在")
        service = (
            await session.execute(
                select(PityApiService).where(
                    PityApiService.id == endpoint.service_id,
                    PityApiService.deleted_at == 0,
                )
            )
        ).scalars().first()
        if service is None:
            return PityResponse.failed("接口所属服务不存在")
        sample = await load_endpoint_sample(session, endpoint_id)
        request_data = {
            "url": str(form.get("url") or endpoint.full_url or ""),
            "request_method": str(form.get("request_method") or endpoint.method or "GET").upper(),
            "request_headers": form.get("request_headers") or {},
            "body": form.get("body") or "",
            "response_headers": form.get("response_headers") or {},
            "response_content": form.get("response_content") or "",
            "status_code": int(form.get("status_code") or 0),
            "created_at": str(form.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        }
        old_sample = deepcopy(sample) if sample else None
        await save_endpoint_sample(
            session=session,
            endpoint=endpoint,
            service=service,
            request_data=request_data,
            user_id=user_info["id"],
            sample_source="manual_associate",
            sample=sample,
        )
        record = await load_endpoint_sample(session, endpoint_id)
        if record is not None:
            await PityOperationDao.insert_log(
                session,
                user_info["id"],
                OperationType.UPDATE if sample else OperationType.INSERT,
                record,
                old_sample,
                record.id,
                changed=["sample_source", "request_url", "request_headers", "request_body", "response_headers", "response_body", "status_code", "recorded_at"],
            )
        await session.commit()
    return PityResponse.success(serialize_model(record) if record else None)


@router.post("/endpoint/sample/manual-input")
async def manual_input_endpoint_sample(form: dict, user_info=Depends(Permission())):
    endpoint_id = int(form.get("endpoint_id") or 0)
    if not endpoint_id:
        return PityResponse.failed("endpoint_id不能为空")
    async with async_session() as session:
        await ensure_interface_schema(session)
        endpoint = (
            await session.execute(
                select(PityApiEndpoint).where(
                    PityApiEndpoint.id == endpoint_id,
                    PityApiEndpoint.deleted_at == 0,
                )
            )
        ).scalars().first()
        if endpoint is None:
            return PityResponse.failed("接口不存在")
        service = (
            await session.execute(
                select(PityApiService).where(
                    PityApiService.id == endpoint.service_id,
                    PityApiService.deleted_at == 0,
                )
            )
        ).scalars().first()
        if service is None:
            return PityResponse.failed("接口所属服务不存在")
        sample = await load_endpoint_sample(session, endpoint_id)
        request_data = {
            "url": str(form.get("request_url") or endpoint.full_url or endpoint.path or ""),
            "request_method": str(form.get("request_method") or endpoint.method or "GET").upper(),
            "request_headers": form.get("request_headers") or {},
            "body": form.get("request_body") or "",
            "response_headers": form.get("response_headers") or {},
            "response_content": form.get("response_body") or "",
            "status_code": int(form.get("status_code") or 200),
            "created_at": str(form.get("recorded_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        }
        record = await save_endpoint_sample(
            session=session,
            endpoint=endpoint,
            service=service,
            request_data=request_data,
            user_id=user_info["id"],
            sample_source="manual_input",
            matched_variant=str(form.get("request_path") or endpoint.path or ""),
            sample=sample,
        )
        record.sample_name = str(form.get("sample_name") or record.sample_name or "").strip() or record.sample_name
        if form.get("request_query") is not None:
            record.request_query = safe_json_dumps(form.get("request_query") or {})
        old_sample = deepcopy(sample) if sample else None
        await session.flush()
        await PityOperationDao.insert_log(
            session,
            user_info["id"],
            OperationType.UPDATE if sample else OperationType.INSERT,
            record,
            old_sample,
            record.id,
            changed=["sample_name", "request_url", "request_path", "request_query", "request_headers", "request_body", "response_headers", "response_body", "status_code", "recorded_at"],
        )
        await session.commit()
        record = await load_endpoint_sample(session, endpoint_id)
    return PityResponse.success(serialize_model(record) if record else None)


@router.post("/endpoint/sample/clear")
async def clear_endpoint_sample(form: dict, user_info=Depends(Permission())):
    endpoint_id = int(form.get("endpoint_id") or 0)
    if not endpoint_id:
        return PityResponse.failed("endpoint_id不能为空")
    async with async_session() as session:
        await ensure_interface_schema(session)
        record = await load_endpoint_sample(session, endpoint_id)
        if record is None:
            return PityResponse.success(True)
        old = deepcopy(record)
        record.deleted_at = int(time.time())
        record.update_user = user_info["id"]
        record.updated_at = datetime.now()
        await session.flush()
        await PityOperationDao.insert_log(session, user_info["id"], OperationType.DELETE, old, key=record.id)
        await session.commit()
    return PityResponse.success(True)


@router.get("/endpoint/version/compare")
async def compare_endpoint_version(left_version_id: int, right_version_id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_interface_schema(session)
        left = (await session.execute(
            select(PityApiEndpointVersion).where(
                PityApiEndpointVersion.id == left_version_id,
                PityApiEndpointVersion.deleted_at == 0,
            )
        )).scalars().first()
        right = (await session.execute(
            select(PityApiEndpointVersion).where(
                PityApiEndpointVersion.id == right_version_id,
                PityApiEndpointVersion.deleted_at == 0,
            )
        )).scalars().first()
        if left is None or right is None:
            return PityResponse.failed("版本不存在")

        comparer = JsonCompare()
        fields = [
            ("name", left.name, right.name),
            ("method", left.method, right.method),
            ("module_name", left.module_name, right.module_name),
            ("path", left.path, right.path),
            ("full_url", left.full_url, right.full_url),
            ("request_headers", left.request_headers, right.request_headers),
            ("request_params", left.request_params, right.request_params),
            ("response_body", left.response_body, right.response_body),
        ]
        diff = {}
        changed_fields = []
        change_points = {}
        left_values = {}
        right_values = {}
        for field_name, l_val, r_val in fields:
            left_values[field_name] = l_val
            right_values[field_name] = r_val
            compare_rows = comparer.compare(l_val, r_val)
            diff[field_name] = compare_rows
            if compare_rows:
                changed_fields.append(field_name)
                change_points[field_name] = extract_change_points(compare_rows)
    return PityResponse.success({
        "left_version_id": left_version_id,
        "right_version_id": right_version_id,
        "changed_fields": changed_fields,
        "change_points": change_points,
        "left_values": left_values,
        "right_values": right_values,
        "diff": diff,
    })


@router.post("/import/swagger")
async def import_swagger(form: dict, user_info=Depends(Permission())):
    service_id = int(form.get("service_id") or 0)
    if not service_id:
        return PityResponse.failed("service_id不能为空")
    source_url = str(form.get("source_url") or "").strip()
    source_text = str(form.get("source_text") or "").strip()
    if not source_url and not source_text:
        return PityResponse.failed("请提供 source_url 或 source_text")

    try:
        if source_text:
            payload = json.loads(source_text)
        else:
            payload = resolve_swagger_payload(source_url)
    except Exception as exc:
        return PityResponse.failed(f"Swagger解析失败: {exc}")

    endpoint_items = parse_swagger_payload(payload)
    resolved_base_url = resolve_swagger_base_url(payload, source_url)
    async with async_session() as session:
        await ensure_interface_schema(session)
        service = (await session.execute(
            select(PityApiService).where(PityApiService.id == service_id, PityApiService.deleted_at == 0)
        )).scalars().first()
        if service is None:
            return PityResponse.failed("服务不存在")
        old = deepcopy(service)
        service.source_type = "swagger"
        service.source_config = safe_json_dumps({"source_url": source_url})
        if resolved_base_url:
            service.base_url = resolved_base_url
        service.last_sync_status = "success"
        service.last_sync_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        service.update_user = user_info["id"]
        service.updated_at = datetime.now()
        summary = await upsert_endpoints(session, service, user_info["id"], endpoint_items)
        await PityOperationDao.insert_log(
            session,
            user_info["id"],
            OperationType.UPDATE,
            service,
            old,
            service.id,
            changed=["source_type", "source_config", "last_sync_status", "last_sync_at"],
        )
        await session.commit()
    return PityResponse.success({"count": len(endpoint_items), **summary})


@router.post("/import/yapi")
async def import_yapi(form: dict, user_info=Depends(Permission())):
    service_id = int(form.get("service_id") or 0)
    if not service_id:
        return PityResponse.failed("service_id不能为空")
    source_url = str(form.get("source_url") or "").strip()
    source_text = str(form.get("source_text") or "").strip()
    token = ""
    try:
        config_data = SystemConfiguration.get_config() or {}
        token = str(((config_data.get("yapi") or {}).get("token")) or "").strip()
    except Exception:
        token = ""

    if not source_text and not source_url:
        return PityResponse.failed("请提供 source_url 或 source_text")
    if not source_text and not token:
        return PityResponse.failed("系统设置未配置YAPI Token，请先到后台管理-系统设置配置")

    try:
        if source_text:
            payload = json.loads(source_text)
        else:
            final_url = source_url
            if token:
                final_url = f"{source_url}{'&' if '?' in source_url else '?'}token={token}"
            response = requests.get(final_url, timeout=120)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return PityResponse.failed(f"YAPI解析失败: {exc}")

    endpoint_items = parse_yapi_payload(payload)
    async with async_session() as session:
        await ensure_interface_schema(session)
        service = (await session.execute(
            select(PityApiService).where(PityApiService.id == service_id, PityApiService.deleted_at == 0)
        )).scalars().first()
        if service is None:
            return PityResponse.failed("服务不存在")
        old = deepcopy(service)
        service.source_type = "yapi"
        service.source_config = safe_json_dumps({"source_url": source_url})
        service.last_sync_status = "success"
        service.last_sync_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        service.update_user = user_info["id"]
        service.updated_at = datetime.now()
        summary = await upsert_endpoints(session, service, user_info["id"], endpoint_items)
        await PityOperationDao.insert_log(
            session,
            user_info["id"],
            OperationType.UPDATE,
            service,
            old,
            service.id,
            changed=["source_type", "source_config", "last_sync_status", "last_sync_at"],
        )
        await session.commit()
    return PityResponse.success({"count": len(endpoint_items), **summary})


@router.post("/service/sync")
async def sync_service(form: dict, user_info=Depends(Permission())):
    service_id = int(form.get("service_id") or 0)
    if not service_id:
        return PityResponse.failed("service_id不能为空")
    async with async_session() as session:
        await ensure_interface_schema(session)
        service = (await session.execute(
            select(PityApiService).where(PityApiService.id == service_id, PityApiService.deleted_at == 0)
        )).scalars().first()
    if service is None:
        return PityResponse.failed("服务不存在")

    source_type = (service.source_type or "manual").lower()
    config_data = safe_json_loads(service.source_config)
    if source_type == "swagger":
        return await import_swagger({
            "service_id": service_id,
            "source_url": config_data.get("source_url") or "",
        }, user_info)
    if source_type == "yapi":
        return await import_yapi({
            "service_id": service_id,
            "source_url": config_data.get("source_url") or "",
        }, user_info)
    return PityResponse.failed("该服务不是可同步来源，请先配置swagger或yapi")


@router.post("/endpoint/deprecate")
async def deprecate_endpoint(form: dict, user_info=Depends(Permission())):
    endpoint_id = int(form.get("endpoint_id") or 0)
    if not endpoint_id:
        return PityResponse.failed("endpoint_id不能为空")
    async with async_session() as session:
        await ensure_interface_schema(session)
        endpoint = (await session.execute(
            select(PityApiEndpoint).where(PityApiEndpoint.id == endpoint_id, PityApiEndpoint.deleted_at == 0)
        )).scalars().first()
        if endpoint is None:
            return PityResponse.failed("接口不存在")
        old = deepcopy(endpoint)
        endpoint.endpoint_status = "deprecated"
        endpoint.update_user = user_info["id"]
        endpoint.updated_at = datetime.now()
        await create_version(session, endpoint, user_info["id"])
        await session.flush()
        await PityOperationDao.insert_log(
            session,
            user_info["id"],
            OperationType.UPDATE,
            endpoint,
            old,
            endpoint.id,
            changed=["endpoint_status", "current_version_no"],
        )
        await session.commit()
    return PityResponse.success()

