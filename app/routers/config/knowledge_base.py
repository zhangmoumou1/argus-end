import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import Depends, File, Form, UploadFile
from sqlalchemy import select, func

from app.crud.config.KnowledgeBaseDao import KnowledgeBaseDao
from app.handler.fatcory import PityResponse
from app.models import async_session
from app.models.knowledge_base import PityKnowledgeBase
from app.models.user import User
from app.routers import Permission, get_session
from app.routers.config.environment import router
from app.schema.knowledge_base import KnowledgeBaseForm
from config import Config

KNOWLEDGE_UPLOAD_ROOT = os.path.join("statics", "knowledge")
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


def _build_knowledge_url(relative_path: str):
    return f"/statics/{str(relative_path or '').replace(os.sep, '/')}"


def _build_knowledge_storage_path(filename: str):
    now = datetime.now()
    relative_dir = os.path.join("knowledge", f"{now:%Y}", f"{now:%m}")
    upload_dir = os.path.join("statics", relative_dir)
    os.makedirs(upload_dir, exist_ok=True)

    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in KNOWLEDGE_ALLOWED_SUFFIXES:
        suffix = ".bin"

    stored_name = f"{uuid.uuid4().hex}{suffix}"
    absolute_path = os.path.join(upload_dir, stored_name)
    relative_path = os.path.join(relative_dir, stored_name)
    return absolute_path, relative_path


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
    model = PityKnowledgeBase(
        title=data.title.strip(),
        summary=(data.summary or "").strip(),
        content=data.content,
        category=(data.category or "").strip(),
        user=user_info['id']
    )
    await KnowledgeBaseDao.insert(model=model, log=True)
    return PityResponse.success(model.id)


@router.post("/knowledge/update")
async def update_knowledge(data: KnowledgeBaseForm, user_info=Depends(Permission(Config.ADMIN))):
    if data.id is None:
        return PityResponse.failed("id不能为空")

    data.title = data.title.strip()
    data.summary = (data.summary or "").strip()
    data.category = (data.category or "").strip()
    ans = await KnowledgeBaseDao.update_record_by_id(user_info['id'], data, True, True)
    return PityResponse.success(PityResponse.model_to_dict(ans))


@router.get("/knowledge/delete")
async def delete_knowledge(id: int, user_info=Depends(Permission(Config.ADMIN)), session=Depends(get_session)):
    await KnowledgeBaseDao.delete_record_by_id(session, user_info['id'], id, log=True)
    return PityResponse.success()


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
        absolute_path, relative_path = _build_knowledge_storage_path(file.filename)
        with open(absolute_path, "wb") as f:
            f.write(content)
        return PityResponse.success({
            "file_name": file.filename,
            "stored_name": os.path.basename(absolute_path),
            "kind": kind or "file",
            "size": len(content),
            "url": _build_knowledge_url(relative_path),
        })
    except Exception as exc:
        return PityResponse.failed(f"上传失败: {exc}")

