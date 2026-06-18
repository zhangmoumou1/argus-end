import base64
import asyncio
import gzip
import json
import os
import re
import shutil
import time
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime

import requests
from app.crud.config.GConfigDao import GConfigDao
from app.crud.operation.ArgusOperationDao import ArgusOperationDao
from app.core.platform_audit import PlatformAuditService
from app.core.platform_task import PlatformTaskService
from app.enums.OperationEnum import OperationType
from app.enums.platform_task import PlatformTaskStatus, PlatformTaskType
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.handler.fatcory import ArgusResponse
from app.models import async_session
from app.models.functional_case import ArgusFunctionalCaseSkillDoc, ArgusFunctionalCaseSkillTask
from app.models.operation_log import ArgusOperationLog
from app.models.user import User
from app.routers import Permission
from app.schema.functional_case import FunctionalCaseSkillDocForm, FunctionalCaseSkillTaskForm
from app.utils.logger import Log
from config import Config

router = APIRouter(prefix="/functional-case")
logger = Log("functional_case_skill")

AI_CASE_CREATOR_ROOT = r"C:\Users\bytde\Desktop\ai_case_creator"
AI_TEXT_LIMIT = 12000
AI_INSTRUCTION_LIMIT = 6000
AI_IMAGE_LIMIT = 6
AI_IMAGE_DATA_URL_LIMIT = 2_000_000
SKILL_TASK_SCHEMA_READY = False
COMPRESSED_PAYLOAD_PREFIX = "gz:"


def serialize_model(model):
    return ArgusResponse.model_to_dict(model)


async def ensure_skill_task_schema(session):
    global SKILL_TASK_SCHEMA_READY
    if SKILL_TASK_SCHEMA_READY:
        return
    if not Config.RUNTIME_SCHEMA_MIGRATION_ENABLED:
        SKILL_TASK_SCHEMA_READY = True
        return
    try:
        for column_name, sql in [
            ("description", "ALTER TABLE argus_functional_case_skill_doc ADD COLUMN description VARCHAR(500) NULL COMMENT '文档描述'"),
            ("case_file_id", "ALTER TABLE argus_functional_case_skill_task ADD COLUMN case_file_id INT NOT NULL DEFAULT 0 COMMENT '目标功能用例文件ID'"),
            ("input_payload", "ALTER TABLE argus_functional_case_skill_task ADD COLUMN input_payload TEXT NULL COMMENT '任务输入'"),
            ("stage", "ALTER TABLE argus_functional_case_skill_task ADD COLUMN stage VARCHAR(64) NOT NULL DEFAULT 'queued' COMMENT '执行阶段'"),
            ("stage_text", "ALTER TABLE argus_functional_case_skill_task ADD COLUMN stage_text VARCHAR(255) NULL COMMENT '阶段说明'"),
            ("progress", "ALTER TABLE argus_functional_case_skill_task ADD COLUMN progress INT NOT NULL DEFAULT 0 COMMENT '进度'"),
            ("review_provider", "ALTER TABLE argus_functional_case_skill_task ADD COLUMN review_provider VARCHAR(32) NULL COMMENT '评审模型'"),
            ("review_rounds", "ALTER TABLE argus_functional_case_skill_task ADD COLUMN review_rounds INT NOT NULL DEFAULT 0 COMMENT '评审轮次'"),
            ("task_logs", "ALTER TABLE argus_functional_case_skill_task ADD COLUMN task_logs TEXT NULL COMMENT '任务日志'"),
        ]:
            result = await session.execute(text(f"SHOW COLUMNS FROM {'argus_functional_case_skill_doc' if column_name == 'description' else 'argus_functional_case_skill_task'} LIKE '{column_name}'"))
            if result.first() is None:
                await session.execute(text(sql))

        for alter_sql in [
            "ALTER TABLE argus_functional_case_skill_doc MODIFY COLUMN content LONGTEXT NOT NULL COMMENT 'Markdown内容'",
            "ALTER TABLE argus_functional_case_skill_task MODIFY COLUMN requirement_text LONGTEXT NULL COMMENT '需求文本'",
            "ALTER TABLE argus_functional_case_skill_task MODIFY COLUMN instruction_text LONGTEXT NULL COMMENT '额外提示'",
            "ALTER TABLE argus_functional_case_skill_task MODIFY COLUMN selected_doc_ids LONGTEXT NULL COMMENT '选中文档ID'",
            "ALTER TABLE argus_functional_case_skill_task MODIFY COLUMN input_payload LONGTEXT NULL COMMENT '任务输入'",
            "ALTER TABLE argus_functional_case_skill_task MODIFY COLUMN task_logs LONGTEXT NULL COMMENT '任务日志'",
            "ALTER TABLE argus_functional_case_skill_task MODIFY COLUMN result_payload LONGTEXT NULL COMMENT '结果JSON'",
            "ALTER TABLE argus_functional_case_skill_task MODIFY COLUMN error_message LONGTEXT NULL COMMENT '失败原因'",
        ]:
            try:
                await session.execute(text(alter_sql))
            except Exception:
                pass
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


