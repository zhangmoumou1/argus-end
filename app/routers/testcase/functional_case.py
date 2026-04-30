import json
import os
import re
import time
import asyncio
import hashlib
from datetime import datetime

from fastapi import APIRouter, Depends
import requests
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from app.handler.fatcory import PityResponse
from app.models import async_session
from app.models.functional_case import PityFunctionalCaseDirectory, PityFunctionalCaseFile, PityFunctionalCaseItem
from app.models.user import User
from app.routers import Permission
from app.schema.functional_case import (
    FunctionalCaseDirectoryForm,
    FunctionalCaseDirectoryMoveForm,
    FunctionalCaseAIGenerateForm,
    FunctionalCaseFileForm,
    FunctionalCaseFileMoveForm,
)
from app.utils.logger import Log
from config import Config

router = APIRouter(prefix="/functional-case")
logger = Log("functional_case_ai")

CASE_FILE_DIR = os.path.join("statics", "functional_cases")
FUNCTIONAL_CASE_SCHEMA_READY = False
AI_TEXT_LIMIT = 12000
AI_INSTRUCTION_LIMIT = 6000
AI_IMAGE_LIMIT = 3
AI_IMAGE_DATA_URL_LIMIT = 800000


def serialize_model(model):
    data = PityResponse.model_to_dict(model)
    return data


def truncate_ai_text(value, limit):
    text_value = str(value or "").strip()
    if len(text_value) <= limit:
        return text_value
    return f"{text_value[:limit]}\n\n[内容已截断，共{len(text_value)}字符，仅保留前{limit}字符]"


def compact_ai_images(images):
    compacted = []
    for image in images[:AI_IMAGE_LIMIT]:
        image_value = str(image or "").strip()
        if not image_value:
            continue
        if image_value.startswith("data:image") and len(image_value) > AI_IMAGE_DATA_URL_LIMIT:
            continue
        compacted.append(image_value)
    return compacted


def preview_text(value, limit=300):
    text_value = str(value or "").strip()
    if len(text_value) <= limit:
        return text_value
    return f"{text_value[:limit]} ...<truncated {len(text_value) - limit} chars>"


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


def summarize_ai_request(form: FunctionalCaseAIGenerateForm, content):
    text_item = next(
        (item for item in content if isinstance(item, dict) and item.get("type") == "text"),
        {},
    )
    sent_images = [
        item.get("image_url", {}).get("url")
        for item in content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    return {
        "project_id": form.project_id,
        "title": form.title,
        "requirement_length": len(str(form.requirement_text or "")),
        "instruction_length": len(str(form.instruction_text or "")),
        "input_image_count": len(form.images or []),
        "sent_image_count": len(sent_images),
        "prompt_text_length": len(str(text_item.get("text") or "")),
        "prompt_text_preview": preview_text(text_item.get("text") or "", 600),
        "images": summarize_ai_images(sent_images),
    }


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
                        "image_url": {
                            "url": f"<{('data_url' if url_value.startswith('data:image') else 'url')}, length={len(url_value)}, preview={preview_text(url_value, 120)}>"
                        },
                    })
                else:
                    next_block = dict(block)
                    if "text" in next_block:
                        next_block["text"] = preview_text(next_block.get("text"), 4000)
                    normalized_content.append(next_block)
            message_item["content"] = normalized_content
        elif isinstance(content, str):
            message_item["content"] = preview_text(content, 4000)
    return cloned


