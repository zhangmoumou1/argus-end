"""
argus oss文件映射表
"""
from sqlalchemy import String, Column, UniqueConstraint

from app.models.basic import ArgusBase

units = (
    "B", "KB", "MB", "GB", "TB"
)


class ArgusOssFile(ArgusBase):
    # 因为没有目录的概念，都是目录+文件名
    file_path = Column(String(255), nullable=False, index=True, comment="文件路径")
    bucket_name = Column(String(64), nullable=False, comment="桶名称")
    object_key = Column(String(255), nullable=False, comment="对象key")
    file_size = Column(String(16), comment="文件大小")

    __tablename__ = "argus_oss_file"
    __fields__ = (file_path, bucket_name, object_key, file_size)
    __tag__ = "oss"
    __alias__ = dict(file_path="文件路径", bucket_name="桶名称", object_key="对象Key", file_size="文件大小")
    __show__ = 1
    __table_args__ = (
        UniqueConstraint('file_path', 'deleted_at'),
    )

    def __init__(self, user, file_path, bucket_name, object_key, file_size, id=None):
        super().__init__(user, id)
        self.file_path = file_path
        self.bucket_name = bucket_name
        self.object_key = object_key
        self.file_size = file_size

    @staticmethod
    def get_size(file_size: int):
        """
        计算文件大小
        :param file_size:
        :return:
        """
        unit_index = 0
        while file_size >= 1024:
            # 说明可以写成kb
            file_size //= 1024
            unit_index += 1
        return f"{file_size}{units[unit_index]}"
