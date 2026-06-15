from app.crud import Mapper, ModelWrapper
from app.models.oss_file import ArgusOssFile


@ModelWrapper(ArgusOssFile)
class ArgusOssDao(Mapper):
    pass
