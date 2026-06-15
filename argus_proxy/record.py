"""
流量录制->生成case功能
record steps and generate testcase
"""
import asyncio
import json
import re

from loguru import logger

from app.core.interface_sample import upsert_endpoint_sample_by_record
from app.core.ws_connection_manager import ws_manage
from app.enums.MessageEnum import WebSocketMessageEnum
from app.middleware.RedisManager import RedisHelper
from app.models import async_session
from app.schema.request import RequestInfo


class ArgusRecorder(object):
    def request(self, flow):
        flow.request.headers["X-Forwarded-For"] = flow.client_conn.address[0]

    async def response(self, flow):
        if "argus.fun" in flow.request.url or flow.request.method.lower() == "options" or \
                flow.request.url.endswith(("js", "css", "ttf", "jpg", "svg", "gif")):
            return
        addr = flow.client_conn.address[0]
        record = await RedisHelper.get_address_record(addr)
        if not record:
            return
        data = json.loads(record)
        pattern = re.compile(data.get("regex"))
        if re.findall(pattern, flow.request.url):
            request_data = RequestInfo(flow)
            dump_data = request_data.dumps()
            await RedisHelper.cache_record(addr, dump_data)
            try:
                async with async_session() as session:
                    async with session.begin():
                        sample = await upsert_endpoint_sample_by_record(
                            session, request_data.dict(), int(data.get("user_id") or 0)
                        )
                        if sample is None:
                            logger.bind(name=None).warning(
                                f"record sample not matched url={flow.request.url}, method={flow.request.method}"
                            )
            except Exception as exc:
                logger.bind(name=None).error(
                    f"record sample upsert failed url={flow.request.url}, method={flow.request.method}, error={exc}"
                )
            asyncio.create_task(
                ws_manage.send_data(data.get("user_id"), WebSocketMessageEnum.RECORD, dump_data)
            )
