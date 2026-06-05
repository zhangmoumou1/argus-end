from enum import Enum


class OssEnum(Enum):
    ALIYUN = "aliyun"
    QINIU = "qiniu"
    TENCENT = "cos"
    S3 = "s3"
