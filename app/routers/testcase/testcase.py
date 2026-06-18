import json
import re
import time
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from typing import List, TypeVar
from types import SimpleNamespace

import requests
from fastapi import APIRouter, Depends, Query, UploadFile, File, Request
from sqlalchemy import select, text

from app.core.request import get_convertor
from app.core.request.generator import CaseGenerator
from app.core.platform_task import PlatformTaskService
from app.crud.config.GConfigDao import GConfigDao
from app.crud.operation.ArgusOperationDao import ArgusOperationDao
from app.crud.project.ProjectRoleDao import ProjectRoleDao
from app.crud.test_case.ConstructorDao import ConstructorDao
from app.crud.test_case.TestCaseAssertsDao import TestCaseAssertsDao
from app.crud.test_case.TestCaseDao import TestCaseDao
from app.crud.test_case.TestCaseDirectory import ArgusTestcaseDirectoryDao
from app.crud.test_case.TestCaseOutParametersDao import ArgusTestCaseOutParametersDao
from app.crud.test_case.TestReport import TestReportDao
from app.crud.test_case.TestcaseDataDao import ArgusTestcaseDataDao
from app.enums.OperationEnum import OperationType
from app.enums.ConvertorEnum import CaseConvertorType
from app.enums.platform_task import PlatformTaskType
from app.exception.error import AuthError
from app.handler.fatcory import ArgusResponse
from app.middleware.RedisManager import RedisHelper
from app.models import async_session
from app.models.operation_log import ArgusOperationLog
from app.models.interface_manage import ArgusApiEndpoint, ArgusApiEndpointVersion, ArgusApiEndpointSample, ArgusApiService
from app.models.out_parameters import ArgusTestCaseOutParameters
from app.models.test_case import TestCase
from app.models.test_plan import ArgusTestPlan
from app.models.project import Project
from app.models.testcase_directory import ArgusTestcaseDirectory
from app.routers import Permission, get_session
from app.schema.constructor import ConstructorForm, ConstructorIndex
from app.schema.testcase_data import ArgusTestcaseDataForm
from app.schema.testcase_directory import ArgusTestcaseDirectoryForm, ArgusMoveTestCaseDto, \
    ArgusTestcaseDirectoryUpdateForm
from app.schema.testcase_out_parameters import ArgusTestCaseOutParametersForm, ArgusTestCaseParametersDto, \
    ArgusTestCaseVariablesDto
from app.schema.testcase_schema import TestCaseAssertsForm, TestCaseForm, TestCaseInfo, TestCaseGeneratorForm
from app.utils.logger import Log
from config import Config

router = APIRouter(prefix="/testcase")
Author = TypeVar("Author", int, str)
logger = Log("testcase_ai_flow")
AI_PREVIEW_LIMIT = 8
AI_KIMI_TIMEOUT = 180
AI_CONTEXT_HEADER_LIMIT = 300
AI_CONTEXT_PARAM_LIMIT = 700
AI_CONTEXT_RESPONSE_LIMIT = 700


def preview_text(value, limit=2000):
    text_value = str(value or "").strip()
    if len(text_value) <= limit:
        return text_value
    return f"{text_value[:limit]} ...<truncated {len(text_value) - limit} chars>"


GPT_RESPONSES_USER_AGENT = "codex_vscode/0.136.0-alpha.2 (Windows 10.0.26200; x86_64) unknown (VS Code; 26.527.60818)"


def is_gpt_model(model):
    return str(model or "").strip().lower().startswith("gpt")


def resolve_ai_wire_api(ai_config, provider="", model=""):
    value = str((ai_config or {}).get("wire_api") or "").strip().lower().replace("-", "_").replace("/", "_")
    if value in ("chat_completions", "responses"):
        return value
    base_url_value = str((ai_config or {}).get("base_url") or "").strip().lower().rstrip("/")
    if base_url_value.endswith("/chat/completions"):
        return "chat_completions"
    if base_url_value.endswith("/responses"):
        return "responses"
    provider_value = str(provider or "").strip().lower()
    if is_gpt_model(model) and provider_value in ("openai", "custom"):
        return "responses"
    return "chat_completions"


def normalize_ai_base_url(base_url, wire_api, provider="", model=""):
    original = str(base_url or "").strip()
    if not original:
        return original
    provider_value = str(provider or "").strip().lower()
    if provider_value == "custom":
        return original
    normalized = original.rstrip("/")
    if wire_api not in ("chat_completions", "responses"):
        return normalized
    parsed = urlsplit(normalized)
    path_value = str(parsed.path or "").strip().rstrip("/")
    if path_value:
        return normalized
    if provider_value == "openai" or is_gpt_model(model):
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", parsed.query, parsed.fragment)).rstrip("/")
    return normalized


def build_ai_request_url(base_url, wire_api, provider="", model=""):
    normalized = normalize_ai_base_url(base_url, wire_api, provider, model)
    normalized_no_slash = str(normalized or "").rstrip("/")
    provider_value = str(provider or "").strip().lower()
    if provider_value == "custom":
        return normalized
    base_url_value = str(base_url or "").strip().lower().rstrip("/")
    if base_url_value.endswith("/chat/completions") or base_url_value.endswith("/responses"):
        return normalized
    if wire_api == "responses":
        return f"{normalized_no_slash}/responses"
    return f"{normalized_no_slash}/chat/completions"


def build_non_json_response_error(provider, model, wire_api, request_url, response, body_preview):
    content_type = str(response.headers.get("Content-Type") or "").strip()
    content_encoding = str(response.headers.get("Content-Encoding") or "").strip()
    provider_value = str(provider or "").strip().lower()
    hint = ""
    if provider_value == "custom":
        hint = " 自定义供应商当前会原样请求你配置的地址，不自动补 /v1、/responses 或 /chat/completions，请确认后台“请求地址”填写的是完整接口地址。"
    return (
        f"AI模型({provider}/{model})响应不是有效 JSON: request_url={request_url}, "
        f"wire_api={wire_api}, content_type={content_type or 'unknown'}, "
        f"content_encoding={content_encoding or 'unknown'}。{hint}响应预览: {body_preview}"
    )


def build_api_key_preview(api_key):
    value = str(api_key or "").strip()
    if not value:
        return ""
    if len(value) <= 12:
        return value
    return f"{value[:8]}***{value[-6:]}"


def normalize_request_model_name(model, provider=""):
    value = str(model or "").strip()
    provider_value = str(provider or "").strip().lower()
    if is_gpt_model(value) and provider_value in ("openai", "custom"):
        return value.lower()
    return value


def build_ai_headers(api_key, model):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }
    if is_gpt_model(model):
        headers["User-Agent"] = GPT_RESPONSES_USER_AGENT
    return headers


def build_responses_request_payload(model, system_content, user_content, temperature=1, max_output_tokens=None):
    payload = {
        "model": model,
        "store": False,
        "instructions": str(system_content or ""),
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": str(user_content or "")}],
            }
        ],
        "text": {"format": {"type": "text"}},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
    return payload


def extract_responses_output_text(payload):
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    text_value = payload.get("text")
    if isinstance(text_value, str) and text_value.strip():
        return text_value.strip()
    if isinstance(text_value, dict):
        for key in ("text", "value", "content"):
            nested = text_value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()

    response_value = payload.get("response")
    if isinstance(response_value, dict):
        nested_output_text = response_value.get("output_text")
        if isinstance(nested_output_text, str) and nested_output_text.strip():
            return nested_output_text.strip()

    text_parts = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        item_role = str(item.get("role") or "").strip().lower()
        if item_type in ("output_text", "text"):
            text_parts.append(str(item.get("text") or ""))
            continue
        if item_role == "assistant":
            assistant_text = item.get("text")
            if isinstance(assistant_text, str) and assistant_text.strip():
                text_parts.append(assistant_text)
        for content_item in item.get("content") or []:
            if not isinstance(content_item, dict):
                continue
            content_type = str(content_item.get("type") or "").strip().lower()
            if content_type in ("output_text", "text", "input_text"):
                text_parts.append(str(content_item.get("text") or ""))
                continue
            if content_type == "message":
                nested_text = content_item.get("text") or content_item.get("content")
                if isinstance(nested_text, str) and nested_text.strip():
                    text_parts.append(nested_text)
    merged = "\n".join([part for part in text_parts if str(part or "").strip()]).strip()
    if merged:
        return merged

    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            merged_content = "\n".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ).strip()
            if merged_content:
                return merged_content
        if isinstance(content, str) and content.strip():
            return content.strip()

    usage = payload.get("usage") or {}
    output_tokens = usage.get("output_tokens")
    response_id = payload.get("id") or ""
    status = payload.get("status") or ""
    output_count = len(payload.get("output") or []) if isinstance(payload.get("output"), list) else 0
    text_meta = payload.get("text") if isinstance(payload.get("text"), dict) else {}
    raise ValueError(
        "AI模型未返回可用结果"
        f"(response_id={response_id or 'unknown'}, status={status or 'unknown'}, "
        f"output_count={output_count}, output_tokens={output_tokens if output_tokens is not None else 'unknown'}, "
        f"text_meta={preview_text(text_meta, 300)})"
    )