def encode_task_input_payload(payload):
    try:
        raw_text = json.dumps(payload or {}, ensure_ascii=False)
    except Exception:
        raw_text = "{}"
    try:
        compressed = gzip.compress(raw_text.encode("utf-8"))
        encoded = base64.b64encode(compressed).decode("ascii")
        if len(encoded) < len(raw_text):
            return f"{COMPRESSED_PAYLOAD_PREFIX}{encoded}"
    except Exception:
        pass
    return raw_text


def decode_task_input_payload(value):
    text_value = str(value or "")
    if not text_value:
        return {}
    try:
        if text_value.startswith(COMPRESSED_PAYLOAD_PREFIX):
            compressed = base64.b64decode(text_value[len(COMPRESSED_PAYLOAD_PREFIX):])
            text_value = gzip.decompress(compressed).decode("utf-8")
        payload = json.loads(text_value)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


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


def get_builtin_doc_text(relative_path):
    return read_text_if_exists(os.path.join(AI_CASE_CREATOR_ROOT, relative_path))


def dedupe_int_list(values):
    result = []
    seen = set()
    for item in values or []:
        try:
            value = int(item)
        except Exception:
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_task_doc_groups(task_payload):
    groups = {
        "rule_doc_ids": dedupe_int_list(task_payload.get("rule_doc_ids") or []),
        "reference_doc_ids": dedupe_int_list(task_payload.get("reference_doc_ids") or []),
        "generate_doc_ids": dedupe_int_list(task_payload.get("generate_doc_ids") or []),
        "review_doc_ids": dedupe_int_list(task_payload.get("review_doc_ids") or []),
    }
    legacy_doc_ids = dedupe_int_list(task_payload.get("doc_ids") or [])
    if legacy_doc_ids and not any(groups.values()):
        groups["rule_doc_ids"] = legacy_doc_ids
    all_doc_ids = []
    seen = set()
    for key in ("rule_doc_ids", "reference_doc_ids", "generate_doc_ids", "review_doc_ids"):
        for item in groups[key]:
            if item in seen:
                continue
            seen.add(item)
            all_doc_ids.append(item)
    groups["all_doc_ids"] = all_doc_ids
    return groups


def group_visible_docs_by_usage(docs, doc_groups):
    doc_map = {int(item["id"]): item for item in (docs or []) if int(item.get("id") or 0) > 0}
    result = {}
    for key in ("rule_doc_ids", "reference_doc_ids", "generate_doc_ids", "review_doc_ids"):
        result[key] = [doc_map[item] for item in (doc_groups.get(key) or []) if item in doc_map]
    result["rule_docs"] = result["rule_doc_ids"]
    result["reference_docs"] = result["reference_doc_ids"]
    result["generate_docs"] = result["generate_doc_ids"]
    result["review_docs"] = result["review_doc_ids"]
    result["all_doc_ids"] = list(doc_groups.get("all_doc_ids") or [])
    return result


def render_docs_as_text(title, docs, limit=24000):
    visible_docs = [item for item in (docs or []) if isinstance(item, dict)]
    if not visible_docs:
        return ""
    blocks = []
    for index, doc in enumerate(visible_docs, start=1):
        blocks.append(
            f"{title}{index}：{doc.get('title') or '未命名文档'}\n"
            f"{truncate_ai_text(doc.get('content') or '', limit)}"
        )
    return "\n\n".join(blocks)


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