async def ensure_functional_case_schema(session):
    global FUNCTIONAL_CASE_SCHEMA_READY
    if FUNCTIONAL_CASE_SCHEMA_READY:
        return
    try:
        file_column = await session.execute(
            text("SHOW COLUMNS FROM pity_functional_case_file LIKE 'sort_index'")
        )
        if file_column.first() is None:
            await session.execute(
                text(
                    "ALTER TABLE pity_functional_case_file "
                    "ADD COLUMN sort_index INT NOT NULL DEFAULT 0 COMMENT '排序'"
                )
            )
        directory_column = await session.execute(
            text("SHOW COLUMNS FROM pity_functional_case_directory LIKE 'sort_index'")
        )
        if directory_column.first() is None:
            await session.execute(
                text(
                    "ALTER TABLE pity_functional_case_directory "
                    "ADD COLUMN sort_index INT NOT NULL DEFAULT 0 COMMENT '排序'"
                )
            )
        file_project_column = await session.execute(
            text("SHOW COLUMNS FROM pity_functional_case_file LIKE 'project_id'")
        )
        if file_project_column.first() is None:
            await session.execute(
                text(
                    "ALTER TABLE pity_functional_case_file "
                    "ADD COLUMN project_id INT NOT NULL DEFAULT 0 COMMENT '项目ID'"
                )
            )
        file_case_data_column = await session.execute(
            text("SHOW COLUMNS FROM pity_functional_case_file LIKE 'case_data'")
        )
        if file_case_data_column.first() is None:
            await session.execute(
                text(
                    "ALTER TABLE pity_functional_case_file "
                    "ADD COLUMN case_data LONGTEXT NULL COMMENT '功能用例JSON内容'"
                )
            )
        directory_project_column = await session.execute(
            text("SHOW COLUMNS FROM pity_functional_case_directory LIKE 'project_id'")
        )
        if directory_project_column.first() is None:
            await session.execute(
                text(
                    "ALTER TABLE pity_functional_case_directory "
                    "ADD COLUMN project_id INT NOT NULL DEFAULT 0 COMMENT '项目ID'"
                )
            )

        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS pity_functional_case_item ("
                "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
                "deleted_at BIGINT NOT NULL DEFAULT 0,"
                "create_user INT NULL,"
                "update_user INT NULL,"
                "project_id INT NOT NULL DEFAULT 0,"
                "directory_id INT NOT NULL DEFAULT 0,"
                "file_id INT NOT NULL DEFAULT 0,"
                "case_uid VARCHAR(64) NOT NULL,"
                "file_title VARCHAR(128) NOT NULL,"
                "case_name VARCHAR(512) NOT NULL,"
                "case_path TEXT NULL,"
                "case_priority VARCHAR(32) NULL,"
                "case_pass INT NOT NULL DEFAULT 0,"
                "KEY idx_fc_item_file_deleted (file_id, deleted_at),"
                "KEY idx_fc_item_file_uid_deleted (file_id, case_uid, deleted_at),"
                "KEY idx_fc_item_project_created (project_id, deleted_at, created_at),"
                "KEY idx_fc_item_creator_created (create_user, deleted_at, created_at)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='功能用例明细表'"
            )
        )
        item_case_uid_column = await session.execute(
            text("SHOW COLUMNS FROM pity_functional_case_item LIKE 'case_uid'")
        )
        if item_case_uid_column.first() is None:
            await session.execute(
                text(
                    "ALTER TABLE pity_functional_case_item "
                    "ADD COLUMN case_uid VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用例稳定标识'"
                )
            )
        item_case_pass_column = await session.execute(
            text("SHOW COLUMNS FROM pity_functional_case_item LIKE 'case_pass'")
        )
        if item_case_pass_column.first() is None:
            await session.execute(
                text(
                    "ALTER TABLE pity_functional_case_item "
                    "ADD COLUMN case_pass INT NOT NULL DEFAULT 0 COMMENT '是否通过(1通过,0不通过)'"
                )
            )

        await session.commit()
    except OperationalError as exc:
        if "Duplicate column name" not in str(exc):
            raise
    FUNCTIONAL_CASE_SCHEMA_READY = True


def pick_user_name(user):
    if user is None:
        return ""
    return (user.name or user.username or "").strip()


def get_root_node(data):
    if isinstance(data, dict) and isinstance(data.get("root"), dict):
        return data.get("root")
    return data


def truncate_case_text(value):
    text = str(value or "").strip() or "未命名节点"
    return text[:10]


def _parse_priority(icons):
    for icon in icons:
        if isinstance(icon, str) and icon.startswith("priority_"):
            return icon.split("_", 1)[1] or ""
    return None


def _resolve_case_uid(node_data, case_path, case_name, case_priority):
    case_uid = str((node_data or {}).get("case_uid") or "").strip()
    if case_uid:
        return case_uid
    uid_value = str((node_data or {}).get("uid") or "").strip()
    if uid_value:
        return f"legacy_uid_{uid_value}"
    raw = f"{case_path}|{case_name}|{case_priority or ''}"
    return f"legacy_{hashlib.md5(raw.encode('utf-8')).hexdigest()}"


def extract_case_items(data):
    root = get_root_node(data)
    if not isinstance(root, dict):
        return []

    items = []

    def walk(node, path_nodes):
        node_data = node.get("data") if isinstance(node, dict) else {}
        node_data = node_data if isinstance(node_data, dict) else {}
        node_text = str(node_data.get("text") or "未命名节点").strip() or "未命名节点"
        raw_icons = node_data.get("icon")
        icons = raw_icons if isinstance(raw_icons, list) else [raw_icons] if raw_icons else []
        priority = _parse_priority(icons)

        next_path = list(path_nodes)
        next_path.append(node_text)

        is_pass = any(isinstance(icon, str) and icon == "progress_8" for icon in icons)
        if priority is not None:
            case_path = " / ".join([p for p in next_path if p])
            items.append({
                "case_uid": _resolve_case_uid(node_data, case_path, node_text, priority),
                "case_name": node_text,
                "case_path": case_path,
                "case_priority": priority,
                "case_pass": 1 if is_pass else 0,
            })

        children = node.get("children") if isinstance(node, dict) else []
        for child in children or []:
            walk(child, next_path)

    walk(root, [])
    return items


def parse_case_data(case_text, source='database'):
    text_value = str(case_text or '').strip()
    if not text_value:
        return None
    try:
        return json.loads(text_value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"read functional case data failed, source={source}, error={exc}")
        return None


