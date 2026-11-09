import asyncio
import json
import copy
import pytz
import time
import random
import aiohttp
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Callable, Union, Tuple
from datetime import datetime, timedelta
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError, UnknownTopicOrPartitionError, NotLeaderForPartitionError
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, descriptor
from google.protobuf.json_format import ParseDict
from sqlalchemy import text
from app import init_logging


logger = init_logging()
NumericValue = Union[int, float]


async def get_product_function_config(module):
    from .products import enabled_products
    return next((x for x in await enabled_products() if x['module'] == module), None)


# ==================== 工具函数（保持不变） ====================
def str_to_datetime(time_str: str) -> datetime:
    return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")


def datetime_to_str(dt: datetime, fmt: str = "%Y%m%d%H%M%S") -> str:
    return dt.strftime(fmt)


def datetime_to_timestamp(dt: datetime) -> int:
    """
    将 datetime 转为时间戳。
    如果 dt 没有时区，假设它是北京时间（Asia/Shanghai）
    """
    if dt.tzinfo is None:
        beijing_tz = pytz.timezone('Asia/Shanghai')
        dt = beijing_tz.localize(dt)
    return int(dt.timestamp())


# ==================== Protobuf 动态构建器（保持不变） ====================
class DynamicProtobufBuilder:
    @staticmethod
    def create_tagdata_descriptor():
        field_name = descriptor_pb2.FieldDescriptorProto(
            name="name", number=1, type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING
        )
        field_time = descriptor_pb2.FieldDescriptorProto(
            name="time", number=2, type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING
        )
        field_value = descriptor_pb2.FieldDescriptorProto(
            name="value", number=3, type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING
        )
        return descriptor_pb2.DescriptorProto(
            name="TagData",
            field=[field_name, field_time, field_value]
        )

    @staticmethod
    def create_outer_descriptor():
        inner_desc = DynamicProtobufBuilder.create_tagdata_descriptor()

        field_tags = descriptor_pb2.FieldDescriptorProto(
            name="tags",
            number=1,
            type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
            type_name="TagData",
            label=descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
        )

        outer_proto = descriptor_pb2.DescriptorProto(
            name="TagList",
            field=[field_tags],
            nested_type=[inner_desc]
        )

        file_proto = descriptor_pb2.FileDescriptorProto()
        file_proto.name = "tag_data.proto"
        file_proto.package = "example"
        file_proto.message_type.add().CopyFrom(outer_proto)

        pool = descriptor_pool.DescriptorPool()
        pool.Add(file_proto)

        msg_desc = pool.FindMessageTypeByName("example.TagList")
        factory = message_factory.MessageFactory(pool)
        msg_class = message_factory.GetMessageClass(msg_desc) if hasattr(message_factory, 'GetMessageClass') else factory.GetPrototype(msg_desc)

        return msg_class


