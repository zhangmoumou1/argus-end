import json
import re
import time
from datetime import datetime
from typing import List, TypeVar
from types import SimpleNamespace

import requests
from fastapi import APIRouter, Depends, UploadFile, File, Request
from sqlalchemy import select, text

from app.core.request import get_convertor
from app.core.request.generator import CaseGenerator
from app.crud.config.GConfigDao import GConfigDao
from app.crud.operation.PityOperationDao import PityOperationDao
from app.crud.project.ProjectRoleDao import ProjectRoleDao
from app.crud.test_case.ConstructorDao import ConstructorDao
from app.crud.test_case.TestCaseAssertsDao import TestCaseAssertsDao
from app.crud.test_case.TestCaseDao import TestCaseDao
from app.crud.test_case.TestCaseDirectory import PityTestcaseDirectoryDao
from app.crud.test_case.TestCaseOutParametersDao import PityTestCaseOutParametersDao
from app.crud.test_case.TestReport import TestReportDao
from app.crud.test_case.TestcaseDataDao import PityTestcaseDataDao
from app.enums.OperationEnum import OperationType
from app.enums.ConvertorEnum import CaseConvertorType
from app.exception.error import AuthError
from app.handler.fatcory import PityResponse
from app.middleware.RedisManager import RedisHelper
from app.models import async_session
from app.models.interface_manage import PityApiEndpoint, PityApiEndpointVersion, PityApiEndpointSample, PityApiService
from app.models.out_parameters import PityTestCaseOutParameters
from app.models.test_case import TestCase
from app.models.testcase_directory import PityTestcaseDirectory
from app.routers import Permission, get_session
from app.schema.constructor import ConstructorForm, ConstructorIndex
from app.schema.testcase_data import PityTestcaseDataForm
from app.schema.testcase_directory import PityTestcaseDirectoryForm, PityMoveTestCaseDto, \
    PityTestcaseDirectoryUpdateForm