async def rebuild_functional_case_items(session, model, operator_user_id: int, case_items=None, refresh_meta=False):
    now_deleted = int(datetime.now().timestamp())
    now_dt = datetime.now()

    if case_items is None:
        case_data = parse_case_data(getattr(model, "case_data", None), source="database")
        case_items = extract_case_items(case_data)
    incoming_items = case_items or []
    incoming_map = {}
    for item in incoming_items:
        uid = str(item.get("case_uid") or "").strip()
        if not uid:
            continue
        incoming_map[uid] = {
            "case_uid": uid,
            "case_name": item.get("case_name") or "未命名节点",
            "case_path": item.get("case_path"),
            "case_priority": item.get("case_priority"),
            "case_pass": int(item.get("case_pass") or 0),
        }

    existing_result = await session.execute(
        text(
            "SELECT id, case_uid, case_name, case_path, case_priority, case_pass "
            "FROM pity_functional_case_item WHERE file_id=:file_id AND deleted_at=0"
        ),
        {"file_id": model.id},
    )
    existing_rows = existing_result.mappings().all()
    existing_map = {str(row.get("case_uid") or ""): row for row in existing_rows if str(row.get("case_uid") or "")}

    if refresh_meta:
        await update_functional_case_item_meta(session, model, operator_user_id)

    if existing_map:
        to_soft_delete = [uid for uid in existing_map.keys() if uid not in incoming_map]
        if to_soft_delete:
            delete_rows = []
            for uid in to_soft_delete:
                row = existing_map.get(uid)
                if row and row.get("id") is not None:
                    delete_rows.append({
                        "id": int(row.get("id")),
                        "deleted_at": now_deleted,
                        "update_user": operator_user_id,
                        "updated_at": now_dt,
                    })
            await session.execute(
                text(
                    "UPDATE pity_functional_case_item "
                    "SET deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at "
                    "WHERE id=:id AND deleted_at=0"
                ),
                delete_rows,
            )

    insert_rows = []
    update_rows = []
    for uid, item in incoming_map.items():
        old = existing_map.get(uid)
        if not old:
            insert_rows.append({
                "project_id": model.project_id,
                "directory_id": model.directory_id,
                "file_id": model.id,
                "case_uid": uid,
                "file_title": model.title,
                "case_name": item["case_name"],
                "case_path": item["case_path"],
                "case_priority": item["case_priority"],
                "case_pass": item["case_pass"],
                "create_user": operator_user_id,
                "update_user": operator_user_id,
                "created_at": now_dt,
                "updated_at": now_dt,
            })
            continue
        if (
            str(old.get("case_name") or "") != str(item["case_name"] or "")
            or str(old.get("case_path") or "") != str(item["case_path"] or "")
            or str(old.get("case_priority") or "") != str(item["case_priority"] or "")
            or int(old.get("case_pass") or 0) != int(item["case_pass"] or 0)
        ):
            update_rows.append({
                "id": int(old.get("id")),
                "project_id": model.project_id,
                "directory_id": model.directory_id,
                "file_title": model.title,
                "case_name": item["case_name"],
                "case_path": item["case_path"],
                "case_priority": item["case_priority"],
                "case_pass": item["case_pass"],
                "update_user": operator_user_id,
                "updated_at": now_dt,
            })

    if insert_rows:
        await session.execute(
            text(
                "INSERT INTO pity_functional_case_item "
                "(project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass, deleted_at, create_user, update_user, created_at, updated_at) "
                "VALUES (:project_id, :directory_id, :file_id, :case_uid, :file_title, :case_name, :case_path, :case_priority, :case_pass, 0, :create_user, :update_user, :created_at, :updated_at)"
            ),
            insert_rows,
        )
    if update_rows:
        await session.execute(
            text(
                "UPDATE pity_functional_case_item SET "
                "project_id=:project_id, directory_id=:directory_id, file_title=:file_title, "
                "case_name=:case_name, case_path=:case_path, case_priority=:case_priority, case_pass=:case_pass, "
                "update_user=:update_user, updated_at=:updated_at "
                "WHERE id=:id"
            ),
            update_rows,
        )


async def update_functional_case_item_meta(session, model, operator_user_id: int):
    await session.execute(
        text(
            "UPDATE pity_functional_case_item "
            "SET project_id=:project_id, directory_id=:directory_id, file_title=:file_title, "
            "update_user=:update_user, updated_at=:updated_at "
            "WHERE file_id=:file_id AND deleted_at=0"
        ),
        {
            "project_id": model.project_id,
            "directory_id": model.directory_id,
            "file_title": model.title,
            "update_user": operator_user_id,
            "updated_at": datetime.now(),
            "file_id": model.id,
        },
    )


async def fetch_case_stats_by_file_id(session, file_id: int):
    result = await session.execute(
        text(
            "SELECT COUNT(1) AS case_count, COALESCE(SUM(case_pass), 0) AS pass_count "
            "FROM pity_functional_case_item WHERE file_id=:file_id AND deleted_at=0"
        ),
        {"file_id": file_id},
    )
    row = result.mappings().first()
    if row is None:
        return {"case_count": 0, "pass_count": 0}
    return {
        "case_count": int(row.get("case_count") or 0),
        "pass_count": int(row.get("pass_count") or 0),
    }


