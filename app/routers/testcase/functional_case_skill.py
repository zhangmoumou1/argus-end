import base64
import asyncio
import json
import os
import re
import shutil
import time
from copy import deepcopy
from datetime import datetime

import requests
from app.crud.config.GConfigDao import GConfigDao
from app.crud.operation.PityOperationDao import PityOperationDao
from app.enums.OperationEnum import OperationType
from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.handler.fatcory import PityResponse
from app.models import async_session
from app.models.functional_case import PityFunctionalCaseSkillDoc, PityFunctionalCaseSkillTask
from app.models.user import User
from app.routers import Permission
from app.schema.functional_case import FunctionalCaseSkillDocForm, FunctionalCaseSkillTaskForm
from app.utils.logger import Log
from config import Config

router = APIRouter(prefix="/functional-case")
logger = Log("functional_case_skill")

AI_CASE_CREATOR_ROOT = r"C:\Users\bytde\Desktop\ai_case_creator"
SKILL_TASK_DIR = os.path.join("statics", "functional_case_skill_tasks")
AI_TEXT_LIMIT = 12000
AI_INSTRUCTION_LIMIT = 6000
AI_IMAGE_LIMIT = 6
AI_IMAGE_DATA_URL_LIMIT = 2_000_000
SKILL_TASK_SCHEMA_READY = False
def serialize_model(model):
    return PityResponse.model_to_dict(model)


async def ensure_skill_task_schema(session):
    global SKILL_TASK_SCHEMA_READY
    if SKILL_TASK_SCHEMA_READY:
        return
    try:
        for column_name, sql in [
            ("description", "ALTER TABLE pity_functional_case_skill_doc ADD COLUMN description VARCHAR(500) NULL COMMENT '文档描述'"),
            ("input_payload", "ALTER TABLE pity_functional_case_skill_task ADD COLUMN input_payload TEXT NULL COMMENT '任务输入'"),
            ("stage", "ALTER TABLE pity_functional_case_skill_task ADD COLUMN stage VARCHAR(64) NOT NULL DEFAULT 'queued' COMMENT '执行阶段'"),
            ("stage_text", "ALTER TABLE pity_functional_case_skill_task ADD COLUMN stage_text VARCHAR(255) NULL COMMENT '阶段说明'"),
            ("progress", "ALTER TABLE pity_functional_case_skill_task ADD COLUMN progress INT NOT NULL DEFAULT 0 COMMENT '进度'"),
            ("review_provider", "ALTER TABLE pity_functional_case_skill_task ADD COLUMN review_provider VARCHAR(32) NULL COMMENT '评审模型'"),
            ("review_rounds", "ALTER TABLE pity_functional_case_skill_task ADD COLUMN review_rounds INT NOT NULL DEFAULT 0 COMMENT '评审轮次'"),
            ("task_logs", "ALTER TABLE pity_functional_case_skill_task ADD COLUMN task_logs TEXT NULL COMMENT '任务日志'"),
        ]:
            result = await session.execute(text(f"SHOW COLUMNS FROM {'pity_functional_case_skill_doc' if column_name == 'description' else 'pity_functional_case_skill_task'} LIKE '{column_name}'"))
            if result.first() is None:
                await session.execute(text(sql))
        await session.commit()
    except OperationalError as exc:
        if "Duplicate column name" not in str(exc):
            raise
    SKILL_TASK_SCHEMA_READY = True


def pick_user_name(user):
    if user is None:
        return ""
    return (user.name or user.username or "").strip()


def truncate_ai_text(value, limit):
    text_value = str(value or "").strip()
    if len(text_value) <= limit:
        return text_value
    return f"{text_value[:limit]}\n\n[内容已截断，共{len(text_value)}字符，仅保留前{limit}字符]"


def preview_text(value, limit=300):
    text_value = str(value or "").strip()
    if len(text_value) <= limit:
        return text_value
    return f"{text_value[:limit]} ...<truncated {len(text_value) - limit} chars>"


def read_text_if_exists(path):
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as file:
        return file.read().strip()


def get_builtin_context_text():
    parts = []
    for relative_path, title in [
        (os.path.join("1_规范与标准", "测试用例编写规范.md"), "系统内置规范"),
        (os.path.join("2_模板", "测试用例模板.md"), "系统内置模板"),
        (os.path.join("3_skills", "create_case.md"), "系统内置生成技能"),
        (os.path.join("3_skills", "evaluate_case.md"), "系统内置评审技能"),
    ]:
        text_value = read_text_if_exists(os.path.join(AI_CASE_CREATOR_ROOT, relative_path))
        if text_value:
            parts.append(f"{title}：\n{text_value}")
    return "\n\n".join(parts)


def summarize_ai_images(images):
    summary = []
    for index, image in enumerate(images or [], start=1):
        image_value = str(image or "")
        image_type = "data_url" if image_value.startswith("data:image") else "url"
        summary.append({
            "index": index,
            "type": image_type,
            "length": len(image_value),
        })
    return summary