def build_ai_flow_preview_operation_log(form, context, normalized):
    service = context.get("service") or {}
    endpoints = context.get("endpoints") or []
    warnings = normalized.get("warnings") or []
    cases = normalized.get("cases") or []
    first_case = cases[0] if cases and isinstance(cases[0], dict) else {}
    return {
        "title": "项目ID={}&目录ID={}".format(form.get("project_id") or 0, form.get("directory_id") or 0),
        "tag": "AI生成流程场景预览",
        "description": json.dumps([
            {"name": "项目ID", "now": form.get("project_id") or 0},
            {"name": "目录ID", "now": form.get("directory_id") or 0},
            {"name": "服务ID", "now": form.get("service_id") or 0},
            {"name": "服务名称", "now": service.get("name") or ""},
            {"name": "接口数量", "now": len(endpoints)},
            {"name": "技能文档数", "now": len(form.get("skill_doc_ids") or [])},
            {"name": "生成风格", "now": form.get("generate_style") or "standard"},
            {"name": "包含异常场景", "now": bool(form.get("include_negative"))},
            {"name": "包含断言", "now": bool(form.get("include_asserts"))},
            {"name": "业务目标", "now": preview_text(form.get("business_goal"), 300)},
            {"name": "预览生成条数", "now": len(cases)},
            {"name": "预警数", "now": len(warnings)},
            {"name": "生成场景", "now": normalized.get("scenario_name") or ""},
            {"name": "生成摘要", "now": preview_text(normalized.get("summary"), 300)},
            {"name": "首个用例", "now": preview_text(first_case.get("name") or "", 120)},
            {"name": "生成结果", "now": preview_text(json.dumps(normalized, ensure_ascii=False), 2000)},
        ], ensure_ascii=False),
    }


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


def parse_case_id_list(raw_value):
    result = []
    for item in str(raw_value or "").split(","):
        value = str(item).strip()
        if not value:
            continue
        try:
            result.append(int(value))
        except Exception:
            continue
    return result


def compact_ai_value(value, limit):
    if value in (None, "", [], {}):
        return {} if isinstance(value, dict) else [] if isinstance(value, list) else ""
    text_value = safe_json_dumps(value) if isinstance(value, (dict, list)) else str(value)
    text_value = re.sub(r"\s+", " ", text_value).strip()
    if len(text_value) > limit:
        return f"{text_value[:limit]} ...<truncated {len(text_value) - limit} chars>"
    try:
        return json.loads(text_value)
    except Exception:
        return text_value


def extract_json_object(text_value: str):
    text_value = (text_value or "").strip()
    if not text_value:
        raise ValueError("AI 未返回内容")
    try:
        return json.loads(text_value)
    except Exception:
        pass
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text_value, re.IGNORECASE)
    for item in fenced:
        try:
            return json.loads(item)
        except Exception:
            continue
    start = text_value.find("{")
    end = text_value.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text_value[start:end + 1])
    raise ValueError("AI 返回结果不是有效 JSON")


def normalize_ai_case(raw_case, index=1):
    name = str(raw_case.get("name") or raw_case.get("title") or f"AI流程场景-{index}").strip()
    method = str(raw_case.get("method") or raw_case.get("request_method") or "GET").upper()
    url = str(raw_case.get("url") or raw_case.get("path") or "").strip()
    headers = raw_case.get("headers") or raw_case.get("request_headers") or {}
    body = raw_case.get("body") if "body" in raw_case else raw_case.get("request_body")
    body_type = int(raw_case.get("body_type") or (1 if body not in (None, "", {}, []) else 0))
    asserts = raw_case.get("asserts") or []
    extractors = raw_case.get("out_parameters") or raw_case.get("extractors") or []
    pre_steps = raw_case.get("pre_steps") or []
    tags = raw_case.get("tags") or ["AI生成", "流程场景"]
    if isinstance(tags, str):
        tags = [tags]
    return {
        "key": f"ai-{index}",
        "name": name[:64],
        "priority": str(raw_case.get("priority") or "P1"),
        "status": int(raw_case.get("status") or 3),
        "request_type": int(raw_case.get("request_type") or 1),
        "request_method": method,
        "url": url,
        "body_type": body_type,
        "request_headers": headers,
        "body": body,
        "asserts": asserts,
        "out_parameters": extractors,
        "pre_steps": pre_steps,
        "reason": raw_case.get("reason") or raw_case.get("description") or "基于所选接口链路生成",
        "tags": tags,
        "api_service_id": int(raw_case.get("api_service_id") or 0),
        "api_endpoint_id": int(raw_case.get("api_endpoint_id") or 0),
        "api_version_id": int(raw_case.get("api_version_id") or 0),
        "api_version_no": raw_case.get("api_version_no") or None,
    }


def normalize_ai_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("AI 返回格式不正确")
    scenario = str(payload.get("scenario_name") or payload.get("title") or "AI流程接口场景").strip()
    cases = payload.get("cases") or payload.get("steps") or []
    if not isinstance(cases, list):
        cases = []
    normalized = [normalize_ai_case(item if isinstance(item, dict) else {}, idx) for idx, item in enumerate(cases[:AI_PREVIEW_LIMIT], start=1)]
    return {
        "scenario_name": scenario,
        "summary": payload.get("summary") or "AI 已根据接口链路生成流程性接口场景",
        "warnings": payload.get("warnings") or [],
        "cases": normalized,
    }