async def sync_functional_case_items_async(file_id: int, operator_user_id: int, case_items=None, rebuild=False):
    try:
        async with async_session() as session:
            await ensure_functional_case_schema(session)
            result = await session.execute(
                select(PityFunctionalCaseFile).where(
                    PityFunctionalCaseFile.id == file_id,
                    PityFunctionalCaseFile.deleted_at == 0,
                )
            )
            model = result.scalars().first()
            if model is None:
                return
            if rebuild:
                await rebuild_functional_case_items(session, model, operator_user_id, case_items=case_items)
            else:
                await update_functional_case_item_meta(session, model, operator_user_id)
            await session.commit()
    except Exception as exc:
        logger.warning(f"sync functional case items async failed, file_id={file_id}, error={exc}")


def analyze_case_data(data):
    root = get_root_node(data)
    if not isinstance(root, dict):
        return {"case_count": 0, "pass_count": 0, "conflicts": []}

    case_count = 0
    pass_count = 0
    conflict_nodes = []
    conflict_seen = set()

    def add_conflict(text):
        key = truncate_case_text(text)
        if key in conflict_seen:
            return
        conflict_seen.add(key)
        conflict_nodes.append(key)

    def walk(node, priority_path):
        nonlocal case_count, pass_count
        node_data = node.get("data") if isinstance(node, dict) else {}
        node_data = node_data if isinstance(node_data, dict) else {}
        node_text = node_data.get("text")
        raw_icons = node_data.get("icon")
        icons = raw_icons if isinstance(raw_icons, list) else [raw_icons] if raw_icons else []
        has_priority = any(isinstance(icon, str) and icon.startswith("priority_") for icon in icons)
        is_pass = any(isinstance(icon, str) and icon == "progress_8" for icon in icons)
        next_priority_path = list(priority_path)
        if has_priority:
            case_count += 1
            if is_pass:
                pass_count += 1
            if priority_path:
                for item in priority_path:
                    add_conflict(item)
                add_conflict(node_text)
            next_priority_path.append(node_text)
        children = node.get("children") if isinstance(node, dict) else []
        for child in children or []:
            walk(child, next_priority_path)

    walk(root, [])
    return {"case_count": case_count, "pass_count": pass_count, "conflicts": conflict_nodes}

def build_ai_prompt_content(form: FunctionalCaseAIGenerateForm):
    requirement_text = truncate_ai_text(form.requirement_text, AI_TEXT_LIMIT)
    instruction_text = truncate_ai_text(form.instruction_text, AI_INSTRUCTION_LIMIT)
    images = compact_ai_images(form.images or [])
    content = []
    text_parts = [
        "你是资深测试分析师，请根据需求材料生成功能测试用例脑图。",
        f"当前用例标题：{form.title}",
        "请严格只返回 JSON，不要返回 Markdown、解释、注释、代码块标记。",
        (
            "JSON 结构必须为："
            '{"title":"用例标题","data":{"text":"根节点"},"children":[{"data":{"text":"节点文本","icon":["priority_1","progress_3"],"note":"可选备注","tag":["可选标签"]},"children":[]}]}'
        ),
        "约束：1. 所有节点都必须使用 data.text。2. 真正测试用例节点请用 icon 中的 priority_1 到 priority_9 标记优先级。3. 如需进度可用 progress_1 到 progress_8。4. children 必须始终返回数组。5. 文本使用中文。6. 根节点标题要和 title 保持一致或高度相关。",
    ]
    if requirement_text:
        text_parts.append(f"需求描述：\n{requirement_text}")
    if instruction_text:
        text_parts.append(f"额外生成要求：\n{instruction_text}")
    if images:
        text_parts.append(f"还有 {len(images)} 张需求截图，请结合截图内容生成。")
    skipped_images = max(0, len(form.images or []) - len(images))
    if skipped_images:
        text_parts.append(f"注意：已有 {skipped_images} 张截图因数量或体积过大被省略。")
    content.append({
        "type": "text",
        "text": "\n\n".join(text_parts),
    })
    for image in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": image},
        })
    return content


def extract_json_object(text_value: str):
    text_value = (text_value or "").strip()
    if not text_value:
        raise ValueError("AI 未返回内容")
    try:
        return json.loads(text_value)
    except Exception:
        pass
    fenced = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text_value, re.IGNORECASE)
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


def normalize_ai_node(node):
    if isinstance(node, str):
        return {"data": {"text": node}, "children": []}
    if not isinstance(node, dict):
        return {"data": {"text": "未命名节点"}, "children": []}

    if "data" in node:
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        text_value = str(data.get("text") or node.get("text") or "未命名节点").strip() or "未命名节点"
        normalized_data = {"text": text_value}
        for key in ("icon", "note", "tag", "hyperlink"):
            value = data.get(key)
            if value not in (None, "", []):
                normalized_data[key] = value
    else:
        text_value = str(node.get("text") or node.get("title") or "未命名节点").strip() or "未命名节点"
        normalized_data = {"text": text_value}
        for key in ("icon", "note", "tag", "hyperlink"):
            value = node.get(key)
            if value not in (None, "", []):
                normalized_data[key] = value

    raw_children = node.get("children") if isinstance(node.get("children"), list) else []
    return {
        "data": normalized_data,
        "children": [normalize_ai_node(child) for child in raw_children],
    }