def summarize_skill_task_request(task_payload, content):
    text_blocks = [
        item.get("text") or ""
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    sent_images = [
        item.get("image_url", {}).get("url")
        for item in content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    return {
        "project_id": task_payload.get("project_id"),
        "title": task_payload.get("title"),
        "requirement_length": len(str(task_payload.get("requirement_text") or "")),
        "instruction_length": len(str(task_payload.get("instruction_text") or "")),
        "requirement_group_count": len(task_payload.get("requirement_items") or []),
        "doc_count": len(task_payload.get("doc_ids") or []),
        "sent_image_count": len(sent_images),
        "prompt_text_length": sum(len(block) for block in text_blocks),
        "prompt_text_preview": preview_text("\n\n".join(text_blocks), 1000),
        "images": summarize_ai_images(sent_images),
    }


def provider_supports_image_content(provider, model=""):
    provider_value = str(provider or "").strip().lower()
    model_value = str(model or "").strip().lower()
    if provider_value == "kimi":
        return True
    if provider_value == "qwen":
        return True
    if provider_value == "deepseek":
        return False
    if any(keyword in model_value for keyword in ("vision", "vl", "omni")):
        return True
    return False


def flatten_prompt_content_to_text(content, provider, model):
    lines = []
    image_count = 0
    omitted_image_count = 0
    for block in content or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type == "text":
            text_value = str(block.get("text") or "").strip()
            if text_value:
                lines.append(text_value)
            continue
        if block_type != "image_url":
            continue
        image_count += 1
        image_url = str((block.get("image_url") or {}).get("url") or "").strip()
        if not image_url:
            continue
        if image_url.startswith("data:image"):
            omitted_image_count += 1
            lines.append(
                f"[需求截图{image_count}：已上传图片，但当前模型 {provider}/{model} 不支持 image_url 输入，无法直接读取该截图内容。]"
            )
        else:
            lines.append(f"[需求截图{image_count} 链接：{image_url}]")
    if image_count:
        lines.append(
            f"注意：当前模型 {provider}/{model} 按文本模式调用。"
            f"{' 其中 ' + str(omitted_image_count) + ' 张 base64 截图未能直接发送给模型。' if omitted_image_count else ''}"
            " 如需直接识别截图，请切换到支持视觉输入的供应商/模型。"
        )
    return "\n\n".join([item for item in lines if str(item or "").strip()])


def is_balance_error(status_code, detail):
    try:
        detail_text = json.dumps(detail, ensure_ascii=False)
    except Exception:
        detail_text = str(detail or "")
    detail_lower = detail_text.lower()
    return (
        int(status_code or 0) == 402
        or "insufficient balance" in detail_lower
        or "余额不足" in detail_text
        or "insufficient_balance" in detail_lower
    )


def build_ai_error(provider, model, status_code, detail):
    if is_balance_error(status_code, detail):
        return ValueError(f"AI模型({provider}/{model})余额不足: {detail}")
    return ValueError(f"AI模型({provider}/{model})调用失败: {detail}")


def order_ai_model_configs(config):
    models = (config or {}).get("models") or {}
    active_provider = str((config or {}).get("active_provider") or "kimi").strip()
    ordered = []
    seen = set()
    for provider in [active_provider, *models.keys()]:
        provider_key = str(provider or "").strip()
        if not provider_key or provider_key in seen:
            continue
        seen.add(provider_key)
        item = models.get(provider_key) if isinstance(models.get(provider_key), dict) else {}
        if not item:
            continue
        api_key = str(item.get("api_key") or "").strip()
        base_url = str(item.get("base_url") or "").strip()
        model = str(item.get("model") or "").strip()
        if not api_key or not base_url or not model:
            continue
        ordered.append(item)
    return ordered


def build_skill_task_prompt_content(task_payload, docs):
    title = str(task_payload.get("title") or "功能用例").strip() or "功能用例"
    requirement_text = truncate_ai_text(task_payload.get("requirement_text"), AI_TEXT_LIMIT)
    instruction_text = truncate_ai_text(task_payload.get("instruction_text"), AI_INSTRUCTION_LIMIT)
    extra_context_text = build_extra_context(docs)
    requirement_items = task_payload.get("requirement_items") or []
    content = []
    remaining_image_slots = AI_IMAGE_LIMIT

    intro_lines = [
        "你是资深测试分析师，请根据需求材料生成功能测试用例脑图。",
        f"当前用例标题：{title}",
        "请严格遵循技能文档中的输出结构，只输出最终 Markdown，不要输出解释、分析过程、注释或代码块标记。",
        "请尽量按 模块-功能-子功能-字段-用例名称-预期 的层级组织内容，覆盖正常、异常、边界场景。",
        "用例名称行请保留优先级标记，如 P0/P1/P2，便于系统自动识别优先级。",
    ]
    if extra_context_text:
        intro_lines.append(f"补充技能与规范材料：\n{truncate_ai_text(extra_context_text, 24000)}")
    if requirement_text:
        intro_lines.append(f"需求总述：\n{requirement_text}")
    if instruction_text:
        intro_lines.append(f"额外生成要求：\n{instruction_text}")
    if requirement_items:
        intro_lines.append("以下按需求组提供材料。每组的说明、设计链接和图片属于同一上下文，禁止跨组混用。")
    content.append({"type": "text", "text": "\n\n".join(intro_lines)})

    normalized_items = []
    for index, item in enumerate(requirement_items, start=1):
        item_title = str(item.get("title") or "").strip() or f"需求组{index}"
        item_text = truncate_ai_text(item.get("text"), AI_TEXT_LIMIT)
        item_links = [str(link or "").strip() for link in (item.get("design_links") or []) if str(link or "").strip()]
        raw_images = item.get("images") or []
        accepted_images = []
        skipped_image_count = 0
        for image in raw_images:
            image_value = str(image or "").strip()
            if not image_value:
                continue
            if image_value.startswith("data:image") and len(image_value) > AI_IMAGE_DATA_URL_LIMIT:
                skipped_image_count += 1
                continue
            if remaining_image_slots <= 0:
                skipped_image_count += 1
                continue
            accepted_images.append(image_value)
            remaining_image_slots -= 1
        if not item_title and not item_text and not item_links and not accepted_images:
            continue
        normalized_items.append({
            "title": item_title,
            "text": item_text,
            "design_links": item_links,
            "images": accepted_images,
            "skipped_image_count": skipped_image_count,
        })

    if normalized_items:
        for index, item in enumerate(normalized_items, start=1):
            lines = [
                f"需求组{index}标题：{item['title']}",
                f"需求组{index}说明：\n{item['text'] or '无'}",
                f"需求组{index}设计链接：{'；'.join(item['design_links']) if item['design_links'] else '无'}",
            ]
            if item["images"]:
                lines.append(f"下面紧跟 {len(item['images'])} 张属于需求组{index} 的截图，请结合当前需求组内容理解。")
            if item["skipped_image_count"]:
                lines.append(f"注意：需求组{index} 有 {item['skipped_image_count']} 张图片因数量或体积限制未发送。")
            content.append({"type": "text", "text": "\n\n".join(lines)})
            for image in item["images"]:
                content.append({"type": "image_url", "image_url": {"url": image}})
    else:
        images = []
        skipped_image_count = 0
        for image in task_payload.get("images") or []:
            image_value = str(image or "").strip()
            if not image_value:
                continue
            if image_value.startswith("data:image") and len(image_value) > AI_IMAGE_DATA_URL_LIMIT:
                skipped_image_count += 1
                continue
            if remaining_image_slots <= 0:
                skipped_image_count += 1
                continue
            images.append(image_value)
            remaining_image_slots -= 1
        if images or skipped_image_count:
            lines = ["以下为补充需求截图材料。"]
            if images:
                lines.append(f"下面紧跟 {len(images)} 张需求截图，请结合总体需求理解。")
            if skipped_image_count:
                lines.append(f"注意：有 {skipped_image_count} 张图片因数量或体积限制未发送。")
            content.append({"type": "text", "text": "\n\n".join(lines)})
            for image in images:
                content.append({"type": "image_url", "image_url": {"url": image}})
    return content


def build_loggable_kimi_payload(payload):
    try:
        cloned = json.loads(json.dumps(payload, ensure_ascii=False))
    except Exception:
        return {"payload_preview": preview_text(payload, 2000)}
    messages = cloned.get("messages") or []
    for message_item in messages:
        content = message_item.get("content")
        if isinstance(content, list):
            normalized_content = []
            for block in content:
                if not isinstance(block, dict):
                    normalized_content.append(block)
                    continue
                if block.get("type") == "image_url":
                    image_url = block.get("image_url") or {}
                    url_value = str(image_url.get("url") or "")
                    normalized_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"<{'data_url' if url_value.startswith('data:image') else 'url'}, length={len(url_value)}>"}
                    })
                else:
                    next_block = dict(block)
                    if "text" in next_block:
                        next_block["text"] = preview_text(next_block.get("text"), 4000)
                    normalized_content.append(next_block)
            message_item["content"] = normalized_content
    return cloned


