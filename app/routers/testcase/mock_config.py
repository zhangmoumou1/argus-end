import asyncio
from datetime import datetime
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from starlette.responses import Response

from app.core.mock_rule import (
    ensure_mock_config_schema,
    invalidate_mock_rule_cache,
    list_mock_rules_for_proxy,
    match_mock_request,
    normalize_path_suffix,
    plain_query_map,
    row_to_dict,
    safe_json_dumps,
    safe_json_loads,
)
from app.crud.operation.PityOperationDao import PityOperationDao
from app.enums.OperationEnum import OperationType
from app.handler.fatcory import PityResponse
from app.models import async_session
from app.routers import Permission, get_session
from config import Config

router = APIRouter()


def _mock_log_model(row, user_id):
    model = SimpleNamespace()
    model.id = row.get("id")
    model.name = row.get("name")
    model.method = row.get("method")
    model.path_suffix = row.get("path_suffix")
    model.enabled = row.get("enabled")
    model.priority = row.get("priority")
    model.response_status = row.get("response_status")
    model.remark = row.get("remark")
    model.create_user = user_id
    model.update_user = user_id
    model.__fields__ = [
        SimpleNamespace(name="name"),
        SimpleNamespace(name="method"),
        SimpleNamespace(name="path_suffix"),
        SimpleNamespace(name="enabled"),
        SimpleNamespace(name="priority"),
        SimpleNamespace(name="response_status"),
    ]
    model.__tag__ = "Mock规则"
    model.__alias__ = dict(
        name="规则名称",
        method="请求方法",
        path_suffix="路径后缀",
        enabled="启用",
        priority="优先级",
        response_status="响应状态码",
        remark="备注",
    )
    return model


def _text_or_none(value):
    if value in (None, "", {}, []):
        return None
    return safe_json_dumps(value) if isinstance(value, (dict, list)) else str(value)


