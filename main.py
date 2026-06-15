import asyncio
import json
from mimetypes import guess_type
from pathlib import Path
from os.path import isfile

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Request, WebSocket, WebSocketDisconnect, Depends
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from app import argus, init_logging
from app.core.platform_worker import platform_task_worker
from app.core.platform_mq import rabbit_connection
from app.core.msg.wss_msg import WebSocketMessage
from app.core.ws_connection_manager import ws_manage
from app.crud import create_table
from app.crud.notification.NotificationDao import ArgusNotificationDao
from app.enums.MessageEnum import MessageStateEnum, MessageTypeEnum
from app.middleware.RedisManager import RedisHelper
from app.middleware.oss import OssClient, get_default_bucket_name
from app.routers.auth import user
from app.routers.config import router as config_router
from app.routers.notification import router as msg_router
from app.routers.online import router as online_router
from app.routers.operation import router as operation_router
from app.routers.notification_admin import router as notification_admin_router
from app.routers.oss import router as oss_router
from app.routers.performance import router as performance_router
from app.routers.platform_task import router as platform_task_router
from app.routers.project import project
from app.routers.request import http
from app.routers.testcase import router as testcase_router
from app.routers.testcase.share import router as share_router
from app.routers.testcase.functional_case import router as functional_case_router
from app.routers.testcase.functional_case_skill import router as functional_case_skill_router
from app.routers.testcase.interface_manage import router as interface_manage_router
from app.routers.testcase.mock_config import router as mock_config_router
from app.routers.ui_test import router as ui_test_router
from app.routers.workspace import router as workspace_router
from app.utils.scheduler import Scheduler
from config import Config, ARGUS_ENV, BANNER
from argus_proxy import start_proxy

logger = init_logging()

logger.bind(name=None).opt(ansi=True).success(f"argus is running at <red>{ARGUS_ENV}</red>")
logger.bind(name=None).success(BANNER)

proxy_task = None
platform_worker_task = None
BASE_DIR = Path(__file__).resolve().parent
STATICS_DIR = BASE_DIR / "statics"


def _skip_request_logging(request: Request):
    if not Config.REQUEST_LOG_ENABLED:
        return True
    path = str(getattr(request.url, "path", "") or "").strip()
    for prefix in Config.REQUEST_LOG_SKIP_PATHS or []:
        if path.startswith(str(prefix or "").strip()):
            return True
    return False


def _handle_proxy_task_done(task):
    try:
        task.result()
    except asyncio.CancelledError:
        logger.bind(name=None).warning("record proxy task cancelled.        🚫")
    except BaseException as e:
        logger.bind(name=None).error(f"record proxy task failed but main service continues.        ❌ {e}")


def _trim_request_payload(payload, limit: int = 2000):
    if payload is None:
        return payload
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="ignore")
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except Exception:
            text = str(payload)
    if len(text) <= limit:
        return text
    return f"{text[:limit]} ...<truncated {len(text) - limit} chars>"


def _normalize_plan_cron_for_scheduler(cron: str) -> str:
    fields = [x.strip() for x in str(cron or "").split() if x.strip()]
    if not fields:
        return ""
    return " ".join("*" if field == "?" else field for field in fields)


async def request_info(request: Request):
    if _skip_request_logging(request):
        return
    logger.bind(name=None).debug(f"{request.method} {request.url}")
    try:
        if str(request.method or "").upper() in {"GET", "HEAD", "OPTIONS"}:
            return
        body = await request.body()
        if len(body) == 0:
            return
        # 大请求体不再额外做一次 JSON 解析，避免日志链路把请求处理成本放大
        if len(body) > int(Config.REQUEST_LOG_BODY_MAX_BYTES or 200 * 1024):
            logger.bind(payload=f"<skipped large request body: {len(body)} bytes>", name=None).debug("request_body: ")
            return
        try:
            parsed = json.loads(body.decode("utf-8"))
            logger.bind(payload=_trim_request_payload(parsed), name=None).debug("request_json: ")
        except Exception:
            logger.bind(payload=_trim_request_payload(body), name=None).debug("request_body: ")
    except Exception:
        # 忽略文件上传类型的数据
        pass


