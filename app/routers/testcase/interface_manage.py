import json
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urljoin

import requests
from fastapi import APIRouter, Depends
from sqlalchemy import select, text, func

from app.handler.fatcory import PityResponse
from app.core.configuration import SystemConfiguration
from app.models import async_session
from app.models.interface_manage import PityApiService, PityApiEndpoint, PityApiEndpointVersion
from app.routers import Permission
from app.utils.json_compare import JsonCompare

router = APIRouter(prefix="/interface-management")
DEFAULT_SYNC_CRON = "0 0 * * *"


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
            ans.append({
                "name": op.get("summary") or op.get("operationId") or f"{method.upper()} {raw_path}",
                "method": method.upper(),
                "module_name": ((op.get("tags") or ["默认模块"])[0] if isinstance(op.get("tags"), list) else "默认模块"),
                "endpoint_status": "deprecated" if bool(op.get("deprecated")) else "available",
                "path": normalize_path(raw_path),
                "request_headers": [x for x in (op.get("parameters") or []) if isinstance(x, dict) and str(x.get("in")) == "header"],
                "request_params": {
                    "parameters": op.get("parameters") or [],
                    "requestBody": op.get("requestBody") or {},
                },
                "response_body": op.get("responses") or {},
            })
    return ans


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
        ans.append({
            "name": item.get("title") or f"{method} {raw_path}",
            "method": method,
            "module_name": item.get("cat_name") or "默认模块",
            "endpoint_status": "deprecated" if str(item.get("status") or "").lower() in ("deprecated", "disable", "disabled") else "available",
            "path": normalize_path(raw_path),
            "request_headers": item.get("req_headers") or [],
            "request_params": {
                "req_query": item.get("req_query") or [],
                "req_headers": item.get("req_headers") or [],
                "req_body_other": item.get("req_body_other") or "",
                "req_body_form": item.get("req_body_form") or [],
            },
            "response_body": item.get("res_body") or "",
        })
    return ans


def resolve_swagger_payload(source_url: str):
    source_url = str(source_url or "").strip()
    if not source_url:
        raise ValueError("source_url不能为空")
    # 1) direct JSON
    direct_resp = requests.get(source_url, timeout=120)
    if direct_resp.ok:
        try:
            direct_json = direct_resp.json()
            if isinstance(direct_json, dict) and (direct_json.get("openapi") or direct_json.get("swagger")):
                return direct_json
        except Exception:
            pass
    # 2) swagger-ui html -> swagger-config -> select named url
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
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS pity_api_service ("
        "id INT PRIMARY KEY AUTO_INCREMENT,"
        "project_id INT NOT NULL DEFAULT 0,"
        "name VARCHAR(128) NOT NULL,"
        "base_url VARCHAR(255) NULL,"
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
    # backward compatible columns for pity_testcase
    for column_sql in [
        "ALTER TABLE pity_api_endpoint ADD COLUMN module_name VARCHAR(128) NOT NULL DEFAULT '默认模块' COMMENT '功能模块'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN module_name VARCHAR(128) NOT NULL DEFAULT '默认模块' COMMENT '功能模块'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT '接口状态'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT '接口状态'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN request_headers LONGTEXT NULL COMMENT '请求头'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN request_headers LONGTEXT NULL COMMENT '请求头'",
        "ALTER TABLE pity_api_service ADD COLUMN developer VARCHAR(128) NULL COMMENT '开发人员'",
        "ALTER TABLE pity_api_service ADD COLUMN tester VARCHAR(128) NULL COMMENT '测试人员'",
        "ALTER TABLE pity_testcase ADD COLUMN api_service_id INT NOT NULL DEFAULT 0 COMMENT '绑定服务ID'",
        "ALTER TABLE pity_testcase ADD COLUMN api_endpoint_id INT NOT NULL DEFAULT 0 COMMENT '绑定接口ID'",
        "ALTER TABLE pity_testcase ADD COLUMN api_version_id INT NOT NULL DEFAULT 0 COMMENT '绑定接口版本ID'",
        "ALTER TABLE pity_testcase ADD COLUMN api_version_no VARCHAR(32) NULL COMMENT '绑定接口版本号'",
        "ALTER TABLE pity_testcase ADD COLUMN api_bind_mode VARCHAR(16) NOT NULL DEFAULT 'pinned' COMMENT '绑定模式'",
        "ALTER TABLE pity_testcase ADD COLUMN api_pending_update INT NOT NULL DEFAULT 0 COMMENT '是否待更新'",
    ]:
        try:
            await session.execute(text(column_sql))
        except Exception:
            pass
    await session.commit()


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


async def upsert_endpoints(session, service: PityApiService, user_id: int, endpoint_items):
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
            result["created"] += 1
            continue

        changed = False
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
        name = str(form.get("name") or model.name).strip()
        if not name:
            return PityResponse.failed("服务名称不能为空")
        if form.get("project_id") is not None:
            model.project_id = int(form.get("project_id") or 0)
        model.name = name
        model.base_url = str(form.get("base_url") or model.base_url or "").strip()
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
            select(PityApiEndpoint).where(*filters).order_by(PityApiEndpoint.module_name.asc(), PityApiEndpoint.updated_at.desc(), PityApiEndpoint.id.desc())
        )
        rows = result.scalars().all()
        data = [serialize_model(item) for item in rows]
        module_result = await session.execute(
            select(PityApiEndpoint.module_name).where(
                PityApiEndpoint.service_id == service_id,
                PityApiEndpoint.deleted_at == 0,
            ).distinct()
        )
        modules = [str(item[0] or "默认模块") for item in module_result.all()]
    return PityResponse.success({"list": data, "modules": sorted(list(set(modules)))})


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
    return PityResponse.success(data)


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
        for field_name, l_val, r_val in fields:
            compare_rows = comparer.compare(l_val, r_val)
            diff[field_name] = compare_rows
            if compare_rows:
                changed_fields.append(field_name)
    return PityResponse.success({
        "left_version_id": left_version_id,
        "right_version_id": right_version_id,
        "changed_fields": changed_fields,
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
    async with async_session() as session:
        await ensure_interface_schema(session)
        service = (await session.execute(
            select(PityApiService).where(PityApiService.id == service_id, PityApiService.deleted_at == 0)
        )).scalars().first()
        if service is None:
            return PityResponse.failed("服务不存在")
        service.source_type = "swagger"
        service.source_config = safe_json_dumps({"source_url": source_url})
        service.last_sync_status = "success"
        service.last_sync_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        service.update_user = user_info["id"]
        service.updated_at = datetime.now()
        summary = await upsert_endpoints(session, service, user_info["id"], endpoint_items)
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
        service.source_type = "yapi"
        service.source_config = safe_json_dumps({"source_url": source_url})
        service.last_sync_status = "success"
        service.last_sync_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        service.update_user = user_info["id"]
        service.updated_at = datetime.now()
        summary = await upsert_endpoints(session, service, user_info["id"], endpoint_items)
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
        endpoint.endpoint_status = "deprecated"
        endpoint.update_user = user_info["id"]
        endpoint.updated_at = datetime.now()
        await create_version(session, endpoint, user_info["id"])
        await session.commit()
    return PityResponse.success()