def normalize_model_text(text_value):
    return str(text_value or "").strip().replace("\ufeff", "")


def strip_fenced_markdown(text_value):
    matched_blocks = re.findall(r"```(?:markdown|md)?\s*([\s\S]*?)\s*```", text_value, re.IGNORECASE)
    if matched_blocks:
        joined = "\n\n".join([item.strip() for item in matched_blocks if str(item or "").strip()])
        if joined.strip():
            return joined.strip()
    return text_value


def extract_priority_icons(text_value):
    priority_match = re.search(r"[（(]\s*P\s*([0-9])\s*[）)]", text_value, re.IGNORECASE)
    if not priority_match:
        priority_match = re.search(r"\bP\s*([0-9])\b", text_value, re.IGNORECASE)
    if not priority_match:
        return []
    priority_level = max(0, min(8, int(priority_match.group(1) or 0)))
    return [f"priority_{priority_level + 1}"]


def clean_outline_node_text(text_value):
    text = str(text_value or "").strip()
    text = re.sub(r"^[\-\*\+]\s+", "", text)
    text = re.sub(r"^\d+\.\s+", "", text)
    text = re.sub(r"[（(]\s*P\s*[0-9]\s*[）)]", "", text, flags=re.IGNORECASE)
    return text.strip() or "未命名节点"