def build_ai_prompt(context):
    def compact_prompt_value(value, limit=380):
        if value in (None, "", [], {}):
            return ""
        try:
            text_value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text_value = str(value)
        text_value = re.sub(r"\s+", " ", text_value).strip()
        if len(text_value) <= limit:
            return text_value
        return f"{text_value[:limit]}...<truncated {len(text_value) - limit} chars>"

    lightweight_endpoints = []
    for item in context.get("endpoints") or []:
        req_params = item.get("request_params")
        req_param_keys = []
        if isinstance(req_params, dict):
            req_param_keys = list(req_params.keys())[:12]
        response_body = item.get("response_body")
        response_keys = []
        if isinstance(response_body, dict):
            response_keys = list(response_body.keys())[:12]
        sample_request = {}
        sample_query = item.get("sample_request_query")
        sample_body = item.get("sample_request_body")
        if sample_query not in (None, "", {}, []):
            sample_request["query"] = sample_query
        if sample_body not in (None, "", {}, []):
            sample_request["body"] = sample_body
        lightweight_endpoints.append({
            "name": item.get("name"),
            "method": item.get("method"),
            "path": item.get("path"),
            "full_url": item.get("full_url"),
            "request_header_hint": item.get("request_headers"),
            "request_param_keys": req_param_keys,
            "request_schema": compact_prompt_value(req_params, 380),
            "response_keys": response_keys,
            "response_schema": compact_prompt_value(response_body, 320),
            "sample_available": item.get("sample_available"),
            "sample_recorded_at": item.get("sample_recorded_at"),
            "sample_url": item.get("sample_url") or item.get("full_url") or item.get("path"),
            "sample_request": compact_prompt_value(sample_request, 520),
            "sample_response": compact_prompt_value(item.get("sample_response_body"), 520),
            "api_endpoint_id": item.get("api_endpoint_id"),
            "api_version_id": item.get("api_version_id"),
            "api_version_no": item.get("api_version_no"),
        })
    headers_from_goal = _extract_headers_from_goal(context.get("business_goal") or "")
    lightweight_context = {
        "project_id": context.get("project_id"),
        "directory_id": context.get("directory_id"),
        "service": context.get("service"),
        "business_goal": context.get("business_goal"),
        "global_headers_from_goal": headers_from_goal,
        "generate_style": context.get("generate_style"),
        "include_negative": context.get("include_negative"),
        "include_asserts": context.get("include_asserts"),
        "include_extractors": context.get("include_extractors"),
        "endpoints": lightweight_endpoints,
    }
    return (
        "任务: 基于给定接口链路生成可直接保存到Argux的流程接口用例。\n"
        "只输出1个JSON对象。禁止输出思考过程、解释、分析、Markdown、代码块、前后缀文本。\n"
        "不要复述上下文，不要总结规则，直接给结果。\n"
        "固定输出: {\"scenario_name\":\"\",\"summary\":\"\",\"warnings\":[],\"cases\":[...]}\n"
        "case固定字段: "
        "{\"name\":\"\",\"priority\":\"P1\",\"method\":\"GET|POST|PUT|DELETE\",\"url\":\"\",\"headers\":{},\"body_type\":0,\"body\":{},\"asserts\":[],\"out_parameters\":[],\"pre_steps\":[],\"tags\":[\"AI生成\",\"流程场景\"],\"reason\":\"\"}\n"
        "硬性约束:\n"
        "1. method大写。GET/DELETE -> body_type=0。POST/PUT/PATCH -> body_type=1。\n"
        "2. headers必须是对象。business_goal或global_headers_from_goal中的请求头应用到每个步骤。\n"
        "3. assert_type只允许: equal, not_equal, contain, not_contain, in, not_in, length_eq, length_gt, length_ge, length_le, length_lt, json_equal, text_in, text_not_in。\n"
        "4. 禁止输出: eq, contains, not_null, not_empty, exists。校验非空统一用not_equal + expected=\"None\"。\n"
        "5. include_asserts=false -> asserts=[]。否则每步至少保留3条断言：\n"
        "   A) 通用业务码断言: $.code == 0；\n"
        "   B) 结果有效性断言: $.data not_equal None 或 $.success == true（按真实响应结构选择）；\n"
        "   C) 业务语义断言: 根据接口动作补充一条（新增/更新断言msg包含成功；列表断言条数length_gt 0或字段存在；删除断言msg包含删除成功或结果状态为true）。\n"
        "   若真实响应结构不包含对应字段，可用等价字段替代，但禁止只输出code单断言。\n"
        "6. include_extractors=false -> out_parameters=[]；include_extractors=true 时，只要后续步骤会用到动态数据，前序步骤必须生成 out_parameters。\n"
        "7. out_parameters格式固定:{\"name\":\"变量名\",\"expression\":\"解析表达式\",\"source\":1,\"match_index\":\"0\"}。name只能用英文/数字/下划线，如 entityId、entityName、latestId。expression禁止以$或$.开头。\n"
        "8. 表达式规则：JSON解析支持 data.itemlist[0]、data.itemlist[random]、data.itemlist[0-99].id、data.itemlist[all].id；正则表达式可指定匹配项，常用字符范围示例 [a-zA-Z]、[0-9]、[一-龥]、[一-龥a-zA-Z0-9]，长度示例 {2,4}/{2}；尽量不要使用*，避免匹配到0个字符。\n"
        "9. 同一流程内变量引用统一使用${变量名}。后续请求的url/query/body/header必须真实引用前序out_parameters的变量。禁止输出${【caseX】变量}写法。\n"
        "10. 变量引入只允许以下形式：固定变量${response}/${status_code}、接口变量${变量名}、系统变量${【snowflake_id】}/${【phone】}/${【rand_4】}/${【cur_ymdhms】}。\n"
        "11. pre_steps必须写清依赖来源，例如: 依赖步骤1提取${entityName}查询列表、依赖步骤2提取${entityId}删除数据。真正依赖关系靠out_parameters和${变量名}。\n"
        "12. 如果business_goal包含 新增/创建/保存 + 列表/查询 + 删除/移除，必须按以下链路生成：新增步骤提取可用于查询的名称/编码变量；列表/查询步骤使用该变量过滤最新数据，并提取删除需要的id；删除步骤必须在url或body中使用${id变量}。\n"
        "13. 如果新增接口响应可能直接返回id，也可以在新增步骤提取 createdId；但删除前仍应优先通过列表/详情再次提取 latestId，避免删除错数据。\n"
        "14. 生成删除/编辑/详情步骤时，禁止写死id、禁止使用示例id、禁止留空id，必须使用${变量名}。\n"
        "15. include_negative=false时只生成主流程。cases数量 <= 已选接口数 + 1。\n"
        "16. 优先按business_goal生成CRUD主链路，例如 新增 -> 列表/查询提取id -> 编辑/删除。\n"
        "17. 当接口上下文中存在 sample_url、sample_request、sample_response 时，必须优先使用这些真实样本数据生成步骤；接口定义(request_schema/response_schema)只作为兜底。\n"
        "18. sample_request 是最重要的真实传参依据。若 sample_request 中已有 query/body 字段，优先直接复用其字段名、层级和真实值风格，不要重新臆造参数结构。\n"
        "19. sample_response 是最重要的真实提取和断言依据。优先从 sample_response 中寻找可提取字段，如 id、code、name、token、list[0].id，并基于真实响应结构生成断言和 out_parameters。\n"
        "   当 sample_response 存在时，断言必须优先引用 sample_response 中真实存在的字段路径，避免使用响应里不存在的路径。\n"
        "20. sample_url 是最重要的真实请求地址依据。优先使用 sample_url 对应的真实路径风格生成 url；若 sample_url 带 query 参数，可将其转入 url 或 body 中，但必须与 sample_request 保持一致。\n"
        "强制示例: 新增数据 body.name=Auto_${timestamp} 且 out_parameters=[{name:'entityName',expression:'$.data.name',source:1}]；列表url或body使用${entityName}查询，并 out_parameters=[{name:'entityId',expression:'$.data.list[0].id',source:1}]；删除url=/delete?id=${entityId}。\n"
        f"上下文:\n{json.dumps(lightweight_context, ensure_ascii=False)}"
    )


def _extract_headers_from_goal(goal_text: str):
    ans = {}
    text_value = str(goal_text or "")
    if not text_value:
        return ans
    for pair in re.split(r"[,，、\n\r]+", text_value):
        matched = re.match(r"\s*([A-Za-z0-9\-]+)\s*=\s*(.+?)\s*$", pair)
        if not matched:
            continue
        key, value = matched.groups()
        k = str(key or "").strip()
        v = str(value or "").strip()
        if k and v:
            ans[k] = v
    return ans


def _guess_body_from_params(params):
    if isinstance(params, dict):
        if "body" in params and isinstance(params.get("body"), (dict, list)):
            return params.get("body")
        if "properties" in params and isinstance(params.get("properties"), dict):
            return {k: "" for k in list(params.get("properties").keys())[:8]}
        if "schema" in params and isinstance(params.get("schema"), dict):
            schema = params.get("schema")
            if isinstance(schema.get("properties"), dict):
                return {k: "" for k in list(schema.get("properties").keys())[:8]}
    return {}


def build_fallback_flow_payload(context, reason=""):
    endpoints = context.get("endpoints") or []
    headers_from_goal = _extract_headers_from_goal(context.get("business_goal") or "")
    cases = []
    for idx, endpoint in enumerate(endpoints[:AI_PREVIEW_LIMIT], start=1):
        method = str(endpoint.get("method") or "GET").upper()
        path = str(endpoint.get("path") or endpoint.get("full_url") or "").strip()
        body = _guess_body_from_params(endpoint.get("request_params"))
        body_type = 1 if method in ("POST", "PUT", "PATCH") else 0
        out_parameters = []
        if idx == 1:
            out_parameters = [{"name": "entityId", "expression": "$.data.id", "source": 1}]
        pre_steps = []
        if idx > 1:
            pre_steps = [f"依赖前一步成功执行，步骤{idx - 1}返回结果用于当前步骤校验"]
        cases.append({
            "name": f"步骤{idx}-{endpoint.get('name') or method + ' ' + path}",
            "priority": "P1",
            "method": method,
            "url": path,
            "headers": headers_from_goal,
            "body_type": body_type,
            "body": body,
            "asserts": [
                {"name": "业务码成功", "assert_type": "equal", "actually": "$.code", "expected": "0"},
                {"name": "响应结果存在", "assert_type": "not_equal", "actually": "$.data", "expected": "None"},
            ],
            "out_parameters": out_parameters,
            "pre_steps": pre_steps,
            "tags": ["AI生成", "流程场景", "超时兜底"],
            "reason": "AI模型超时后自动按接口顺序生成，可直接微调后保存",
        })
    return {
        "scenario_name": "流程场景-超时兜底预览",
        "summary": "AI模型超时，已使用本地规则生成可编辑的流程场景预览",
        "warnings": [reason] if reason else [],
        "cases": cases,
    }


