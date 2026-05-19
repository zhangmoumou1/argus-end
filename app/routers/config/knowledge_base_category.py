from datetime import datetime

from fastapi import Depends
from sqlalchemy import select

from app.crud.operation.PityOperationDao import PityOperationDao
from app.enums.OperationEnum import OperationType
from app.handler.fatcory import PityResponse
from app.models import async_session
from app.models.knowledge_base_category import PityKnowledgeBaseCategory
from app.routers import Permission
from app.routers.config.environment import router
from app.schema.knowledge_base_category import KnowledgeBaseCategoryForm
from config import Config


@router.get("/knowledge/category/list")
async def list_knowledge_category(_=Depends(Permission())):
    async with async_session() as session:
        sql = (
            select(PityKnowledgeBaseCategory)
            .where(PityKnowledgeBaseCategory.deleted_at == 0)
            .order_by(PityKnowledgeBaseCategory.sort_order, PityKnowledgeBaseCategory.id.desc())
        )
        result = await session.execute(sql)
        data = result.scalars().all()
        return PityResponse.success(PityResponse.model_to_list(data))


@router.get("/knowledge/category/public/list")
async def list_public_knowledge_category():
    async with async_session() as session:
        sql = (
            select(PityKnowledgeBaseCategory)
            .where(PityKnowledgeBaseCategory.deleted_at == 0)
            .order_by(PityKnowledgeBaseCategory.sort_order, PityKnowledgeBaseCategory.id.desc())
        )
        result = await session.execute(sql)
        data = result.scalars().all()
        return PityResponse.success(PityResponse.model_to_list(data))


@router.post("/knowledge/category/insert")
async def insert_knowledge_category(data: KnowledgeBaseCategoryForm, user_info=Depends(Permission(Config.ADMIN))):
    async with async_session() as session:
        async with session.begin():
            query = await session.execute(
                select(PityKnowledgeBaseCategory)
                .where(PityKnowledgeBaseCategory.name == data.name.strip(), PityKnowledgeBaseCategory.deleted_at == 0)
            )
            if query.scalars().first() is not None:
                return PityResponse.failed("分类已存在")
            model = PityKnowledgeBaseCategory(
                name=data.name.strip(),
                sort_order=data.sort_order or 0,
                user=user_info['id']
            )
            session.add(model)
            await session.flush()
            await PityOperationDao.insert_log(session, user_info['id'], OperationType.INSERT, model, key=model.id)
    return PityResponse.success()


@router.post("/knowledge/category/update")
async def update_knowledge_category(data: KnowledgeBaseCategoryForm, user_info=Depends(Permission(Config.ADMIN))):
    if data.id is None:
        return PityResponse.failed("id不能为空")
    async with async_session() as session:
        async with session.begin():
            query = await session.execute(
                select(PityKnowledgeBaseCategory)
                .where(
                    PityKnowledgeBaseCategory.name == data.name.strip(),
                    PityKnowledgeBaseCategory.deleted_at == 0,
                    PityKnowledgeBaseCategory.id != data.id
                )
            )
            if query.scalars().first() is not None:
                return PityResponse.failed("分类名称已存在")
            result = await session.execute(
                select(PityKnowledgeBaseCategory).where(PityKnowledgeBaseCategory.id == data.id)
            )
            model = result.scalars().first()
            if model is None:
                return PityResponse.failed("分类不存在")
            old = PityKnowledgeBaseCategory(model.name, model.sort_order or 0, model.create_user, id=model.id)
            old.parent = getattr(model, "parent", None)
            old.project_id = getattr(model, "project_id", None)
            model.name = data.name.strip()
            model.sort_order = data.sort_order or 0
            model.update_user = user_info['id']
            model.updated_at = datetime.now()
            await session.flush()
            await PityOperationDao.insert_log(
                session,
                user_info['id'],
                OperationType.UPDATE,
                model,
                old,
                model.id,
                changed=["name", "sort_order"],
            )
    return PityResponse.success()


@router.get("/knowledge/category/delete")
async def delete_knowledge_category(id: int, user_info=Depends(Permission(Config.ADMIN))):
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(PityKnowledgeBaseCategory).where(PityKnowledgeBaseCategory.id == id)
            )
            model = result.scalars().first()
            if model is None:
                return PityResponse.failed("分类不存在")
            model.deleted_at = int(datetime.now().timestamp())
            model.update_user = user_info['id']
            model.updated_at = datetime.now()
            await session.flush()
            await PityOperationDao.insert_log(session, user_info['id'], OperationType.DELETE, model, key=id)
    return PityResponse.success()

