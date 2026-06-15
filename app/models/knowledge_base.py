from sqlalchemy import Column, String, Text

from app.models.basic import ArgusBase


class ArgusKnowledgeBase(ArgusBase):
    __tablename__ = "argus_knowledge_base"

    title = Column(String(128), nullable=False, comment="文档标题")
    summary = Column(String(512), nullable=True, comment="文档摘要")
    content = Column(Text, nullable=False, comment="文档内容")
    category = Column(String(64), nullable=True, comment="文档分类")

    __tag__ = "知识库"
    __fields__ = (title, summary, content, category)
    __alias__ = dict(title="标题", summary="摘要", content="文档内容", category="分类")
    __show__ = 1

    def __init__(self, title: str, summary: str, content: str, user: int, category: str = None):
        super().__init__(user)
        self.title = title
        self.summary = summary
        self.content = content
        self.category = category
