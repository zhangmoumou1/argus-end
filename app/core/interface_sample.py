import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from loguru import logger
from sqlalchemy import select, text

from app.models.interface_manage import ArgusApiEndpoint, ArgusApiEndpointSample, ArgusApiService


MANUAL_SAMPLE_SOURCES = {"manual", "manual_associate", "manual_input"}


SAMPLE_CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS argus_api_endpoint_sample ("
    "id INT PRIMARY KEY AUTO_INCREMENT,"
    "project_id INT NOT NULL DEFAULT 0,"
    "service_id INT NOT NULL DEFAULT 0,"
    "endpoint_id INT NOT NULL DEFAULT 0,"
    "api_version_id INT NOT NULL DEFAULT 0,"
    "sample_source VARCHAR(32) NOT NULL DEFAULT 'record',"
    "sample_name VARCHAR(128) NULL,"
    "request_url VARCHAR(1024) NULL,"
    "request_path VARCHAR(512) NULL,"
    "request_query LONGTEXT NULL,"
    "request_headers LONGTEXT NULL,"
    "request_body LONGTEXT NULL,"
    "response_headers LONGTEXT NULL,"
    "response_body LONGTEXT NULL,"
    "status_code INT NOT NULL DEFAULT 0,"
    "recorded_at VARCHAR(32) NULL,"
    "created_at TIMESTAMP NOT NULL,"
    "updated_at TIMESTAMP NOT NULL,"
    "deleted_at BIGINT NOT NULL DEFAULT 0,"
    "create_user INT NOT NULL,"
    "update_user INT NOT NULL,"
    "UNIQUE KEY uk_endpoint_deleted (endpoint_id, deleted_at)"
    ")"
)