# 注册路由
argus.include_router(user.router)
argus.include_router(project.router, dependencies=[Depends(request_info)])
argus.include_router(http.router, dependencies=[Depends(request_info)])
argus.include_router(testcase_router, dependencies=[Depends(request_info)])
argus.include_router(functional_case_router, dependencies=[Depends(request_info)])
argus.include_router(functional_case_skill_router, dependencies=[Depends(request_info)])
argus.include_router(interface_manage_router, dependencies=[Depends(request_info)])
argus.include_router(mock_config_router, dependencies=[Depends(request_info)])
argus.include_router(config_router, dependencies=[Depends(request_info)])
argus.include_router(online_router, dependencies=[Depends(request_info)])
argus.include_router(oss_router, dependencies=[Depends(request_info)])
argus.include_router(operation_router, dependencies=[Depends(request_info)])
argus.include_router(platform_task_router, dependencies=[Depends(request_info)])
argus.include_router(msg_router, dependencies=[Depends(request_info)])
argus.include_router(workspace_router, dependencies=[Depends(request_info)])
argus.include_router(performance_router, dependencies=[Depends(request_info)])
argus.include_router(ui_test_router, dependencies=[Depends(request_info)])
argus.include_router(share_router)
argus.include_router(notification_admin_router, dependencies=[Depends(request_info)])

STATICS_DIR.mkdir(parents=True, exist_ok=True)
argus.mount("/statics", StaticFiles(directory=str(STATICS_DIR)), name="statics")

templates = Jinja2Templates(directory=str(STATICS_DIR))


