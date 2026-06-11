import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, File, Form, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func, text

from app.crud.config.KnowledgeBaseDao import KnowledgeBaseDao
from app.handler.fatcory import PityResponse
from app.middleware.oss import OssClient, normalize_oss_upload_result
from app.models import async_session
from app.models.knowledge_base import PityKnowledgeBase
from app.models.user import User
from app.routers import Permission, get_session
from app.routers.config.environment import router
from app.schema.knowledge_base import KnowledgeBaseForm
from config import Config

KNOWLEDGE_BUCKET_NAME = "argus-end"
KNOWLEDGE_OBJECT_PREFIX = "knowledge"
KNOWLEDGE_CHARSET_READY = False
KNOWLEDGE_MAX_UPLOAD_SIZE = 20 * 1024 * 1024
KNOWLEDGE_ALLOWED_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".md",
    ".csv",
    ".zip",
    ".rar",
}


def _build_knowledge_object_key(filename: str):
    now = datetime.now()
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in KNOWLEDGE_ALLOWED_SUFFIXES:
        suffix = ".bin"
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    return f"{KNOWLEDGE_OBJECT_PREFIX}/{now:%Y}/{now:%m}/{stored_name}"


def _build_knowledge_asset_url(object_key: str, bucket_name: str = KNOWLEDGE_BUCKET_NAME):
    encoded_key = quote(str(object_key or "").strip(), safe="/")
    encoded_bucket = quote(str(bucket_name or KNOWLEDGE_BUCKET_NAME).strip(), safe="")
    return f"/config/knowledge/asset/view?object_key={encoded_key}&bucket_name={encoded_bucket}"


def _sanitize_mysql_utf8_text(value: str):
    if value is None:
        return value
    text = str(value)
    return ''.join(ch for ch in text if ord(ch) <= 0xFFFF)


