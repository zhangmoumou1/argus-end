from fastapi import Depends
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
            .order_by(PityKnowledgeBase.id.desc())
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
            .order_by(PityKnowledgeBase.id.desc())
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

