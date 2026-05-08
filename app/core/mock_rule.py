import json
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text

from app.models import async_session


MOCK_CONFIG_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS pity_mock_config ("
    "id INT PRIMARY KEY AUTO_INCREMENT,"
    "name VARCHAR(128) NOT NULL,"
    "method VARCHAR(16) NOT NULL DEFAULT 'ANY',"
    "path_suffix VARCHAR(512) NOT NULL,"
    "enabled INT NOT NULL DEFAULT 1,"
    "priority INT NOT NULL DEFAULT 0,"
    "match_query LONGTEXT NULL,"
    "match_headers LONGTEXT NULL,"
    "match_body LONGTEXT NULL,"
    "response_status INT NOT NULL DEFAULT 200,"
    "response_headers LONGTEXT NULL,"
    "response_body LONGTEXT NULL,"
    "response_delay_ms INT NOT NULL DEFAULT 0,"
    "remark VARCHAR(512) NULL,"
    "created_at TIMESTAMP NOT NULL,"
    "updated_at TIMESTAMP NOT NULL,"
    "deleted_at BIGINT NOT NULL DEFAULT 0,"
    "create_user INT NOT NULL,"
    "update_user INT NOT NULL,"
    "INDEX idx_mock_enabled (enabled, deleted_at),"
    "INDEX idx_mock_method (method)"
    ")"
)

_RULE_CACHE = {"expired_at": 0, "rules": []}


def invalidate_mock_rule_cache():
    _RULE_CACHE["expired_at"] = 0
    _RULE_CACHE["rules"] = []


def safe_json_loads(value, default=None):
    if default is None:
        default = {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def safe_json_dumps(value):
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def normalize_path_suffix(value: str):
    text_value = str(value or "").strip()
    if not text_value:
        return ""
    if not text_value.startswith("/"):
        text_value = "/" + text_value
    while "//" in text_value:
        text_value = text_value.replace("//", "/")
    return text_value


async def ensure_mock_config_schema(session=None):
    if session is not None:
        await session.execute(text(MOCK_CONFIG_TABLE_SQL))
        return
    async with async_session() as new_session:
        await new_session.execute(text(MOCK_CONFIG_TABLE_SQL))
        await new_session.commit()


def row_to_dict(row):
    data = dict(row._mapping if hasattr(row, "_mapping") else row)
    for key in ("created_at", "updated_at"):
        if isinstance(data.get(key), datetime):
            data[key] = data[key].strftime("%Y-%m-%d %H:%M:%S")
    for key in ("match_query", "match_headers", "response_headers"):
        data[key] = safe_json_loads(data.get(key), {})
    return data


async def list_mock_rules_for_proxy():
    now = time.time()
    if _RULE_CACHE["expired_at"] > now:
        return _RULE_CACHE["rules"]
    await ensure_mock_config_schema()
    async with async_session() as session:
        result = await session.execute(text(
            "SELECT * FROM pity_mock_config "
            "WHERE deleted_at = 0 AND enabled = 1 "
            "ORDER BY priority DESC, id DESC"
        ))
        rules = [row_to_dict(row) for row in result.fetchall()]
    _RULE_CACHE["rules"] = rules
    _RULE_CACHE["expired_at"] = now + 2
    return rules


def plain_query_map(query: str):
    parsed = parse_qs(query or "", keep_blank_values=True)
    return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}


def lower_header_map(headers):
    return {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}


def body_matches(expected, body_text: str):
    if expected in (None, "", {}, []):
        return True
    if isinstance(expected, str):
        return expected in (body_text or "")
    if isinstance(expected, dict):
        actual = safe_json_loads(body_text, None)
        if not isinstance(actual, dict):
            return False
        for key, value in expected.items():
            if key not in actual:
                return False
            if str(actual.get(key)) != str(value):
                return False
        return True
    return False


def kv_subset_matches(expected, actual, lower_keys=False):
    if not expected:
        return True
    actual_map = lower_header_map(actual) if lower_keys else dict(actual or {})
    for key, value in dict(expected).items():
        lookup_key = str(key).lower() if lower_keys else str(key)
        if lookup_key not in actual_map:
            return False
        if str(actual_map.get(lookup_key)) != str(value):
            return False
    return True


def match_mock_request(method, path, query=None, headers=None, body_text="", rules=None):
    request_method = str(method or "GET").upper()
    request_path = normalize_path_suffix(path or "/")
    request_query = query or {}
    request_headers = dict(headers or {})

    for rule in rules or []:
        rule_method = str(rule.get("method") or "ANY").upper()
        if rule_method not in ("ANY", request_method):
            continue
        suffix = normalize_path_suffix(rule.get("path_suffix"))
        if not suffix or not request_path.endswith(suffix):
            continue
        if not kv_subset_matches(rule.get("match_query"), request_query):
            continue
        if not kv_subset_matches(rule.get("match_headers"), request_headers, lower_keys=True):
            continue
        expected_body = safe_json_loads(rule.get("match_body"), rule.get("match_body"))
        if not body_matches(expected_body, body_text):
            continue
        return rule
    return None


def match_mock_rule(flow, rules):
    parsed = urlparse(flow.request.url)
    try:
        request_body = flow.request.get_text(strict=False) or ""
    except Exception:
        request_body = ""
    return match_mock_request(
        method=flow.request.method,
        path=parsed.path or "/",
        query=plain_query_map(parsed.query),
        headers=dict(flow.request.headers),
        body_text=request_body,
        rules=rules,
    )
