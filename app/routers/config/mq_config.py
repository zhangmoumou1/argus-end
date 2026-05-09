import asyncio
from functools import partial
import json
import ssl
from datetime import datetime
from math import ceil

from fastapi import Depends
from sqlalchemy import select, text

from app.handler.fatcory import PityResponse
from app.models.mq_config import PityMQConfig
from app.routers import Permission, get_session
from app.routers.config.environment import router
from app.schema.mq_config import (
    MQConfigForm, MQPublishForm, MQConsumeForm, MQConsumerStatsForm,
    RabbitQueueListForm, KafkaTopicListForm, KafkaTopicMessagesForm,
    KafkaConsumerGroupListForm, KafkaConsumerGroupDetailForm,
    RabbitGetMessagesForm, RabbitQueueOperateForm,
)
from config import Config


def _safe_json_loads(raw, default=None):
    if default is None:
        default = {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw or "")
    except Exception:
        return default


async def ensure_mq_schema(session):
    await session.execute(text(
        "CREATE TABLE IF NOT EXISTS pity_mq_config ("
        "id INT PRIMARY KEY AUTO_INCREMENT,"
        "env INT NOT NULL,"
        "name VARCHAR(64) NOT NULL,"
        "mq_type VARCHAR(16) NOT NULL,"
        "host VARCHAR(128) NOT NULL,"
        "port INT NOT NULL DEFAULT 0,"
        "username VARCHAR(64) NOT NULL DEFAULT '',"
        "password VARCHAR(128) NOT NULL DEFAULT '',"
        "virtual_host VARCHAR(64) NOT NULL DEFAULT '/',"
        "use_ssl TINYINT(1) NOT NULL DEFAULT 0,"
        "description VARCHAR(255) NULL,"
        "created_at TIMESTAMP NOT NULL,"
        "updated_at TIMESTAMP NOT NULL,"
        "deleted_at BIGINT NOT NULL DEFAULT 0,"
        "create_user INT NOT NULL,"
        "update_user INT NOT NULL,"
        "UNIQUE KEY uk_mq_env_name_type_deleted (env, name, mq_type, deleted_at)"
        ")"
    ))
    await session.commit()


def _build_kafka_server(record: PityMQConfig):
    return f"{record.host}:{record.port}"


def _connect_kafka(record: PityMQConfig):
    try:
        from kafka import KafkaProducer
    except Exception:
        raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
    kwargs = _build_kafka_kwargs(record)
    producer = KafkaProducer(**kwargs)
    return producer


def _build_kafka_kwargs(record: PityMQConfig):
    kwargs = {
        "bootstrap_servers": [_build_kafka_server(record)],
        "request_timeout_ms": 30000,
        "api_version_auto_timeout_ms": 5000,
    }
    if record.username:
        kwargs.update({
            "security_protocol": "SASL_SSL" if record.use_ssl else "SASL_PLAINTEXT",
            "sasl_mechanism": "PLAIN",
            "sasl_plain_username": record.username,
            "sasl_plain_password": record.password or "",
        })
    elif record.use_ssl:
        kwargs.update({
            "security_protocol": "SSL",
            "ssl_context": ssl.create_default_context(),
        })
    return kwargs


def _build_kafka_consumer_kwargs(record: PityMQConfig):
    return _build_kafka_kwargs(record)


def _with_kafka_consumer_timeout(kwargs: dict):
    data = dict(kwargs or {})
    data.setdefault("session_timeout_ms", 10000)
    return data


def _connect_rabbit(record: PityMQConfig):
    try:
        import pika
    except Exception:
        raise Exception("未安装 pika，请先执行 pip install pika")
    credentials = None
    if record.username:
        credentials = pika.PlainCredentials(record.username, record.password or "")
    params = pika.ConnectionParameters(
        host=record.host,
        port=record.port,
        virtual_host=record.virtual_host or "/",
        credentials=credentials,
        ssl_options=pika.SSLOptions(ssl.create_default_context()) if record.use_ssl else None,
        blocked_connection_timeout=5,
        socket_timeout=5,
    )
    return pika.BlockingConnection(params)