async def _ensure_knowledge_charset(session):
    global KNOWLEDGE_CHARSET_READY
    if KNOWLEDGE_CHARSET_READY:
        return
    try:
        # 统一表字符集，避免中文/特殊字符写入报 1366
        await session.execute(text(
            "ALTER TABLE pity_knowledge_base CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
    except Exception:
        # 可能已是目标字符集或权限受限，继续做列级兜底
        pass
    try:
        await session.execute(text(
            "ALTER TABLE pity_knowledge_base MODIFY COLUMN content LONGTEXT "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '文档内容'"
        ))
    except Exception:
        pass
    KNOWLEDGE_CHARSET_READY = True


@router.get("/knowledge/list")
async def list_knowledge(page: int = 1, size: int = 12, title: str = "", category: str = "", _=Depends(Permission())):
    async with async_session() as session:
        filters = [PityKnowledgeBase.deleted_at == 0]
        if title:
            filters.append(PityKnowledgeBase.title.like(f"%{title}%"))
        if category:
            filters.append(PityKnowledgeBase.category == category)

        total_sql = select(func.count(PityKnowledgeBase.id)).where(*filters)
        total = (await session.execute(total_sql)).scalar() or 0

        sql = (
            select(PityKnowledgeBase, User.name.label("create_user_name"))
            .outerjoin(User, User.id == PityKnowledgeBase.create_user)
            .where(*filters)
            .order_by(PityKnowledgeBase.id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
        result = await session.execute(sql)
        rows = []
        for kb, create_user_name in result.all():
            item = PityResponse.model_to_dict(kb)
            item["create_user_name"] = create_user_name
            rows.append(item)

        return PityResponse.success_with_size(data=rows, total=total)


@router.get("/knowledge/public/list")
async def list_public_knowledge(page: int = 1, size: int = 1000, title: str = "", category: str = ""):
    async with async_session() as session:
        filters = [PityKnowledgeBase.deleted_at == 0]
        if title:
            filters.append(PityKnowledgeBase.title.like(f"%{title}%"))
        if category:
            filters.append(PityKnowledgeBase.category == category)

        total_sql = select(func.count(PityKnowledgeBase.id)).where(*filters)
        total = (await session.execute(total_sql)).scalar() or 0

        sql = (
            select(PityKnowledgeBase, User.name.label("create_user_name"))
            .outerjoin(User, User.id == PityKnowledgeBase.create_user)
            .where(*filters)
            .order_by(PityKnowledgeBase.id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
        result = await session.execute(sql)
        rows = []
        for kb, create_user_name in result.all():
            item = PityResponse.model_to_dict(kb)
            item["create_user_name"] = create_user_name
            rows.append(item)

        return PityResponse.success_with_size(data=rows, total=total)


@router.post("/knowledge/insert")
async def insert_knowledge(data: KnowledgeBaseForm, user_info=Depends(Permission(Config.ADMIN))):
    async with async_session() as session:
        await _ensure_knowledge_charset(session)
        await session.commit()
    model = PityKnowledgeBase(
        title=data.title.strip(),
        summary=(data.summary or "").strip(),
        content=_sanitize_mysql_utf8_text(data.content),
        category=(data.category or "").strip(),
        user=user_info['id']
    )
    await KnowledgeBaseDao.insert(model=model, log=True)
    return PityResponse.success(model.id)


@router.post("/knowledge/update")
async def update_knowledge(data: KnowledgeBaseForm, user_info=Depends(Permission(Config.ADMIN))):
    if data.id is None:
        return PityResponse.failed("id不能为空")
    async with async_session() as session:
        await _ensure_knowledge_charset(session)
        await session.commit()

    data.title = data.title.strip()
    data.summary = (data.summary or "").strip()
    data.category = (data.category or "").strip()
    data.content = _sanitize_mysql_utf8_text(data.content)
    ans = await KnowledgeBaseDao.update_record_by_id(user_info['id'], data, True, True)
    return PityResponse.success(PityResponse.model_to_dict(ans))


@router.get("/knowledge/delete")
async def delete_knowledge(id: int, user_info=Depends(Permission(Config.ADMIN)), session=Depends(get_session)):
    await KnowledgeBaseDao.delete_record_by_id(session, user_info['id'], id, log=True)
    return PityResponse.success()


@router.get("/knowledge/asset/view")
async def view_knowledge_asset(object_key: str, bucket_name: str = KNOWLEDGE_BUCKET_NAME):
    try:
        normalized_key = str(object_key or "").replace("\\", "/").strip().strip("/")
        if not normalized_key:
            return PityResponse.failed("object_key不能为空")
        client = OssClient.get_oss_client()
        detail = await client.get_object_detail(
            normalized_key,
            bucket_name=str(bucket_name or KNOWLEDGE_BUCKET_NAME).strip() or KNOWLEDGE_BUCKET_NAME,
        )
        view_url = str(detail.get("view_url") or "").strip()
        if not view_url:
            return PityResponse.failed("资源访问地址不存在")
        return RedirectResponse(url=view_url, status_code=307)
    except Exception as exc:
        return PityResponse.failed(f"资源访问失败: {exc}")


@router.post("/knowledge/upload")
async def upload_knowledge_file(
    file: UploadFile = File(...),
    kind: str = Form("file"),
    user_info=Depends(Permission(Config.ADMIN)),
):
    try:
        content = await file.read()
        if not content:
            return PityResponse.failed("文件不能为空")
        if len(content) > KNOWLEDGE_MAX_UPLOAD_SIZE:
            return PityResponse.failed("文件不能超过20MB")
        object_key = _build_knowledge_object_key(file.filename)
        client = OssClient.get_oss_client()
        upload_result, file_size = await client.create_file(
            object_key,
            content,
            bucket_name=KNOWLEDGE_BUCKET_NAME,
            content_type=file.content_type or "application/octet-stream",
        )
        upload_meta = normalize_oss_upload_result(
            client,
            upload_result,
            object_key,
            bucket_name=KNOWLEDGE_BUCKET_NAME,
        )
        asset_url = _build_knowledge_asset_url(
            upload_meta.get("object_key") or object_key,
            upload_meta.get("bucket_name") or KNOWLEDGE_BUCKET_NAME,
        )
        return PityResponse.success({
            "file_name": file.filename,
            "stored_name": Path(object_key).name,
            "kind": kind or "file",
            "size": int(file_size or len(content)),
            "url": asset_url,
            "bucket_name": upload_meta.get("bucket_name") or KNOWLEDGE_BUCKET_NAME,
            "object_key": upload_meta.get("object_key") or object_key,
        })
    except Exception as exc:
        return PityResponse.failed(f"上传失败: {exc}")
