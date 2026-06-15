from pydantic.v1 import BaseModel, validator

from app.schema.base import ArgusModel


class MQConfigForm(BaseModel):
    id: int = None
    env: int
    name: str
    mq_type: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    virtual_host: str = "/"
    use_ssl: bool = False
    description: str = ""

    @validator("env", "name", "mq_type", "host", "port")
    def data_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class MQPublishForm(BaseModel):
    id: int
    destination: str
    body: str
    key: str = ""
    headers: str = "{}"

    @validator("id", "destination")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class MQConsumeForm(BaseModel):
    id: int
    destination: str
    limit: int = 5
    auto_ack: bool = True
    timeout_ms: int = 3000
    group_id: str = "argus-mq-preview"

    @validator("id", "destination")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class MQConsumerStatsForm(BaseModel):
    id: int
    destination: str
    group_id: str = "argus-mq-preview"

    @validator("id", "destination")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class RabbitQueueListForm(BaseModel):
    id: int

    @validator("id")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class KafkaTopicListForm(BaseModel):
    id: int

    @validator("id")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class KafkaTopicMessagesForm(BaseModel):
    id: int
    topic: str
    limit: int = 100
    partition: int = None
    before_offset: int = None

    @validator("id", "topic")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class KafkaConsumerGroupListForm(BaseModel):
    id: int

    @validator("id")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class KafkaConsumerGroupDetailForm(BaseModel):
    id: int
    group_id: str

    @validator("id", "group_id")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class RabbitGetMessagesForm(BaseModel):
    id: int
    queue: str
    count: int = 5
    auto_ack: bool = False
    requeue: bool = True
    encoding: str = "utf-8"

    @validator("id", "queue")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)


class RabbitQueueOperateForm(BaseModel):
    id: int
    queue: str

    @validator("id", "queue")
    def required_not_empty(cls, v):
        return ArgusModel.not_empty(v)
