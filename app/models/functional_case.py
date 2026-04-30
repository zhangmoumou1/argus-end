from sqlalchemy import BIGINT, Column, ForeignKey, INT, String, TEXT

from app.models.basic import PityBase


class PityFunctionalCaseDirectory(PityBase):
    __tablename__ = "pity_functional_case_directory"

    project_id = Column(INT, ForeignKey("pity_project.id"), index=True, nullable=True, comment="所属项目")
    name = Column(String(64), nullable=False, comment="目录名称")
    parent = Column(INT, nullable=True, comment="父目录")
    sort_index = Column(INT, nullable=False, default=0, comment="排序")

    def __init__(self, project_id, name, user, parent=None, sort_index=0):
        super().__init__(user)
        self.project_id = project_id
        self.name = name
        self.parent = parent
        self.sort_index = sort_index


class PityFunctionalCaseFile(PityBase):
    __tablename__ = "pity_functional_case_file"

    project_id = Column(INT, ForeignKey("pity_project.id"), index=True, nullable=True, comment="所属项目")
    title = Column(String(128), nullable=False, comment="功能用例名称")
    directory_id = Column(INT, nullable=False, comment="所属目录")
    file_path = Column(String(255), nullable=False, comment="功能用例JSON文件路径")
    case_data = Column(TEXT, nullable=True, comment="功能用例JSON内容")
    sort_index = Column(INT, nullable=False, default=0, comment="排序")

    def __init__(self, project_id, title, directory_id, file_path, user, sort_index=0, case_data=None):
        super().__init__(user)
        self.project_id = project_id
        self.title = title
        self.directory_id = directory_id
        self.file_path = file_path
        self.case_data = case_data
        self.sort_index = sort_index


class PityFunctionalCaseItem(PityBase):
    __tablename__ = "pity_functional_case_item"

    project_id = Column(INT, ForeignKey("pity_project.id"), index=True, nullable=True, comment="所属项目")
    directory_id = Column(INT, nullable=False, comment="所属目录")
    file_id = Column(INT, nullable=False, index=True, comment="所属功能用例文件ID")
    case_uid = Column(String(64), nullable=False, index=True, comment="用例稳定标识")
    file_title = Column(String(128), nullable=False, comment="功能用例文件标题")
    case_name = Column(String(512), nullable=False, comment="功能用例名称")
    case_path = Column(TEXT, nullable=True, comment="功能用例节点路径")
    case_priority = Column(String(32), nullable=True, comment="优先级")
    case_pass = Column(INT, nullable=False, default=0, comment="是否通过(1通过,0不通过)")

    def __init__(self, project_id, directory_id, file_id, case_uid, file_title, case_name, user, case_path=None,
                 case_priority=None, case_pass=0):
        super().__init__(user)
        self.project_id = project_id
        self.directory_id = directory_id
        self.file_id = file_id
        self.case_uid = case_uid
        self.file_title = file_title
        self.case_name = case_name
        self.case_path = case_path
        self.case_priority = case_priority
        self.case_pass = case_pass


class PityFunctionalCaseSkillDoc(PityBase):
    __tablename__ = "pity_functional_case_skill_doc"

    title = Column(String(128), nullable=False, comment="文档名称")
    description = Column(String(500), nullable=True, comment="文档描述")
    doc_type = Column(String(32), nullable=False, default="skill_md", comment="文档类型")
    content = Column(TEXT, nullable=False, comment="Markdown内容")
    is_shared = Column(INT, nullable=False, default=1, comment="是否共享")

    def __init__(self, title, doc_type, content, user, description="", is_shared=1):
        super().__init__(user)
        self.title = title
        self.description = description
        self.doc_type = doc_type
        self.content = content
        self.is_shared = is_shared


class PityFunctionalCaseSkillTask(PityBase):
    __tablename__ = "pity_functional_case_skill_task"

    project_id = Column(INT, nullable=False, default=0, comment="所属项目")
    title = Column(String(128), nullable=False, comment="用例标题")
    status = Column(String(32), nullable=False, default="pending", comment="任务状态")
    requirement_text = Column(TEXT, nullable=True, comment="需求文本")
    instruction_text = Column(TEXT, nullable=True, comment="额外提示")
    selected_doc_ids = Column(TEXT, nullable=True, comment="选中文档ID")
    input_payload = Column(TEXT, nullable=True, comment="任务输入")
    runtime_dir = Column(String(255), nullable=True, comment="运行目录")
    stage = Column(String(64), nullable=False, default="queued", comment="执行阶段")
    stage_text = Column(String(255), nullable=True, comment="阶段说明")
    progress = Column(INT, nullable=False, default=0, comment="进度")
    review_provider = Column(String(32), nullable=True, comment="评审模型")
    review_rounds = Column(INT, nullable=False, default=0, comment="评审轮次")
    task_logs = Column(TEXT, nullable=True, comment="任务日志")
    result_file_path = Column(String(255), nullable=True, comment="结果JSON路径")
    result_md_path = Column(String(255), nullable=True, comment="结果Markdown路径")
    result_xmind_path = Column(String(255), nullable=True, comment="结果XMind路径")
    result_title = Column(String(128), nullable=True, comment="结果标题")
    result_case_count = Column(INT, nullable=False, default=0, comment="生成用例数")
    result_payload = Column(TEXT, nullable=True, comment="结果JSON")
    error_message = Column(TEXT, nullable=True, comment="失败原因")
    finished_at = Column(BIGINT, nullable=False, default=0, comment="完成时间戳")

    def __init__(self, project_id, title, user, requirement_text="", instruction_text="", selected_doc_ids=""):
        super().__init__(user)
        self.project_id = project_id
        self.title = title
        self.status = "pending"
        self.requirement_text = requirement_text
        self.instruction_text = instruction_text
        self.selected_doc_ids = selected_doc_ids
        self.stage = "queued"
        self.stage_text = "任务已创建，等待执行"
        self.progress = 0
        self.review_rounds = 0
        self.task_logs = "[]"
        self.result_case_count = 0
        self.finished_at = 0