@argus.get("/")
async def serve_spa(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@argus.get("/{filename}")
async def get_site(filename):
    filename = str(STATICS_DIR / filename)

    if not isfile(filename):
        return Response(status_code=404)

    with open(filename, mode='rb') as f:
        content = f.read()

    content_type, _ = guess_type(filename)
    return Response(content, media_type=content_type)


@argus.get("/static/{filename}")
async def get_site_static(filename):
    filename = str(STATICS_DIR / "static" / filename)

    if not isfile(filename):
        return Response(status_code=404)

    with open(filename, mode='rb') as f:
        content = f.read()

    content_type, _ = guess_type(filename)
    return Response(content, media_type=content_type)


@argus.on_event('startup')
async def init_redis():
    """
    初始化redis，失败则服务起不来
    :return:
    """
    try:
        await RedisHelper.ping()
        logger.bind(name=None).success("redis connected successfully.        ✔")
    except Exception as e:
        if not Config.REDIS_ON:
            logger.bind(name=None).warning("redis disabled.        🚫")
            return
        logger.bind(name=None).warning(f"redis connect failed.        🚫 {e}")
        raise e


@argus.on_event('startup')
async def init_rabbitmq():
    if not Config.PLATFORM_TASK_WORKER_ENABLED:
        return
    try:
        with rabbit_connection():
            pass
        logger.bind(name=None).success("rabbitmq connected successfully.        ✔")
    except Exception as e:
        logger.bind(name=None).warning(f"rabbitmq connect failed.        🚫 {e}")


@argus.on_event('startup')
def init_scheduler():
    """
    初始化定时任务
    :return:
    """
    job_store = {
        'default': SQLAlchemyJobStore(url=Config.SQLALCHEMY_DATABASE_URI, engine_options={"pool_recycle": 1500},
                                      pickle_protocol=3)
    }
    scheduler = AsyncIOScheduler()
    Scheduler.init(scheduler)
    Scheduler.configure(jobstores=job_store)
    Scheduler.start()
    logger.bind(name=None).success("scheduler started successfully.        ✔")


@argus.on_event('startup')
async def init_database():
    """
    初始化数据库，建表
    :return:
    """
    if not Config.RUNTIME_SCHEMA_MIGRATION_ENABLED:
        logger.bind(name=None).success("database runtime migration disabled, use alembic.        ✔")
        return
    try:
        await create_table()
        logger.bind(name=None).success("database initialized successfully.        ✔")
    except Exception as e:
        logger.bind(name=None).error(f"database and tables  created failed.        ❌\nerror: {e}")
        raise


@argus.on_event('startup')
async def init_record_proxy():
    """
    启动录制代理，避免部署时需要额外手动运行 proxy.py
    """
    global proxy_task
    if proxy_task is not None:
        return
    proxy_task = asyncio.create_task(start_proxy(logger))
    proxy_task.add_done_callback(_handle_proxy_task_done)
    logger.bind(name=None).success(f"record proxy startup task created.        ✔ port={Config.PROXY_PORT}")


@argus.on_event('shutdown')
def stop_test():
    pass


@argus.on_event('startup')
async def ensure_oss_file_columns():
    """对象存储真实连通性检查"""
    try:
        client = OssClient.get_oss_client()
        bucket_name = get_default_bucket_name() or None
        if hasattr(client, 'client') and hasattr(client.client, 'head_bucket') and bucket_name:
            client.client.head_bucket(Bucket=bucket_name)
        else:
            await client.list_objects(prefix='', recursive=False, bucket_name=bucket_name)
        logger.bind(name=None).success("object storage connected successfully.        ✔")
    except Exception as e:
        logger.bind(name=None).warning(f"object storage connect failed.        🚫 {e}")


@argus.on_event('startup')
async def start_platform_task_worker():
    global platform_worker_task
    if not Config.PLATFORM_TASK_WORKER_ENABLED:
        logger.bind(name=None).success("platform task worker disabled.        ✔")
        return
    if platform_worker_task is not None and not platform_worker_task.done():
        return
    platform_worker_task = asyncio.create_task(platform_task_worker.start())
    logger.bind(name=None).success("platform task worker startup task created.        ✔")


@argus.on_event('shutdown')
async def stop_platform_task_worker():
    global platform_worker_task
    await platform_task_worker.stop()
    if platform_worker_task is not None:
        platform_worker_task.cancel()
        try:
            await platform_worker_task
        except Exception:
            pass
        platform_worker_task = None


@argus.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    async def send_heartbeat():
        while True:
            logger.debug("sending heartbeat")
            await websocket.send_json({
                'type': 3
            })
            await asyncio.sleep(Config.HEARTBEAT)

    await ws_manage.connect(websocket, user_id)
    try:
        # 定义特殊值的回复，配合前端实现确定连接，心跳检测等逻辑
        questions_and_answers_map: dict = {
            "HELLO SERVER": F"hello {user_id}",
            "HEARTBEAT": F"{user_id}",
        }

        # 存储连接后获取消息
        msg_records = await ArgusNotificationDao.list_messages(msg_type=MessageTypeEnum.all.value, receiver=user_id,
                                                              msg_status=MessageStateEnum.unread.value)
        # 如果有未读消息, 则推送给前端对应的count
        if len(msg_records) > 0:
            await websocket.send_json(WebSocketMessage.msg_count(len(msg_records), True))
        # 发送心跳包
        # asyncio.create_task(send_heartbeat())
        while True:
            data: str = await websocket.receive_text()
            du = data.upper()
            if du in questions_and_answers_map:
                await ws_manage.send_personal_message(message=questions_and_answers_map.get(du), websocket=websocket)
    except WebSocketDisconnect:
        if user_id in ws_manage.active_connections:
            ws_manage.disconnect(user_id)
    except Exception as e:
        logger.bind(name=None).debug(f"websocket: 用户: {user_id} 异常退出: {e}")