def summarize_skill_task_request(task_payload, messages):
    text_blocks = []
    sent_images = []
    for message_item in messages or []:
        content = message_item.get("content") if isinstance(message_item, dict) else None
        if isinstance(content, str):
            text_blocks.append(content)
            continue
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text_blocks.append(item.get("text") or "")
            elif item.get("type") == "image_url":
                sent_images.append(item.get("image_url", {}).get("url"))
    doc_groups = normalize_task_doc_groups(task_payload)
    return {
        "project_id": task_payload.get("project_id"),
        "title": task_payload.get("title"),
        "requirement_length": len(str(task_payload.get("requirement_text") or "")),
        "instruction_length": len(str(task_payload.get("instruction_text") or "")),
        "generate_instruction_length": len(str(task_payload.get("generate_instruction_text") or "")),
        "review_instruction_length": len(str(task_payload.get("review_instruction_text") or "")),
        "requirement_group_count": len(task_payload.get("requirement_items") or []),
        "rule_doc_count": len(doc_groups.get("rule_doc_ids") or []),
        "reference_doc_count": len(doc_groups.get("reference_doc_ids") or []),
        "generate_doc_count": len(doc_groups.get("generate_doc_ids") or []),
        "review_doc_count": len(doc_groups.get("review_doc_ids") or []),
        "doc_count": len(doc_groups.get("all_doc_ids") or []),
        "message_count": len(messages or []),
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


def build_skill_task_system_prompt(task_payload, grouped_docs):
    title = str(task_payload.get("title") or "功能用例").strip() or "功能用例"
    rule_doc_text = render_docs_as_text("规则文档", grouped_docs.get("rule_docs"))
    blocks = [
        "你是资深测试分析师，负责根据输入的需求材料生成功能测试用例脑图。",
        f"当前用例标题：{title}",
    ]
    if rule_doc_text:
        blocks.append(f"用户选择的规则文档：\n{truncate_ai_text(rule_doc_text, 24000)}")
    return "\n\n".join(blocks)


def build_skill_task_assistant_prompt(task_payload, grouped_docs):
    generate_instruction_text = truncate_ai_text(task_payload.get("generate_instruction_text"), AI_INSTRUCTION_LIMIT)
    review_instruction_text = truncate_ai_text(task_payload.get("review_instruction_text"), AI_INSTRUCTION_LIMIT)
    legacy_instruction_text = truncate_ai_text(task_payload.get("instruction_text"), AI_INSTRUCTION_LIMIT)
    generate_doc_text = render_docs_as_text("生成要求文档", grouped_docs.get("generate_docs"))
    review_doc_text = render_docs_as_text("审查要求文档", grouped_docs.get("review_docs"))
    blocks = []
    if generate_doc_text:
        blocks.append(f"用户选择的生成要求文档：\n{truncate_ai_text(generate_doc_text, 24000)}")
    if generate_instruction_text:
        blocks.append(f"生成补充说明：\n{generate_instruction_text}")
    if review_doc_text:
        blocks.append(f"用户选择的审查要求文档：\n{truncate_ai_text(review_doc_text, 24000)}")
    if review_instruction_text:
        blocks.append(f"审查补充说明：\n{review_instruction_text}")
    if legacy_instruction_text and not (generate_instruction_text or review_instruction_text):
        blocks.append(f"兼容旧版额外说明：\n{legacy_instruction_text}")
    return "\n\n".join(blocks)


def build_skill_task_user_content(task_payload, grouped_docs):
    requirement_text = truncate_ai_text(task_payload.get("requirement_text"), AI_TEXT_LIMIT)
    requirement_items = task_payload.get("requirement_items") or []
    content = []
    remaining_image_slots = AI_IMAGE_LIMIT

    intro_lines = ["以下是本次生成的真实需求材料，请据此输出结果。"]
    if requirement_text:
        intro_lines.append(f"需求总述：\n{requirement_text}")
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


def build_skill_task_messages(task_payload, docs):
    doc_groups = normalize_task_doc_groups(task_payload)
    grouped_docs = group_visible_docs_by_usage(docs, doc_groups)
    return [
        {"role": "system", "content": build_skill_task_system_prompt(task_payload, grouped_docs)},
        {"role": "assistant", "content": build_skill_task_assistant_prompt(task_payload, grouped_docs)},
        {"role": "user", "content": build_skill_task_user_content(task_payload, grouped_docs)},
    ]


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
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"[（(]\s*P\s*[0-9]\s*[）)]", "", text, flags=re.IGNORECASE)
    return text.strip() or "未命名节点"


def _label_outline_level(text_value):
    normalized = str(text_value or "").strip()
    normalized = re.sub(r"^[\-\*\+\d\.\s#]+", "", normalized).strip()
    normalized = normalized.replace("：", ":")
    label = normalized.split(":", 1)[0].strip().lower()
    mapping = {
        "模块": 0,
        "功能": 1,
        "子功能": 2,
        "字段": 3,
        "用例名称": 4,
        "预期": 5,
    }
    return mapping.get(label)


def _load_json_from_text(text_value):
    normalized_text = normalize_model_text(strip_fenced_markdown(text_value))
    if not normalized_text:
        return None
    try:
        return json.loads(normalized_text)
    except Exception:
        return None


def _looks_like_case_json_payload(payload):
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("root"), dict):
        return True
    if isinstance(payload.get("children"), list):
        return True
    if isinstance(payload.get("data"), dict) and isinstance(payload.get("children"), list):
        return True
    if isinstance(payload.get("data"), dict) and payload.get("data", {}).get("text"):
        return True
    return False