def parse_markdown_outline_to_case_data(text_value, fallback_title):
    normalized_text = normalize_model_text(strip_fenced_markdown(text_value)).replace("\r\n", "\n")
    lines = []
    for raw_line in normalized_text.split("\n"):
        if not str(raw_line or "").strip():
            continue
        normalized_line = raw_line.replace("\t", "  ")
        if not re.match(r"^\s*([\-\*\+]|\d+\.)\s+", normalized_line):
            continue
        indent = re.match(r"^\s*", normalized_line).group(0)
        level = max(0, len(indent) // 2)
        lines.append({
            "level": level,
            "text": clean_outline_node_text(normalized_line),
            "icons": extract_priority_icons(normalized_line),
        })

    if not lines:
        raise ValueError("AI 未返回可识别的 Markdown 大纲")

    root_title = str(fallback_title or "AI生成功能用例").strip() or "AI生成功能用例"
    root_node = {"data": {"text": root_title}, "children": []}
    stack = [{"level": -1, "node": root_node}]

    for item in lines:
        node_data = {"text": item["text"]}
        if item["icons"]:
            node_data["icon"] = item["icons"]
        node = {"data": node_data, "children": []}
        while len(stack) > 1 and stack[-1]["level"] >= item["level"]:
            stack.pop()
        parent = stack[-1]["node"]
        parent["children"].append(node)
        stack.append({"level": item["level"], "node": node})

    return root_title, root_node


def extract_model_result_content(text_value):
    normalized_text = normalize_model_text(text_value)
    if not normalized_text:
        raise ValueError("AI 未返回内容")
    return strip_fenced_markdown(normalized_text)


def normalize_ai_node(node):
    if isinstance(node, str):
        return {"data": {"text": node}, "children": []}
    if not isinstance(node, dict):
        return {"data": {"text": "未命名节点"}, "children": []}
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    text_value = str(data.get("text") or node.get("text") or node.get("title") or "未命名节点").strip() or "未命名节点"
    normalized_data = {"text": text_value}
    for key in ("icon", "note", "tag", "hyperlink"):
        value = data.get(key) if key in data else node.get(key)
        if value not in (None, "", []):
            normalized_data[key] = value
    raw_children = node.get("children") if isinstance(node.get("children"), list) else []
    return {"data": normalized_data, "children": [normalize_ai_node(child) for child in raw_children]}


def normalize_ai_case_data(payload, fallback_title):
    if isinstance(payload, str):
        return parse_markdown_outline_to_case_data(payload, fallback_title)
    if not isinstance(payload, dict):
        raise ValueError("AI 返回结果格式不正确")
    title = str(payload.get("title") or fallback_title or "AI生成功能用例").strip() or "AI生成功能用例"
    if "root" in payload and isinstance(payload.get("root"), dict):
        root_node = normalize_ai_node(payload.get("root"))
    elif "data" in payload and isinstance(payload.get("data"), dict):
        root_node = normalize_ai_node({"data": payload.get("data"), "children": payload.get("children") or []})
    else:
        root_node = normalize_ai_node(payload)
    if not root_node.get("data", {}).get("text"):
        root_node["data"]["text"] = title
    return title, root_node


def analyze_case_data(data):
    if not isinstance(data, dict):
        return {"case_count": 0}
    case_count = 0

    def walk(node):
        nonlocal case_count
        node_data = node.get("data") if isinstance(node, dict) else {}
        node_icons = node_data.get("icon") if isinstance(node_data, dict) else []
        icons = node_icons if isinstance(node_icons, list) else [node_icons] if node_icons else []
        if any(isinstance(icon, str) and icon.startswith("priority_") for icon in icons):
            case_count += 1
        for child in node.get("children") or []:
            walk(child)

    walk(data)
    return {"case_count": case_count}


def call_active_model_generate(task_payload, docs, ai_config):
    provider = ai_config.get("provider") or "kimi"
    model = ai_config.get("model") or ""
    base_url = str(ai_config.get("base_url") or "").rstrip("/")
    api_key = str(ai_config.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("未配置 AI API Key")
    if not base_url:
        raise ValueError("未配置 AI Base URL")
    if not model:
        raise ValueError("未配置 AI 模型名称")

    prompt_content = build_skill_task_prompt_content(task_payload, docs)
    request_summary = summarize_skill_task_request(task_payload, prompt_content)
    supports_image_content = provider_supports_image_content(provider, model)
    user_content = prompt_content if supports_image_content else flatten_prompt_content_to_text(prompt_content, provider, model)
    request_payload = {
        "model": model,
        "temperature": 1,
        "messages": [
            {
                "role": "system",
                "content": "你是资深测试分析师。请严格遵循输入中的技能文档和规范文档，只输出最终 Markdown 大纲，不要输出解释、分析过程、注释或代码块标记。",
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
    }
    loggable_payload = build_loggable_kimi_payload(request_payload)
    logger.info(f"functional skill task ai provider={provider}, model={model}, base_url={base_url}")
    logger.info(
        f"functional skill task ai request summary provider={provider}, model={model}, "
        f"summary={json.dumps(request_summary, ensure_ascii=False)}"
    )
    logger.info(
        f"functional skill task ai capability provider={provider}, model={model}, "
        f"supports_image_content={supports_image_content}"
    )
    logger.info(
        f"functional skill task ai request payload provider={provider}, model={model}, "
        f"payload={json.dumps(loggable_payload, ensure_ascii=False)}"
    )
    started_at = time.perf_counter()
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=600,
        )
    except requests.Timeout as exc:
        elapsed = round(time.perf_counter() - started_at, 2)
        logger.warning(
            f"functional skill task ai timeout provider={provider}, model={model}, elapsed={elapsed}s, "
            f"summary={json.dumps(request_summary, ensure_ascii=False)}"
        )
        raise ValueError(f"AI模型({provider}/{model})请求超时({elapsed}s)，请减少图片数量或缩小需求内容后重试") from exc
    except requests.RequestException as exc:
        elapsed = round(time.perf_counter() - started_at, 2)
        logger.error(
            f"functional skill task ai request failed provider={provider}, model={model}, elapsed={elapsed}s, "
            f"summary={json.dumps(request_summary, ensure_ascii=False)}, error={exc}"
        )
        raise ValueError(f"AI模型({provider}/{model})请求失败({elapsed}s): {exc}") from exc
    elapsed = round(time.perf_counter() - started_at, 2)
    logger.info(
        f"functional skill task ai response provider={provider}, model={model}, status={response.status_code}, "
        f"elapsed={elapsed}s, body_preview={preview_text(response.text, 1000)}"
    )
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise build_ai_error(provider, model, response.status_code, detail)
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError(f"AI模型({provider}/{model})未返回可用结果")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        text_parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        content = "\n".join(text_parts)
    if not isinstance(content, str):
        raise ValueError(f"AI模型({provider}/{model})返回内容格式不支持")
    return provider, model, extract_model_result_content(content)


async def generate_with_fallback(task_payload, docs):
    ai_config = await GConfigDao.get_active_ai_model_config()
    loop = asyncio.get_running_loop()
    provider = str(ai_config.get("provider") or "").strip() or "unknown"
    model = str(ai_config.get("model") or "").strip() or "unknown"
    logger.info(
        f"functional skill task ai attempt provider={provider}, model={model}, "
        f"base_url={ai_config.get('base_url')}"
    )
    return await loop.run_in_executor(
        None,
        call_active_model_generate,
        task_payload,
        docs,
        ai_config,
    )


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def write_text(path, content):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)


def file_path_to_static_url(file_path):
    if not file_path:
        return ""
    normalized = os.path.normpath(file_path)
    statics_root = os.path.normpath("statics")
    try:
        relative_path = os.path.relpath(normalized, statics_root)
    except ValueError:
        return ""
    if relative_path.startswith(".."):
        return ""
    return f"/statics/{relative_path.replace(os.sep, '/')}"


def append_task_log(task, stage, stage_text):
    if not stage_text:
        return
    try:
        logs = json.loads(task.task_logs or "[]")
        if not isinstance(logs, list):
            logs = []
    except Exception:
        logs = []
    logs.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": stage,
        "text": stage_text,
    })
    task.task_logs = json.dumps(logs[-50:], ensure_ascii=False)


