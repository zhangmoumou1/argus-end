from sqlalchemy import Column, String, INT, UniqueConstraint

from app.models.basic import PityBase


class PityKnowledgeBaseCategory(PityBase):
    __tablename__ = 'pity_knowledge_base_category'

    name = Column(String(64), nullable=False, comment="分类名称")
    sort_order = Column(INT, nullable=True, default=0, comment="排序")

    __table_args__ = (
        UniqueConstraint('name', 'deleted_at'),
    )

    __fields__ = [name]
    __tag__ = "知识库分类"
    __alias__ = dict(name="分类名称", sort_order="排序")
    __show__ = 1

    def __init__(self, name: str, sort_order: int, user: int, id=None):
        super().__init__(user, id)
        self.name = name
        self.sort_order = sort_order