def _validate_form(form: dict):
    name = str(form.get("name") or "").strip()
    suffix = normalize_path_suffix(form.get("path_suffix"))
    method = str(form.get("method") or "ANY").upper()
    if not name:
        raise ValueError("Mock名称不能为空")
    if not suffix:
        raise ValueError("接口后缀不能为空")
    if method not in ("ANY", "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        raise ValueError("请求方法不支持")
    response_status = int(form.get("response_status") or 200)
    if response_status < 100 or response_status > 599:
        raise ValueError("响应状态码必须在100-599之间")
    return {
        "name": name,
        "method": method,
        "path_suffix": suffix,
        "enabled": 1 if form.get("enabled", True) else 0,
        "priority": int(form.get("priority") or 0),
        "match_query": _text_or_none(form.get("match_query")),
        "match_headers": _text_or_none(form.get("match_headers")),
        "match_body": _text_or_none(form.get("match_body")),
        "response_status": response_status,
        "response_headers": _text_or_none(form.get("response_headers")),
        "response_body": str(form.get("response_body") or ""),
        "response_delay_ms": int(form.get("response_delay_ms") or 0),
        "remark": str(form.get("remark") or "").strip() or None,
    }


@router.get("/mock-config/list", summary="Mock规则列表")
async def list_mock_config(keyword: str = "", enabled: int = None, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_mock_config_schema(session)
        filters = ["deleted_at = 0"]
        params = {}
        if keyword:
            filters.append("(name LIKE :keyword OR path_suffix LIKE :keyword)")
            params["keyword"] = f"%{keyword}%"
        if enabled is not None:
            filters.append("enabled = :enabled")
            params["enabled"] = int(enabled)
        result = await session.execute(text(
            "SELECT * FROM pity_mock_config "
            f"WHERE {' AND '.join(filters)} "
            "ORDER BY priority DESC, id DESC"
        ), params)
        rows = [row_to_dict(row) for row in result.fetchall()]
    for row in rows:
        row["mock_url"] = f"{Config.SERVER_HOST if hasattr(Config, 'SERVER_HOST') else ''}/mock-api{row.get('path_suffix')}"
    return PityResponse.success(rows)


@router.post("/mock-config/save", summary="新增或更新Mock规则")
async def save_mock_config(form: dict, user_info=Depends(Permission()), session=Depends(get_session)):
    try:
        await ensure_mock_config_schema(session)
        payload = _validate_form(form)
        now = datetime.now()
        mock_id = int(form.get("id") or 0)
        if mock_id:
            exists_row = (await session.execute(text(
                "SELECT * FROM pity_mock_config WHERE id = :id AND deleted_at = 0"
            ), {"id": mock_id})).mappings().first()
            exists = exists_row
            if exists is None:
                return PityResponse.failed("Mock规则不存在")
            await session.execute(text(
                "UPDATE pity_mock_config SET "
                "name=:name, method=:method, path_suffix=:path_suffix, enabled=:enabled, priority=:priority, "
                "match_query=:match_query, match_headers=:match_headers, match_body=:match_body, "
                "response_status=:response_status, response_headers=:response_headers, response_body=:response_body, "
                "response_delay_ms=:response_delay_ms, remark=:remark, updated_at=:updated_at, update_user=:user_id "
                "WHERE id=:id AND deleted_at = 0"
            ), {**payload, "updated_at": now, "user_id": user_info["id"], "id": mock_id})
            new_row = dict(exists_row)
            new_row.update(payload)
            new_row["id"] = mock_id
            await PityOperationDao.insert_log(
                session,
                user_info["id"],
                OperationType.UPDATE,
                _mock_log_model(new_row, user_info["id"]),
                _mock_log_model(dict(exists_row), user_info["id"]),
                mock_id,
                changed=list(payload.keys()),
            )
        else:
            await session.execute(text(
                "INSERT INTO pity_mock_config "
                "(name, method, path_suffix, enabled, priority, match_query, match_headers, match_body, "
                "response_status, response_headers, response_body, response_delay_ms, remark, "
                "created_at, updated_at, deleted_at, create_user, update_user) "
                "VALUES "
                "(:name, :method, :path_suffix, :enabled, :priority, :match_query, :match_headers, :match_body, "
                ":response_status, :response_headers, :response_body, :response_delay_ms, :remark, "
                ":created_at, :updated_at, 0, :user_id, :user_id)"
            ), {**payload, "created_at": now, "updated_at": now, "user_id": user_info["id"]})
            inserted = (await session.execute(text("SELECT * FROM pity_mock_config ORDER BY id DESC LIMIT 1"))).mappings().first()
            if inserted:
                await PityOperationDao.insert_log(
                    session,
                    user_info["id"],
                    OperationType.INSERT,
                    _mock_log_model(dict(inserted), user_info["id"]),
                    key=inserted.get("id"),
                    changed=list(payload.keys()),
                )
        await session.commit()
        invalidate_mock_rule_cache()
        return PityResponse.success()
    except Exception as exc:
        await session.rollback()
        return PityResponse.failed(exc)


@router.post("/mock-config/toggle", summary="启停Mock规则")
async def toggle_mock_config(form: dict, user_info=Depends(Permission()), session=Depends(get_session)):
    mock_id = int(form.get("id") or 0)
    enabled = 1 if form.get("enabled") else 0
    if not mock_id:
        return PityResponse.failed("id不能为空")
    await ensure_mock_config_schema(session)
    old_row = (await session.execute(text(
        "SELECT * FROM pity_mock_config WHERE id=:id AND deleted_at = 0"
    ), {"id": mock_id})).mappings().first()
    await session.execute(text(
        "UPDATE pity_mock_config SET enabled=:enabled, updated_at=:updated_at, update_user=:user_id "
        "WHERE id=:id AND deleted_at = 0"
    ), {"enabled": enabled, "updated_at": datetime.now(), "user_id": user_info["id"], "id": mock_id})
    if old_row:
        new_row = dict(old_row)
        new_row["enabled"] = enabled
        await PityOperationDao.insert_log(
            session,
            user_info["id"],
            OperationType.UPDATE,
            _mock_log_model(new_row, user_info["id"]),
            _mock_log_model(dict(old_row), user_info["id"]),
            mock_id,
            changed=["enabled"],
        )
    await session.commit()
    invalidate_mock_rule_cache()
    return PityResponse.success()


@router.get("/mock-config/delete", summary="删除Mock规则")
async def delete_mock_config(id: int, user_info=Depends(Permission()), session=Depends(get_session)):
    await ensure_mock_config_schema(session)
    old_row = (await session.execute(text(
        "SELECT * FROM pity_mock_config WHERE id=:id AND deleted_at = 0"
    ), {"id": id})).mappings().first()
    await session.execute(text(
        "UPDATE pity_mock_config SET deleted_at=:deleted_at, updated_at=:updated_at, update_user=:user_id "
        "WHERE id=:id AND deleted_at = 0"
    ), {"deleted_at": int(datetime.now().timestamp()), "updated_at": datetime.now(), "user_id": user_info["id"], "id": id})
    if old_row:
        deleted_row = dict(old_row)
        deleted_row["deleted_at"] = int(datetime.now().timestamp())
        await PityOperationDao.insert_log(session, user_info["id"], OperationType.DELETE, _mock_log_model(deleted_row, user_info["id"]), key=id)
    await session.commit()
    invalidate_mock_rule_cache()
    return PityResponse.success()


@router.api_route("/mock-api/{mock_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def mock_api(mock_path: str, request: Request):
    rules = await list_mock_rules_for_proxy()
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="ignore") if body_bytes else ""
    query = plain_query_map(str(request.url.query))
    path = normalize_path_suffix(mock_path)
    matched = match_mock_request(request.method, path, query, dict(request.headers), body_text, rules)
    if matched is None:
        return Response(
            safe_json_dumps({"code": 404, "msg": "Mock规则未匹配", "path": path}),
            status_code=404,
            media_type="application/json",
            headers={"x-argux-mock": "miss"},
        )
    delay_ms = int(matched.get("response_delay_ms") or 0)
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
    headers = safe_json_loads(matched.get("response_headers"), {})
    headers["x-argux-mock"] = "hit"
    headers["x-argux-mock-rule"] = str(matched.get("id"))
    media_type = headers.pop("Content-Type", headers.pop("content-type", "application/json; charset=utf-8"))
    return Response(
        content=str(matched.get("response_body") or ""),
        status_code=int(matched.get("response_status") or 200),
        headers=headers,
        media_type=media_type,
    )