def call_kimi_for_flow_cases(context, ai_config=None):
    ai_config = ai_config or {}
    api_key = ai_config.get("api_key")
    model = ai_config.get("model") or Config.KIMI_MODEL
    provider = ai_config.get("provider") or "kimi"
    request_model = normalize_request_model_name(model, provider)
    wire_api = resolve_ai_wire_api(ai_config, provider, request_model)
    base_url = normalize_ai_base_url(ai_config.get("base_url") or Config.KIMI_BASE_URL, wire_api, provider, model)
    request_url = build_ai_request_url(ai_config.get("base_url") or Config.KIMI_BASE_URL, wire_api, provider, model)
    if not api_key:
        raise ValueError("未配置 AI API Key")
    prompt = build_ai_prompt(context)
    endpoint_count = len(context.get("endpoints") or [])
    prompt_chars = len(prompt)
    logger.info(
        f"flow-preview ai start provider={provider}, model={model}, request_model={request_model}, wire_api={wire_api}, base_url={base_url}, request_url={request_url}, "
        f"endpoint_count={endpoint_count}, prompt_chars={prompt_chars}, timeout={AI_KIMI_TIMEOUT}s"
    )
    try:
        context_text = json.dumps(context, ensure_ascii=False)
    except Exception:
        context_text = str(context)
    logger.info(f"flow-preview ai context provider={provider}, model={model}, context={context_text}")
    logger.info(f"flow-preview ai prompt begin provider={provider}, model={model}")
    chunk_size = 1800
    for idx in range(0, len(prompt), chunk_size):
        logger.info(f"flow-preview ai prompt chunk[{idx // chunk_size + 1}] provider={provider}, model={model}, text={prompt[idx:idx + chunk_size]}")
    logger.info(f"flow-preview ai prompt end provider={provider}, model={model}")
    system_prompt = "你只输出严格 JSON，用于接口自动化测试用例生成。"
    if wire_api == "responses":
        request_payload = build_responses_request_payload(request_model, system_prompt, prompt, temperature=1, max_output_tokens=4096)
    else:
        request_payload = {
            "model": request_model,
            "temperature": 1,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
    request_headers = build_ai_headers(api_key, model)
    api_key_preview = build_api_key_preview(api_key)
    logger.info(f"flow-preview ai resolved provider={provider}, model={model}, request_model={request_model}, wire_api={wire_api}, base_url={base_url}, request_url={request_url}, api_key={api_key_preview}")
    started_at = time.perf_counter()
    try:
        response = requests.post(
            request_url,
            headers=request_headers,
            json=request_payload,
            timeout=(10, AI_KIMI_TIMEOUT),
        )
    except requests.Timeout as exc:
        elapsed = round(time.perf_counter() - started_at, 2)
        logger.warning(f"flow-preview ai timeout provider={provider}, model={model}, wire_api={wire_api}, elapsed={elapsed}s, endpoint_count={endpoint_count}, prompt_chars={prompt_chars}")
        raise ValueError(f"AI模型({provider}/{model})请求超时({elapsed}s)，已压缩接口上下文仍未返回，请减少接口数量或稍后重试") from exc
    except requests.RequestException as exc:
        elapsed = round(time.perf_counter() - started_at, 2)
        logger.error(f"flow-preview ai request error provider={provider}, model={model}, wire_api={wire_api}, elapsed={elapsed}s, error={exc}")
        raise ValueError(f"AI模型({provider}/{model})请求失败({elapsed}s): {exc}") from exc
    elapsed = round(time.perf_counter() - started_at, 2)
    request_id = response.headers.get("x-request-id") or response.headers.get("request-id") or ""
    logger.info(f"flow-preview ai done provider={provider}, model={model}, wire_api={wire_api}, status={response.status_code}, elapsed={elapsed}s, request_id={request_id}")
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise ValueError(f"AI模型({provider}/{model})调用失败({elapsed}s): {detail}")
    try:
        payload = response.json()
    except Exception as exc:
        body_preview = preview_text(response.text, 2000)
        logger.error(f"flow-preview ai invalid-json-response provider={provider}, model={model}, wire_api={wire_api}, request_id={request_id}, body_preview={body_preview}")
        raise ValueError(build_non_json_response_error(provider, model, wire_api, request_url, response, body_preview)) from exc
    logger.info(f"flow-preview ai raw-payload-preview provider={provider}, model={model}, wire_api={wire_api}, payload={preview_text(json.dumps(payload, ensure_ascii=False), 2000)}")
    if wire_api == "responses":
        content = extract_responses_output_text(payload)
    else:
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError(f"AI模型({provider}/{model})未返回可用结果")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join([item.get("text", "") for item in content if isinstance(item, dict)])
        if isinstance(content, str):
            content = content.strip()
        if not content:
            finish_reason = choices[0].get("finish_reason")
            reasoning_content = message.get("reasoning_content")
            refusal = message.get("refusal")
            logger.warning(
                f"flow-preview ai empty-content provider={provider}, model={model}, "
                f"request_id={request_id}, finish_reason={finish_reason}, "
                f"reasoning_preview={preview_text(reasoning_content, 1000)}, "
                f"refusal_preview={preview_text(refusal, 1000)}"
            )
            raise ValueError(f"AI模型({provider}/{model})返回空内容")
        if not isinstance(content, str):
            raise ValueError(f"AI模型({provider}/{model})返回内容格式不支持")
    logger.info(
        f"flow-preview ai content provider={provider}, model={model}, wire_api={wire_api}, "
        f"content={content}"
    )
    return extract_json_object(content)


def case_text_has_var(value):
    try:
        text_value = json.dumps(value, ensure_ascii=False)
    except Exception:
        text_value = str(value)
    return "${" in text_value


def append_unique_out_parameter(case_item, name, expression):
    out_parameters = case_item.get("out_parameters")
    if not isinstance(out_parameters, list):
        out_parameters = []
    if not any(str(item.get("name") or "") == name for item in out_parameters if isinstance(item, dict)):
        out_parameters.append({"name": name, "expression": expression, "source": 1, "match_index": "0"})
    case_item["out_parameters"] = out_parameters


def append_pre_step(case_item, text_value):
    pre_steps = case_item.get("pre_steps")
    if not isinstance(pre_steps, list):
        pre_steps = []
    if text_value not in pre_steps:
        pre_steps.append(text_value)
    case_item["pre_steps"] = pre_steps


def normalize_extractor_expression(expr):
    text_value = str(expr or "").strip()
    if not text_value:
        return text_value
    if text_value.startswith("$."):
        return text_value[2:]
    if text_value.startswith("$"):
        return text_value[1:]
    return text_value


def sanitize_case_extractors(payload):
    if not isinstance(payload, dict):
        return payload
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return payload
    for case_item in cases:
        if not isinstance(case_item, dict):
            continue
        out_parameters = case_item.get("out_parameters")
        if not isinstance(out_parameters, list):
            continue
        for out_item in out_parameters:
            if not isinstance(out_item, dict):
                continue
            out_item["expression"] = normalize_extractor_expression(out_item.get("expression"))
    return payload


def apply_simple_success_assert_rule(case_item, matched_endpoint):
    if not isinstance(case_item, dict) or not isinstance(matched_endpoint, dict):
        return
    sample_response = matched_endpoint.get("sample_response_body_raw")
    if not isinstance(sample_response, dict):
        return
    expected_response = {"code": 0, "msg": None, "data": "success", "pageFlag": None}
    if sample_response != expected_response:
        return
    case_item["asserts"] = [{
        "name": "校验内容为全部响应",
        "assert_type": "equal",
        "actually": "${response}",
        "expected": safe_json_dumps(expected_response),
    }]


def ensure_flow_dependencies(payload, context):
    cases = payload.get("cases") if isinstance(payload, dict) else []
    if not isinstance(cases, list) or len(cases) < 2 or not context.get("include_extractors", True):
        return payload
    goal_text = str(context.get("business_goal") or "")
    flow_text = goal_text + " " + " ".join(str(item.get("name") or "") for item in cases if isinstance(item, dict))
    has_create = any(key in flow_text for key in ("新增", "创建", "保存", "添加", "create", "add", "save"))
    has_query = any(key in flow_text for key in ("列表", "查询", "详情", "最新", "list", "query", "detail"))
    has_delete = any(key in flow_text for key in ("删除", "移除", "delete", "remove"))
    if not (has_create and (has_query or has_delete)):
        return payload

    create_case = cases[0]
    append_unique_out_parameter(create_case, "entityName", "$.data.name")
    create_body = create_case.get("body")
    if isinstance(create_body, dict):
        if not any(case_text_has_var(v) for v in create_body.values()):
            for key in ("name", "dimName", "title", "code", "key"):
                if key in create_body:
                    create_body[key] = create_body.get(key) or "Auto_${timestamp}"
                    break

    if len(cases) >= 2:
        query_case = cases[1]
        append_pre_step(query_case, "依赖步骤1提取${entityName}作为查询条件")
        if not case_text_has_var({"url": query_case.get("url"), "body": query_case.get("body")}):
            method = str(query_case.get("method") or query_case.get("request_method") or "GET").upper()
            if method == "GET":
                url = str(query_case.get("url") or "")
                sep = "&" if "?" in url else "?"
                query_case["url"] = f"{url}{sep}name=${{entityName}}" if url else "?name=${entityName}"
            else:
                body = query_case.get("body") if isinstance(query_case.get("body"), dict) else {}
                body.setdefault("name", "${entityName}")
                query_case["body"] = body
        append_unique_out_parameter(query_case, "entityId", "$.data.list[0].id")

    if len(cases) >= 3:
        delete_case = cases[-1]
        append_pre_step(delete_case, "依赖列表/查询步骤提取${entityId}作为删除目标")
        if not case_text_has_var({"url": delete_case.get("url"), "body": delete_case.get("body")}):
            method = str(delete_case.get("method") or delete_case.get("request_method") or "DELETE").upper()
            if method == "DELETE" or not isinstance(delete_case.get("body"), dict):
                url = str(delete_case.get("url") or "")
                sep = "&" if "?" in url else "?"
                delete_case["url"] = f"{url}{sep}id=${{entityId}}" if url else "?id=${entityId}"
            else:
                body = delete_case.get("body") if isinstance(delete_case.get("body"), dict) else {}
                body.setdefault("id", "${entityId}")
                delete_case["body"] = body
    return payload


async def ensure_ai_interface_columns(session):
    for sql in [
        "ALTER TABLE argus_api_service ADD COLUMN developer VARCHAR(128) NULL COMMENT '开发人员'",
        "ALTER TABLE argus_api_service ADD COLUMN tester VARCHAR(128) NULL COMMENT '测试人员'",
        "ALTER TABLE argus_api_endpoint ADD COLUMN module_name VARCHAR(128) NOT NULL DEFAULT '默认模块' COMMENT '功能模块'",
        "ALTER TABLE argus_api_endpoint ADD COLUMN endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT '接口状态'",
        "ALTER TABLE argus_api_endpoint ADD COLUMN request_headers LONGTEXT NULL COMMENT '请求头'",
        "ALTER TABLE argus_api_endpoint ADD COLUMN current_version_id INT NOT NULL DEFAULT 0 COMMENT '当前版本ID'",
        "ALTER TABLE argus_api_endpoint ADD COLUMN current_version_no VARCHAR(32) NOT NULL DEFAULT 'v1' COMMENT '当前版本号'",
        "ALTER TABLE argus_api_endpoint_version ADD COLUMN module_name VARCHAR(128) NOT NULL DEFAULT '默认模块' COMMENT '功能模块'",
        "ALTER TABLE argus_api_endpoint_version ADD COLUMN endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT '接口状态'",
        "ALTER TABLE argus_api_endpoint_version ADD COLUMN request_headers LONGTEXT NULL COMMENT '请求头'",
    ]:
        try:
            await session.execute(text(sql))
        except Exception:
            pass
    await session.commit()


async def build_endpoint_context(session, form):
    await ensure_ai_interface_columns(session)
    service_id = int(form.get("service_id") or 0)
    version_ids = form.get("api_version_ids") or form.get("version_ids") or []
    endpoint_ids = form.get("endpoint_ids") or []
    service = None
    if service_id:
        service = (await session.execute(select(ArgusApiService).where(ArgusApiService.id == service_id))).scalars().first()
    versions = []
    if version_ids:
        result = await session.execute(select(ArgusApiEndpointVersion).where(ArgusApiEndpointVersion.id.in_(version_ids)))
        version_map = {item.id: item for item in result.scalars().all()}
        versions = [version_map[item] for item in version_ids if item in version_map]
    elif endpoint_ids:
        result = await session.execute(select(ArgusApiEndpoint).where(ArgusApiEndpoint.id.in_(endpoint_ids)))
        endpoint_map = {item.id: item for item in result.scalars().all()}
        endpoints = [endpoint_map[item] for item in endpoint_ids if item in endpoint_map]
        version_id_list = [item.current_version_id for item in endpoints if item.current_version_id]
        if version_id_list:
            result = await session.execute(select(ArgusApiEndpointVersion).where(ArgusApiEndpointVersion.id.in_(version_id_list)))
            version_map = {item.id: item for item in result.scalars().all()}
            versions = [version_map[item] for item in version_id_list if item in version_map]
    endpoint_id_list = [item.endpoint_id for item in versions if item.endpoint_id]
    sample_map = {}
    if endpoint_id_list:
        sample_result = await session.execute(
            select(ArgusApiEndpointSample).where(
                ArgusApiEndpointSample.endpoint_id.in_(endpoint_id_list),
                ArgusApiEndpointSample.deleted_at == 0,
            )
        )
        sample_map = {item.endpoint_id: item for item in sample_result.scalars().all()}
    endpoint_items = []
    warnings = []
    for version in versions:
        sample = sample_map.get(version.endpoint_id)
        if sample is None:
            warnings.append(f"{version.method} {version.path} 缺少录制实例，AI将回退使用接口定义生成")
        endpoint_items.append({
            "api_endpoint_id": version.endpoint_id,
            "api_version_id": version.id,
            "api_version_no": version.version_no,
            "name": version.name,
            "method": version.method,
            "module_name": version.module_name,
            "path": version.path,
            "full_url": version.full_url,
            "request_headers": compact_ai_value(safe_json_loads(version.request_headers, []), AI_CONTEXT_HEADER_LIMIT),
            "request_params": compact_ai_value(safe_json_loads(version.request_params, {}), AI_CONTEXT_PARAM_LIMIT),
            "response_body": compact_ai_value(safe_json_loads(version.response_body, {}), AI_CONTEXT_RESPONSE_LIMIT),
            "sample_available": 1 if sample else 0,
            "sample_recorded_at": sample.recorded_at if sample else None,
            "sample_status_code": sample.status_code if sample else None,
            "sample_url": sample.request_url if sample else "",
            "sample_request_headers_raw": safe_json_loads(sample.request_headers, {}) if sample else {},
            "sample_request_headers": compact_ai_value(safe_json_loads(sample.request_headers, {}), AI_CONTEXT_HEADER_LIMIT) if sample else {},
            "sample_request_query_raw": safe_json_loads(sample.request_query, {}) if sample else {},
            "sample_request_query": compact_ai_value(safe_json_loads(sample.request_query, {}), AI_CONTEXT_PARAM_LIMIT) if sample else {},
            "sample_request_body_raw": safe_json_loads(sample.request_body, sample.request_body or "") if sample else "",
            "sample_request_body": compact_ai_value(safe_json_loads(sample.request_body, sample.request_body or ""), AI_CONTEXT_PARAM_LIMIT) if sample else "",
            "sample_response_headers": compact_ai_value(safe_json_loads(sample.response_headers, {}), AI_CONTEXT_HEADER_LIMIT) if sample else {},
            "sample_response_body_raw": safe_json_loads(sample.response_body, sample.response_body or "") if sample else "",
            "sample_response_body": compact_ai_value(safe_json_loads(sample.response_body, sample.response_body or ""), AI_CONTEXT_RESPONSE_LIMIT) if sample else "",
        })
    return {
        "project_id": form.get("project_id"),
        "directory_id": form.get("directory_id"),
        "service": {
            "id": service.id if service else service_id,
            "name": service.name if service else "",
            "base_url": service.base_url if service else "",
        },
        "endpoints": endpoint_items,
        "business_goal": form.get("business_goal") or "",
        "generate_style": form.get("generate_style") or "standard",
        "include_negative": bool(form.get("include_negative", True)),
        "include_asserts": bool(form.get("include_asserts", True)),
        "include_extractors": bool(form.get("include_extractors", True)),
        "warnings": warnings,
    }


def to_assert_form(raw):
    raw_assert_type = str(raw.get("assert_type") or raw.get("type") or "equal")
    assert_type_map = {
        "eq": "equal",
        "equals": "equal",
        "==": "equal",
        "neq": "not_equal",
        "!=": "not_equal",
        "contains": "contain",
        "not_contains": "not_contain",
        "not_null": "not_equal",
        "not_empty": "not_equal",
        "exists": "not_equal",
    }
    assert_type = assert_type_map.get(raw_assert_type, raw_assert_type)
    expected = raw.get("expected")
    if expected in (None, ""):
        expected = "None" if raw_assert_type in ("not_null", "not_empty", "exists") else "0"
    return TestCaseAssertsForm(
        name=str(raw.get("name") or "AI断言")[:64],
        assert_type=assert_type,
        actually=str(raw.get("actually") or raw.get("expression") or "$.code"),
        expected=str(expected),
    )


def to_out_parameter_form(raw):
    return ArgusTestCaseOutParametersForm(
        name=str(raw.get("name") or "ai_var")[:64],
        expression=str(raw.get("expression") or raw.get("actually") or ""),
        match_index=str(raw.get("match_index") or "0"),
        source=int(raw.get("source") or 1),
    )


def to_testcase_info(raw_case, directory_id):
    tag_list = raw_case.get("tags") or ["AI生成", "流程场景"]
    tag_text = ",".join(tag_list) if isinstance(tag_list, list) else str(tag_list)
    body = raw_case.get("body")
    headers = raw_case.get("request_headers") or raw_case.get("headers") or {}
    if isinstance(headers, str):
        try:
            headers = json.loads(headers)
        except Exception:
            headers = {}
    case = TestCaseForm(
        priority=str(raw_case.get("priority") or "P1"),
        url=str(raw_case.get("url") or ""),
        name=str(raw_case.get("name") or "AI流程接口用例")[:64],
        case_type=0,
        base_path=None,
        tag=tag_text,
        body=safe_json_dumps(body) if body not in (None, "") else None,
        body_type=int(raw_case.get("body_type") or (1 if body not in (None, "", {}, []) else 0)),
        request_headers=safe_json_dumps(headers) if headers not in (None, "") else None,
        request_method=str(raw_case.get("request_method") or raw_case.get("method") or "GET").upper(),
        status=int(raw_case.get("status") or 3),
        directory_id=int(directory_id),
        request_type=int(raw_case.get("request_type") or 1),
        api_service_id=int(raw_case.get("api_service_id") or 0),
        api_endpoint_id=int(raw_case.get("api_endpoint_id") or 0),
        api_version_id=int(raw_case.get("api_version_id") or 0),
        api_version_no=raw_case.get("api_version_no"),
        api_bind_mode="pinned",
        api_pending_update=0,
    )
    asserts = [to_assert_form(item) for item in raw_case.get("asserts") or [] if isinstance(item, dict)]
    out_parameters = [to_out_parameter_form(item) for item in raw_case.get("out_parameters") or [] if isinstance(item, dict) and item.get("name")]
    return TestCaseInfo(case=case, asserts=asserts, data=[], constructor=[], out_parameters=out_parameters)


@router.post("/ai-generate/flow-preview", summary="AI生成流程接口场景预览")
async def ai_generate_flow_preview(form: dict, user_info=Depends(Permission()), session=Depends(get_session)):
    try:
        if not form.get("directory_id"):
            return ArgusResponse.failed("请先选择用例目录")
        context = await build_endpoint_context(session, form)
        if not context.get("endpoints"):
            return ArgusResponse.failed("请至少选择一个接口版本")
        ai_config = await GConfigDao.get_active_ai_model_config(form.get("ai_model_id"))
        try:
            payload = call_kimi_for_flow_cases(context, ai_config)
        except ValueError as kimi_error:
            err_msg = str(kimi_error)
            fallback_keywords = [
                "AI模型",
                "返回空内容",
                "AI 未返回内容",
                "AI 返回结果不是有效 JSON",
                "未返回可用结果",
                "返回内容格式不支持",
                "响应不是有效 JSON",
            ]
            if any(keyword in err_msg for keyword in fallback_keywords):
                payload = build_fallback_flow_payload(context, err_msg)
            else:
                raise
        normalized = normalize_ai_payload(payload)
        normalized = sanitize_case_extractors(normalized)
        normalized = ensure_flow_dependencies(normalized, context)
        normalized["warnings"] = list(dict.fromkeys((normalized.get("warnings") or []) + (context.get("warnings") or [])))
        for index, item in enumerate(normalized["cases"]):
            item["api_service_id"] = context["service"].get("id") or 0
            matched = next((v for v in context["endpoints"] if str(v.get("path")) == str(item.get("url")) or str(v.get("full_url")) == str(item.get("url"))), None)
            if matched is None and index < len(context["endpoints"]):
                matched = context["endpoints"][index]
            if matched:
                item["api_endpoint_id"] = matched.get("api_endpoint_id") or 0
                item["api_version_id"] = matched.get("api_version_id") or 0
                item["api_version_no"] = matched.get("api_version_no")
                apply_simple_success_assert_rule(item, matched)
                if not item.get("request_headers") and matched.get("sample_request_headers_raw"):
                    item["request_headers"] = matched.get("sample_request_headers_raw")
                if item.get("body") in (None, "", {}, []) and matched.get("sample_request_body_raw") not in (None, ""):
                    item["body"] = matched.get("sample_request_body_raw")
                    item["body_type"] = 1 if str(item.get("request_method") or "").upper() in ("POST", "PUT", "PATCH") else 0
        log_payload = build_ai_flow_preview_operation_log(form, context, normalized)
        session.add(ArgusOperationLog(
            user_info["id"],
            OperationType.EXECUTE,
            log_payload["title"],
            log_payload["tag"],
            log_payload["description"],
            int(form.get("directory_id") or 0),
        ))
        await session.commit()
        return ArgusResponse.success(normalized)
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/ai-generate/flow-save", summary="保存AI生成流程接口场景")
async def ai_generate_flow_save(form: dict, user_info=Depends(Permission()), session=Depends(get_session)):
    try:
        directory_id = int(form.get("directory_id") or 0)
        cases = form.get("cases") or []
        if not directory_id:
            return ArgusResponse.failed("请先选择用例目录")
        if not cases:
            return ArgusResponse.failed("请选择需要保存的用例")
        saved_ids = []
        async with session.begin():
            for raw_case in cases:
                info = to_testcase_info(raw_case, directory_id)
                case_id = await TestCaseDao.insert_test_case(session, info, user_info['id'])
                saved_ids.append(case_id)
        return ArgusResponse.success({"saved_ids": saved_ids, "count": len(saved_ids)})
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/list")
async def list_testcase(directory_id: int = None, name: str = "", url: str = "", create_user: str = ''):
    data = await TestCaseDao.list_test_case(directory_id, name, url, create_user)
    return ArgusResponse.success(data)


@router.post("/insert")
async def insert_testcase(data: TestCaseForm, user_info=Depends(Permission())):
    try:
        record = await TestCaseDao.query_record(name=data.name, directory_id=data.directory_id)
        if record is not None:
            return ArgusResponse.failed("用例已存在")
        model = TestCase(**data.dict(), create_user=user_info['id'])
        model = await TestCaseDao.insert(model=model, log=True)
        return ArgusResponse.success(model.id)
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/create", summary="创建接口测试用例")
async def create_testcase(data: TestCaseInfo, user_info=Depends(Permission()), session=Depends(get_session)):
    async with session.begin():
        await TestCaseDao.insert_test_case(session, data, user_info['id'])
    return ArgusResponse.success()


@router.post("/update")
async def update_testcase(form: TestCaseForm, user_info=Depends(Permission())):
    try:
        data = await TestCaseDao.update_test_case(form, user_info['id'])
        result = await ArgusTestCaseOutParametersDao.update_many(form.id, form.out_parameters, user_info['id'])
        return ArgusResponse.success(dict(case_info=data, out_parameters=result))
    except Exception as e:
        return ArgusResponse.failed(e)


@router.delete("/delete", description="删除测试用例")
async def delete_testcase(id_list: List[int], user_info=Depends(Permission()), session=Depends(get_session)):
    try:
        async with session.begin():
            await TestCaseDao.delete_records(session, user_info['id'], id_list, log=True)
            await TestCaseAssertsDao.delete_records(session, user_info['id'], id_list, column="case_id", log=True)
            await ArgusTestcaseDataDao.delete_records(session, user_info['id'], id_list, column="case_id", log=True)
            return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/query")
async def query_testcase(caseId: int, _=Depends(Permission())):
    try:
        data = await TestCaseDao.query_test_case(caseId)
        return ArgusResponse.success(ArgusResponse.dict_model_to_dict(data))
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/asserts/insert")
async def insert_testcase_asserts(data: TestCaseAssertsForm, user_info=Depends(Permission())):
    try:
        new_assert = await TestCaseAssertsDao.insert_test_case_asserts(data, user_id=user_info["id"])
        return ArgusResponse.success(new_assert)
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/asserts/update")
async def update_testcase_asserts(data: TestCaseAssertsForm, user_info=Depends(Permission())):
    try:
        updated = await TestCaseAssertsDao.update_test_case_asserts(data, user_id=user_info["id"])
        return ArgusResponse.success(updated)
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/asserts/delete")
async def delete_testcase_asserts(id: int, user_info=Depends(Permission())):
    await TestCaseAssertsDao.delete_test_case_asserts(id, user_id=user_info["id"])
    return ArgusResponse.success()


@router.post("/constructor/insert")
async def insert_constructor(data: ConstructorForm, user_info=Depends(Permission())):
    await ConstructorDao.insert_constructor(data, user_id=user_info["id"])
    return ArgusResponse.success()


@router.post("/constructor/update")
async def update_constructor(data: ConstructorForm, user_info=Depends(Permission())):
    await ConstructorDao.update_constructor(data, user_id=user_info["id"])
    return ArgusResponse.success()


@router.get("/constructor/delete")
async def delete_constructor(id: int, user_info=Depends(Permission())):
    await ConstructorDao.delete_constructor(id, user_id=user_info["id"])
    return ArgusResponse.success()


@router.post("/constructor/order")
async def update_constructor_index(data: List[ConstructorIndex], user_info=Depends(Permission())):
    await ConstructorDao.update_constructor_index(data)
    return ArgusResponse.success()


@router.get("/constructor/tree")
async def get_constructor_tree(suffix: bool, name: str = "", user_info=Depends(Permission())):
    result = await ConstructorDao.get_constructor_tree(name, suffix)
    return ArgusResponse.success(result)


@router.get("/constructor")
async def get_constructor(id: int, user_info=Depends(Permission())):
    result = await ConstructorDao.get_constructor_data(id)
    return ArgusResponse.success(result)


@router.get("/constructor/list")
async def list_case_and_constructor(constructor_type: int, suffix: bool):
    ans = await ConstructorDao.get_case_and_constructor(constructor_type, suffix)
    return ArgusResponse.success(ans)


def _apply_report_list_style_status(report):
    if report is None:
        return None
    try:
        normalized_status = int(getattr(report, "status", 0) or 0)
    except Exception:
        normalized_status = 0
    report.display_status = normalized_status
    report.raw_status = normalized_status
    if normalized_status == 1:
        report.plan_result = "running"
        report.plan_result_text = "运行中"
        report.display_success_count = 0
        report.display_failed_count = 0
        report.display_error_count = 0
        report.display_skipped_count = 0
    elif normalized_status == 2:
        report.plan_result = "cancelled"
        report.plan_result_text = "停止"
        report.display_success_count = int(getattr(report, "success_count", 0) or 0)
        report.display_failed_count = int(getattr(report, "failed_count", 0) or 0)
        report.display_error_count = int(getattr(report, "error_count", 0) or 0)
        report.display_skipped_count = int(getattr(report, "skipped_count", 0) or 0)
    elif normalized_status == 3:
        failed_count = int(getattr(report, "failed_count", 0) or 0)
        error_count = int(getattr(report, "error_count", 0) or 0)
        report.display_success_count = int(getattr(report, "success_count", 0) or 0)
        report.display_failed_count = failed_count
        report.display_error_count = error_count
        report.display_skipped_count = int(getattr(report, "skipped_count", 0) or 0)
        if failed_count > 0 or error_count > 0:
            report.plan_result = "failed"
            report.plan_result_text = "测试失败"
        else:
            report.plan_result = "success"
            report.plan_result_text = "测试成功"
    else:
        report.plan_result = "pending"
        report.plan_result_text = "排队中"
        report.display_success_count = 0
        report.display_failed_count = 0
        report.display_error_count = 0
        report.display_skipped_count = 0
    return report


@router.get("/report")
async def query_report(id: int, status: int = Query(default=None), user_info=Depends(Permission())):
    report, case_list, plan_name = await TestReportDao.query(id)
    report = _apply_report_list_style_status(report)
    case_ids = [int(getattr(item, "case_id", 0) or 0) for item in case_list if int(getattr(item, "case_id", 0) or 0) > 0]
    pending_map = {}
    case_name_map = {}
    if case_ids:
        async with async_session() as session:
            rows = await session.execute(
                select(TestCase.id).where(
                    TestCase.id.in_(case_ids),
                    TestCase.deleted_at == 0,
                    TestCase.api_pending_update == 1,
                )
            )
            pending_map = {int(case_id): 1 for case_id, in rows.all()}
            name_rows = await session.execute(
                select(TestCase.id, TestCase.name).where(
                    TestCase.id.in_(case_ids),
                    TestCase.deleted_at == 0,
                )
            )
            case_name_map = {int(case_id): case_name for case_id, case_name in name_rows.all()}
    for item in case_list:
        # 兜底补全 case_name，避免历史报告详情返回缺失
        if not getattr(item, "case_name", None):
            item.case_name = case_name_map.get(int(getattr(item, "case_id", 0) or 0), "")
        item.api_pending_update = int(pending_map.get(int(getattr(item, "case_id", 0) or 0), 0) or 0)
    project_name = None
    if report is not None:
        report.pending_review = 1 if pending_map else 0
        if report.plan_id:
            async with async_session() as session:
                plan_row = await session.execute(
                    select(ArgusTestPlan.project_id).where(ArgusTestPlan.id == report.plan_id, ArgusTestPlan.deleted_at == 0)
                )
                plan_result = plan_row.first()
                if plan_result:
                    proj_row = await session.execute(
                        select(Project.name).where(Project.id == plan_result[0], Project.deleted_at == 0)
                    )
                    proj_result = proj_row.first()
                    project_name = proj_result[0] if proj_result else None
    if status is not None:
        case_list = [item for item in case_list if getattr(item, "status", None) == status]
    return ArgusResponse.success(dict(report=report, plan_name=plan_name, case_list=case_list, project_name=project_name))


@router.get("/report/list")
async def list_report(page: int, size: int, start_time: str, end_time: str, executor: Author = None,
                      _=Depends(Permission())):
    start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    report_list, total = await TestReportDao.list_report(page, size, start, end, executor)
    report_ids = [int(getattr(item, "id", 0) or 0) for item in report_list if int(getattr(item, "id", 0) or 0) > 0]
    pending_report_ids = set()
    if report_ids:
        async with async_session() as session:
            result = await session.execute(text(
                "SELECT DISTINCT r.report_id "
                "FROM argus_test_result r "
                "JOIN argus_testcase c ON c.id = r.case_id "
                "WHERE r.deleted_at = 0 AND c.deleted_at = 0 AND c.api_pending_update = 1 "
                f"AND r.report_id IN ({','.join([str(x) for x in report_ids])})"
            ))
            pending_report_ids = {int(report_id) for report_id, in result.all()}
    for item in report_list:
        _apply_report_list_style_status(item)
        item.pending_review = 1 if int(getattr(item, "id", 0) or 0) in pending_report_ids else 0
    return ArgusResponse.success_with_size(data=report_list, total=total)


@router.post("/report/stop")
async def stop_report(payload: dict, _=Depends(Permission())):
    report_id = int((payload or {}).get("id") or (payload or {}).get("report_id") or 0)
    if report_id <= 0:
        return ArgusResponse.failed("report_id不能为空")
    stopped = await TestReportDao.stop(report_id)
    if not stopped:
        return ArgusResponse.failed("当前报告不是运行中状态，无法停止")
    return ArgusResponse.success({"id": report_id, "status": 2, "stopped": True})


@router.get("/pending-review/list")
async def list_pending_review_cases(project_id: int = None, url: str = "", create_user: int = None,
                                    _=Depends(Permission())):
    try:
        async with async_session() as session:
            filters = [
                TestCase.deleted_at == 0,
                TestCase.api_pending_update == 1,
                ArgusTestcaseDirectory.id == TestCase.directory_id,
                ArgusTestcaseDirectory.deleted_at == 0,
            ]
            if project_id is not None:
                filters.append(ArgusTestcaseDirectory.project_id == project_id)
            if url:
                filters.append(TestCase.url.like(f"%{url}%"))
            if create_user is not None:
                filters.append(TestCase.create_user == create_user)
            rows = (
                await session.execute(
                    select(
                        TestCase.id,
                        TestCase.name,
                        TestCase.url,
                        TestCase.create_user,
                        TestCase.directory_id,
                        ArgusTestcaseDirectory.project_id,
                        ArgusApiService.name.label("service_name"),
                        ArgusApiEndpoint.name.label("endpoint_name"),
                    )
                    .select_from(TestCase)
                    .join(ArgusTestcaseDirectory, ArgusTestcaseDirectory.id == TestCase.directory_id)
                    .outerjoin(ArgusApiService, ArgusApiService.id == TestCase.api_service_id)
                    .outerjoin(ArgusApiEndpoint, ArgusApiEndpoint.id == TestCase.api_endpoint_id)
                    .where(*filters)
                    .order_by(TestCase.updated_at.desc(), TestCase.id.desc())
                )
            ).all()
            return ArgusResponse.success([
                {
                    "id": item.id,
                    "name": item.name,
                    "url": item.url,
                    "create_user": item.create_user,
                    "directory_id": item.directory_id,
                    "project_id": item.project_id,
                    "service_name": item.service_name or "-",
                    "endpoint_name": item.endpoint_name or item.name,
                }
                for item in rows
            ])
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/xmind")
async def get_xmind_data(case_id: int, user_info=Depends(Permission())):
    tree_data = await TestCaseDao.get_xmind_data(case_id)
    return ArgusResponse.success(tree_data)


@router.get("/directory")
async def get_testcase_directory(project_id: int, move: bool = False, user_info=Depends(Permission())):
    tree_data, _ = await ArgusTestcaseDirectoryDao.get_directory_tree(project_id, move=move)
    return ArgusResponse.success(tree_data)


@router.get("/tree")
async def get_directory_and_case(project_id: int, user_info=Depends(Permission())):
    try:
        tree_data, cs_map = await ArgusTestcaseDirectoryDao.get_directory_tree(project_id,
                                                                              TestCaseDao.get_test_case_by_directory_id)
        pending_map = {}
        case_ids = [int(case_id) for case_id in cs_map.keys()]
        if case_ids:
            async with async_session() as session:
                rows = await session.execute(
                    select(TestCase.id).where(
                        TestCase.id.in_(case_ids),
                        TestCase.deleted_at == 0,
                        TestCase.api_pending_update == 1,
                    )
                )
                pending_map = {int(case_id): 1 for case_id, in rows.all()}
        return ArgusResponse.success(dict(tree=tree_data, case_map=cs_map, pending_map=pending_map))
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/directory/query")
async def query_testcase_directory(directory_id: int, user_info=Depends(Permission())):
    try:
        data = await ArgusTestcaseDirectoryDao.query_directory(directory_id)
        await ProjectRoleDao.read_permission(data.project_id, user_info["id"], user_info['role'])
        return ArgusResponse.success(data)
    except AuthError:
        return ArgusResponse.forbidden()
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/directory/insert")
async def insert_testcase_directory(form: ArgusTestcaseDirectoryForm, user_info=Depends(Permission())):
    try:
        await ArgusTestcaseDirectoryDao.insert_directory(form, user_info['id'])
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/directory/update")
async def update_testcase_directory(form: ArgusTestcaseDirectoryUpdateForm, user_info=Depends(Permission())):
    try:
        await ArgusTestcaseDirectoryDao.update_directory(form, user_info['id'])
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/directory/delete")
async def delete_testcase_directory(id: int, user_info=Depends(Permission())):
    try:
        await ArgusTestcaseDirectoryDao.delete_directory(id, user_info['id'])
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/data/insert")
async def insert_testcase_data(form: ArgusTestcaseDataForm, user_info=Depends(Permission())):
    try:
        data = await ArgusTestcaseDataDao.insert_testcase_data(form, user_info['id'])
        return ArgusResponse.success(data)
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/data/update")
async def update_testcase_data(form: ArgusTestcaseDataForm, user_info=Depends(Permission())):
    try:
        data = await ArgusTestcaseDataDao.update_testcase_data(form, user_info['id'])
        return ArgusResponse.success(data)
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/data/delete")
async def delete_testcase_data(id: int, user_info=Depends(Permission())):
    try:
        await ArgusTestcaseDataDao.delete_testcase_data(id, user_info['id'])
        return ArgusResponse.success()
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/move", description="移动case到其他目录")
async def move_testcase(form: ArgusMoveTestCaseDto, user_info=Depends(Permission())):
    try:
        await ProjectRoleDao.read_permission(form.project_id, user_info["id"], user_info['role'])
        await TestCaseDao.update_by_map(
            user_info['id'],
            TestCase.id.in_(form.id_list),
            directory_id=form.directory_id,
            log=True,
        )
        async with async_session() as session:
            async with session.begin():
                log_model = SimpleNamespace(
                    target_directory=form.directory_id,
                    move_count=len(form.id_list or []),
                    action="移动接口用例",
                    __fields__=[SimpleNamespace(name="target_directory"), SimpleNamespace(name="move_count")],
                    __tag__="接口用例",
                    __alias__={
                        "target_directory": "目标目录",
                        "move_count": "移动数量",
                    },
                    __show__=2,
                )
                await ArgusOperationDao.insert_log(
                    session,
                    user_info["id"],
                    OperationType.UPDATE,
                    log_model,
                    key=form.directory_id,
                    changed=["target_directory", "move_count"],
                )
        return ArgusResponse.success()
    except AuthError:
        return ArgusResponse.forbidden()
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/copy", description="复制case到其他项目目录")
async def copy_testcase(form: dict, user_info=Depends(Permission())):
    try:
        id_list = form.get("id_list") or []
        project_id = int(form.get("project_id") or 0)
        directory_id = int(form.get("directory_id") or 0)
        if not id_list:
            return ArgusResponse.failed("请选择需要复制的用例")
        if not project_id or not directory_id:
            return ArgusResponse.failed("请选择目标项目和目标目录")
        await ProjectRoleDao.read_permission(project_id, user_info["id"], user_info['role'])
        new_ids = await TestCaseDao.copy_test_cases(id_list, directory_id, user_info['id'])
        async with async_session() as session:
            async with session.begin():
                log_model = SimpleNamespace(
                    target_directory=directory_id,
                    copy_count=len(new_ids),
                    action="复制接口用例",
                    __fields__=[SimpleNamespace(name="target_directory"), SimpleNamespace(name="copy_count")],
                    __tag__="接口用例",
                    __alias__={
                        "target_directory": "目标目录",
                        "copy_count": "复制数量",
                    },
                    __show__=2,
                )
                await ArgusOperationDao.insert_log(
                    session,
                    user_info["id"],
                    OperationType.INSERT,
                    log_model,
                    key=directory_id,
                    changed=["target_directory", "copy_count"],
                )
        return ArgusResponse.success({"id_list": new_ids, "count": len(new_ids)})
    except AuthError:
        return ArgusResponse.forbidden()
    except Exception as e:
        return ArgusResponse.failed(e)


@router.post("/parameters/insert")
async def insert_testcase_out_parameters(form: ArgusTestCaseParametersDto, user_info=Depends(Permission())):
    query = await ArgusTestCaseOutParametersDao.query_record(name=form.name, case_id=form.case_id)
    if query is not None:
        return ArgusResponse.failed("参数名称已存在")
    data = ArgusTestCaseOutParameters(**form.dict(), user_id=user_info['id'])
    data = await ArgusTestCaseOutParametersDao.insert(model=data, log=True)
    return ArgusResponse.success(data)


@router.post("/parameters/update/batch", summary="批量更新出参数据")
async def update_batch_testcase_out_parameters(case_id: int, form: List[ArgusTestCaseOutParametersForm],
                                               user_info=Depends(Permission())):
    result = await ArgusTestCaseOutParametersDao.update_many(case_id, form, user_info['id'])
    return ArgusResponse.success(result)


@router.post("/parameters/update")
async def update_testcase_out_parameters(form: ArgusTestCaseOutParametersForm, user_info=Depends(Permission())):
    data = await ArgusTestCaseOutParametersDao.update_record_by_id(user_info['id'], form, log=True)
    return ArgusResponse.success(data)


@router.get("/parameters/delete")
async def delete_testcase_out_parameters(id: int, user_info=Depends(Permission()), session=Depends(get_session)):
    await ArgusTestCaseOutParametersDao.delete_record_by_id(session, user_info['id'], id, log=True)
    return ArgusResponse.success()


@router.get("/record/start", summary="开始录制接口请求")
async def record_requests(request: Request, regex: str, user_info=Depends(Permission())):
    await RedisHelper.set_address_record(user_info['id'], request.client.host, regex)
    return ArgusResponse.success(msg="开始录制，可以在浏览器/app上操作啦！")


@router.get("/record/stop", summary="停止录制接口请求")
async def record_requests(request: Request, user_info=Depends(Permission())):
    recorded_requests = await RedisHelper.list_record_data(request.client.host)
    await RedisHelper.remove_address_record(request.client.host)
    valid_requests = [item for item in (recorded_requests or []) if isinstance(item, dict) and str(item.get("url") or item.get("request_url") or "").strip()]
    platform_task = None
    if valid_requests:
        resource_key = "record_associate_default"
        platform_task = await PlatformTaskService.create_task(
            PlatformTaskType.INTERFACE_RECORD_ASSOCIATE.value,
            user_id=int(user_info["id"]),
            biz_id=0,
            biz_type="interface_record_associate",
            project_id=0,
            resource_key=resource_key,
            payload={
                "user_id": int(user_info["id"]),
                "address": str(request.client.host or "").strip(),
                "requests": valid_requests,
                "request_count": len(valid_requests),
            },
            max_retries=1,
        )
    return ArgusResponse.success({
        "status": "stopped",
        "record_count": len(valid_requests),
        "auto_associate_enqueued": bool(platform_task),
        "platform_task_id": int(getattr(platform_task, "id", 0) or 0),
    }, msg="停止成功，录制接口资产关联任务已入队")


@router.get("/record/status", summary="获取录制接口请求状态")
async def record_requests(request: Request, _=Depends(Permission())):
    record = await RedisHelper.get_address_record(request.client.host)
    status = False
    regex = ''
    if record is not None:
        record_data = json.loads(record)
        regex = record_data.get('regex', '')
        status = True
    data = await RedisHelper.list_record_data(request.client.host)
    return ArgusResponse.success(dict(data=data, regex=regex, status=status))


@router.get("/record/remove", summary="删除录制接口")
async def remove_record(index: int, request: Request, _=Depends(Permission())):
    await RedisHelper.remove_record_data(request.client.host, index)
    return ArgusResponse.success()


@router.post("/record/remove/batch", summary="批量删除录制接口")
async def remove_record_batch(payload: dict, request: Request, _=Depends(Permission())):
    index_list = payload.get("index_list") or []
    valid_indexes = sorted({int(index) for index in index_list}, reverse=True)
    for index in valid_indexes:
        await RedisHelper.remove_record_data(request.client.host, index)
    return ArgusResponse.success()


@router.post("/generate", summary="生成用例")
async def generate_case(form: TestCaseGeneratorForm, user=Depends(Permission()), session=Depends(get_session)):
    if len(form.requests) == 0:
        return ArgusResponse.failed("无http请求，请检查参数")
    if len(form.requests) > 1:
        created_cases = []
        async with session.begin():
            for case_form in CaseGenerator.generate_case_batch(form.directory_id, form.name, form.requests):
                case_info = TestCaseInfo(case=case_form)
                ans = await TestCaseDao.insert_test_case(session, case_info, user['id'])
                created_cases.append(ans)
        return ArgusResponse.success({
            "directory_id": form.directory_id,
            "id": created_cases[0].id if created_cases else None,
            "ids": [item.id for item in created_cases],
            "count": len(created_cases),
        })
    CaseGenerator.extract_field(form.requests)
    cs = CaseGenerator.generate_case(form.directory_id, form.name, form.requests[-1])
    constructors = CaseGenerator.generate_constructors(form.requests)
    info = TestCaseInfo(constructor=constructors, case=cs)
    async with session.begin():
        ans = await TestCaseDao.insert_test_case(session, info, user['id'])
        return ArgusResponse.success(ans)


@router.post("/import", summary="导入har或其他用例数据文件")
async def convert_case(import_type: CaseConvertorType, file: UploadFile = File(...), _=Depends(Permission())):
    convert, file_ext = get_convertor(import_type)
    if convert is None:
        return ArgusResponse.failed(f"不支持的导入数据")
    if not file.filename.endswith(f".{file_ext}"):
        return ArgusResponse.failed(f"请传入{file_ext}后缀文件")
    requests = convert(file.file)
    return ArgusResponse.success(requests)


@router.post("/variables", summary="根据前后置步骤查询变量名", tags=["测试用例"])
async def query_variables(steps: List[ArgusTestCaseVariablesDto], session=Depends(get_session)):
    var_list = list()
    await TestCaseDao.query_test_case_out_parameters(session, steps, var_list=var_list)
    return ArgusResponse.success(var_list)