def _build_temp_record(form: MQConfigForm):
    return PityMQConfig(
        env=form.env,
        name=form.name,
        mq_type=form.mq_type,
        host=form.host,
        port=form.port,
        user=0,
        username=form.username or "",
        password=form.password or "",
        virtual_host=form.virtual_host or "/",
        use_ssl=form.use_ssl,
        description=form.description or "",
    )


def _test_mq_connection(record: PityMQConfig):
    mq_type = (record.mq_type or "").lower()
    if mq_type == "kafka":
        producer = _connect_kafka(record)
        producer.close()
        return "连接成功"
    if mq_type == "rabbitmq":
        conn = _connect_rabbit(record)
        conn.close()
        return "连接成功"
    raise Exception("仅支持 kafka / rabbitmq")


def _safe_group_text(value):
    return str(value or "").strip()


def _extract_group_id(desc, fallback=""):
    for attr in ("group_id", "group", "groupId"):
        value = _safe_group_text(getattr(desc, attr, ""))
        if value:
            return value
    return _safe_group_text(fallback)


def _format_kafka_timestamp(timestamp_ms):
    if not timestamp_ms:
        return "-"
    try:
        return datetime.fromtimestamp(int(timestamp_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(timestamp_ms)


async def _run_blocking(func, *args, timeout=12):
    loop = asyncio.get_running_loop()
    task = partial(func, *args)
    return await asyncio.wait_for(loop.run_in_executor(None, task), timeout=timeout)


def _detect_kafka_api_version(record: PityMQConfig):
    try:
        from kafka import KafkaConsumer
    except Exception:
        raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
    kwargs = _build_kafka_consumer_kwargs(record)
    kwargs["consumer_timeout_ms"] = 1000
    consumer = KafkaConsumer(**_with_kafka_consumer_timeout(kwargs))
    try:
        version = None
        try:
            version = consumer.config.get("api_version")
        except Exception:
            version = None
        if version:
            return version
        try:
            version = consumer._client.config.get("api_version")
        except Exception:
            version = None
        if version:
            return version
        return (1, 0, 0)
    finally:
        consumer.close()


def _build_kafka_admin_kwargs(record: PityMQConfig):
    kwargs = _build_kafka_kwargs(record)
    kwargs["api_version"] = _detect_kafka_api_version(record)
    return kwargs


def _list_kafka_topics_sync(record: PityMQConfig):
    try:
        from kafka import KafkaConsumer
    except Exception:
        raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
    kwargs = _build_kafka_kwargs(record)
    kwargs["consumer_timeout_ms"] = 1000
    consumer = KafkaConsumer(**_with_kafka_consumer_timeout(kwargs))
    try:
        topics = sorted(list(consumer.topics() or []))
        return [{
            "topic": topic,
            "partitions": None,
            "end_offset_total": None,
        } for topic in topics]
    finally:
        consumer.close()


def _list_kafka_consumer_groups_sync(record: PityMQConfig):
    try:
        from kafka.admin import KafkaAdminClient
    except Exception:
        raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
    admin = KafkaAdminClient(**_build_kafka_admin_kwargs(record))
    try:
        raw_groups = admin.list_consumer_groups() or []
        group_ids = sorted({_safe_group_text(item[0]) for item in raw_groups if item and _safe_group_text(item[0])})
        result_map = {}
        for group_id in group_ids:
            result_map[group_id] = {
                "group_id": group_id,
                "state": "-",
                "protocol_type": "-",
                "members": 0,
            }
        for item in raw_groups:
            if not item:
                continue
            group_id = _safe_group_text(item[0]) if len(item) > 0 else ""
            if not group_id:
                continue
            if len(item) > 1:
                result_map[group_id]["protocol_type"] = _safe_group_text(item[1]) or result_map[group_id]["protocol_type"]
        if group_ids:
            try:
                described = admin.describe_consumer_groups(group_ids) or []
                for index, item in enumerate(described):
                    fallback_group_id = group_ids[index] if index < len(group_ids) else ""
                    group_id = _extract_group_id(item, fallback_group_id)
                    if not group_id:
                        continue
                    result_map[group_id] = {
                        "group_id": group_id,
                        "state": _safe_group_text(getattr(item, "state", "")) or "-",
                        "protocol_type": _safe_group_text(getattr(item, "protocol_type", "")) or result_map.get(group_id, {}).get("protocol_type", "-"),
                        "members": len(getattr(item, "members", []) or []),
                    }
            except Exception:
                # 某些broker/版本组合会在describe阶段触发解码异常，保留基础列表即可
                pass
        result = list(result_map.values())
        result.sort(key=lambda x: x["group_id"])
        return result
    finally:
        admin.close()


def _kafka_consumer_group_detail_sync(record: PityMQConfig, group_id: str):
    try:
        from kafka import KafkaConsumer
        from kafka import TopicPartition
        from kafka.admin import KafkaAdminClient
    except Exception:
        raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
    kafka_kwargs = _build_kafka_consumer_kwargs(record)
    admin = KafkaAdminClient(**_build_kafka_admin_kwargs(record))
    try:
        desc = None
        try:
            described = admin.describe_consumer_groups([group_id]) or []
            desc = described[0] if described else None
        except Exception:
            desc = None
        brokers = []
        try:
            cluster = admin.describe_cluster()
            for item in cluster.get("brokers", []) or []:
                brokers.append({
                    "node_id": item.get("node_id"),
                    "host": item.get("host"),
                    "port": item.get("port"),
                })
        except Exception:
            pass
        members = getattr(desc, "members", []) if desc is not None else []
        members = members or []
        topic_partition_map = {}
        member_rows = []
        for member in members:
            try:
                assignment = getattr(member, "member_assignment", None)
                assignments = getattr(assignment, "assignment", []) or []
            except Exception:
                assignments = []
            for item in assignments:
                topic = _safe_group_text(getattr(item, "topic", ""))
                partitions = list(getattr(item, "partitions", []) or [])
                if topic:
                    topic_partition_map.setdefault(topic, set()).update(partitions)
            member_rows.append({
                "member_id": _safe_group_text(getattr(member, "member_id", "")),
                "client_id": _safe_group_text(getattr(member, "client_id", "")),
                "client_host": _safe_group_text(getattr(member, "client_host", "")),
            })
        # describe解码失败或成员为空时，回退到offset接口拿topic/partition
        if not topic_partition_map:
            try:
                offsets_map = admin.list_consumer_group_offsets(group_id) or {}
                for tp in offsets_map.keys():
                    topic = _safe_group_text(getattr(tp, "topic", ""))
                    partition = getattr(tp, "partition", None)
                    if topic and partition is not None:
                        topic_partition_map.setdefault(topic, set()).add(int(partition))
            except Exception:
                pass
        consumer_kwargs = dict(kafka_kwargs)
        consumer_kwargs["group_id"] = group_id
        consumer = KafkaConsumer(**_with_kafka_consumer_timeout(consumer_kwargs))
        try:
            rows = []
            now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for topic, partitions in sorted(topic_partition_map.items()):
                tps = [TopicPartition(topic, p) for p in sorted(partitions)]
                beginning = consumer.beginning_offsets(tps) if tps else {}
                end_offsets = consumer.end_offsets(tps) if tps else {}
                for tp in tps:
                    start_offset = int(beginning.get(tp, 0) or 0)
                    end_exclusive = int(end_offsets.get(tp, 0) or 0)
                    end_offset = max(end_exclusive - 1, -1)
                    committed = consumer.committed(tp)
                    offset = int(committed) if committed is not None else -1
                    lag = max(end_exclusive - (offset if offset >= 0 else start_offset), 0)
                    rows.append({
                        "topic": topic,
                        "partition": tp.partition,
                        "start": start_offset,
                        "end": end_offset,
                        "offset": offset,
                        "lag": lag,
                        "last_commit_time": now_text if offset >= 0 else "-",
                    })
        finally:
            consumer.close()
        return {
            "group_id": group_id,
            "state": _safe_group_text(getattr(desc, "state", "")) or "-",
            "protocol_type": _safe_group_text(getattr(desc, "protocol_type", "")) or "-",
            "brokers": brokers,
            "members": member_rows,
            "rows": rows,
        }
    finally:
        admin.close()


def _kafka_topic_messages_sync(record: PityMQConfig, topic: str, limit: int, partition: int = None,
                               before_offset: int = None):
    try:
        from kafka import KafkaConsumer
        from kafka import TopicPartition
    except Exception:
        raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
    kwargs = _build_kafka_consumer_kwargs(record)
    kwargs.update({
        "group_id": None,
        "enable_auto_commit": False,
        "auto_offset_reset": "latest",
        "consumer_timeout_ms": 1500,
    })
    consumer = KafkaConsumer(**_with_kafka_consumer_timeout(kwargs))
    try:
        partitions = sorted(list(consumer.partitions_for_topic(topic) or []))
        if not partitions:
            return {"messages": [], "next_before_offset": None, "has_more": False, "partition": partition}
        selected_partition = partition if partition in partitions else partitions[0]
        topic_partitions = [TopicPartition(topic, selected_partition)]
        end_offsets = consumer.end_offsets(topic_partitions)
        end_exclusive = int(end_offsets.get(topic_partitions[0], 0) or 0)
        upper_exclusive = end_exclusive
        if before_offset is not None and int(before_offset) > 0:
            upper_exclusive = min(int(before_offset), end_exclusive)
        start_offset = max(upper_exclusive - limit, 0)
        consumer.assign(topic_partitions)
        consumer.seek(topic_partitions[0], start_offset)
        messages = []
        for message in consumer:
            if message.partition != selected_partition:
                continue
            if message.offset >= upper_exclusive:
                continue
            headers = {}
            for item in list(message.headers or []):
                if not item or len(item) != 2:
                    continue
                key, value = item
                headers[str(key)] = value.decode("utf-8", errors="ignore") if isinstance(value, (bytes, bytearray)) else value
            value_text = message.value.decode("utf-8", errors="ignore") if message.value else ""
            messages.append({
                "topic": message.topic,
                "partition": message.partition,
                "offset": message.offset,
                "timestamp": _format_kafka_timestamp(message.timestamp),
                "timestamp_ms": int(message.timestamp or 0),
                "key": message.key.decode("utf-8", errors="ignore") if message.key else "",
                "message": value_text,
                "message_preview": value_text[:240],
                "headers": headers,
            })
            if len(messages) >= limit:
                break
        messages.sort(key=lambda item: int(item.get("offset") or 0), reverse=True)
        next_before_offset = None
        if messages:
            next_before_offset = min(int(item.get("offset") or 0) for item in messages)
        has_more = start_offset > 0 or (next_before_offset is not None and next_before_offset > 0)
        return {
            "messages": messages,
            "next_before_offset": next_before_offset,
            "has_more": has_more,
            "partition": selected_partition,
        }
    finally:
        consumer.close()


def _kafka_topic_partitions_sync(record: PityMQConfig, topic: str):
    try:
        from kafka import KafkaConsumer
        from kafka import TopicPartition
    except Exception:
        raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
    kwargs = _build_kafka_consumer_kwargs(record)
    kwargs["consumer_timeout_ms"] = 1000
    consumer = KafkaConsumer(**_with_kafka_consumer_timeout(kwargs))
    try:
        partitions = sorted(list(consumer.partitions_for_topic(topic) or []))
        if not partitions:
            return []
        tps = [TopicPartition(topic, p) for p in partitions]
        end_offsets = consumer.end_offsets(tps)
        data = []
        for tp in tps:
            end_exclusive = int(end_offsets.get(tp, 0) or 0)
            data.append({
                "partition": tp.partition,
                "latest_offset": max(end_exclusive - 1, -1),
                "next_offset": end_exclusive,
            })
        return data
    finally:
        consumer.close()


@router.get("/mq/list")
async def list_mq_config(name: str = "", env: int = None, mq_type: str = "", host: str = "",
                         _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        filters = [PityMQConfig.deleted_at == 0]
        if name:
            filters.append(PityMQConfig.name.like(f"%{name}%"))
        if host:
            filters.append(PityMQConfig.host.like(f"%{host}%"))
        if env is not None:
            filters.append(PityMQConfig.env == env)
        if mq_type:
            filters.append(PityMQConfig.mq_type == mq_type)
        result = await session.execute(
            select(PityMQConfig).where(*filters).order_by(PityMQConfig.updated_at.desc(), PityMQConfig.id.desc())
        )
        return PityResponse.success([item for item in result.scalars().all()])
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/insert")
async def insert_mq_config(form: MQConfigForm, user_info=Depends(Permission(Config.ADMIN)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        query = await session.execute(
            select(PityMQConfig).where(
                PityMQConfig.deleted_at == 0,
                PityMQConfig.env == form.env,
                PityMQConfig.name == form.name,
                PityMQConfig.mq_type == form.mq_type,
            )
        )
        if query.scalars().first() is not None:
            return PityResponse.failed("数据已存在, 请勿重复添加")
        model = PityMQConfig(**form.dict(), user=user_info["id"])
        session.add(model)
        await session.commit()
        await session.refresh(model)
        return PityResponse.success(model)
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/update")
async def update_mq_config(form: MQConfigForm, user_info=Depends(Permission(Config.ADMIN)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        model = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if model is None:
            return PityResponse.failed("记录不存在")
        for key, value in form.dict().items():
            if key != "id":
                setattr(model, key, value)
        model.update_user = user_info["id"]
        model.updated_at = datetime.now()
        await session.commit()
        await session.refresh(model)
        return PityResponse.success(model)
    except Exception as err:
        return PityResponse.failed(err)


@router.get("/mq/delete")
async def delete_mq_config(id: int, user_info=Depends(Permission(Config.ADMIN)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        model = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if model is None:
            return PityResponse.failed("记录不存在")
        model.deleted_at = int(datetime.now().timestamp())
        model.updated_at = datetime.now()
        model.update_user = user_info["id"]
        await session.commit()
        return PityResponse.success()
    except Exception as err:
        return PityResponse.failed(err)


@router.get("/mq/connect")
async def test_mq_connect(id: int, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        _test_mq_connection(record)
        return PityResponse.success(msg="连接成功")
    except Exception as err:
        return PityResponse.failed(f"连接失败: {err}")


@router.post("/mq/connect/test")
async def test_mq_connect_by_form(form: MQConfigForm, _=Depends(Permission(Config.MEMBER))):
    try:
        record = _build_temp_record(form)
        _test_mq_connection(record)
        return PityResponse.success(msg="连接成功")
    except Exception as err:
        return PityResponse.failed(f"连接失败: {err}")


@router.post("/mq/publish")
async def publish_mq_message(form: MQPublishForm, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        headers = _safe_json_loads(form.headers, {})
        body_text = str(form.body or "")
        mq_type = (record.mq_type or "").lower()
        if mq_type == "kafka":
            producer = _connect_kafka(record)
            k_headers = [(str(k), str(v).encode("utf-8")) for k, v in (headers or {}).items()]
            future = producer.send(
                topic=form.destination,
                key=(form.key or "").encode("utf-8") if form.key else None,
                value=body_text.encode("utf-8"),
                headers=k_headers,
            )
            meta = future.get(timeout=5)
            producer.flush()
            producer.close()
            return PityResponse.success({
                "topic": meta.topic,
                "partition": meta.partition,
                "offset": meta.offset,
            })
        if mq_type == "rabbitmq":
            conn = _connect_rabbit(record)
            ch = conn.channel()
            ch.queue_declare(queue=form.destination, durable=True)
            ch.basic_publish(
                exchange="",
                routing_key=form.destination,
                body=body_text.encode("utf-8"),
                properties=None,
            )
            conn.close()
            return PityResponse.success({"queue": form.destination, "status": "published"})
        return PityResponse.failed("仅支持 kafka / rabbitmq")
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/consume")
async def consume_mq_message(form: MQConsumeForm, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        limit = max(1, min(int(form.limit or 5), 50))
        mq_type = (record.mq_type or "").lower()
        if mq_type == "kafka":
            try:
                from kafka import KafkaConsumer
            except Exception:
                raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
            kwargs = _build_kafka_consumer_kwargs(record)
            kwargs.update({
                "group_id": form.group_id or "argus-mq-preview",
                "auto_offset_reset": "earliest",
                "consumer_timeout_ms": max(1000, int(form.timeout_ms or 3000)),
                "enable_auto_commit": form.auto_ack,
            })
            consumer = KafkaConsumer(form.destination, **_with_kafka_consumer_timeout(kwargs))
            messages = []
            for msg in consumer:
                messages.append({
                    "topic": msg.topic,
                    "partition": msg.partition,
                    "offset": msg.offset,
                    "key": msg.key.decode("utf-8", errors="ignore") if msg.key else "",
                    "value": msg.value.decode("utf-8", errors="ignore") if msg.value else "",
                })
                if len(messages) >= limit:
                    break
            consumer.close()
            return PityResponse.success(messages)
        if mq_type == "rabbitmq":
            conn = _connect_rabbit(record)
            ch = conn.channel()
            messages = []
            for _ in range(limit):
                method_frame, header_frame, body = ch.basic_get(queue=form.destination, auto_ack=form.auto_ack)
                if method_frame is None:
                    break
                messages.append({
                    "delivery_tag": method_frame.delivery_tag,
                    "exchange": method_frame.exchange,
                    "routing_key": method_frame.routing_key,
                    "headers": getattr(header_frame, "headers", {}) if header_frame else {},
                    "value": body.decode("utf-8", errors="ignore") if body else "",
                })
            conn.close()
            return PityResponse.success(messages)
        return PityResponse.failed("仅支持 kafka / rabbitmq")
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/consumers")
async def kafka_consumer_stats(form: MQConsumerStatsForm, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "kafka":
            return PityResponse.failed("Consumers指标仅支持Kafka")
        try:
            from kafka import KafkaConsumer
            from kafka import TopicPartition
            from kafka.admin import KafkaAdminClient
        except Exception:
            raise Exception("未安装 kafka-python，请先执行 pip install kafka-python")
        kwargs = _build_kafka_consumer_kwargs(record)
        kwargs["group_id"] = form.group_id or "argus-mq-preview"

        topic = form.destination
        consumer = KafkaConsumer(**_with_kafka_consumer_timeout(kwargs))
        partitions = consumer.partitions_for_topic(topic) or set()
        if not partitions:
            consumer.close()
            return PityResponse.success([])
        tps = [TopicPartition(topic, p) for p in sorted(partitions)]
        beginning = consumer.beginning_offsets(tps)
        end_offsets = consumer.end_offsets(tps)

        consumer_count = 0
        brokers = []
        try:
            admin = KafkaAdminClient(**kwargs)
            try:
                cluster = admin.describe_cluster()
                for item in cluster.get("brokers", []) or []:
                    brokers.append({
                        "node_id": item.get("node_id"),
                        "host": item.get("host"),
                        "port": item.get("port"),
                    })
            except Exception:
                pass
            groups = admin.describe_consumer_groups([kwargs["group_id"]])
            if groups and groups[0] and getattr(groups[0], "members", None) is not None:
                consumer_count = len(groups[0].members or [])
            admin.close()
        except Exception:
            consumer_count = 0
        if not brokers:
            try:
                for broker in (consumer._client.cluster.brokers() or []):
                    brokers.append({
                        "node_id": getattr(broker, "nodeId", None),
                        "host": getattr(broker, "host", ""),
                        "port": getattr(broker, "port", 0),
                    })
            except Exception:
                pass

        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = []
        for tp in tps:
            start_offset = int(beginning.get(tp, 0) or 0)
            end_exclusive = int(end_offsets.get(tp, 0) or 0)
            end_offset = max(end_exclusive - 1, -1)
            committed = consumer.committed(tp)
            offset = int(committed) if committed is not None else -1
            lag = max(end_exclusive - (offset if offset >= 0 else start_offset), 0)
            result.append({
                "topic": topic,
                "partition": tp.partition,
                "consumers": consumer_count,
                "start": start_offset,
                "end": end_offset,
                "offset": offset,
                "lag": lag,
                "last_commit_time": now_text if offset >= 0 else "-",
            })
        consumer.close()
        return PityResponse.success({
            "brokers": brokers,
            "brokers_count": len(brokers),
            "rows": result,
        })
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/kafka/topics")
async def kafka_list_topics(form: KafkaTopicListForm, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "kafka":
            return PityResponse.failed("仅支持Kafka")
        rows = await _run_blocking(_list_kafka_topics_sync, record, timeout=12)
        return PityResponse.success(rows)
    except asyncio.TimeoutError:
        return PityResponse.failed("查询Kafka topics超时，请检查Broker连通性、鉴权配置或Topic数量")
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/kafka/topic/messages")
async def kafka_topic_messages(form: KafkaTopicMessagesForm, _=Depends(Permission(Config.MEMBER)),
                               session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "kafka":
            return PityResponse.failed("仅支持Kafka")
        limit = max(1, min(int(form.limit or 100), 300))
        messages = await _run_blocking(
            _kafka_topic_messages_sync,
            record,
            form.topic,
            limit,
            form.partition,
            form.before_offset,
            timeout=18
        )
        return PityResponse.success(messages)
    except asyncio.TimeoutError:
        return PityResponse.failed("查询Kafka消息超时，请检查Broker连通性、Topic积压情况或缩小查询范围")
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/kafka/topic/partitions")
async def kafka_topic_partitions(form: KafkaTopicMessagesForm, _=Depends(Permission(Config.MEMBER)),
                                 session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "kafka":
            return PityResponse.failed("仅支持Kafka")
        data = await _run_blocking(_kafka_topic_partitions_sync, record, form.topic, timeout=12)
        return PityResponse.success(data)
    except asyncio.TimeoutError:
        return PityResponse.failed("查询Kafka分区超时，请检查Broker连通性")
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/kafka/consumer-groups")
async def kafka_list_consumer_groups(form: KafkaConsumerGroupListForm, _=Depends(Permission(Config.MEMBER)),
                                     session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "kafka":
            return PityResponse.failed("仅支持Kafka")
        result = await _run_blocking(_list_kafka_consumer_groups_sync, record, timeout=12)
        return PityResponse.success(result)
    except asyncio.TimeoutError:
        return PityResponse.failed("查询Kafka消费组超时，请检查Broker连通性或鉴权配置")
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/kafka/consumer-group/detail")
async def kafka_consumer_group_detail(form: KafkaConsumerGroupDetailForm, _=Depends(Permission(Config.MEMBER)),
                                      session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "kafka":
            return PityResponse.failed("仅支持Kafka")
        result = await _run_blocking(_kafka_consumer_group_detail_sync, record, form.group_id, timeout=18)
        return PityResponse.success(result)
    except asyncio.TimeoutError:
        return PityResponse.failed("查询Kafka消费组详情超时，请检查Broker连通性或消费组状态")
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/rabbit/queues")
async def rabbit_list_queues(form: RabbitQueueListForm, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "rabbitmq":
            return PityResponse.failed("仅支持RabbitMQ")
        import requests
        port = 15671 if record.use_ssl else 15672
        base = f"{'https' if record.use_ssl else 'http'}://{record.host}:{port}"
        vhost = (record.virtual_host or "/").strip() or "/"
        encoded_vhost = "%2F" if vhost == "/" else vhost.replace("/", "%2F")
        resp = requests.get(
            f"{base}/api/queues/{encoded_vhost}",
            auth=(record.username or "guest", record.password or "guest"),
            timeout=8,
            verify=False,
        )
        if resp.status_code >= 400:
            return PityResponse.failed(
                f"RabbitMQ Management API不可用({resp.status_code})，请确认已启用插件 rabbitmq_management 且端口{port}可访问"
            )
        rows = resp.json() if resp.content else []
        data = [{
            "name": item.get("name"),
            "vhost": item.get("vhost"),
            "durable": item.get("durable"),
            "auto_delete": item.get("auto_delete"),
            "messages": item.get("messages"),
            "messages_ready": item.get("messages_ready"),
            "messages_unacknowledged": item.get("messages_unacknowledged"),
            "consumers": item.get("consumers"),
        } for item in (rows or [])]
        return PityResponse.success(data)
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/rabbit/get-messages")
async def rabbit_get_messages(form: RabbitGetMessagesForm, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "rabbitmq":
            return PityResponse.failed("仅支持RabbitMQ")
        conn = _connect_rabbit(record)
        ch = conn.channel()
        ch.queue_declare(queue=form.queue, durable=True)
        count = max(1, min(int(form.count or 5), 100))
        messages = []
        for _ in range(count):
            method_frame, header_frame, body = ch.basic_get(queue=form.queue, auto_ack=form.auto_ack)
            if method_frame is None:
                break
            decoded = ""
            if body is not None:
                try:
                    decoded = body.decode(form.encoding or "utf-8", errors="ignore")
                except Exception:
                    decoded = str(body)
            message_item = {
                "delivery_tag": method_frame.delivery_tag,
                "exchange": method_frame.exchange,
                "routing_key": method_frame.routing_key,
                "redelivered": method_frame.redelivered,
                "headers": getattr(header_frame, "headers", {}) if header_frame else {},
                "properties": {
                    "content_type": getattr(header_frame, "content_type", None) if header_frame else None,
                    "content_encoding": getattr(header_frame, "content_encoding", None) if header_frame else None,
                    "timestamp": getattr(header_frame, "timestamp", None) if header_frame else None,
                },
                "body": decoded,
            }
            messages.append(message_item)
            if not form.auto_ack:
                if form.requeue:
                    ch.basic_nack(delivery_tag=method_frame.delivery_tag, multiple=False, requeue=True)
                else:
                    ch.basic_ack(delivery_tag=method_frame.delivery_tag, multiple=False)
        conn.close()
        return PityResponse.success({
            "queue": form.queue,
            "count": len(messages),
            "messages": messages,
        })
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/rabbit/purge")
async def rabbit_purge_queue(form: RabbitQueueOperateForm, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "rabbitmq":
            return PityResponse.failed("仅支持RabbitMQ")
        conn = _connect_rabbit(record)
        ch = conn.channel()
        result = ch.queue_purge(queue=form.queue)
        conn.close()
        return PityResponse.success({
            "queue": form.queue,
            "message_count": getattr(result, "method", result).message_count if result else 0
        })
    except Exception as err:
        return PityResponse.failed(err)


@router.post("/mq/rabbit/delete-queue")
async def rabbit_delete_queue(form: RabbitQueueOperateForm, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        await ensure_mq_schema(session)
        record = (await session.execute(
            select(PityMQConfig).where(PityMQConfig.id == form.id, PityMQConfig.deleted_at == 0)
        )).scalars().first()
        if record is None:
            return PityResponse.failed("配置不存在")
        if (record.mq_type or "").lower() != "rabbitmq":
            return PityResponse.failed("仅支持RabbitMQ")
        conn = _connect_rabbit(record)
        ch = conn.channel()
        ch.queue_delete(queue=form.queue)
        conn.close()
        return PityResponse.success({"queue": form.queue, "status": "deleted"})
    except Exception as err:
        return PityResponse.failed(err)