def normalize_ai_case_data(payload, fallback_title: str):
    if not isinstance(payload, dict):
        raise ValueError("AI 返回结果格式不正确")
    title = str(payload.get("title") or fallback_title or "AI生成功能用例").strip() or "AI生成功能用例"
    if "root" in payload and isinstance(payload.get("root"), dict):
        root_node = normalize_ai_node(payload.get("root"))
    elif "data" in payload and isinstance(payload.get("data"), dict):
        root_node = normalize_ai_node({
            "data": payload.get("data"),
            "children": payload.get("children") or [],
        })
    else:
        root_node = normalize_ai_node(payload)
    if not root_node.get("data", {}).get("text"):
        root_node["data"]["text"] = title
    return title, root_node


def call_kimi_generate(form: FunctionalCaseAIGenerateForm):
    if not Config.KIMI_API_KEY:
        raise ValueError("未配置 Kimi API Key")
    prompt_content = build_ai_prompt_content(form)
    request_summary = summarize_ai_request(form, prompt_content)
    request_payload = {
        "model": Config.KIMI_MODEL,
        "temperature": 1,
        "messages": [
            {
                "role": "system",
                "content": "你只输出符合要求的 JSON，对象内不要出现 markdown 代码块标记。",
            },
            {
                "role": "user",
                "content": prompt_content,
            },
        ],
    }
    loggable_payload = build_loggable_kimi_payload(request_payload)
    logger.info(f"Kimi request summary: {json.dumps(request_summary, ensure_ascii=False)}")
    logger.info(f"Kimi request payload: {json.dumps(loggable_payload, ensure_ascii=False)}")
    started_at = time.perf_counter()
    try:
        response = requests.post(
            f"{Config.KIMI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {Config.KIMI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=600,
        )
    except requests.Timeout as exc:
        elapsed = round(time.perf_counter() - started_at, 2)
        logger.warning(
            f"Kimi request timeout after {elapsed}s, summary={json.dumps(request_summary, ensure_ascii=False)}"
        )
        raise ValueError(f"Kimi 请求超时({elapsed}s)，请减少图片数量或缩小需求内容后重试") from exc
    except requests.RequestException as exc:
        elapsed = round(time.perf_counter() - started_at, 2)
        logger.error(
            f"Kimi request failed after {elapsed}s, summary={json.dumps(request_summary, ensure_ascii=False)}, error={exc}"
        )
        raise
    elapsed = round(time.perf_counter() - started_at, 2)
    logger.info(
        f"Kimi response status={response.status_code}, elapsed={elapsed}s, body_preview={preview_text(response.text, 1000)}"
    )
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise ValueError(f"Kimi 调用失败: {detail}")
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("Kimi 未返回可用结果")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        text_parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        content = "\n".join(text_parts)
    if not isinstance(content, str):
        raise ValueError("Kimi 返回内容格式不支持")
    return extract_json_object(content)


def build_tree(records, file_stats_map=None):
    node_map = {}
    roots = []
    file_stats_map = file_stats_map or {}
    own_case_count_map = {}
    own_pass_count_map = {}
    for item in records:
        node = {
            "title": item.name,
            "label": item.name,
            "value": item.id,
            "key": item.id,
            "id": item.id,
            "name": item.name,
            "parent": item.parent,
            "sort_index": item.sort_index,
            "case_count": 0,
            "children": [],
        }
        node_map[item.id] = node
        own_case_count_map[item.id] = 0
        own_pass_count_map[item.id] = 0
    for item in records:
        node = node_map[item.id]
        if item.parent and item.parent in node_map:
            node_map[item.parent]["children"].append(node)
        else:
            roots.append(node)
    for directory_id, stats in file_stats_map.items():
        own_case_count_map[directory_id] = own_case_count_map.get(directory_id, 0) + int(stats.get("case_count", 0))
        own_pass_count_map[directory_id] = own_pass_count_map.get(directory_id, 0) + int(stats.get("pass_count", 0))

    def sort_nodes(nodes):
        nodes.sort(key=lambda x: (x.get("sort_index") or 0, x.get("id") or 0))
        for child in nodes:
            sort_nodes(child["children"])

    def calc_case_count(nodes):
        total_case = 0
        total_pass = 0
        for node in nodes:
            child_case, child_pass = calc_case_count(node["children"])
            own_case = own_case_count_map.get(node["id"], 0)
            own_pass = own_pass_count_map.get(node["id"], 0)
            node["case_count"] = own_case + child_case
            node["pass_count"] = own_pass + child_pass
            total_case += node["case_count"]
            total_pass += node["pass_count"]
        return total_case, total_pass

    sort_nodes(roots)
    calc_case_count(roots)
    return roots


def read_case_file(file_path):
    return None


def dump_case_data(data):
    try:
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.warning(f"dump functional case data failed, error={exc}")
        return None


def read_case_payload(model):
    if model is None:
        return None
    return parse_case_data(getattr(model, 'case_data', None), source='database')


async def collect_directory_ids(project_id: int, directory_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(PityFunctionalCaseDirectory).where(
                PityFunctionalCaseDirectory.deleted_at == 0,
                PityFunctionalCaseDirectory.project_id == project_id,
            )
        )
        records = result.scalars().all()
    child_map = {}
    for item in records:
        child_map.setdefault(item.parent, []).append(item.id)
    ans = [directory_id]
    cursor = [directory_id]
    while cursor:
        current = cursor.pop(0)
        children = child_map.get(current, [])
        ans.extend(children)
        cursor.extend(children)
    return ans