def parse_markdown_outline_to_case_data(text_value, fallback_title):
    normalized_text = normalize_model_text(strip_fenced_markdown(text_value)).replace("\r\n", "\n")
    lines = []
    for raw_line in normalized_text.split("\n"):
        if not str(raw_line or "").strip():
            continue
        normalized_line = raw_line.replace("\t", "  ")
        indent = re.match(r"^\s*", normalized_line).group(0)
        level = None
        if re.match(r"^\s*([\-\*\+]|\d+\.)\s+", normalized_line):
            level = max(0, len(indent) // 2)
        elif re.match(r"^\s*#{1,6}\s+", normalized_line):
            heading = re.match(r"^\s*(#{1,6})\s+", normalized_line)
            level = max(0, len(heading.group(1)) - 1) if heading else 0
        else:
            labeled_level = _label_outline_level(normalized_line)
            if labeled_level is not None:
                level = labeled_level
        if level is None:
            continue
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
        json_payload = _load_json_from_text(payload)
        if _looks_like_case_json_payload(json_payload):
            return normalize_ai_case_data(json_payload, fallback_title)
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
        node_text = str(node_data.get("text") or "").strip() if isinstance(node_data, dict) else ""
        node_icons = node_data.get("icon") if isinstance(node_data, dict) else []
        icons = node_icons if isinstance(node_icons, list) else [node_icons] if node_icons else []
        has_priority_icon = any(isinstance(icon, str) and icon.startswith("priority_") for icon in icons)
        has_priority_text = re.search(r"(^|[\s_（(-])P[0-2]([\s_）)-]|$)", node_text, re.IGNORECASE) is not None
        if has_priority_icon or has_priority_text:
            case_count += 1
        for child in node.get("children") or []:
            walk(child)

    walk(data)
    return {"case_count": case_count}


def build_ai_case_result(ai_payload, fallback_title):
    case_title, case_data = normalize_ai_case_data(ai_payload, fallback_title)
    stats = analyze_case_data(case_data)
    return case_title, case_data, stats


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

    messages = build_skill_task_messages(task_payload, docs)
    request_summary = summarize_skill_task_request(task_payload, messages)
    supports_image_content = provider_supports_image_content(provider, model)
    request_messages = json.loads(json.dumps(messages, ensure_ascii=False))
    if not supports_image_content:
        for message_item in request_messages:
            if message_item.get("role") == "user":
                message_item["content"] = flatten_prompt_content_to_text(message_item.get("content") or [], provider, model)
    request_payload = {
        "model": model,
        "temperature": 1,
        "messages": request_messages,
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
    ai_config = await GConfigDao.get_active_ai_model_config(task_payload.get("ai_model_id"))
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


async def convert_ai_payload_to_case_result(ai_payload, fallback_title):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        build_ai_case_result,
        ai_payload,
        fallback_title,
    )


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def write_text(path, content):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)


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
        select(ArgusFunctionalCaseSkillDoc).where(
            ArgusFunctionalCaseSkillDoc.id.in_(doc_ids),
            ArgusFunctionalCaseSkillDoc.deleted_at == 0,
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
    payload = decode_task_input_payload(task.input_payload)
    payload.setdefault("project_id", task.project_id)
    payload.setdefault("title", task.title)
    payload.setdefault("ai_model_id", "")
    payload.setdefault("requirement_text", task.requirement_text or "")
    payload.setdefault("instruction_text", task.instruction_text or "")
    payload.setdefault("generate_instruction_text", "")
    payload.setdefault("review_instruction_text", "")
    payload.setdefault("images", [])
    payload.setdefault("requirement_items", [])
    try:
        selected_doc_ids = json.loads(task.selected_doc_ids or "[]")
        if isinstance(selected_doc_ids, list):
            payload.setdefault("doc_ids", selected_doc_ids)
        elif isinstance(selected_doc_ids, dict):
            for key in ("doc_ids", "rule_doc_ids", "reference_doc_ids", "generate_doc_ids", "review_doc_ids", "all_doc_ids"):
                if key in selected_doc_ids and key not in payload:
                    payload[key] = selected_doc_ids.get(key)
    except Exception:
        pass
    payload.setdefault("doc_ids", [])
    payload.setdefault("rule_doc_ids", [])
    payload.setdefault("reference_doc_ids", [])
    payload.setdefault("generate_doc_ids", [])
    payload.setdefault("review_doc_ids", [])
    return payload


def build_skill_task_result_operation_log_payload(
    task,
    docs,
    requirement_items,
    status,
    elapsed_seconds,
    review_provider,
    model_name,
    case_title="",
    case_data=None,
    ai_payload="",
    stats=None,
    result_json_path="",
    result_md_path="",
    error_message="",
):
    stats = stats or {}
    case_data = case_data or {}
    status_text = "成功" if str(status or "").lower() == "success" else "失败"
    return {
        "title": "任务标题={}&项目ID={}&日志={}".format(task.title or "功能用例", task.project_id or 0, status_text),
        "tag": "功能用例AI生成",
        "description": json.dumps([
            {"name": "任务ID", "now": task.id},
            {"name": "任务标题", "now": task.title or "功能用例"},
            {"name": "项目ID", "now": task.project_id or 0},
            {"name": "选中文档数", "now": len(docs or [])},
            {"name": "需求组数", "now": len(requirement_items or [])},
            {"name": "结果状态", "now": status_text},
            {"name": "耗时(秒)", "now": elapsed_seconds},
            {"name": "结果标题", "now": case_title},
            {"name": "结果用例数", "now": int(stats.get("case_count") or 0)},
            {"name": "模型供应商", "now": review_provider or ""},
            {"name": "模型名称", "now": model_name or ""},
            {"name": "结果JSON路径", "now": result_json_path or ""},
            {"name": "结果Markdown路径", "now": result_md_path or ""},
            {"name": "模型返回结果", "now": preview_text(ai_payload or error_message, 2000)},
            {"name": "标准化结果预览", "now": preview_text(json.dumps(case_data, ensure_ascii=False), 2000) if case_data else ""},
        ], ensure_ascii=False),
    }


async def append_skill_task_result_operation_log(
    task,
    docs,
    requirement_items,
    status,
    elapsed_seconds,
    review_provider,
    model_name,
    case_title="",
    case_data=None,
    ai_payload="",
    stats=None,
    result_json_path="",
    result_md_path="",
    error_message="",
):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        payload = build_skill_task_result_operation_log_payload(
            task,
            docs,
            requirement_items,
            status,
            elapsed_seconds,
            review_provider,
            model_name,
            case_title=case_title,
            case_data=case_data,
            ai_payload=ai_payload,
            stats=stats,
            result_json_path=result_json_path,
            result_md_path=result_md_path,
            error_message=error_message,
        )
        existed = await session.execute(
            text("SELECT id FROM argus_operation_log WHERE `key`=:key AND tag=:tag AND title=:title LIMIT 1"),
            {"key": task.id, "tag": payload["tag"], "title": payload["title"]},
        )
        if existed.first() is not None:
            return
        session.add(ArgusOperationLog(
            task.create_user,
            OperationType.EXECUTE,
            payload["title"],
            payload["tag"],
            payload["description"],
            task.id,
        ))
        await session.commit()


async def execute_skill_task(task_id, task_payload=None, docs=None):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(ArgusFunctionalCaseSkillTask).where(
                ArgusFunctionalCaseSkillTask.id == task_id,
                ArgusFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.scalars().first()
        if task is None:
            return
        user_id = task.create_user
        if task_payload is None:
            task_payload = load_task_request_payload(task, "")
        if docs is None:
            docs = await load_skill_docs(session, user_id, task_payload.get("doc_ids") or [])

    review_provider = ""
    review_rounds = 0
    task_started_at = time.perf_counter()
    try:
        await PlatformAuditService.record_ai_event(
            user_id=user_id,
            biz_id=task_id,
            project_id=int(task_payload.get("project_id") or 0),
            action="start",
            summary="开始执行AI生成功能用例任务",
            detail={
                "task_id": int(task_id or 0),
                "case_file_id": int(task_payload.get("case_file_id") or 0),
                "doc_count": len(docs or []),
            },
        )
        await update_task_state(task_id, user_id, status="running", stage="prepare", stage_text="正在组装模型请求", progress=10)

        ai_config = await GConfigDao.get_active_ai_model_config(task_payload.get("ai_model_id"))
        review_provider = ai_config.get("provider") or ""
        logger.info(
            f"functional skill task execute task_id={task_id}, generator=structured-messages, "
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
        try:
            case_title, case_data, stats = await convert_ai_payload_to_case_result(
                ai_payload,
                task_payload.get("title") or "功能用例",
            )
        except Exception as parse_exc:
            raise ValueError(
                f"{parse_exc}；模型返回预览：{preview_text(ai_payload, 800)}"
            ) from parse_exc

        await update_task_state(
            task_id,
            user_id,
            status="success",
            stage="success",
            stage_text="模型生成完成，结果已可回填画布",
            progress=100,
            result_title=case_title,
            result_case_count=int(stats["case_count"] or 0),
            result_file_path="",
            result_md_path="",
            result_xmind_path="",
            result_payload=json.dumps({
                "title": case_title,
                "data": case_data,
                "case_count": int(stats["case_count"] or 0),
                "case_num": int(stats["case_count"] or 0),
                "provider": review_provider,
                "model": model_name,
                "markdown": str(ai_payload or ""),
            }, ensure_ascii=False),
            error_message="",
            finished_at=int(time.time()),
            review_provider=review_provider,
            review_rounds=review_rounds,
        )
        try:
            elapsed_seconds = round(time.perf_counter() - task_started_at, 2)
            await append_skill_task_result_operation_log(
                task,
                docs,
                task_payload.get("requirement_items") or [],
                "success",
                elapsed_seconds,
                review_provider,
                model_name,
                case_title=case_title,
                case_data=case_data,
                ai_payload=ai_payload,
                stats=stats,
            )
        except Exception as log_exc:
            logger.warning(f"functional skill task result log skipped: {log_exc}")
        await PlatformAuditService.record_ai_event(
            user_id=user_id,
            biz_id=task_id,
            project_id=int(task_payload.get("project_id") or 0),
            action="success",
            summary="AI生成功能用例任务执行成功",
            detail={
                "task_id": int(task_id or 0),
                "provider": review_provider,
                "model": model_name,
                "case_count": int(stats.get("case_count") or 0),
                "elapsed_seconds": round(time.perf_counter() - task_started_at, 2),
            },
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
        try:
            elapsed_seconds = round(time.perf_counter() - task_started_at, 2)
            await append_skill_task_result_operation_log(
                task,
                docs,
                task_payload.get("requirement_items") or [],
                "failed",
                elapsed_seconds,
                review_provider,
                "",
                error_message=str(exc),
            )
        except Exception as log_exc:
            logger.warning(f"functional skill task failed log skipped: {log_exc}")
        await PlatformAuditService.record_ai_event(
            user_id=user_id,
            biz_id=task_id,
            project_id=int((task_payload or {}).get("project_id") or 0),
            action="failed",
            summary="AI生成功能用例任务执行失败",
            detail={
                "task_id": int(task_id or 0),
                "provider": review_provider,
                "error_message": str(exc),
                "elapsed_seconds": round(time.perf_counter() - task_started_at, 2),
            },
        )

async def try_finalize_task_from_runtime(task_id, user_id):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(ArgusFunctionalCaseSkillTask).where(
                ArgusFunctionalCaseSkillTask.id == task_id,
                ArgusFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.scalars().first()
        return task


def build_task_result(task):
    payload = {}
    logs = []
    is_mapping = isinstance(task, Mapping)
    task_result_payload = task.get("result_payload") if is_mapping else task.result_payload
    task_logs_payload = task.get("task_logs") if is_mapping else task.task_logs
    task_result_case_count = task.get("result_case_count") if is_mapping else task.result_case_count
    task_result_md_path = task.get("result_md_path") if is_mapping else task.result_md_path
    task_result_xmind_path = task.get("result_xmind_path") if is_mapping else task.result_xmind_path
    task_case_file_id = task.get("case_file_id") if is_mapping else getattr(task, "case_file_id", 0)
    task_id = task.get("id") if is_mapping else task.id
    task_project_id = task.get("project_id") if is_mapping else task.project_id
    task_status = task.get("status") if is_mapping else task.status
    task_stage = task.get("stage") if is_mapping else task.stage
    task_stage_text = task.get("stage_text") if is_mapping else task.stage_text
    task_progress = task.get("progress") if is_mapping else task.progress
    task_review_provider = task.get("review_provider") if is_mapping else task.review_provider
    task_review_rounds = task.get("review_rounds") if is_mapping else task.review_rounds
    task_error_message = task.get("error_message") if is_mapping else task.error_message
    if task_result_payload:
        try:
            payload = json.loads(task_result_payload)
        except Exception:
            payload = {}
    if task_logs_payload:
        try:
            logs = json.loads(task_logs_payload)
            if not isinstance(logs, list):
                logs = []
        except Exception:
            logs = []
    markdown_value = str(payload.get("markdown") or "")
    data_value = payload.get("data")
    data_children = data_value.get("children") if isinstance(data_value, dict) else []
    data_text = str((data_value.get("data") or {}).get("text") or "") if isinstance(data_value, dict) else ""
    if markdown_value and isinstance(data_value, dict) and not data_children and data_text in {"", "未命名节点"}:
        try:
            rebuilt_title, rebuilt_data = parse_markdown_outline_to_case_data(
                markdown_value,
                payload.get("title") or "功能用例",
            )
            rebuilt_stats = analyze_case_data(rebuilt_data)
            payload["title"] = payload.get("title") or rebuilt_title
            payload["data"] = rebuilt_data
            payload["case_count"] = int(rebuilt_stats.get("case_count") or 0)
            payload["case_num"] = int(rebuilt_stats.get("case_count") or 0)
        except Exception:
            pass
    payload.update({
        "task_id": task_id,
        "project_id": task_project_id,
        "case_file_id": int(task_case_file_id or 0),
        "status": task_status,
        "stage": task_stage,
        "stage_text": task_stage_text,
        "progress": task_progress,
        "review_provider": task_review_provider,
        "review_rounds": task_review_rounds,
        "case_count": int(payload.get("case_count") or payload.get("case_num") or task_result_case_count or 0),
        "case_num": int(payload.get("case_num") or payload.get("case_count") or task_result_case_count or 0),
        "error_message": task_error_message,
        "result_md_path": task_result_md_path,
        "result_xmind_path": task_result_xmind_path,
        "result_md_url": "",
        "result_xmind_url": "",
        "task_logs": logs,
    })
    return payload


async def update_task_state(task_id, user_id=None, **fields):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(ArgusFunctionalCaseSkillTask).where(
                ArgusFunctionalCaseSkillTask.id == task_id,
                ArgusFunctionalCaseSkillTask.deleted_at == 0,
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
            select(ArgusFunctionalCaseSkillDoc).where(ArgusFunctionalCaseSkillDoc.deleted_at == 0)
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
    return ArgusResponse.success(data)


@router.post("/skill-doc/insert")
async def insert_skill_doc(form: FunctionalCaseSkillDocForm, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        duplicate_result = await session.execute(
            select(ArgusFunctionalCaseSkillDoc).where(
                ArgusFunctionalCaseSkillDoc.deleted_at == 0,
                ArgusFunctionalCaseSkillDoc.create_user == user_info["id"],
                ArgusFunctionalCaseSkillDoc.title == form.title,
            )
        )
        if duplicate_result.scalars().first() is not None:
            return ArgusResponse.failed("文档名称已存在，请更换后重试")
        model = ArgusFunctionalCaseSkillDoc(
            title=form.title,
            description=form.description,
            doc_type=form.doc_type,
            content=form.content,
            is_shared=form.is_shared,
            user=user_info["id"],
        )
        session.add(model)
        await session.flush()
        await ArgusOperationDao.insert_log(session, user_info["id"], OperationType.INSERT, model, key=model.id)
        await session.commit()
        await session.refresh(model)
    data = serialize_model(model)
    data["owner_name"] = (user_info.get("name") or user_info.get("username") or "").strip()
    return ArgusResponse.success(data)


@router.post("/skill-doc/update")
async def update_skill_doc(form: FunctionalCaseSkillDocForm, user_info=Depends(Permission())):
    if not form.id:
        return ArgusResponse.failed("id不能为空")
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(ArgusFunctionalCaseSkillDoc).where(
                ArgusFunctionalCaseSkillDoc.id == form.id,
                ArgusFunctionalCaseSkillDoc.deleted_at == 0,
            )
        )
        model = result.scalars().first()
        if model is None:
            return ArgusResponse.failed("文档不存在")
        if model.create_user != user_info["id"]:
            return ArgusResponse.failed("只能编辑自己的文档")
        duplicate_result = await session.execute(
            select(ArgusFunctionalCaseSkillDoc).where(
                ArgusFunctionalCaseSkillDoc.deleted_at == 0,
                ArgusFunctionalCaseSkillDoc.create_user == user_info["id"],
                ArgusFunctionalCaseSkillDoc.title == form.title,
                ArgusFunctionalCaseSkillDoc.id != form.id,
            )
        )
        if duplicate_result.scalars().first() is not None:
            return ArgusResponse.failed("文档名称已存在，请更换后重试")
        old = deepcopy(model)
        model.title = form.title
        model.description = form.description
        model.doc_type = form.doc_type
        model.content = form.content
        model.is_shared = form.is_shared
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.flush()
        await ArgusOperationDao.insert_log(
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
    return ArgusResponse.success(serialize_model(model))


@router.get("/skill-doc/delete")
async def delete_skill_doc(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        result = await session.execute(
            select(ArgusFunctionalCaseSkillDoc).where(
                ArgusFunctionalCaseSkillDoc.id == id,
                ArgusFunctionalCaseSkillDoc.deleted_at == 0,
            )
        )
        model = result.scalars().first()
        if model is None:
            return ArgusResponse.failed("文档不存在")
        if model.create_user != user_info["id"]:
            return ArgusResponse.failed("只能删除自己的文档")
        old = deepcopy(model)
        model.deleted_at = int(datetime.now().timestamp())
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.flush()
        await ArgusOperationDao.insert_log(session, user_info["id"], OperationType.DELETE, old, key=model.id)
        await session.commit()
    return ArgusResponse.success()


@router.post("/skill-task/create")
async def create_skill_task(form: FunctionalCaseSkillTaskForm, user_info=Depends(Permission())):
    requirement_items = [item.dict() for item in (form.requirement_items or [])]
    doc_groups = {
        "rule_doc_ids": dedupe_int_list(form.rule_doc_ids),
        "reference_doc_ids": dedupe_int_list(form.reference_doc_ids),
        "generate_doc_ids": dedupe_int_list(form.generate_doc_ids),
        "review_doc_ids": dedupe_int_list(form.review_doc_ids),
    }
    legacy_doc_ids = dedupe_int_list(form.doc_ids)
    if legacy_doc_ids and not any(doc_groups.values()):
        doc_groups["rule_doc_ids"] = legacy_doc_ids
    all_doc_ids = dedupe_int_list(
        (doc_groups["rule_doc_ids"] or [])
        + (doc_groups["reference_doc_ids"] or [])
        + (doc_groups["generate_doc_ids"] or [])
        + (doc_groups["review_doc_ids"] or [])
    )
    if not (
        form.requirement_text
        or form.instruction_text
        or form.generate_instruction_text
        or form.review_instruction_text
        or form.images
        or has_requirement_items(requirement_items)
        or all_doc_ids
    ):
        return ArgusResponse.failed("请至少提供需求说明、需求图片、设计链接、规则文档或生成补充说明")
    execution_payload = {
        "project_id": form.project_id,
        "case_file_id": int(form.case_file_id or 0),
        "title": form.title,
        "ai_model_id": form.ai_model_id,
        "requirement_text": form.requirement_text,
        "instruction_text": form.instruction_text,
        "generate_instruction_text": form.generate_instruction_text,
        "review_instruction_text": form.review_instruction_text,
        "images": form.images,
        "requirement_items": requirement_items,
        "doc_ids": all_doc_ids,
        "all_doc_ids": all_doc_ids,
        **doc_groups,
        "visible_doc_count": 0,
        "image_count": len(form.images or []),
        "requirement_group_count": len(requirement_items),
    }
    async with async_session() as session:
        await ensure_skill_task_schema(session)
        docs = await load_skill_docs(session, user_info["id"], all_doc_ids)
        execution_payload["visible_doc_count"] = len(docs)
        task = ArgusFunctionalCaseSkillTask(
            project_id=form.project_id,
            case_file_id=int(form.case_file_id or 0),
            title=form.title,
            user=user_info["id"],
            requirement_text=form.requirement_text,
            instruction_text=form.instruction_text or form.generate_instruction_text,
            selected_doc_ids=json.dumps({
                "all_doc_ids": all_doc_ids,
                "doc_ids": all_doc_ids,
                **doc_groups,
            }, ensure_ascii=False),
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
        task.input_payload = ""
        session.add(task)
        await session.flush()
        await session.commit()
        await session.refresh(task)
    try:
        ai_config = await GConfigDao.get_active_ai_model_config(form.ai_model_id)
        logger.info(
            f"functional skill task create task_id={task.id}, generator=structured-messages, "
            f"system_active_ai_provider={ai_config.get('provider')}, system_active_ai_model={ai_config.get('model')}, system_active_ai_base_url={ai_config.get('base_url')}"
        )
    except Exception as config_exc:
        logger.warning(f"functional skill task create task_id={task.id}, load ai config failed: {config_exc}")
    platform_task = await PlatformTaskService.create_task(
        task_type=PlatformTaskType.AI_FUNCTIONAL_CASE.value,
        user_id=user_info["id"],
        biz_id=task.id,
        biz_type="functional_case_skill_task",
        project_id=int(form.project_id or 0),
        resource_key=f"functional_case_file_{int(form.case_file_id or 0) or task.id}",
        payload={
            "skill_task_id": int(task.id),
            "task_payload": execution_payload,
            "docs": docs,
        },
        max_retries=2,
    )
    if not getattr(platform_task, "published", False):
        await update_task_state(
            int(task.id or 0),
            int(user_info["id"] or 0),
            status="failed",
            stage="failed",
            stage_text="任务入队失败",
            progress=100,
            error_message="RabbitMQ 入队失败，请检查消息队列连接后重试",
            finished_at=int(time.time()),
        )
        await PlatformTaskService.mark_failed(
            int(platform_task.id or 0),
            "RabbitMQ 入队失败，请检查消息队列连接后重试",
        )
        return ArgusResponse.failed("任务创建成功但消息队列入队失败，请检查 RabbitMQ 后重试")
    return ArgusResponse.success({
        "task_id": task.id,
        "platform_task_id": int(platform_task.id or 0),
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
    })


@router.get("/skill-task/status")
async def query_skill_task_status(id: int, user_info=Depends(Permission())):
    async with async_session() as session:
        result = await session.execute(
            select(
                ArgusFunctionalCaseSkillTask.id,
                ArgusFunctionalCaseSkillTask.project_id,
                ArgusFunctionalCaseSkillTask.case_file_id,
                ArgusFunctionalCaseSkillTask.status,
                ArgusFunctionalCaseSkillTask.stage,
                ArgusFunctionalCaseSkillTask.stage_text,
                ArgusFunctionalCaseSkillTask.progress,
                ArgusFunctionalCaseSkillTask.review_provider,
                ArgusFunctionalCaseSkillTask.review_rounds,
                ArgusFunctionalCaseSkillTask.result_case_count,
                ArgusFunctionalCaseSkillTask.error_message,
                ArgusFunctionalCaseSkillTask.create_user,
            ).where(
                ArgusFunctionalCaseSkillTask.id == id,
                ArgusFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.mappings().first()
        if task is None:
            return ArgusResponse.failed("任务不存在")
        if int(task.get("create_user") or 0) != int(user_info["id"]):
            return ArgusResponse.failed("只能查看自己的任务")
        normalized_status = str(task.get("status") or "").strip().lower()
        normalized_stage = str(task.get("stage") or "").strip().lower()
        if normalized_status not in {"success", "failed", "cancelled"} and normalized_stage not in {"success", "failed", "cancelled"}:
            return ArgusResponse.success(build_task_result(task))
        detail_result = await session.execute(
            select(
                ArgusFunctionalCaseSkillTask.id,
                ArgusFunctionalCaseSkillTask.project_id,
                ArgusFunctionalCaseSkillTask.case_file_id,
                ArgusFunctionalCaseSkillTask.status,
                ArgusFunctionalCaseSkillTask.stage,
                ArgusFunctionalCaseSkillTask.stage_text,
                ArgusFunctionalCaseSkillTask.progress,
                ArgusFunctionalCaseSkillTask.review_provider,
                ArgusFunctionalCaseSkillTask.review_rounds,
                ArgusFunctionalCaseSkillTask.task_logs,
                ArgusFunctionalCaseSkillTask.result_payload,
                ArgusFunctionalCaseSkillTask.result_md_path,
                ArgusFunctionalCaseSkillTask.result_xmind_path,
                ArgusFunctionalCaseSkillTask.result_case_count,
                ArgusFunctionalCaseSkillTask.error_message,
            ).where(
                ArgusFunctionalCaseSkillTask.id == id,
                ArgusFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = detail_result.mappings().first() or task
    return ArgusResponse.success(build_task_result(task))


@router.post("/skill-task/cancel")
async def cancel_skill_task(request: Request, id: int = 0, user_info=Depends(Permission())):
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    task_id = int(id or payload.get("id") or payload.get("task_id") or 0)
    if task_id <= 0:
        return ArgusResponse.failed("id不能为空")
    async with async_session() as session:
        result = await session.execute(
            select(ArgusFunctionalCaseSkillTask).where(
                ArgusFunctionalCaseSkillTask.id == task_id,
                ArgusFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.scalars().first()
        if task is None:
            return ArgusResponse.failed("任务不存在")
        if int(task.create_user or 0) != int(user_info["id"]):
            return ArgusResponse.failed("只能停止自己的任务")
        if str(task.status or "").lower() in {"success", "failed", "cancelled"}:
            return ArgusResponse.success(build_task_result(task))
    await update_task_state(
        task_id,
        user_info["id"],
        status="cancelled",
        stage="cancelled",
        stage_text="任务已手动停止",
        progress=100,
        error_message="用户手动停止AI生成功能用例任务",
        finished_at=int(time.time()),
    )
    try:
        async with async_session() as session:
            rows = await session.execute(text(
                "SELECT id FROM argus_platform_task "
                "WHERE deleted_at=0 AND task_type='ai_functional_case' "
                "AND biz_type='functional_case_skill_task' AND biz_id=:biz_id "
                "ORDER BY id DESC LIMIT 5"
            ), {"biz_id": task_id})
            platform_task_ids = [int(row[0]) for row in rows.fetchall()]
        for platform_task_id in platform_task_ids:
            await PlatformTaskService.update_task(
                platform_task_id,
                status=PlatformTaskStatus.CANCELLED.value,
                stage="cancelled",
                stage_text="关联AI生成功能用例任务已手动停止",
                progress=100,
                error_message="用户手动停止AI生成功能用例任务",
            )
    except Exception as exc:
        logger.warning(f"cancel platform task skipped, skill_task_id={task_id}, error={exc}")
    async with async_session() as session:
        result = await session.execute(
            select(ArgusFunctionalCaseSkillTask).where(
                ArgusFunctionalCaseSkillTask.id == task_id,
                ArgusFunctionalCaseSkillTask.deleted_at == 0,
            )
        )
        task = result.scalars().first()
    return ArgusResponse.success(build_task_result(task))
