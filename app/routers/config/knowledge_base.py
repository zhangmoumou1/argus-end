from fastapi import Depends

from app.crud.config.KnowledgeBaseDao import KnowledgeBaseDao
from app.handler.fatcory import PityResponse
from app.models.knowledge_base import PityKnowledgeBase
from app.routers import Permission, get_session
from app.routers.config.environment import router
from app.schema.knowledge_base import KnowledgeBaseForm
from config import Config


@router.get("/knowledge/list")
async def list_knowledge(page: int = 1, size: int = 12, title: str = "", _=Depends(Permission())):
    data, total = await KnowledgeBaseDao.list_docs(page, size, title)
    return PityResponse.success_with_size(data=data, total=total)


@router.post("/knowledge/insert")
async def insert_knowledge(data: KnowledgeBaseForm, user_info=Depends(Permission(Config.ADMIN))):
    model = PityKnowledgeBase(
        title=data.title.strip(),
        summary=(data.summary or "").strip(),
        content=data.content,
        user=user_info['id']
    )
    await KnowledgeBaseDao.insert(model=model, log=True)
    return PityResponse.success(model.id)


@router.post("/knowledge/update")
async def update_knowledge(data: KnowledgeBaseForm, user_info=Depends(Permission(Config.ADMIN))):
    if data.id is None:
        return PityResponse.failed("id不能为空")

    # 使用表单对象更新，避免 SQLAlchemy 实例状态字段参与序列化
    data.title = data.title.strip()
    data.summary = (data.summary or "").strip()
    ans = await KnowledgeBaseDao.update_record_by_id(user_info['id'], data, True, True)
    return PityResponse.success(PityResponse.model_to_dict(ans))


@router.get("/knowledge/delete")
async def delete_knowledge(id: int, user_info=Depends(Permission(Config.ADMIN)), session=Depends(get_session)):
    await KnowledgeBaseDao.delete_record_by_id(session, user_info['id'], id, log=True)
    return PityResponse.success()
