import json
from contextlib import contextmanager

from app.utils.logger import Log
from config import Config

logger = Log("platform_mq")


def build_task_queue_name(task_type, resource_key="default"):
    return f"{Config.RABBITMQ_QUEUE_PREFIX}.{str(task_type or '').strip()}"


def build_retry_queue_name(queue_name):
    return f"{queue_name}{Config.RABBITMQ_RETRY_SUFFIX}"


def build_dead_queue_name(queue_name):
    return f"{queue_name}{Config.RABBITMQ_DEAD_LETTER_SUFFIX}"


def declare_platform_task_topology(channel, queue_name):
    retry_queue = build_retry_queue_name(queue_name)
    dead_queue = build_dead_queue_name(queue_name)
    channel.exchange_declare(exchange=Config.RABBITMQ_EXCHANGE, exchange_type="direct", durable=True)
    channel.queue_declare(
        queue=queue_name,
        durable=True,
    )
    channel.queue_bind(exchange=Config.RABBITMQ_EXCHANGE, queue=queue_name, routing_key=queue_name)
    channel.queue_declare(
        queue=retry_queue,
        durable=True,
        arguments={
            "x-message-ttl": int(getattr(Config, "RABBITMQ_RETRY_TTL_MS", 10000) or 10000),
            "x-dead-letter-exchange": Config.RABBITMQ_EXCHANGE,
            "x-dead-letter-routing-key": queue_name,
        },
    )
    channel.queue_bind(exchange=Config.RABBITMQ_EXCHANGE, queue=retry_queue, routing_key=retry_queue)
    channel.queue_declare(queue=dead_queue, durable=True)
    channel.queue_bind(exchange=Config.RABBITMQ_EXCHANGE, queue=dead_queue, routing_key=dead_queue)
    return retry_queue, dead_queue


@contextmanager
def rabbit_connection():
    try:
        import pika
    except Exception as exc:
        raise RuntimeError("pika 未安装，无法连接 RabbitMQ") from exc
    credentials = pika.PlainCredentials(Config.RABBITMQ_USER, Config.RABBITMQ_PASSWORD)
    params = pika.ConnectionParameters(
        host=Config.RABBITMQ_HOST,
        port=int(Config.RABBITMQ_PORT or 5672),
        virtual_host=Config.RABBITMQ_VHOST or "/",
        credentials=credentials,
        heartbeat=30,
        socket_timeout=int(getattr(Config, "RABBITMQ_CONNECT_TIMEOUT", 2) or 2),
        stack_timeout=int(getattr(Config, "RABBITMQ_CONNECT_TIMEOUT", 2) or 2),
        blocked_connection_timeout=int(getattr(Config, "RABBITMQ_CONNECT_TIMEOUT", 2) or 2),
    )
    connection = pika.BlockingConnection(params)
    try:
        yield connection
    finally:
        try:
            connection.close()
        except Exception:
            pass


def publish_platform_task(task_id, task_type, payload=None, resource_key="default", queue_name=""):
    import pika

    queue = queue_name or build_task_queue_name(task_type, resource_key)
    routing_key = queue
    body = json.dumps({
        "task_id": int(task_id or 0),
        "task_type": str(task_type or ""),
        "resource_key": str(resource_key or "default"),
        "payload": payload or {},
    }, ensure_ascii=False)
    with rabbit_connection() as conn:
        channel = conn.channel()
        declare_platform_task_topology(channel, queue)
        channel.basic_publish(
            exchange=Config.RABBITMQ_EXCHANGE,
            routing_key=routing_key,
            body=body.encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
            ),
        )
    logger.info(f"platform task published task_id={task_id}, queue={queue}")
    return queue


def publish_platform_retry_message(queue_name, message):
    import pika

    retry_queue = build_retry_queue_name(queue_name)
    body = json.dumps(message or {}, ensure_ascii=False)
    with rabbit_connection() as conn:
        channel = conn.channel()
        declare_platform_task_topology(channel, queue_name)
        channel.basic_publish(
            exchange=Config.RABBITMQ_EXCHANGE,
            routing_key=retry_queue,
            body=body.encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
            ),
        )
    logger.info(f"platform task retry published queue={retry_queue}")
    return retry_queue


def publish_platform_dead_message(queue_name, message):
    import pika

    dead_queue = build_dead_queue_name(queue_name)
    body = json.dumps(message or {}, ensure_ascii=False)
    with rabbit_connection() as conn:
        channel = conn.channel()
        declare_platform_task_topology(channel, queue_name)
        channel.basic_publish(
            exchange=Config.RABBITMQ_EXCHANGE,
            routing_key=dead_queue,
            body=body.encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
            ),
        )
    logger.info(f"platform task dead-letter published queue={dead_queue}")
    return dead_queue


def declare_platform_task_queue(queue_name):
    with rabbit_connection() as conn:
        channel = conn.channel()
        declare_platform_task_topology(channel, queue_name)
    return queue_name