def safe_json_dumps(value):
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def safe_json_loads(value, default=None):
    if default is None:
        default = {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def normalize_api_path(path: str):
    value = str(path or "").strip()
    if not value.startswith("/"):
        value = "/" + value
    while "//" in value:
        value = value.replace("//", "/")
    if len(value) > 1 and value.endswith("/"):
        value = value[:-1]
    return value or "/"


def split_segments(path: str):
    normalized = normalize_api_path(path)
    return [item for item in normalized.split("/") if item]


def is_dynamic_segment(segment: str):
    text_value = str(segment or "").strip()
    return (
        (text_value.startswith("{") and text_value.endswith("}"))
        or text_value.startswith(":")
        or (text_value.startswith("<") and text_value.endswith(">"))
    )


def _parse_record_url(url: str):
    raw_url = str(url or "").strip()
    parsed = urlparse(raw_url)
    if not parsed.scheme and not parsed.netloc:
        first_segment = raw_url.split("/", 1)[0].strip()
        if first_segment and re.match(r"^([a-zA-Z0-9.-]+|\d{1,3}(?:\.\d{1,3}){3})(:\d+)?$", first_segment):
            parsed = urlparse(f"http://{raw_url}")
    host = str(parsed.netloc or "").strip().lower()
    path = normalize_api_path(parsed.path or "/")
    query = {
        key: values[0] if len(values) == 1 else values
        for key, values in parse_qs(parsed.query or "", keep_blank_values=True).items()
    }
    return {
        "host": host,
        "path": path,
        "query": query,
        "raw": parsed,
    }


def _candidate_paths(endpoint: ArgusApiEndpoint):
    ans = []
    endpoint_path = normalize_api_path(getattr(endpoint, "path", "") or "/")
    if endpoint_path not in ans:
        ans.append(endpoint_path)
    full_url = str(getattr(endpoint, "full_url", "") or "").strip()
    if full_url:
        parsed = urlparse(full_url)
        full_path = normalize_api_path(parsed.path or "/")
        if full_path not in ans:
            ans.append(full_path)
    return ans


def _extract_service_host(service: ArgusApiService):
    base_url = str(getattr(service, "base_url", "") or "").strip()
    if not base_url:
        return ""
    parsed = urlparse(base_url)
    return str(parsed.netloc or "").strip().lower()


def _build_request_path_variants(request_path: str):
    normalized = normalize_api_path(request_path)
    segments = split_segments(normalized)
    variants = []
    seen = set()
    for index in range(len(segments)):
        candidate = "/" + "/".join(segments[index:])
        candidate = normalize_api_path(candidate)
        if candidate not in seen:
            variants.append((candidate, index))
            seen.add(candidate)
    if normalized not in seen:
        variants.insert(0, (normalized, 0))
    if not variants:
        variants.append(("/", 0))
    return variants


def _template_match(request_path: str, candidate_path: str):
    request_segments = split_segments(request_path)
    candidate_segments = split_segments(candidate_path)
    if len(request_segments) != len(candidate_segments):
        return False
    for request_segment, candidate_segment in zip(request_segments, candidate_segments):
        if is_dynamic_segment(candidate_segment):
            continue
        if request_segment != candidate_segment:
            return False
    return True


def _score_variant_match(request_variant: str, trim_count: int, candidate_path: str):
    candidate_path = normalize_api_path(candidate_path)
    if request_variant == candidate_path:
        return 1000 - min(trim_count, 20) * 10 + len(split_segments(candidate_path))
    if _template_match(request_variant, candidate_path):
        return 800 - min(trim_count, 20) * 10 + len(split_segments(candidate_path))
    return 0


def _score_candidate(request_host: str, request_path: str, endpoint: ArgusApiEndpoint, service: ArgusApiService):
    service_host = _extract_service_host(service)
    best = 0
    matched_variant = ""
    for candidate_path in _candidate_paths(endpoint):
        for request_variant, trim_count in _build_request_path_variants(request_path):
            score = _score_variant_match(request_variant, trim_count, candidate_path)
            if service_host and request_host and service_host == request_host and score:
                score += 20
            if score > best:
                best = score
                matched_variant = request_variant
    return best, matched_variant


async def ensure_interface_sample_schema(session):
    await session.execute(text(SAMPLE_CREATE_SQL))
    for column_sql in [
        "ALTER TABLE argus_api_endpoint_sample MODIFY COLUMN request_query LONGTEXT NULL",
        "ALTER TABLE argus_api_endpoint_sample MODIFY COLUMN request_headers LONGTEXT NULL",
        "ALTER TABLE argus_api_endpoint_sample MODIFY COLUMN request_body LONGTEXT NULL",
        "ALTER TABLE argus_api_endpoint_sample MODIFY COLUMN response_headers LONGTEXT NULL",
        "ALTER TABLE argus_api_endpoint_sample MODIFY COLUMN response_body LONGTEXT NULL",
    ]:
        try:
            await session.execute(text(column_sql))
        except Exception:
            pass


async def match_endpoint_for_record(session, request_data: dict):
    request_url = str(request_data.get("url") or "").strip()
    if not request_url:
        return None, None, ""
    method = str(request_data.get("request_method") or "GET").upper()
    parsed = _parse_record_url(request_url)
    result = await session.execute(
        select(ArgusApiEndpoint, ArgusApiService)
        .join(ArgusApiService, ArgusApiService.id == ArgusApiEndpoint.service_id)
        .where(
            ArgusApiEndpoint.deleted_at == 0,
            ArgusApiService.deleted_at == 0,
            ArgusApiEndpoint.method == method,
            ArgusApiEndpoint.endpoint_status != "deprecated",
        )
    )
    best_pair = (None, None, "")
    best_score = 0
    for endpoint, service in result.all():
        score, matched_variant = _score_candidate(parsed["host"], parsed["path"], endpoint, service)
        if score > best_score:
            best_pair = (endpoint, service, matched_variant)
            best_score = score
    if best_pair[0] is None:
        logger.bind(name=None).warning(
            f"record sample match miss method={method}, url={request_url}, normalized_path={parsed['path']}"
        )
    else:
        logger.bind(name=None).info(
            f"record sample matched method={method}, url={request_url}, "
            f"endpoint_id={best_pair[0].id}, endpoint_path={best_pair[0].path}, matched_variant={best_pair[2]}, score={best_score}"
        )
    return best_pair


async def upsert_endpoint_sample_by_record(session, request_data: dict, user_id: int = 0):
    await ensure_interface_sample_schema(session)
    endpoint, service, matched_variant = await match_endpoint_for_record(session, request_data)
    if endpoint is None or service is None:
        return None
    sample = await get_endpoint_sample(session, endpoint.id)
    if sample is not None and str(getattr(sample, "sample_source", "") or "").lower() in MANUAL_SAMPLE_SOURCES:
        logger.bind(name=None).info(
            f"record sample skipped endpoint_id={endpoint.id}, service_id={service.id}, reason=manual_sample_locked"
        )
        return sample
    return await save_endpoint_sample(
        session=session,
        endpoint=endpoint,
        service=service,
        request_data=request_data,
        user_id=user_id,
        sample_source="record",
        matched_variant=matched_variant,
        sample=sample,
    )


def normalize_record_request_item(request_data: dict):
    payload = request_data if isinstance(request_data, dict) else {}
    return {
        "url": str(payload.get("url") or payload.get("request_url") or "").strip(),
        "request_method": str(payload.get("request_method") or payload.get("method") or "GET").upper(),
        "request_headers": payload.get("request_headers") or {},
        "body": payload.get("body") or payload.get("request_body") or "",
        "response_headers": payload.get("response_headers") or {},
        "response_content": payload.get("response_content") or payload.get("response_body") or "",
        "status_code": int(payload.get("status_code") or 0),
        "created_at": str(payload.get("created_at") or payload.get("recorded_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    }


async def batch_upsert_endpoint_samples_by_record(session, requests_data, user_id: int = 0):
    await ensure_interface_sample_schema(session)
    records = [normalize_record_request_item(item) for item in (requests_data or []) if isinstance(item, dict)]
    matched_count = 0
    saved_count = 0
    skipped_manual_count = 0
    missed_count = 0
    affected_endpoint_ids = []
    details = []

    for item in records:
        if not str(item.get("url") or "").strip():
            continue
        endpoint, service, matched_variant = await match_endpoint_for_record(session, item)
        if endpoint is None or service is None:
            missed_count += 1
            details.append({
                "url": item.get("url"),
                "method": item.get("request_method"),
                "status": "missed",
            })
            continue
        matched_count += 1
        sample = await get_endpoint_sample(session, endpoint.id)
        source = str(getattr(sample, "sample_source", "") or "").lower()
        if sample is not None and source in MANUAL_SAMPLE_SOURCES:
            skipped_manual_count += 1
            details.append({
                "endpoint_id": int(endpoint.id or 0),
                "service_id": int(service.id or 0),
                "url": item.get("url"),
                "method": item.get("request_method"),
                "status": "skipped_manual_locked",
            })
            logger.bind(name=None).info(
                f"record sample skipped endpoint_id={endpoint.id}, service_id={service.id}, reason=manual_sample_locked"
            )
            continue
        await save_endpoint_sample(
            session=session,
            endpoint=endpoint,
            service=service,
            request_data=item,
            user_id=user_id,
            sample_source="record",
            matched_variant=matched_variant,
            sample=sample,
        )
        saved_count += 1
        affected_endpoint_ids.append(int(endpoint.id or 0))
        details.append({
            "endpoint_id": int(endpoint.id or 0),
            "service_id": int(service.id or 0),
            "url": item.get("url"),
            "method": item.get("request_method"),
            "status": "saved",
        })

    return {
        "total_count": len(records),
        "matched_count": matched_count,
        "saved_count": saved_count,
        "skipped_manual_count": skipped_manual_count,
        "missed_count": missed_count,
        "affected_endpoint_ids": sorted(set(affected_endpoint_ids)),
        "details": details,
    }


async def get_endpoint_sample(session, endpoint_id: int):
    return (
        await session.execute(
            select(ArgusApiEndpointSample).where(
                ArgusApiEndpointSample.endpoint_id == endpoint_id,
                ArgusApiEndpointSample.deleted_at == 0,
            )
        )
    ).scalars().first()


async def save_endpoint_sample(
    session,
    endpoint: ArgusApiEndpoint,
    service: ArgusApiService,
    request_data: dict,
    user_id: int = 0,
    sample_source: str = "record",
    matched_variant: str = "",
    sample: ArgusApiEndpointSample = None,
):
    parsed = _parse_record_url(request_data.get("url"))
    sample_name = f"最近录制实例-{datetime_now_text(request_data.get('created_at'))}"
    payload = {
        "project_id": int(getattr(service, "project_id", 0) or 0),
        "service_id": int(getattr(service, "id", 0) or 0),
        "endpoint_id": int(getattr(endpoint, "id", 0) or 0),
        "api_version_id": int(getattr(endpoint, "current_version_id", 0) or 0),
        "sample_source": str(sample_source or "record").strip() or "record",
        "sample_name": sample_name,
        "request_url": str(request_data.get("url") or ""),
        "request_path": matched_variant or parsed["path"],
        "request_query": safe_json_dumps(parsed["query"]),
        "request_headers": safe_json_dumps(request_data.get("request_headers") or {}),
        "request_body": str(request_data.get("body") or ""),
        "response_headers": safe_json_dumps(request_data.get("response_headers") or {}),
        "response_body": str(request_data.get("response_content") or ""),
        "status_code": int(request_data.get("status_code") or 0),
        "recorded_at": str(request_data.get("created_at") or ""),
    }
    if sample is None:
        sample = ArgusApiEndpointSample(user=user_id or 0, **payload)
        session.add(sample)
    else:
        for key, value in payload.items():
            setattr(sample, key, value)
        sample.update_user = user_id or sample.update_user
    endpoint.update_user = user_id or endpoint.update_user
    logger.bind(name=None).info(
        f"endpoint sample saved endpoint_id={endpoint.id}, service_id={service.id}, "
        f"source={payload['sample_source']}, recorded_at={payload['recorded_at']}"
    )
    return sample


def datetime_now_text(value):
    value = str(value or "").strip()
    return value or "最新"