# ==================== 主类：支持 log_callback 实时推送 ====================
class MessageSender:
    def __init__(self, fields: Dict[str, Any], log_callback: Optional[Callable[[str], None]] = None):
        self.fields = fields
        self.module = fields.get("module")
        self.sendWay = fields.get("sendWay")
        self.topicName = fields.get("topicName")
        self.startTime = str_to_datetime(fields.get("startTime"))
        self.endTime = str_to_datetime(fields.get("endTime"))
        self.kafkaServer = fields.get("kafkaServer")
        self.messageFormat = fields.get("messageFormat")
        self.frequency = int(fields.get("frequency", 1))
        self.increment = int(fields.get("increment", 1))  # 默认步长

        # ✅ 新结构：从 tagList 解析标签配置
        self.tag_configs = fields.get("tagList", [])
        if not isinstance(self.tag_configs, list):
            self.tag_configs = []
        self.tag_config_map: Dict[str, Dict] = {
            item["pointTag"]: item for item in self.tag_configs
        }
        self.points: List[str] = [item["pointTag"] for item in self.tag_configs]

        self.current_values: Dict[str, NumericValue] = {}
        self.alternating_indexes: Dict[str, int] = {}
        self.session: aiohttp.ClientSession = None
        self.producer: AIOKafkaProducer = None
        self.kafka_bootstrap_servers: Optional[str] = None
        self.product_address: Optional[str] = None
        self.route_key: Optional[str] = None
        self.api_path: Optional[str] = None
        self.log_callback = log_callback

        self.protobuf_msg_class = None
        if self.module in ["tagXts", "tagScys", "tagRCC"] or (self.module == "Custom" and self.messageFormat == "Protobuf"):
            self.protobuf_msg_class = DynamicProtobufBuilder.create_outer_descriptor()

    async def __aenter__(self):
        product_cfg = await get_product_function_config(self.module)
        if product_cfg:
            cfg_send_way = (product_cfg.get("send_way") or "").upper()
            cfg_route_key = product_cfg.get("route_key")
            cfg_address = product_cfg.get("address")
            cfg_api_path = product_cfg.get("api_path")
            if cfg_send_way:
                self.sendWay = cfg_send_way
            if cfg_route_key:
                self.route_key = cfg_route_key
            if cfg_address:
                self.product_address = cfg_address
                if "KAFKA" in self.sendWay:
                    self.kafkaServer = cfg_address
            if cfg_api_path:
                self.api_path = cfg_api_path

        # 只要走 kafka 标签线路，就确保 protobuf 消息类可用（例如 tagSh 配置为 kafka）
        if self.route_key == "kafka" and self.module != "Custom" and self.protobuf_msg_class is None:
            self.protobuf_msg_class = DynamicProtobufBuilder.create_outer_descriptor()

        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        if "kafka" in (self.sendWay or "").lower():
            self.kafka_bootstrap_servers = self.kafkaServer or "192.168.8.134:9092"
            self.producer = AIOKafkaProducer(bootstrap_servers=self.kafka_bootstrap_servers)
            try:
                await self.producer.start()
            except BaseException:
                await self.__aexit__(None, None, None)
                raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self._log("HTTP 会话已关闭")
        if self.producer:
            await self.producer.stop()
            self._log("Kafka 生产者已关闭")

    def _parse_numeric_value(self, raw_value: Any) -> Optional[NumericValue]:
        try:
            decimal_value = Decimal(str(raw_value).strip())
        except (InvalidOperation, ValueError, TypeError, AttributeError):
            return None

        if decimal_value == decimal_value.to_integral_value():
            return int(decimal_value)
        return float(decimal_value)

    def _normalize_numeric_value(self, value: NumericValue) -> NumericValue:
        if isinstance(value, float):
            value = round(value, 2)
            if value.is_integer():
                return int(value)
        return value

    def _get_initial_value_for_tag(self, point: str) -> NumericValue:
        config = self.tag_config_map.get(point, {})
        value_str = config.get("value", "0")
        parsed_value = self._parse_numeric_value(value_str)
        if parsed_value is None:
            self._log(f"⚠️ 标签 {point} 的初始值无效: '{value_str}'，使用 0.0")
            return 0
        return parsed_value

    def _get_value_type_for_tag(self, point: str) -> str:
        return self.tag_config_map.get(point, {}).get("valueType", "Fixed")

    def _get_increasing_config(self, point: str) -> Tuple[NumericValue, NumericValue]:
        """获取 Increasing 类型的配置 (起始值, 步长)。

        value 字段支持两种格式：
        - 单个数字（如 "5"）：起始值=0，步长=5
        - 逗号分隔的两个数字（如 "100,1"）：起始值=100，步长=1
        默认返回 (0, 1)
        """
        config = self.tag_config_map.get(point, {})
        value_str = config.get("value", "1")

        parts = [item.strip() for item in value_str.replace("，", ",").split(",") if item.strip()]

        if len(parts) == 1:
            step = self._parse_numeric_value(parts[0])
            if step is None:
                self._log(f"⚠️ 标签 {point} 的步长无效: '{value_str}'，使用默认步长 1")
                return 0, 1
            return 0, step
        elif len(parts) >= 2:
            initial = self._parse_numeric_value(parts[0])
            step = self._parse_numeric_value(parts[1])
            if initial is None or step is None:
                self._log(f"⚠️ 标签 {point} 的递增配置无效: '{value_str}'，使用默认值 0,1")
                return 0, 1
            return initial, step

        self._log(f"⚠️ 标签 {point} 的递增配置无效: '{value_str}'，使用默认值 0,1")
        return 0, 1

    def _get_alternating_values_for_tag(self, point: str) -> Tuple[NumericValue, NumericValue]:
        config = self.tag_config_map.get(point, {})
        alt_a = config.get("altValueA")
        alt_b = config.get("altValueB")
        if alt_a is not None and alt_b is not None:
            raw_value = f"{alt_a},{alt_b}"
        else:
            raw_value = str(config.get("value", "0,1")).strip()
        parts = [item.strip() for item in raw_value.replace("，", ",").split(",") if item.strip()]
        if len(parts) != 2:
            self._log(f"⚠️ 标签 {point} 的交替值配置无效: '{raw_value}'，使用默认值 0,1")
            return 0, 1

        first = self._parse_numeric_value(parts[0])
        second = self._parse_numeric_value(parts[1])
        if first is None or second is None:
            self._log(f"⚠️ 标签 {point} 的交替值包含非数字: '{raw_value}'，使用默认值 0,1")
            return 0, 1
        return first, second

    def _update_value(self, point: str) -> NumericValue:
        value_type = self._get_value_type_for_tag(point)

        if value_type == "Increasing":
            initial_value, step = self._get_increasing_config(point)

            if point not in self.current_values:
                # 第一次：返回起始值
                self.current_values[point] = initial_value
                return initial_value
            else:
                # 后续：加上步长
                new_val = self._normalize_numeric_value(self.current_values[point] + step)
                self.current_values[point] = new_val
                return new_val

        elif value_type == "Alternating":
            first, second = self._get_alternating_values_for_tag(point)
            values = (first, second)
            idx = self.alternating_indexes.get(point, 0)
            value = values[idx]
            self.alternating_indexes[point] = 1 - idx
            return value

        elif value_type == "Random":
            try:
                min_val = int(self.fields.get("minValue", 0))
            except (ValueError, TypeError):
                min_val = 0
            try:
                max_val = int(self.fields.get("maxValue", 100))
            except (ValueError, TypeError):
                max_val = 100
            if min_val > max_val:
                min_val, max_val = max_val, min_val
            return random.randint(min_val, max_val)

        else:  # Fixed 或未知类型
            return self._get_initial_value_for_tag(point)

    async def _send_kafka_message(self, message_bytes: bytes, key: str = None, msg: str = None,
                                  log_time_str: str = None):
        key_bytes = key.encode('utf-8') if key else None
        max_retries = 6
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                # 先刷新元数据并探测分区可见性，避免 topic 创建/leader 变更窗口直接发送失败
                await self.producer.client.force_metadata_update()
                partitions = await self.producer.partitions_for(self.topicName)
                if not partitions:
                    raise UnknownTopicOrPartitionError(
                        f"Topic '{self.topicName}' metadata has no partitions yet"
                    )

                await self.producer.send_and_wait(
                    topic=self.topicName,
                    value=message_bytes,
                    key=key_bytes
                )
                last_error = None
                break
            except (UnknownTopicOrPartitionError, NotLeaderForPartitionError) as e:
                last_error = e
                # 这两类通常由 topic 自动创建延迟、leader 选举或 broker 元数据未就绪导致
                self._log(
                    f"⚠️ Kafka暂不可用，准备重试({attempt}/{max_retries}) | server：{self.kafka_bootstrap_servers} | Topic: {self.topicName} | 错误: {type(e).__name__}"
                )
                await asyncio.sleep(min(0.5 * attempt, 3.0))
            except KafkaError as e:
                last_error = e
                self._log(
                    f"⚠️ Kafka发送异常，准备重试({attempt}/{max_retries}) | server：{self.kafka_bootstrap_servers} | Topic: {self.topicName} | 错误: {type(e).__name__}: {e}"
                )
                await asyncio.sleep(min(0.5 * attempt, 3.0))

        if last_error is not None:
            raise last_error

        display_time = log_time_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log(f"✅ {display_time} Kafka消息[Protobuf序列化]发送成功|server：{self.kafka_bootstrap_servers} | Topic: {self.topicName} | 消息内容: {msg}")

    async def _post_request(self, url, payload, label='HTTP', log_time_str=None):
        await self._post_request_raw(url, payload)
        self._log(f'{label} 消息发送成功 | URL: {url} | 内容: {payload}')

    def _log(self, msg: str):
        if self.log_callback:
            asyncio.create_task(self.log_callback(msg))
        else:
            logger.info(f"[MessageSender] {msg}")

    async def run(self):
        self._log(f"开始执行 {self.sendWay} 任务")
        # 优先按照配置表中的 route_key/sendWay 分发线路
        if self.module == "Custom":
            await self._send_custom_messages()
        elif self.route_key == "kafka" or "kafka" in (self.sendWay or "").lower():
            await self._send_tagXts_messages()
        elif self.route_key == "api_point_byd" or self.module == "pointByd":
            await self._send_pointByd_messages()
        elif self.route_key == "api_point_cloud" or self.module in ["pointXts", "pointSh"]:
            await self._send_pointXts_or_pointSh_messages()

        else:
            self._log(f"不支持的模块: {self.module}")
            raise ValueError(f"Unsupported module: {self.module}")

    async def _send_tagXts_messages(self):
        if self.protobuf_msg_class is None:
            raise RuntimeError("Kafka标签线路缺少Protobuf消息定义，请检查模块配置或messageFormat")
        current_time = self.startTime
        while current_time <= self.endTime:
            time_str = datetime_to_str(current_time)
            proto_msg = self.protobuf_msg_class()

            for point in self.points:
                value = self._update_value(point)
                tag = proto_msg.tags.add()
                tag.name = point
                tag.time = time_str
                tag.value = str(value)
                self.current_values[point] = value

            # ✅ 不再硬编码 IoStatus —— 它已在 tagList 中（如有）

            readable_msg = [{"name": t.name, "time": t.time, "value": t.value} for t in proto_msg.tags]
            time_str_for_log = current_time.strftime("%Y-%m-%d %H:%M:%S")
            serialized = proto_msg.SerializeToString()
            await self._send_kafka_message(serialized, key=self.topicName, msg=readable_msg,
                                           log_time_str=time_str_for_log)

            current_time += timedelta(seconds=self.frequency)
            await asyncio.sleep(self.frequency)

    async def _send_pointByd_messages(self):
        base_url = (self.product_address or "http://192.168.8.25").strip().rstrip("/")
        base_path = f"/grid-db/influx/sendData/{self.topicName}"
        send_url = f"{base_url}/bytserver{base_path}"
        save_base_path = f"/grid-db/influx/saveData/{self.topicName}"
        save_url = f"{base_url}/bytserver{save_base_path}"

        current_time = self.startTime
        while current_time <= self.endTime:
            timestamp = datetime_to_timestamp(current_time)
            time_str_for_log = current_time.strftime("%Y-%m-%d %H:%M:%S")
            rts_list = []

            for point in self.points:
                value = self._update_value(point)
                rts_list.append({
                    "pn0": 0,
                    "tnm": point,
                    "tsm": 0,
                    "tss": timestamp,
                    "val": value,
                    "vq": 0,
                    "vt": 3
                })
                self.current_values[point] = value

            payload = {
                "knd": "2",
                "meter": "",
                "pver": "",
                "rts": rts_list
            }

            task1 = self._post_request_raw(send_url, payload)
            task2 = self._post_request_raw(save_url, payload)
            results = await asyncio.gather(task1, task2, return_exceptions=True)

            success_count = sum(1 for r in results if r is True)
            if success_count == 2:
                self._log(
                    f"✅ {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())) + f'.{int((time.time() % 1) * 100):02d}'}模拟发送盒子点位，发送点位时间{time_str_for_log} 消息发送成功 | "
                    f"URL: {send_url} 和 {save_url} | "
                    f"消息内容: {payload}"[:1000]
                )
            else:
                for r in results:
                    if isinstance(r, Exception):
                        raise RuntimeError(f"pointByd 请求失败: {r}")

            current_time += timedelta(seconds=self.frequency)
            await asyncio.sleep(self.frequency)

    async def _post_request_raw(self, url: str, payload: Dict) -> bool:
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return True
                else:
                    text = await resp.text()
                    raise Exception(f"{resp.status} {text[:1000]}")
        except Exception as e:
            raise Exception(f"请求 {url.split('://')[-1].split('?')[0]} 失败: {str(e)}")

    async def _send_pointXts_or_pointSh_messages(self):
        base_url = (self.product_address or "http://192.168.8.87").strip().rstrip("/")
        api_path_prefix = self.api_path or "/bytserver/symlink-box/tag/saveData"
        send_url = f"{base_url}{api_path_prefix}/{self.topicName}"
        current_time = self.startTime
        use_kafka = self.producer is not None and "kafka" in self.sendWay.lower()

        while current_time <= self.endTime:
            timestamp = datetime_to_timestamp(current_time)
            time_str_for_log = current_time.strftime("%Y-%m-%d %H:%M:%S")
            rts_list = []
            for point in self.points:
                value = self._update_value(point)
                rts_list.append({
                    "pn0": 0,
                    "tnm": point,
                    "tsm": 0,
                    "tss": timestamp,
                    "val": value,
                    "vq": 0,
                    "vt": 3
                })
                self.current_values[point] = value

            payload = {
                "knd": "2",
                "meter": "",
                "pver": "",
                "rts": rts_list
            }

            await self._post_request(send_url, payload, log_time_str=time_str_for_log)

            if use_kafka:
                try:
                    proto_msg = self.protobuf_msg_class()
                    for item in rts_list:
                        tag = proto_msg.tags.add()
                        tag.name = item["tnm"]
                        tag.time = time_str_for_log
                        tag.value = str(item["val"])
                    # ✅ 不再添加 IoStatus —— 由 tagList 控制

                    readable_msg = [{"name": t.name, "time": t.time, "value": t.value} for t in proto_msg.tags]
                    serialized = proto_msg.SerializeToString()

                    await self._send_kafka_message(
                        message_bytes=serialized,
                        key=self.topicName,
                        msg=readable_msg,
                        log_time_str=time_str_for_log
                    )
                except Exception as e:
                    self._log(f"❌ Kafka 发送失败: {str(e)} | 时间: {time_str_for_log}")

            current_time += timedelta(seconds=self.frequency)
            await asyncio.sleep(self.frequency)

    async def _send_custom_messages(self):
        if not self.fields.get("tagContent"):
            self._log("Custom 模块缺少 tagContent")
            raise ValueError("tagContent is required for Custom module")

        current_time = self.startTime
        use_kafka = "kafka" in self.sendWay.lower()

        # 解析模板
        try:
            raw_clean = self.fields["tagContent"].replace('\\\\', '\\') \
                .replace('\\n', '\n') \
                .replace('\\r', '\r') \
                .replace('\\"', '"') \
                .replace('\\t', '\t') \
                .strip()
            template_data = json.loads(raw_clean)

            is_array_template = isinstance(template_data, list)
            if is_array_template:
                if not template_data:
                    raise ValueError("模板数组不能为空")
                if not isinstance(template_data[0], dict):
                    raise ValueError("数组模板元素必须是对象")
                item_template = template_data[0]
            elif isinstance(template_data, dict):
                item_template = template_data
            else:
                raise ValueError("模板必须是 JSON 对象或数组")

            if "{{tag}}" not in json.dumps(item_template):
                raise ValueError("模板必须包含 {{tag}} 占位符")

            original_item_template = copy.deepcopy(item_template)
        except Exception as e:
            self._log(f"❌ 模板处理失败: {e}")
            raise

        # 构建 Protobuf（仅当需要时）
        proto_msg_class = None
        if self.messageFormat.lower() == "protobuf" and use_kafka:
            try:
                pool = descriptor_pool.DescriptorPool()
                factory = message_factory.MessageFactory(pool)

                TYPE_MAP = {
                    'int': (descriptor_pb2.FieldDescriptorProto.TYPE_INT32, descriptor.FieldDescriptor.CPPTYPE_INT32),
                    'string': (descriptor_pb2.FieldDescriptorProto.TYPE_STRING, descriptor.FieldDescriptor.CPPTYPE_STRING),
                    'bool': (descriptor_pb2.FieldDescriptorProto.TYPE_BOOL, descriptor.FieldDescriptor.CPPTYPE_BOOL),
                    'float': (descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE, descriptor.FieldDescriptor.CPPTYPE_DOUBLE),
                }

                def _get_type(value):
                    if isinstance(value, bool):
                        return TYPE_MAP['bool']
                    elif isinstance(value, int):
                        return TYPE_MAP['int']
                    elif isinstance(value, float):
                        return TYPE_MAP['float']
                    else:
                        return TYPE_MAP['string']

                def build_item_descriptor(data, name="Item"):
                    fields = []
                    nested_msgs = []
                    for i, (key, value) in enumerate(data.items()):
                        field_num = i + 1
                        if isinstance(value, dict):
                            nested_desc = build_item_descriptor(value, f"{name}_{key}")
                            nested_msgs.append(nested_desc)
                            field = descriptor_pb2.FieldDescriptorProto(
                                name=key, number=field_num,
                                type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
                                label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
                                type_name=nested_desc.name
                            )
                        elif isinstance(value, list) and value:
                            first = value[0]
                            if isinstance(first, dict):
                                nested_desc = build_item_descriptor(first, f"{name}_{key}_Item")
                                nested_msgs.append(nested_desc)
                                field = descriptor_pb2.FieldDescriptorProto(
                                    name=key, number=field_num,
                                    type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
                                    label=descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED,
                                    type_name=nested_desc.name
                                )
                            else:
                                ptype, _ = _get_type(first)
                                field = descriptor_pb2.FieldDescriptorProto(
                                    name=key, number=field_num,
                                    type=ptype,
                                    label=descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
                                )
                        else:
                            ptype, _ = _get_type(value)
                            field = descriptor_pb2.FieldDescriptorProto(
                                name=key, number=field_num,
                                type=ptype,
                                label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
                            )
                        fields.append(field)
                    return descriptor_pb2.DescriptorProto(name=name, field=fields, nested_type=nested_msgs)

                item_desc = build_item_descriptor(original_item_template, "Item")
                root_fields = [
                    descriptor_pb2.FieldDescriptorProto(
                        name="items", number=1,
                        type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
                        label=descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED,
                        type_name="Item"
                    )
                ]
                root_msg_proto = descriptor_pb2.DescriptorProto(
                    name="RootMessage", field=root_fields, nested_type=[item_desc]
                )

                file_proto = descriptor_pb2.FileDescriptorProto()
                file_proto.name = "custom.proto"
                file_proto.package = "custom"
                file_proto.message_type.add().MergeFrom(root_msg_proto)
                pool.Add(file_proto)

                root_desc = pool.FindMessageTypeByName("custom.RootMessage")
                proto_msg_class = message_factory.GetMessageClass(root_desc) if hasattr(message_factory, 'GetMessageClass') else factory.GetPrototype(root_desc)
            except Exception as e:
                self._log(f"❌ 动态 Protobuf 类构建失败: {e}")
                raise

        # 主循环
        while current_time <= self.endTime:
            timestamp = datetime_to_timestamp(current_time)
            fmt_ymdhms = current_time.strftime("%Y-%m-%d %H:%M:%S")
            fmt_yyyymmddhhmmss = current_time.strftime("%Y%m%d%H%M%S")
            time_str_for_log = fmt_ymdhms

            filled_items = []
            for point in self.points:
                value = self._update_value(point)
                self.current_values[point] = value

                def fill_placeholders(obj):
                    if isinstance(obj, str):
                        return (obj.replace("{{tag}}", str(point))
                                   .replace("{{value}}", str(value))
                                   .replace("{{timestamp}}", str(timestamp))
                                   .replace("{{yyyy-mm-dd hh:mm:ss}}", fmt_ymdhms)
                                   .replace("{{yyyymmddhhmmss}}", fmt_yyyymmddhhmmss))
                    elif isinstance(obj, dict):
                        return {k: fill_placeholders(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [fill_placeholders(i) for i in obj]
                    return obj

                filled_item = fill_placeholders(copy.deepcopy(original_item_template))
                filled_items.append(filled_item)

            # 序列化
            send_bytes = None
            log_content = None
            try:
                if self.messageFormat.lower() == "json":
                    send_bytes = json.dumps(filled_items, ensure_ascii=False).encode('utf-8')
                    log_content = filled_items
                elif self.messageFormat.lower() == "protobuf" and use_kafka and proto_msg_class:
                    msg = proto_msg_class()
                    for item in filled_items:
                        item_msg = msg.items.add()
                        ParseDict(item, item_msg, ignore_unknown_fields=True)
                    send_bytes = msg.SerializeToString()
                    log_content = filled_items
                else:
                    send_bytes = json.dumps(filled_items, ensure_ascii=False).encode('utf-8')
                    log_content = filled_items
            except Exception as e:
                self._log(f"❌ 消息序列化失败: {e}")
                current_time += timedelta(seconds=self.frequency)
                await asyncio.sleep(0.001)
                raise

            # 发送
            if use_kafka and self.producer:
                try:
                    key = str(int(current_time.timestamp()))
                    await self._send_kafka_message(
                        message_bytes=send_bytes,
                        key=key,
                        msg=filled_items,
                        log_time_str=time_str_for_log
                    )
                except Exception as e:
                    raise RuntimeError(f"Kafka 发送失败: {e}")
            else:
                self._log(f"🟡 非Kafka模式 | 内容: {log_content}")

            current_time += timedelta(seconds=self.frequency)
            await asyncio.sleep(max(0.001, self.frequency))

        self._log("✅ 自定义消息发送完成")


# ==================== 示例运行 ====================
