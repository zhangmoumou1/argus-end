from sqlalchemy import Column, String, Text

from app.models.basic import PityBase


class PityKnowledgeBase(PityBase):
    __tablename__ = "pity_knowledge_base"

    title = Column(String(128), nullable=False, comment="文档标题")
    summary = Column(String(512), nullable=True, comment="文档摘要")
    content = Column(Text, nullable=False, comment="文档内容")

    __tag__ = "知识库"
    __fields__ = (title, summary, content)
    __alias__ = dict(title="标题", summary="摘要", content="文档内容")
    __show__ = 1

    def __init__(self, title: str, summary: str, content: str, user: int):
        super().__init__(user)
        self.title = title
        self.summary = summary
        self.content = content