def save_data_url_image(image_value, output_path):
    matched = re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", image_value)
    if not matched:
        return False
    ensure_dir(os.path.dirname(output_path))
    with open(output_path, "wb") as file:
        file.write(base64.b64decode(matched.group(2)))
    return True


def has_requirement_items(items):
    for item in items or []:
        title = str(item.get("title") or "").strip()
        text_value = str(item.get("text") or "").strip()
        images = item.get("images") or []
        design_links = item.get("design_links") or []
        if title or text_value or images or design_links:
            return True
    return False


def export_runtime_materials(task_dir, requirement_text, instruction_text, images, docs, requirement_items=None):
    requirement_dir = os.path.join(task_dir, "需求文档")
    standard_dir = os.path.join(task_dir, "规范与标准")
    skills_dir = os.path.join(task_dir, "skills")

    overview_lines = ["# 需求总览", ""]
    if requirement_text:
        overview_lines.extend([requirement_text, ""])
    else:
        overview_lines.extend(["未提供需求总览文本", ""])

    if requirement_items:
        overview_lines.extend(["## 需求组索引", ""])
        for index, item in enumerate(requirement_items, start=1):
            title = str(item.get("title") or "").strip() or f"需求组{index}"
            overview_lines.append(f"- 需求组{index}: {title}")
        overview_lines.append("")

    write_text(os.path.join(requirement_dir, "需求总览.md"), "\n".join(overview_lines))

    if instruction_text:
        write_text(os.path.join(task_dir, "生成提示词.md"), instruction_text)

    if requirement_items:
        for index, item in enumerate(requirement_items, start=1):
            title = str(item.get("title") or "").strip() or f"需求组{index}"
            text_value = str(item.get("text") or "").strip()
            design_links = item.get("design_links") or []
            group_images = item.get("images") or []
            image_names = []

            for image_index, image_value in enumerate(group_images, start=1):
                image_prefix = os.path.join(requirement_dir, f"需求组_{index}_图片_{image_index}")
                if str(image_value or "").startswith("data:image"):
                    save_data_url_image(image_value, f"{image_prefix}.png")
                    image_names.append(f"需求组_{index}_图片_{image_index}.png")
                else:
                    write_text(f"{image_prefix}.txt", str(image_value or ""))
                    image_names.append(f"需求组_{index}_图片_{image_index}.txt")

            lines = [
                f"# {title}",
                "",
                "## 需求说明",
                "",
                text_value or "无",
                "",
                "## 关联设计链接",
                "",
            ]
            if design_links:
                lines.extend([f"- {link}" for link in design_links])
            else:
                lines.append("- 无")

            lines.extend(["", "## 关联图片", ""])
            if image_names:
                lines.extend([f"- {name}" for name in image_names])
            else:
                lines.append("- 无")

            write_text(os.path.join(requirement_dir, f"需求组_{index}.md"), "\n".join(lines))
    else:
        write_text(
            os.path.join(requirement_dir, "需求文档详细说明文档.md"),
            requirement_text or "# 需求文档\n\n未提供需求正文",
        )
        for index, image_value in enumerate(images or [], start=1):
            image_prefix = os.path.join(requirement_dir, f"需求截图_{index}")
            if str(image_value or "").startswith("data:image"):
                save_data_url_image(image_value, f"{image_prefix}.png")
            else:
                write_text(f"{image_prefix}.txt", str(image_value or ""))

    for doc in docs:
        safe_title = re.sub(r'[\\/:*?"<>|]+', "_", doc["title"]).strip() or f"doc_{doc['id']}"
        if doc.get("doc_type") == "skill_md":
            target_dir = skills_dir
            target_name = f"用户技能_{safe_title}.md"
        else:
            target_dir = standard_dir
            target_name = f"用户文档_{safe_title}.md"
        write_text(os.path.join(target_dir, target_name), doc["content"])

    builtin_files = [
        (os.path.join("1_规范与标准", "测试用例编写规范.md"), os.path.join(standard_dir, "测试用例编写规范.md")),
        (os.path.join("2_模板", "测试用例模板.md"), os.path.join(standard_dir, "测试用例模板.md")),
        (os.path.join("3_skills", "create_case.md"), os.path.join(skills_dir, "create_case.md")),
        (os.path.join("3_skills", "evaluate_case.md"), os.path.join(skills_dir, "evaluate_case.md")),
    ]
    for relative_path, target_path in builtin_files:
        source_path = os.path.join(AI_CASE_CREATOR_ROOT, relative_path)
        if os.path.exists(source_path):
            ensure_dir(os.path.dirname(target_path))
            shutil.copy2(source_path, target_path)


