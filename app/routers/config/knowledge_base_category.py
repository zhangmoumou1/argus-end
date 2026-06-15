from datetime import datetime

from fastapi import Depends
from sqlalchemy import select

from app.crud.operation.ArgusOperationDao import ArgusOperationDao
from app.enums.OperationEnum import OperationType
from app.handler.fatcory import ArgusResponse
from app.models import async_session
from app.models.knowledge_base_category import ArgusKnowledgeBaseCategory
from app.routers import Permission
from app.routers.config.environment import router
from app.schema.knowledge_base_category import KnowledgeBaseCategoryForm
from config import Config


@router.get("/knowledge/category/list")
async def list_knowledge_category(_=Depends(Permission())):
    async with async_session() as session:
        sql = (
            select(ArgusKnowledgeBaseCategory)
            .where(ArgusKnowledgeBaseCategory.deleted_at == 0)
            .order_by(ArgusKnowledgeBaseCategory.sort_order, ArgusKnowledgeBaseCategory.id.desc())
        )
        result = await session.execute(sql)
        data = result.scalars().all()
        return ArgusResponse.success(ArgusResponse.model_to_list(data))


@router.get("/knowledge/category/public/list")
async def list_public_knowledge_category():
    async with async_session() as session:
        sql = (
            select(ArgusKnowledgeBaseCategory)
            .where(ArgusKnowledgeBaseCategory.deleted_at == 0)
            .order_by(ArgusKnowledgeBaseCategory.sort_order, ArgusKnowledgeBaseCategory.id.desc())
        )
        result = await session.execute(sql)
        data = result.scalars().all()
        return ArgusResponse.success(ArgusResponse.model_to_list(data))


@router.post("/knowledge/category/insert")
async def insert_knowledge_category(data: KnowledgeBaseCategoryForm, user_info=Depends(Permission(Config.ADMIN))):
    async with async_session() as session:
        async with session.begin():
            query = await session.execute(
                select(ArgusKnowledgeBaseCategory)
                .where(ArgusKnowledgeBaseCategory.name == data.name.strip(), ArgusKnowledgeBaseCategory.deleted_at == 0)
            )
            if query.scalars().first() is not None:
                return ArgusResponse.failed("分类已存在")
            model = ArgusKnowledgeBaseCategory(
                name=data.name.strip(),
                sort_order=data.sort_order or 0,
                user=user_info['id']
            )
            session.add(model)
            await session.flush()
            await ArgusOperationDao.insert_log(session, user_info['id'], OperationType.INSERT, model, key=model.id)
    return ArgusResponse.success()


@router.post("/knowledge/category/update")
async def update_knowledge_category(data: KnowledgeBaseCategoryForm, user_info=Depends(Permission(Config.ADMIN))):
    if data.id is None:
        return ArgusResponse.failed("id不能为空")
    async with async_session() as session:
        async with session.begin():
            query = await session.execute(
                select(ArgusKnowledgeBaseCategory)
                .where(
                    ArgusKnowledgeBaseCategory.name == data.name.strip(),
                    ArgusKnowledgeBaseCategory.deleted_at == 0,
                    ArgusKnowledgeBaseCategory.id != data.id
                )
            )
            if query.scalars().first() is not None:
                return ArgusResponse.failed("分类名称已存在")
            result = await session.execute(
                select(ArgusKnowledgeBaseCategory).where(ArgusKnowledgeBaseCategory.id == data.id)
            )
            model = result.scalars().first()
            if model is None:
                return ArgusResponse.failed("分类不存在")
            old = ArgusKnowledgeBaseCategory(model.name, model.sort_order or 0, model.create_user, id=model.id)
            old.parent = getattr(model, "parent", None)
            old.project_id = getattr(model, "project_id", None)
            model.name = data.name.strip()
            model.sort_order = data.sort_order or 0
            model.update_user = user_info['id']
            model.updated_at = datetime.now()
            await session.flush()
            await ArgusOperationDao.insert_log(
                session,
                user_info['id'],
                OperationType.UPDATE,
                model,
                old,
                model.id,
                changed=["name", "sort_order"],
            )
    return ArgusResponse.success()


@router.get("/knowledge/category/delete")
async def delete_knowledge_category(id: int, user_info=Depends(Permission(Config.ADMIN))):
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(ArgusKnowledgeBaseCategory).where(ArgusKnowledgeBaseCategory.id == id)
            )
            model = result.scalars().first()
            if model is None:
                return ArgusResponse.failed("分类不存在")
            model.deleted_at = int(datetime.now().timestamp())
            model.update_user = user_info['id']
            model.updated_at = datetime.now()
            await session.flush()
            await ArgusOperationDao.insert_log(session, user_info['id'], OperationType.DELETE, model, key=id)
    return ArgusResponse.success()