@router.get("/directory")
async def list_directory(project_id: int, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        directory_result = await session.execute(
            select(PityFunctionalCaseDirectory)
            .where(
                PityFunctionalCaseDirectory.deleted_at == 0,
                PityFunctionalCaseDirectory.project_id == project_id,
            )
            .order_by(PityFunctionalCaseDirectory.sort_index.asc(), PityFunctionalCaseDirectory.id.asc())
        )
        file_result = await session.execute(
            select(PityFunctionalCaseFile).where(
                PityFunctionalCaseFile.deleted_at == 0,
                PityFunctionalCaseFile.project_id == project_id,
            )
        )
        records = directory_result.scalars().all()
        files = file_result.scalars().all()
    file_stats_map = {}
    for item in files:
        stats = analyze_case_data(read_case_payload(item))
        directory_id = item.directory_id
        if directory_id not in file_stats_map:
            file_stats_map[directory_id] = {"case_count": 0, "pass_count": 0}
        file_stats_map[directory_id]["case_count"] += int(stats.get("case_count") or 0)
        file_stats_map[directory_id]["pass_count"] += int(stats.get("pass_count") or 0)
    return PityResponse.success(build_tree(records, file_stats_map))


@router.post("/directory/insert")
async def insert_directory(form: FunctionalCaseDirectoryForm, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        if form.parent:
            parent_result = await session.execute(
                select(PityFunctionalCaseDirectory).where(
                    PityFunctionalCaseDirectory.id == form.parent,
                    PityFunctionalCaseDirectory.deleted_at == 0,
                    PityFunctionalCaseDirectory.project_id == form.project_id,
                )
            )
            if parent_result.scalars().first() is None:
                return PityResponse.failed("父目录不存在")
        model = PityFunctionalCaseDirectory(
            project_id=form.project_id,
            name=form.name,
            parent=form.parent,
            sort_index=form.sort_index,
            user=user_info["id"],
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)
    return PityResponse.success(serialize_model(model))


@router.post("/directory/update")
async def update_directory(form: FunctionalCaseDirectoryForm, user_info=Depends(Permission())):
    if not form.id:
        return PityResponse.failed("id不能为空")
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseDirectory).where(
                PityFunctionalCaseDirectory.id == form.id,
                PityFunctionalCaseDirectory.deleted_at == 0,
                PityFunctionalCaseDirectory.project_id == form.project_id,
            )
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("目录不存在")
        if form.parent:
            parent_result = await session.execute(
                select(PityFunctionalCaseDirectory).where(
                    PityFunctionalCaseDirectory.id == form.parent,
                    PityFunctionalCaseDirectory.deleted_at == 0,
                    PityFunctionalCaseDirectory.project_id == form.project_id,
                )
            )
            if parent_result.scalars().first() is None:
                return PityResponse.failed("父目录不存在")
        model.name = form.name
        model.parent = form.parent
        model.sort_index = form.sort_index
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.commit()
        await session.refresh(model)
    return PityResponse.success(serialize_model(model))


@router.post("/directory/move")
async def move_directory(form: FunctionalCaseDirectoryMoveForm, user_info=Depends(Permission())):
    if form.parent == form.id:
        return PityResponse.failed("父目录不能选择自己")
    if form.parent:
        children = await collect_directory_ids(form.project_id, form.id)
        if form.parent in children:
            return PityResponse.failed("父目录不能选择自身或子目录")
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseDirectory).where(
                PityFunctionalCaseDirectory.id == form.id,
                PityFunctionalCaseDirectory.deleted_at == 0,
                PityFunctionalCaseDirectory.project_id == form.project_id,
            )
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("目录不存在")
        model.parent = form.parent
        model.sort_index = form.sort_index
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.commit()
        await session.refresh(model)
    return PityResponse.success(serialize_model(model))


@router.get("/directory/delete")
async def delete_directory(id: int, project_id: int, user_info=Depends(Permission())):
    ids = await collect_directory_ids(project_id, id)
    now_deleted = int(datetime.now().timestamp())
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        directory_result = await session.execute(
            select(PityFunctionalCaseDirectory).where(
                PityFunctionalCaseDirectory.id.in_(ids),
                PityFunctionalCaseDirectory.deleted_at == 0,
                PityFunctionalCaseDirectory.project_id == project_id,
            )
        )
        case_result = await session.execute(
            select(PityFunctionalCaseFile).where(
                PityFunctionalCaseFile.directory_id.in_(ids),
                PityFunctionalCaseFile.deleted_at == 0,
                PityFunctionalCaseFile.project_id == project_id,
            )
        )
        files = case_result.scalars().all()

        for item in directory_result.scalars().all():
            item.deleted_at = now_deleted
            item.update_user = user_info["id"]
            item.updated_at = datetime.now()
        for item in files:
            item.deleted_at = now_deleted
            item.update_user = user_info["id"]
            item.updated_at = datetime.now()

        for item in files:
            await session.execute(
                text(
                    "UPDATE pity_functional_case_item "
                    "SET deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at "
                    "WHERE file_id=:file_id AND deleted_at=0"
                ),
                {
                    "deleted_at": now_deleted,
                    "update_user": user_info["id"],
                    "updated_at": datetime.now(),
                    "file_id": item.id,
                },
            )

        await session.commit()
    return PityResponse.success()