def build_extra_context(docs):
    parts = []
    builtin = get_builtin_context_text()
    if builtin:
        parts.append(builtin)
    for doc in docs:
        parts.append(f"用户文档[{doc['doc_type']}]-{doc['title']}：\n{doc['content']}")
    return "\n\n".join(parts)


async def load_skill_docs(session, user_id, doc_ids):
    if not doc_ids:
        return []
    result = await session.execute(
        select(PityFunctionalCaseSkillDoc).where(
            PityFunctionalCaseSkillDoc.id.in_(doc_ids),
            PityFunctionalCaseSkillDoc.deleted_at == 0,
        )
    )
    records = result.scalars().all()
    visible_docs = []
    for item in records:
        if item.create_user == user_id or int(item.is_shared or 0) == 1:
            visible_docs.append({
                "id": item.id,
                "title": item.title,
                "doc_type": item.doc_type,
                "content": item.content,
            })
    return visible_docs


def load_task_request_payload(task, task_dir):
    payload = {}
    try:
        payload = json.loads(task.input_payload or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    request_payload_path = str(payload.get("request_payload_path") or "").strip()
    if request_payload_path and os.path.exists(request_payload_path):
        try:
            file_payload = json.loads(read_text_if_exists(request_payload_path) or "{}")
            if isinstance(file_payload, dict):
                payload.update(file_payload)
        except Exception:
            pass
    payload.setdefault("project_id", task.project_id)
    payload.setdefault("title", task.title)
    payload.setdefault("requirement_text", task.requirement_text or "")
    payload.setdefault("instruction_text", task.instruction_text or "")
    payload.setdefault("images", [])
    payload.setdefault("requirement_items", [])
    try:
        selected_doc_ids = json.loads(task.selected_doc_ids or "[]")
        if isinstance(selected_doc_ids, list):
            payload.setdefault("doc_ids", selected_doc_ids)
    except Exception:
        pass
    payload.setdefault("doc_ids", [])
    return payload


async def execute_skill_task(task_id):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseSkillTask).where(
                PityFunctionalCaseSkillTask.id == task_id,
                PityFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.scalars().first()
        if task is None:
            return
        user_id = task.create_user
        task_dir = task.runtime_dir or os.path.join(SKILL_TASK_DIR, str(user_id), str(task.id))
        if not task.runtime_dir:
            task.runtime_dir = task_dir
            await session.commit()
        task_payload = load_task_request_payload(task, task_dir)
        docs = await load_skill_docs(session, user_id, task_payload.get("doc_ids") or [])

    review_provider = ""
    review_rounds = 0
    try:
        await update_task_state(task_id, user_id, status="running", stage="prepare", stage_text="正在组装需求目录与技能材料", progress=10)
        export_runtime_materials(
            task_dir,
            task_payload.get("requirement_text"),
            task_payload.get("instruction_text"),
            task_payload.get("images"),
            docs,
            task_payload.get("requirement_items") or [],
        )

        ai_config = await GConfigDao.get_active_ai_model_config()
        review_provider = ai_config.get("provider") or ""
        logger.info(
            f"functional skill task execute task_id={task_id}, generator=model-api, "
            f"system_active_ai_provider={ai_config.get('provider')}, "
            f"system_active_ai_model={ai_config.get('model')}, "
            f"system_active_ai_base_url={ai_config.get('base_url')}"
        )

        await update_task_state(
            task_id,
            user_id,
            stage="generate",
            stage_text="正在调用启用中的模型配置生成测试用例",
            progress=35,
            review_provider=review_provider,
            review_rounds=review_rounds,
        )
        review_provider, model_name, ai_payload = await generate_with_fallback(task_payload, docs)

        await update_task_state(
            task_id,
            user_id,
            stage="convert",
            stage_text="正在解析模型结果并转换画布数据",
            progress=80,
            review_provider=review_provider,
            review_rounds=review_rounds,
        )
        case_title, case_data = normalize_ai_case_data(ai_payload, task_payload.get("title") or "功能用例")
        stats = analyze_case_data(case_data)
        result_json_path = os.path.join(task_dir, "generated_case.json")
        result_md_path = os.path.join(task_dir, "generated_case.md")
        write_text(result_json_path, json.dumps(case_data, ensure_ascii=False, indent=2))
        write_text(result_md_path, str(ai_payload or ""))

        await update_task_state(
            task_id,
            user_id,
            status="success",
            stage="success",
            stage_text="模型生成完成，结果已可回填画布",
            progress=100,
            result_title=case_title,
            result_case_count=int(stats["case_count"] or 0),
            result_file_path=result_json_path,
            result_md_path=result_md_path,
            result_xmind_path="",
            result_payload=json.dumps({
                "title": case_title,
                "data": case_data,
                "case_count": int(stats["case_count"] or 0),
                "case_num": int(stats["case_count"] or 0),
                "provider": review_provider,
                "model": model_name,
            }, ensure_ascii=False),
            error_message="",
            finished_at=int(time.time()),
            review_provider=review_provider,
            review_rounds=review_rounds,
        )
    except Exception as exc:
        logger.error(f"functional skill task failed: {exc}")
        await update_task_state(
            task_id,
            user_id,
            status="failed",
            stage="failed",
            stage_text="生成失败",
            progress=100,
            error_message=str(exc),
            finished_at=int(time.time()),
            review_provider=review_provider,
            review_rounds=review_rounds,
        )

async def try_finalize_task_from_runtime(task_id, user_id):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseSkillTask).where(
                PityFunctionalCaseSkillTask.id == task_id,
                PityFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.scalars().first()
        if task is None:
            return None
        if task.create_user != user_id:
            return task
        if task.status in ("success", "failed"):
            return task
        task_dir = task.runtime_dir or os.path.join(SKILL_TASK_DIR, str(task.create_user), str(task.id))
        result_json_path = task.result_file_path or os.path.join(task_dir, "generated_case.json")
        if not os.path.exists(result_json_path):
            return task
        result_text = read_text_if_exists(result_json_path)
        if not result_text.strip():
            return task
        if time.time() - os.path.getmtime(result_json_path) < 3:
            return task
    try:
        result_payload = json.loads(result_text)
        case_title, case_data = normalize_ai_case_data(result_payload, task.title or "功能用例")
        stats = analyze_case_data(case_data)
        result_md_path = task.result_md_path or os.path.join(task_dir, "generated_case.md")
        updated_task = await update_task_state(
            task_id,
            user_id,
            status="success",
            stage="success",
            stage_text="检测到结果文件已生成，已自动完成画布回填",
            progress=100,
            result_title=case_title,
            result_case_count=int(stats["case_count"] or 0),
            result_file_path=result_json_path,
            result_md_path=result_md_path if os.path.exists(result_md_path) else "",
            result_xmind_path="",
            result_payload=json.dumps({
                "title": case_title,
                "data": case_data,
                "case_count": int(stats["case_count"] or 0),
                "case_num": int(stats["case_count"] or 0),
            }, ensure_ascii=False),
            error_message="",
            finished_at=int(time.time()),
            review_provider=task.review_provider or "",
            review_rounds=int(task.review_rounds or 0),
        )
        return updated_task
    except Exception as exc:
        logger.warning(f"fallback finalize skipped: {exc}")
        return task


def build_task_result(task):
    payload = {}
    logs = []
    if task.result_payload:
        try:
            payload = json.loads(task.result_payload)
        except Exception:
            payload = {}
    if task.task_logs:
        try:
            logs = json.loads(task.task_logs)
            if not isinstance(logs, list):
                logs = []
        except Exception:
            logs = []
    payload.update({
        "task_id": task.id,
        "status": task.status,
        "stage": task.stage,
        "stage_text": task.stage_text,
        "progress": task.progress,
        "review_provider": task.review_provider,
        "review_rounds": task.review_rounds,
        "error_message": task.error_message,
        "result_md_path": task.result_md_path,
        "result_xmind_path": task.result_xmind_path,
        "result_md_url": file_path_to_static_url(task.result_md_path),
        "result_xmind_url": file_path_to_static_url(task.result_xmind_path),
        "task_logs": logs,
    })
    return payload


async def update_task_state(task_id, user_id=None, **fields):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseSkillTask).where(
                PityFunctionalCaseSkillTask.id == task_id,
                PityFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.scalars().first()
        if task is None:
            return None
        stage = fields.get("stage", task.stage)
        stage_text = fields.get("stage_text")
        for key, value in fields.items():
            setattr(task, key, value)
        append_task_log(task, stage, stage_text)
        if user_id is not None:
            task.update_user = user_id
        task.updated_at = datetime.now()
        await session.commit()
        await session.refresh(task)
        return task


@router.get("/skill-doc/list")
async def list_skill_docs(title: str = "", user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseSkillDoc).where(PityFunctionalCaseSkillDoc.deleted_at == 0)
        )
        docs = result.scalars().all()
        user_ids = list({item.create_user for item in docs})
        user_result = await session.execute(select(User).where(User.id.in_(user_ids))) if user_ids else None
        user_name_map = {item.id: pick_user_name(item) for item in user_result.scalars().all()} if user_result else {}
        data = []
        for item in docs:
            if item.create_user != user_info["id"] and int(item.is_shared or 0) != 1:
                continue
            if title and title not in (item.title or "") and title not in (item.description or ""):
                continue
            row = serialize_model(item)
            row["owner_name"] = user_name_map.get(item.create_user, "")
            data.append(row)
    data.sort(key=lambda item: (0 if item["create_user"] == user_info["id"] else 1, -(item["id"] or 0)))
    return PityResponse.success(data)