from app.schema.testcase_out_parameters import PityTestCaseOutParametersForm, PityTestCaseParametersDto, \
    PityTestCaseVariablesDto
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
    api_key = ai_config.get("api_key") or Config.KIMI_API_KEY
    base_url = (ai_config.get("base_url") or Config.KIMI_BASE_URL).rstrip("/")
    model = ai_config.get("model") or Config.KIMI_MODEL
    provider = ai_config.get("provider") or "kimi"
    if not api_key:
        raise ValueError("未配置 AI API Key")
    prompt = build_ai_prompt(context)
    endpoint_count = len(context.get("endpoints") or [])
    prompt_chars = len(prompt)
    logger.info(
        f"flow-preview ai start provider={provider}, model={model}, base_url={base_url}, "
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
    request_payload = {
        "model": model,
        "temperature": 1,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": "你只输出严格 JSON，用于接口自动化测试用例生成。"},
            {"role": "user", "content": prompt},
        ],
    }
    started_at = time.perf_counter()
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_payload,
            timeout=(10, AI_KIMI_TIMEOUT),
        )
    except requests.Timeout as exc:
        elapsed = round(time.perf_counter() - started_at, 2)
        logger.warning(f"flow-preview ai timeout provider={provider}, model={model}, elapsed={elapsed}s, endpoint_count={endpoint_count}, prompt_chars={prompt_chars}")
        raise ValueError(f"AI模型({provider}/{model})请求超时({elapsed}s)，已压缩接口上下文仍未返回，请减少接口数量或稍后重试") from exc
    except requests.RequestException as exc:
        elapsed = round(time.perf_counter() - started_at, 2)
        logger.error(f"flow-preview ai request error provider={provider}, model={model}, elapsed={elapsed}s, error={exc}")
        raise ValueError(f"AI模型({provider}/{model})请求失败({elapsed}s): {exc}") from exc
    elapsed = round(time.perf_counter() - started_at, 2)
    request_id = response.headers.get("x-request-id") or response.headers.get("request-id") or ""
    logger.info(f"flow-preview ai done provider={provider}, model={model}, status={response.status_code}, elapsed={elapsed}s, request_id={request_id}")
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
        logger.error(f"flow-preview ai invalid-json-response provider={provider}, model={model}, request_id={request_id}, body_preview={body_preview}")
        raise ValueError(f"AI模型({provider}/{model})响应不是有效 JSON: {body_preview}") from exc
    logger.info(f"flow-preview ai raw-payload-preview provider={provider}, model={model}, payload={preview_text(json.dumps(payload, ensure_ascii=False), 2000)}")
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
        "ALTER TABLE pity_api_service ADD COLUMN developer VARCHAR(128) NULL COMMENT '开发人员'",
        "ALTER TABLE pity_api_service ADD COLUMN tester VARCHAR(128) NULL COMMENT '测试人员'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN module_name VARCHAR(128) NOT NULL DEFAULT '默认模块' COMMENT '功能模块'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT '接口状态'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN request_headers LONGTEXT NULL COMMENT '请求头'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN current_version_id INT NOT NULL DEFAULT 0 COMMENT '当前版本ID'",
        "ALTER TABLE pity_api_endpoint ADD COLUMN current_version_no VARCHAR(32) NOT NULL DEFAULT 'v1' COMMENT '当前版本号'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN module_name VARCHAR(128) NOT NULL DEFAULT '默认模块' COMMENT '功能模块'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN endpoint_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT '接口状态'",
        "ALTER TABLE pity_api_endpoint_version ADD COLUMN request_headers LONGTEXT NULL COMMENT '请求头'",
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
        service = (await session.execute(select(PityApiService).where(PityApiService.id == service_id))).scalars().first()
    versions = []
    if version_ids:
        result = await session.execute(select(PityApiEndpointVersion).where(PityApiEndpointVersion.id.in_(version_ids)))
        version_map = {item.id: item for item in result.scalars().all()}
        versions = [version_map[item] for item in version_ids if item in version_map]
    elif endpoint_ids:
        result = await session.execute(select(PityApiEndpoint).where(PityApiEndpoint.id.in_(endpoint_ids)))
        endpoint_map = {item.id: item for item in result.scalars().all()}
        endpoints = [endpoint_map[item] for item in endpoint_ids if item in endpoint_map]
        version_id_list = [item.current_version_id for item in endpoints if item.current_version_id]
        if version_id_list:
            result = await session.execute(select(PityApiEndpointVersion).where(PityApiEndpointVersion.id.in_(version_id_list)))
            version_map = {item.id: item for item in result.scalars().all()}
            versions = [version_map[item] for item in version_id_list if item in version_map]
    endpoint_id_list = [item.endpoint_id for item in versions if item.endpoint_id]
    sample_map = {}
    if endpoint_id_list:
        sample_result = await session.execute(
            select(PityApiEndpointSample).where(
                PityApiEndpointSample.endpoint_id.in_(endpoint_id_list),
                PityApiEndpointSample.deleted_at == 0,
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
    return PityTestCaseOutParametersForm(
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
async def ai_generate_flow_preview(form: dict, _=Depends(Permission()), session=Depends(get_session)):
    try:
        if not form.get("directory_id"):
            return PityResponse.failed("请先选择用例目录")
        context = await build_endpoint_context(session, form)
        if not context.get("endpoints"):
            return PityResponse.failed("请至少选择一个接口版本")
        ai_config = await GConfigDao.get_active_ai_model_config()
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
        return PityResponse.success(normalized)
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/ai-generate/flow-save", summary="保存AI生成流程接口场景")
async def ai_generate_flow_save(form: dict, user_info=Depends(Permission()), session=Depends(get_session)):
    try:
        directory_id = int(form.get("directory_id") or 0)
        cases = form.get("cases") or []
        if not directory_id:
            return PityResponse.failed("请先选择用例目录")
        if not cases:
            return PityResponse.failed("请选择需要保存的用例")
        saved_ids = []
        async with session.begin():
            for raw_case in cases:
                info = to_testcase_info(raw_case, directory_id)
                case_id = await TestCaseDao.insert_test_case(session, info, user_info['id'])
                saved_ids.append(case_id)
        return PityResponse.success({"saved_ids": saved_ids, "count": len(saved_ids)})
    except Exception as e:
        return PityResponse.failed(e)


@router.get("/list")
async def list_testcase(directory_id: int = None, name: str = "", create_user: str = ''):
    data = await TestCaseDao.list_test_case(directory_id, name, create_user)
    return PityResponse.success(data)


@router.post("/insert")
async def insert_testcase(data: TestCaseForm, user_info=Depends(Permission())):
    try:
        record = await TestCaseDao.query_record(name=data.name, directory_id=data.directory_id)
        if record is not None:
            return PityResponse.failed("用例已存在")
        model = TestCase(**data.dict(), create_user=user_info['id'])
        model = await TestCaseDao.insert(model=model, log=True)
        return PityResponse.success(model.id)
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/create", summary="创建接口测试用例")
async def create_testcase(data: TestCaseInfo, user_info=Depends(Permission()), session=Depends(get_session)):
    async with session.begin():
        await TestCaseDao.insert_test_case(session, data, user_info['id'])
    return PityResponse.success()


@router.post("/update")
async def update_testcase(form: TestCaseForm, user_info=Depends(Permission())):
    try:
        data = await TestCaseDao.update_test_case(form, user_info['id'])
        result = await PityTestCaseOutParametersDao.update_many(form.id, form.out_parameters, user_info['id'])
        return PityResponse.success(dict(case_info=data, out_parameters=result))
    except Exception as e:
        return PityResponse.failed(e)


@router.delete("/delete", description="删除测试用例")
async def delete_testcase(id_list: List[int], user_info=Depends(Permission()), session=Depends(get_session)):
    try:
        async with session.begin():
            await TestCaseDao.delete_records(session, user_info['id'], id_list, log=True)
            await TestCaseAssertsDao.delete_records(session, user_info['id'], id_list, column="case_id", log=True)
            await PityTestcaseDataDao.delete_records(session, user_info['id'], id_list, column="case_id", log=True)
            return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(e)


@router.get("/query")
async def query_testcase(caseId: int, _=Depends(Permission())):
    try:
        data = await TestCaseDao.query_test_case(caseId)
        return PityResponse.success(PityResponse.dict_model_to_dict(data))
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/asserts/insert")
async def insert_testcase_asserts(data: TestCaseAssertsForm, user_info=Depends(Permission())):
    try:
        new_assert = await TestCaseAssertsDao.insert_test_case_asserts(data, user_id=user_info["id"])
        return PityResponse.success(new_assert)
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/asserts/update")
async def update_testcase_asserts(data: TestCaseAssertsForm, user_info=Depends(Permission())):
    try:
        updated = await TestCaseAssertsDao.update_test_case_asserts(data, user_id=user_info["id"])
        return PityResponse.success(updated)
    except Exception as e:
        return PityResponse.failed(e)


@router.get("/asserts/delete")
async def delete_testcase_asserts(id: int, user_info=Depends(Permission())):
    await TestCaseAssertsDao.delete_test_case_asserts(id, user_id=user_info["id"])
    return PityResponse.success()


@router.post("/constructor/insert")
async def insert_constructor(data: ConstructorForm, user_info=Depends(Permission())):
    await ConstructorDao.insert_constructor(data, user_id=user_info["id"])
    return PityResponse.success()


@router.post("/constructor/update")
async def update_constructor(data: ConstructorForm, user_info=Depends(Permission())):
    await ConstructorDao.update_constructor(data, user_id=user_info["id"])
    return PityResponse.success()


@router.get("/constructor/delete")
async def delete_constructor(id: int, user_info=Depends(Permission())):
    await ConstructorDao.delete_constructor(id, user_id=user_info["id"])
    return PityResponse.success()


@router.post("/constructor/order")
async def update_constructor_index(data: List[ConstructorIndex], user_info=Depends(Permission())):
    await ConstructorDao.update_constructor_index(data)
    return PityResponse.success()


@router.get("/constructor/tree")
async def get_constructor_tree(suffix: bool, name: str = "", user_info=Depends(Permission())):
    result = await ConstructorDao.get_constructor_tree(name, suffix)
    return PityResponse.success(result)


@router.get("/constructor")
async def get_constructor(id: int, user_info=Depends(Permission())):
    result = await ConstructorDao.get_constructor_data(id)
    return PityResponse.success(result)


@router.get("/constructor/list")
async def list_case_and_constructor(constructor_type: int, suffix: bool):
    ans = await ConstructorDao.get_case_and_constructor(constructor_type, suffix)
    return PityResponse.success(ans)


@router.get("/report")
async def query_report(id: int, user_info=Depends(Permission())):
    report, case_list, plan_name = await TestReportDao.query(id)
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
    if report is not None:
        report.pending_review = 1 if pending_map else 0
    return PityResponse.success(dict(report=report, plan_name=plan_name, case_list=case_list))


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
                "FROM pity_test_result r "
                "JOIN pity_testcase c ON c.id = r.case_id "
                "WHERE r.deleted_at = 0 AND c.deleted_at = 0 AND c.api_pending_update = 1 "
                f"AND r.report_id IN ({','.join([str(x) for x in report_ids])})"
            ))
            pending_report_ids = {int(report_id) for report_id, in result.all()}
    for item in report_list:
        item.pending_review = 1 if int(getattr(item, "id", 0) or 0) in pending_report_ids else 0
    return PityResponse.success_with_size(data=report_list, total=total)


@router.get("/pending-review/list")
async def list_pending_review_cases(project_id: int = None, url: str = "", create_user: int = None,
                                    _=Depends(Permission())):
    try:
        async with async_session() as session:
            filters = [
                TestCase.deleted_at == 0,
                TestCase.api_pending_update == 1,
                PityTestcaseDirectory.id == TestCase.directory_id,
                PityTestcaseDirectory.deleted_at == 0,
            ]
            if project_id is not None:
                filters.append(PityTestcaseDirectory.project_id == project_id)
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
                        PityTestcaseDirectory.project_id,
                        PityApiService.name.label("service_name"),
                        PityApiEndpoint.name.label("endpoint_name"),
                    )
                    .select_from(TestCase)
                    .join(PityTestcaseDirectory, PityTestcaseDirectory.id == TestCase.directory_id)
                    .outerjoin(PityApiService, PityApiService.id == TestCase.api_service_id)
                    .outerjoin(PityApiEndpoint, PityApiEndpoint.id == TestCase.api_endpoint_id)
                    .where(*filters)
                    .order_by(TestCase.updated_at.desc(), TestCase.id.desc())
                )
            ).all()
            return PityResponse.success([
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
        return PityResponse.failed(e)


@router.get("/xmind")
async def get_xmind_data(case_id: int, user_info=Depends(Permission())):
    tree_data = await TestCaseDao.get_xmind_data(case_id)
    return PityResponse.success(tree_data)


@router.get("/directory")
async def get_testcase_directory(project_id: int, move: bool = False, user_info=Depends(Permission())):
    tree_data, _ = await PityTestcaseDirectoryDao.get_directory_tree(project_id, move=move)
    return PityResponse.success(tree_data)


@router.get("/tree")
async def get_directory_and_case(project_id: int, user_info=Depends(Permission())):
    try:
        tree_data, cs_map = await PityTestcaseDirectoryDao.get_directory_tree(project_id,
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
        return PityResponse.success(dict(tree=tree_data, case_map=cs_map, pending_map=pending_map))
    except Exception as e:
        return PityResponse.failed(e)


@router.get("/directory/query")
async def query_testcase_directory(directory_id: int, user_info=Depends(Permission())):
    try:
        data = await PityTestcaseDirectoryDao.query_directory(directory_id)
        await ProjectRoleDao.read_permission(data.project_id, user_info["id"], user_info['role'])
        return PityResponse.success(data)
    except AuthError:
        return PityResponse.forbidden()
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/directory/insert")
async def insert_testcase_directory(form: PityTestcaseDirectoryForm, user_info=Depends(Permission())):
    try:
        await PityTestcaseDirectoryDao.insert_directory(form, user_info['id'])
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/directory/update")
async def update_testcase_directory(form: PityTestcaseDirectoryUpdateForm, user_info=Depends(Permission())):
    try:
        await PityTestcaseDirectoryDao.update_directory(form, user_info['id'])
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(e)


@router.get("/directory/delete")
async def delete_testcase_directory(id: int, user_info=Depends(Permission())):
    try:
        await PityTestcaseDirectoryDao.delete_directory(id, user_info['id'])
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/data/insert")
async def insert_testcase_data(form: PityTestcaseDataForm, user_info=Depends(Permission())):
    try:
        data = await PityTestcaseDataDao.insert_testcase_data(form, user_info['id'])
        return PityResponse.success(data)
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/data/update")
async def update_testcase_data(form: PityTestcaseDataForm, user_info=Depends(Permission())):
    try:
        data = await PityTestcaseDataDao.update_testcase_data(form, user_info['id'])
        return PityResponse.success(data)
    except Exception as e:
        return PityResponse.failed(e)


@router.get("/data/delete")
async def delete_testcase_data(id: int, user_info=Depends(Permission())):
    try:
        await PityTestcaseDataDao.delete_testcase_data(id, user_info['id'])
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/move", description="移动case到其他目录")
async def move_testcase(form: PityMoveTestCaseDto, user_info=Depends(Permission())):
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
                await PityOperationDao.insert_log(
                    session,
                    user_info["id"],
                    OperationType.UPDATE,
                    log_model,
                    key=form.directory_id,
                    changed=["target_directory", "move_count"],
                )
        return PityResponse.success()
    except AuthError:
        return PityResponse.forbidden()
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/copy", description="复制case到其他项目目录")
async def copy_testcase(form: dict, user_info=Depends(Permission())):
    try:
        id_list = form.get("id_list") or []
        project_id = int(form.get("project_id") or 0)
        directory_id = int(form.get("directory_id") or 0)
        if not id_list:
            return PityResponse.failed("请选择需要复制的用例")
        if not project_id or not directory_id:
            return PityResponse.failed("请选择目标项目和目标目录")
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
                await PityOperationDao.insert_log(
                    session,
                    user_info["id"],
                    OperationType.INSERT,
                    log_model,
                    key=directory_id,
                    changed=["target_directory", "copy_count"],
                )
        return PityResponse.success({"id_list": new_ids, "count": len(new_ids)})
    except AuthError:
        return PityResponse.forbidden()
    except Exception as e:
        return PityResponse.failed(e)


@router.post("/parameters/insert")
async def insert_testcase_out_parameters(form: PityTestCaseParametersDto, user_info=Depends(Permission())):
    query = await PityTestCaseOutParametersDao.query_record(name=form.name, case_id=form.case_id)
    if query is not None:
        return PityResponse.failed("参数名称已存在")
    data = PityTestCaseOutParameters(**form.dict(), user_id=user_info['id'])
    data = await PityTestCaseOutParametersDao.insert(model=data, log=True)
    return PityResponse.success(data)


@router.post("/parameters/update/batch", summary="批量更新出参数据")
async def update_batch_testcase_out_parameters(case_id: int, form: List[PityTestCaseOutParametersForm],
                                               user_info=Depends(Permission())):
    result = await PityTestCaseOutParametersDao.update_many(case_id, form, user_info['id'])
    return PityResponse.success(result)


@router.post("/parameters/update")
async def update_testcase_out_parameters(form: PityTestCaseOutParametersForm, user_info=Depends(Permission())):
    data = await PityTestCaseOutParametersDao.update_record_by_id(user_info['id'], form, log=True)
    return PityResponse.success(data)


@router.get("/parameters/delete")
async def delete_testcase_out_parameters(id: int, user_info=Depends(Permission()), session=Depends(get_session)):
    await PityTestCaseOutParametersDao.delete_record_by_id(session, user_info['id'], id, log=True)
    return PityResponse.success()


@router.get("/record/start", summary="开始录制接口请求")
async def record_requests(request: Request, regex: str, user_info=Depends(Permission())):
    await RedisHelper.set_address_record(user_info['id'], request.client.host, regex)
    return PityResponse.success(msg="开始录制，可以在浏览器/app上操作啦！")


@router.get("/record/stop", summary="停止录制接口请求")
async def record_requests(request: Request, _=Depends(Permission())):
    await RedisHelper.remove_address_record(request.client.host)
    return PityResponse.success(msg="停止成功，快去生成用例吧~")


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
    return PityResponse.success(dict(data=data, regex=regex, status=status))


@router.get("/record/remove", summary="删除录制接口")
async def remove_record(index: int, request: Request, _=Depends(Permission())):
    await RedisHelper.remove_record_data(request.client.host, index)
    return PityResponse.success()


@router.post("/record/remove/batch", summary="批量删除录制接口")
async def remove_record_batch(payload: dict, request: Request, _=Depends(Permission())):
    index_list = payload.get("index_list") or []
    valid_indexes = sorted({int(index) for index in index_list}, reverse=True)
    for index in valid_indexes:
        await RedisHelper.remove_record_data(request.client.host, index)
    return PityResponse.success()


@router.post("/generate", summary="生成用例")
async def generate_case(form: TestCaseGeneratorForm, user=Depends(Permission()), session=Depends(get_session)):
    if len(form.requests) == 0:
        return PityResponse.failed("无http请求，请检查参数")
    if len(form.requests) > 1:
        created_cases = []
        async with session.begin():
            for case_form in CaseGenerator.generate_case_batch(form.directory_id, form.name, form.requests):
                case_info = TestCaseInfo(case=case_form)
                ans = await TestCaseDao.insert_test_case(session, case_info, user['id'])
                created_cases.append(ans)
        return PityResponse.success({
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
        return PityResponse.success(ans)


@router.post("/import", summary="导入har或其他用例数据文件")
async def convert_case(import_type: CaseConvertorType, file: UploadFile = File(...), _=Depends(Permission())):
    convert, file_ext = get_convertor(import_type)
    if convert is None:
        return PityResponse.failed(f"不支持的导入数据")
    if not file.filename.endswith(f".{file_ext}"):
        return PityResponse.failed(f"请传入{file_ext}后缀文件")
    requests = convert(file.file)
    return PityResponse.success(requests)


@router.post("/variables", summary="根据前后置步骤查询变量名", tags=["测试用例"])
async def query_variables(steps: List[PityTestCaseVariablesDto], session=Depends(get_session)):
    var_list = list()
    await TestCaseDao.query_test_case_out_parameters(session, steps, var_list=var_list)
    return PityResponse.success(var_list)