@router.get("/file/list")
async def list_files(project_id: int, directory_id: int = None, title: str = "", _=Depends(Permission())):
    directory_ids = await collect_directory_ids(project_id, directory_id) if directory_id else []
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        filters = [
            PityFunctionalCaseFile.deleted_at == 0,
            PityFunctionalCaseFile.project_id == project_id,
        ]
        if directory_id:
            filters.append(PityFunctionalCaseFile.directory_id.in_(directory_ids))
        if title:
            filters.append(PityFunctionalCaseFile.title.like(f"%{title}%"))
        total_sql = select(func.count(PityFunctionalCaseFile.id)).where(*filters)
        total = (await session.execute(total_sql)).scalar()
        result = await session.execute(
            select(PityFunctionalCaseFile)
            .where(*filters)
            .order_by(PityFunctionalCaseFile.sort_index.asc(), PityFunctionalCaseFile.id.desc())
        )
        files = result.scalars().all()
        user_ids = list({item.create_user for item in files if item.create_user is not None})
        user_name_map = {}
        if user_ids:
            user_result = await session.execute(select(User).where(User.id.in_(user_ids)))
            user_name_map = {item.id: pick_user_name(item) for item in user_result.scalars().all()}
        data = []
        for item in files:
            case_data = read_case_payload(item)
            stats = analyze_case_data(case_data)
            row = serialize_model(item)
            row["case_count"] = int(stats["case_count"] or 0)
            row["pass_count"] = int(stats.get("pass_count") or 0)
            row["case_num"] = row["case_count"]
            row["create_user_name"] = user_name_map.get(item.create_user, "")
            row["creator_name"] = row["create_user_name"]
            data.append(row)
    return PityResponse.success_with_size(data=data, total=total)


@router.get("/file/query")
async def query_file(id: int, project_id: int = None, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        filters = [PityFunctionalCaseFile.id == id, PityFunctionalCaseFile.deleted_at == 0]
        if project_id is not None:
            filters.append(PityFunctionalCaseFile.project_id == project_id)
        result = await session.execute(select(PityFunctionalCaseFile).where(*filters))
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("功能用例不存在")
        user_result = await session.execute(select(User).where(User.id == model.create_user))
        user = user_result.scalars().first()
        data = serialize_model(model)
        case_data = read_case_payload(model)
        stats = await fetch_case_stats_by_file_id(session, model.id)
        if stats["case_count"] == 0 and case_data:
            fallback_stats = analyze_case_data(case_data)
            stats = {
                "case_count": int(fallback_stats.get("case_count") or 0),
                "pass_count": int(fallback_stats.get("pass_count") or 0),
            }
        data["data"] = case_data
        data["case_count"] = int(stats["case_count"] or 0)
        data["pass_count"] = int(stats.get("pass_count") or 0)
        data["case_num"] = data["case_count"]
        data["create_user_name"] = pick_user_name(user)
        data["creator_name"] = data["create_user_name"]
    return PityResponse.success(data)


@router.post("/file/insert")
async def insert_file(form: FunctionalCaseFileForm, user_info=Depends(Permission())):
    stats = analyze_case_data(form.data)
    case_items = extract_case_items(form.data)
    if stats["conflicts"]:
        return PityResponse.failed(f"{'、'.join(stats['conflicts'])}用例冲突")
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        directory_result = await session.execute(
            select(PityFunctionalCaseDirectory).where(
                PityFunctionalCaseDirectory.id == form.directory_id,
                PityFunctionalCaseDirectory.deleted_at == 0,
                PityFunctionalCaseDirectory.project_id == form.project_id,
            )
        )
        if directory_result.scalars().first() is None:
            return PityResponse.failed("目录不存在")
        model = PityFunctionalCaseFile(
            project_id=form.project_id,
            title=form.title,
            directory_id=form.directory_id,
            file_path="",
            user=user_info["id"],
            sort_index=form.sort_index,
            case_data=dump_case_data(form.data),
        )
        session.add(model)
        await session.flush()
        await session.refresh(model)
        await session.commit()
        user_result = await session.execute(select(User).where(User.id == model.create_user))
        user = user_result.scalars().first()

    asyncio.create_task(
        sync_functional_case_items_async(
            file_id=model.id,
            operator_user_id=user_info["id"],
            case_items=case_items,
            rebuild=True,
        )
    )

    data = serialize_model(model)
    data["data"] = form.data
    data["case_count"] = int(stats["case_count"] or 0)
    data["pass_count"] = int(stats.get("pass_count") or 0)
    data["case_num"] = data["case_count"]
    data["create_user_name"] = pick_user_name(user)
    data["creator_name"] = data["create_user_name"]
    return PityResponse.success(data)


@router.post("/file/update")
async def update_file(form: FunctionalCaseFileForm, user_info=Depends(Permission())):
    if not form.id:
        return PityResponse.failed("id不能为空")
    stats = analyze_case_data(form.data)
    case_items = extract_case_items(form.data)
    if stats["conflicts"]:
        return PityResponse.failed(f"{'、'.join(stats['conflicts'])}用例冲突")
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseFile).where(
                PityFunctionalCaseFile.id == form.id,
                PityFunctionalCaseFile.deleted_at == 0,
                PityFunctionalCaseFile.project_id == form.project_id,
            )
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("功能用例不存在")
        directory_result = await session.execute(
            select(PityFunctionalCaseDirectory).where(
                PityFunctionalCaseDirectory.id == form.directory_id,
                PityFunctionalCaseDirectory.deleted_at == 0,
                PityFunctionalCaseDirectory.project_id == form.project_id,
            )
        )
        if directory_result.scalars().first() is None:
            return PityResponse.failed("目录不存在")
        dumped_case_data = dump_case_data(form.data)
        old_case_data = str(model.case_data or "")
        new_case_data = str(dumped_case_data or "")
        case_data_changed = old_case_data != new_case_data

        old_title = model.title
        old_project_id = model.project_id
        old_directory_id = model.directory_id

        model.title = form.title
        model.project_id = form.project_id
        model.directory_id = form.directory_id
        model.case_data = dumped_case_data
        model.sort_index = form.sort_index
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()

        file_meta_changed = (
            old_title != model.title
            or old_project_id != model.project_id
            or old_directory_id != model.directory_id
        )
        await session.commit()
        await session.refresh(model)
        user_result = await session.execute(select(User).where(User.id == model.create_user))
        user = user_result.scalars().first()

    if case_data_changed:
        asyncio.create_task(
            sync_functional_case_items_async(
                file_id=model.id,
                operator_user_id=user_info["id"],
                case_items=case_items,
                rebuild=True,
            )
        )
    elif file_meta_changed:
        asyncio.create_task(
            sync_functional_case_items_async(
                file_id=model.id,
                operator_user_id=user_info["id"],
                rebuild=False,
            )
        )

    data = serialize_model(model)
    data["data"] = form.data
    data["case_count"] = int(stats["case_count"] or 0)
    data["pass_count"] = int(stats.get("pass_count") or 0)
    data["case_num"] = data["case_count"]
    data["create_user_name"] = pick_user_name(user)
    data["creator_name"] = data["create_user_name"]
    return PityResponse.success(data)