@router.post("/skill-doc/insert")
async def insert_skill_doc(form: FunctionalCaseSkillDocForm, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        duplicate_result = await session.execute(
            select(PityFunctionalCaseSkillDoc).where(
                PityFunctionalCaseSkillDoc.deleted_at == 0,
                PityFunctionalCaseSkillDoc.create_user == user_info["id"],
                PityFunctionalCaseSkillDoc.title == form.title,
            )
        )
        if duplicate_result.scalars().first() is not None:
            return PityResponse.failed("文档名称已存在，请更换后重试")
        model = PityFunctionalCaseSkillDoc(
            title=form.title,
            description=form.description,
            doc_type=form.doc_type,
            content=form.content,
            is_shared=form.is_shared,
            user=user_info["id"],
        )
        session.add(model)
        await session.flush()
        await PityOperationDao.insert_log(session, user_info["id"], OperationType.INSERT, model, key=model.id)
        await session.commit()
        await session.refresh(model)
    data = serialize_model(model)
    data["owner_name"] = (user_info.get("name") or user_info.get("username") or "").strip()
    return PityResponse.success(data)


@router.post("/skill-doc/update")
async def update_skill_doc(form: FunctionalCaseSkillDocForm, user_info=Depends(Permission())):
    if not form.id:
        return PityResponse.failed("id不能为空")
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseSkillDoc).where(
                PityFunctionalCaseSkillDoc.id == form.id,
                PityFunctionalCaseSkillDoc.deleted_at == 0,
            )
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("文档不存在")
        if model.create_user != user_info["id"]:
            return PityResponse.failed("只能编辑自己的文档")
        duplicate_result = await session.execute(
            select(PityFunctionalCaseSkillDoc).where(
                PityFunctionalCaseSkillDoc.deleted_at == 0,
                PityFunctionalCaseSkillDoc.create_user == user_info["id"],
                PityFunctionalCaseSkillDoc.title == form.title,
                PityFunctionalCaseSkillDoc.id != form.id,
            )
        )
        if duplicate_result.scalars().first() is not None:
            return PityResponse.failed("文档名称已存在，请更换后重试")
        old = deepcopy(model)
        model.title = form.title
        model.description = form.description
        model.doc_type = form.doc_type
        model.content = form.content
        model.is_shared = form.is_shared
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
            changed=["title", "description", "doc_type", "content", "is_shared"],
        )
        await session.commit()
        await session.refresh(model)
    return PityResponse.success(serialize_model(model))


