import json
import os
import re
import time
from datetime import datetime

from fastapi import APIRouter, Depends
import requests
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from app.handler.fatcory import PityResponse
from app.models import async_session
from app.models.functional_case import PityFunctionalCaseDirectory, PityFunctionalCaseFile
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
        await session.commit()
    except OperationalError as exc:
        # duplicate column race in concurrent requests
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


def analyze_case_data(data):
    root = get_root_node(data)
    if not isinstance(root, dict):
        return {"case_count": 0, "conflicts": []}

    case_count = 0
    conflict_nodes = []
    conflict_seen = set()

    def add_conflict(text):
        key = truncate_case_text(text)
        if key in conflict_seen:
            return
        conflict_seen.add(key)
        conflict_nodes.append(key)

    def walk(node, priority_path):
        nonlocal case_count
        node_data = node.get("data") if isinstance(node, dict) else {}
        node_data = node_data if isinstance(node_data, dict) else {}
        node_text = node_data.get("text")
        raw_icons = node_data.get("icon")
        icons = raw_icons if isinstance(raw_icons, list) else [raw_icons] if raw_icons else []
        has_priority = any(isinstance(icon, str) and icon.startswith("priority_") for icon in icons)
        next_priority_path = list(priority_path)
        if has_priority:
            case_count += 1
            if priority_path:
                for item in priority_path:
                    add_conflict(item)
                add_conflict(node_text)
            next_priority_path.append(node_text)
        children = node.get("children") if isinstance(node, dict) else []
        for child in children or []:
            walk(child, next_priority_path)

    walk(root, [])
    return {"case_count": case_count, "conflicts": conflict_nodes}


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


def build_tree(records, file_case_count_map=None):
    node_map = {}
    roots = []
    file_case_count_map = file_case_count_map or {}
    own_case_count_map = {}
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
    for item in records:
        node = node_map[item.id]
        if item.parent and item.parent in node_map:
            node_map[item.parent]["children"].append(node)
        else:
            roots.append(node)
    for directory_id, count in file_case_count_map.items():
        own_case_count_map[directory_id] = own_case_count_map.get(directory_id, 0) + count

    def sort_nodes(nodes):
        nodes.sort(key=lambda x: (x.get("sort_index") or 0, x.get("id") or 0))
        for child in nodes:
            sort_nodes(child["children"])

    def calc_case_count(nodes):
        total = 0
        for node in nodes:
            child_total = calc_case_count(node["children"])
            own = own_case_count_map.get(node["id"], 0)
            node["case_count"] = own + child_total
            total += node["case_count"]
        return total

    sort_nodes(roots)
    calc_case_count(roots)
    return roots


def read_case_file(file_path):
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"read functional case file failed, path={file_path}, error={exc}")
        return None


def dump_case_data(data):
    try:
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.warning(f"dump functional case data failed, error={exc}")
        return None


def parse_case_data(case_text, source='database'):
    text_value = str(case_text or '').strip()
    if not text_value:
        return None
    try:
        return json.loads(text_value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"read functional case data failed, source={source}, error={exc}")
        return None


def read_case_payload(model):
    if model is None:
        return None
    case_data = parse_case_data(getattr(model, 'case_data', None), source='database')
    if case_data is not None:
        return case_data
    return read_case_file(getattr(model, 'file_path', None))


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
    file_case_count_map = {}
    for item in files:
        stats = analyze_case_data(read_case_payload(item))
        file_case_count_map[item.directory_id] = file_case_count_map.get(item.directory_id, 0) + stats["case_count"]
    return PityResponse.success(build_tree(records, file_case_count_map))


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
        for item in directory_result.scalars().all():
            item.deleted_at = now_deleted
            item.update_user = user_info["id"]
            item.updated_at = datetime.now()
        for item in case_result.scalars().all():
            item.deleted_at = now_deleted
            item.update_user = user_info["id"]
            item.updated_at = datetime.now()
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
            user_result = await session.execute(
                select(User).where(User.id.in_(user_ids))
            )
            user_name_map = {item.id: pick_user_name(item) for item in user_result.scalars().all()}
        data = []
        for item in files:
            case_data = read_case_payload(item)
            stats = analyze_case_data(case_data)
            row = serialize_model(item)
            row["case_count"] = int(stats["case_count"] or 0)
            row["case_num"] = row["case_count"]
            row["create_user_name"] = user_name_map.get(item.create_user, "")
            row["creator_name"] = row["create_user_name"]
            data.append(row)
    return PityResponse.success_with_size(data=data, total=total)


@router.get("/file/query")
async def query_file(id: int, project_id: int = None, _=Depends(Permission())):
    async with async_session() as session:
        await ensure_functional_case_schema(session)
        filters = [
            PityFunctionalCaseFile.id == id,
            PityFunctionalCaseFile.deleted_at == 0,
        ]
        if project_id is not None:
            filters.append(PityFunctionalCaseFile.project_id == project_id)
        result = await session.execute(
            select(PityFunctionalCaseFile).where(*filters)
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("功能用例不存在")
        user_result = await session.execute(
            select(User).where(User.id == model.create_user)
        )
        user = user_result.scalars().first()
        data = serialize_model(model)
        case_data = read_case_payload(model)
        stats = analyze_case_data(case_data)
        data["data"] = case_data
        data["case_count"] = int(stats["case_count"] or 0)
        data["case_num"] = data["case_count"]
        data["create_user_name"] = pick_user_name(user)
        data["creator_name"] = data["create_user_name"]
    return PityResponse.success(data)


@router.post("/file/insert")
async def insert_file(form: FunctionalCaseFileForm, user_info=Depends(Permission())):
    stats = analyze_case_data(form.data)
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
        await session.commit()
        await session.refresh(model)
        user_result = await session.execute(
            select(User).where(User.id == model.create_user)
        )
        user = user_result.scalars().first()
    data = serialize_model(model)
    data["data"] = form.data
    data["case_count"] = int(stats["case_count"] or 0)
    data["case_num"] = data["case_count"]
    data["create_user_name"] = pick_user_name(user)
    data["creator_name"] = data["create_user_name"]
    return PityResponse.success(data)


@router.post("/file/update")
async def update_file(form: FunctionalCaseFileForm, user_info=Depends(Permission())):
    if not form.id:
        return PityResponse.failed("id不能为空")
    stats = analyze_case_data(form.data)
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
        model.title = form.title
        model.project_id = form.project_id
        model.directory_id = form.directory_id
        model.case_data = dump_case_data(form.data)
        model.sort_index = form.sort_index
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.commit()
        await session.refresh(model)
        user_result = await session.execute(
            select(User).where(User.id == model.create_user)
        )
        user = user_result.scalars().first()
    data = serialize_model(model)
    data["data"] = form.data
    data["case_count"] = int(stats["case_count"] or 0)
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
        filters = [
            PityFunctionalCaseFile.id == id,
            PityFunctionalCaseFile.deleted_at == 0,
        ]
        if project_id is not None:
            filters.append(PityFunctionalCaseFile.project_id == project_id)
        result = await session.execute(
            select(PityFunctionalCaseFile).where(*filters)
        )
        model = result.scalars().first()
        if model is None:
            return PityResponse.failed("功能用例不存在")
        model.deleted_at = int(datetime.now().timestamp())
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.commit()
    return PityResponse.success()