@router.post("/file/ai-generate")
async def ai_generate_file(form: FunctionalCaseAIGenerateForm, _=Depends(Permission())):
    if not form.requirement_text and not form.instruction_text and not form.images:
        return PityResponse.failed("请至少提供需求描述、生成要求或需求截图")
    try:
        ai_payload = call_kimi_generate(form)
        title, data = normalize_ai_case_data(ai_payload, form.title)
        stats = analyze_case_data(data)
        if stats["conflicts"]:
            return PityResponse.failed(f"{'、'.join(stats['conflicts'])}用例冲突")
        return PityResponse.success({
            "title": title,
            "data": data,
            "case_count": int(stats["case_count"] or 0),
            "pass_count": int(stats.get("pass_count") or 0),
            "case_num": int(stats["case_count"] or 0),
        })
    except Exception as exc:
        return PityResponse.failed(str(exc))


@router.post("/file/move")
async def move_file(form: FunctionalCaseFileMoveForm, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        result = await session.execute(
            select(PityFunctionalCaseFile).where(
                PityFunctionalCaseFile.id == form.id,
                PityFunctionalCaseFile.deleted_at == 0,
                PityFunctionalCaseFile.project_id == form.project_id,
            )
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("功能用例不存在")
        directory_result = await session.execute(
            select(PityFunctionalCaseDirectory).where(
                PityFunctionalCaseDirectory.id == form.directory_id,
                PityFunctionalCaseDirectory.deleted_at == 0,
                PityFunctionalCaseDirectory.project_id == form.project_id,
            )
        )
        if directory_result.scalars().first() is None:
            return PityResponse.failed("目标目录不存在")
        model.directory_id = form.directory_id
        model.project_id = form.project_id
        model.sort_index = form.sort_index
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.commit()
        await session.refresh(model)
    return PityResponse.success(serialize_model(model))


@router.get("/file/delete")
async def delete_file(id: int, project_id: int = None, user_info=Depends(Permission())):
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        filters = [PityFunctionalCaseFile.id == id, PityFunctionalCaseFile.deleted_at == 0]
        if project_id is not None:
            filters.append(PityFunctionalCaseFile.project_id == project_id)
        result = await session.execute(select(PityFunctionalCaseFile).where(*filters))
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("功能用例不存在")
        model.deleted_at = int(datetime.now().timestamp())
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.execute(
            text(
                "UPDATE pity_functional_case_item "
                "SET deleted_at=:deleted_at, update_user=:update_user, updated_at=:updated_at "
                "WHERE file_id=:file_id AND deleted_at=0"
            ),
            {
                "deleted_at": model.deleted_at,
                "update_user": user_info["id"],
                "updated_at": datetime.now(),
                "file_id": model.id,
            },
        )
        await session.commit()
    return PityResponse.success()
