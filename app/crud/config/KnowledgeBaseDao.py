from sqlalchemy import select, func

from app.crud import Mapper, ModelWrapper
from app.models import async_session
from app.models.knowledge_base import PityKnowledgeBase
from app.models.user import User


@ModelWrapper(PityKnowledgeBase)
class KnowledgeBaseDao(Mapper):

    @staticmethod
    async def list_docs(page: int, size: int, title: str = ""):
        try:
            filters = [PityKnowledgeBase.deleted_at == 0]
            if title:
                filters.append(PityKnowledgeBase.title.like(f"%{title}%"))

            async with async_session() as session:
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
                for doc, create_user_name in result.all():
                    item = {
                        "id": doc.id,
                        "title": doc.title,
                        "summary": doc.summary,
                        "content": doc.content,
                        "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M:%S") if doc.created_at else None,
                        "updated_at": doc.updated_at.strftime("%Y-%m-%d %H:%M:%S") if doc.updated_at else None,
                        "create_user": doc.create_user,
                        "create_user_name": create_user_name,
                    }
                    rows.append(item)
                return rows, total
        except Exception as e:
            raise Exception(f"查询知识库列表失败: {str(e)}")
