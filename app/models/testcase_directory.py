from datetime import datetime

from sqlalchemy import Column, INT, String, UniqueConstraint

from app.models.basic import PityBase
from app.schema.testcase_directory import PityTestcaseDirectoryForm


class PityTestcaseDirectory(PityBase):
    """
    用例目录表
    """
    __tablename__ = 'pity_testcase_directory'
    __table_args__ = (
        UniqueConstraint('project_id', 'name', 'parent', 'deleted_at'),
    )
    id = Column(INT, primary_key=True)
    project_id = Column(INT, index=True)
    name = Column(String(18), nullable=False)
    parent = Column(INT)
    sort_index = Column(INT, nullable=False, default=0)

    __fields__ = [name]
    __tag__ = "用例目录"
    __alias__ = dict(name="目录名称", parent="上级目录", sort_index="排序", project_id="项目")
    __show__ = 1

    def __init__(self, form: PityTestcaseDirectoryForm, user):
        super().__init__(user)
        self.project_id = form.project_id
        self.name = form.name
        self.parent = form.parent
        self.sort_index = form.sort_index if form.sort_index is not None else 0
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.create_user = user
        self.update_user = user