@router.get("/skill-doc/delete")
async def delete_skill_doc(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseSkillDoc).where(
                PityFunctionalCaseSkillDoc.id == id,
                PityFunctionalCaseSkillDoc.deleted_at == 0,
            )
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("文档不存在")
        if model.create_user != user_info["id"]:
            return PityResponse.failed("只能删除自己的文档")
        old = deepcopy(model)
        model.deleted_at = int(datetime.now().timestamp())
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.flush()
        await PityOperationDao.insert_log(session, user_info["id"], OperationType.DELETE, old, key=model.id)
        await session.commit()
    return PityResponse.success()


@router.post("/skill-task/create")
async def create_skill_task(form: FunctionalCaseSkillTaskForm, user_info=Depends(Permission())):
    requirement_items = [item.dict() for item in (form.requirement_items or [])]
    if not form.requirement_text and not form.instruction_text and not form.images and not has_requirement_items(requirement_items):
        return PityResponse.failed("请至少提供需求说明、需求图片、设计链接或生成提示词")
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        docs = await load_skill_docs(session, user_info["id"], form.doc_ids)
        task = PityFunctionalCaseSkillTask(
            project_id=form.project_id,
            title=form.title,
            user=user_info["id"],
            requirement_text=form.requirement_text,
            instruction_text=form.instruction_text,
            selected_doc_ids=json.dumps(form.doc_ids, ensure_ascii=False),
        )
        task.status = "queued"
        task.stage = "queued"
        task.stage_text = "任务已创建，等待后台执行"
        task.progress = 0
        task.task_logs = json.dumps([{
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stage": "queued",
            "text": "任务已创建，等待后台执行",
        }], ensure_ascii=False)
        task.input_payload = json.dumps({
            "project_id": form.project_id,
            "title": form.title,
            "doc_ids": form.doc_ids,
            "visible_doc_count": len(docs),
            "image_count": len(form.images or []),
            "requirement_group_count": len(requirement_items),
        }, ensure_ascii=False)
        session.add(task)
        await session.flush()
        await PityOperationDao.insert_log(session, user_info["id"], OperationType.INSERT, task, key=task.id)
        await session.commit()
        await session.refresh(task)

        task_dir = os.path.join(SKILL_TASK_DIR, str(user_info["id"]), str(task.id))
        ensure_dir(task_dir)
        request_payload_path = os.path.join(task_dir, "request_payload.json")
        write_text(request_payload_path, json.dumps({
            "project_id": form.project_id,
            "title": form.title,
            "requirement_text": form.requirement_text,
            "instruction_text": form.instruction_text,
            "images": form.images,
            "requirement_items": requirement_items,
            "doc_ids": form.doc_ids,
            "visible_doc_count": len(docs),
        }, ensure_ascii=False))
        task.runtime_dir = task_dir
        task.input_payload = json.dumps({
            "project_id": form.project_id,
            "title": form.title,
            "doc_ids": form.doc_ids,
            "visible_doc_count": len(docs),
            "image_count": len(form.images or []),
            "requirement_group_count": len(requirement_items),
            "request_payload_path": request_payload_path,
        }, ensure_ascii=False)
        await session.commit()
    try:
        ai_config = await GConfigDao.get_active_ai_model_config()
        logger.info(
            f"functional skill task create task_id={task.id}, generator=model-api, "
            f"system_active_ai_provider={ai_config.get('provider')}, system_active_ai_model={ai_config.get('model')}, system_active_ai_base_url={ai_config.get('base_url')}"
        )
    except Exception as config_exc:
        logger.warning(f"functional skill task create task_id={task.id}, load ai config failed: {config_exc}")
    asyncio.create_task(execute_skill_task(task.id))
    return PityResponse.success({"task_id": task.id, "status": task.status, "stage": task.stage, "progress": task.progress})


@router.get("/skill-task/status")
async def query_skill_task_status(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseSkillTask).where(
                PityFunctionalCaseSkillTask.id == id,
                PityFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.scalars().first()
        if task is None:
            return PityResponse.failed("任务不存在")
        if task.create_user != user_info["id"]:
            return PityResponse.failed("只能查看自己的任务")
    task = await try_finalize_task_from_runtime(id, user_info["id"]) or task
    return PityResponse.success(build_task_result(task))


