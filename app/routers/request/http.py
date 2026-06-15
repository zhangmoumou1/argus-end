import asyncio
import json
import random
import uuid
from json import JSONDecodeError
from typing import List, Dict
from types import SimpleNamespace

from fastapi import Depends, APIRouter

from app.core.executor import Executor
from app.crud.operation.ArgusOperationDao import ArgusOperationDao
from app.enums.OperationEnum import OperationType
from app.crud.test_case.TestcaseDataDao import ArgusTestcaseDataDao
from app.enums.CertEnum import CertType
from app.handler.fatcory import ArgusResponse
from app.middleware.AsyncHttpClient import AsyncRequest
from app.models import async_session
from app.routers import Permission
from app.routers.request.http_schema import HttpRequestForm

router = APIRouter(prefix="/request")

# random_dict = dict()
CERT_URL = "http://mitm.it/cert/"


@router.post("/http")
async def http_request(data: HttpRequestForm, _=Depends(Permission())):
    try:
        r = await AsyncRequest.client(data.url, data.body_type, headers=data.headers, body=data.body)
        response = await r.invoke(data.method)
        if response.get("status"):
            return ArgusResponse.success(response)
        return ArgusResponse.failed(response.get("msg"), data=response)
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/cert")
async def http_request(cert: CertType):
    try:
        suffix = cert.get_suffix()
        client = AsyncRequest(CERT_URL + suffix)
        content = await client.download()
        shuffle = list(range(0, 9))
        random.shuffle(shuffle)
        filename = f"{''.join(map(lambda x: str(x), shuffle))}mitmproxy.{suffix}"
        with open(filename, 'wb') as f:
            f.write(content)
        return ArgusResponse.file(filename, f"mitmproxy.{suffix}")
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/run")
async def execute_case(env: int, case_id: int, user_info=Depends(Permission())):
    try:
        executor = Executor(runtime_user_id=user_info.get("id", 0))
        test_data = await ArgusTestcaseDataDao.list_testcase_data_by_env(env, case_id)
        ans = dict()
        if not test_data:
            result, _ = await executor.run(env, case_id)
            ans["默认数据"] = result
        else:
            for data in test_data:
                params = json.loads(data.json_data)
                result, _ = await executor.run(env, case_id, request_param=params)
                ans[data.name] = result
        async with async_session() as session:
            async with session.begin():
                log_model = SimpleNamespace(
                    env=env,
                    case_id=case_id,
                    action="执行接口用例",
                    __fields__=[SimpleNamespace(name="env"), SimpleNamespace(name="case_id"), SimpleNamespace(name="action")],
                    __tag__="接口用例",
                    __alias__={
                        "env": "环境",
                        "case_id": "用例ID",
                        "action": "执行动作",
                    },
                    __show__=2,
                )
                await ArgusOperationDao.insert_log(
                    session,
                    user_info["id"],
                    OperationType.EXECUTE,
                    log_model,
                    key=case_id,
                    changed=["action"],
                )
        return ArgusResponse.success(ans)
    except JSONDecodeError:
        return ArgusResponse.failed("测试数据不为合法的JSON")
    except Exception as e:
        return ArgusResponse.failed(e)


@router.get("/retry", summary="根据测试数据重新运行测试用例")
async def re_run_case(env: int, case_id: int, data_id: int = 0, user_info=Depends(Permission())):
    try:
        executor = Executor(runtime_user_id=user_info.get("id", 0))
        params = dict()
        if data_id != 0:
            # if data_id not exists, use original params (empty dict)
            test_data = await ArgusTestcaseDataDao.query_record(id=data_id)
            params = json.loads(test_data.json_data)
        result, _ = await executor.run(env, case_id, request_param=params)
        return ArgusResponse.success(result)
    except JSONDecodeError:
        return ArgusResponse.failed("测试数据不为合法的JSON")


@router.post("/run/async")
async def execute_case(env: int, case_id: List[int], user_info=Depends(Permission())):
    data = dict()
    # s = time.perf_counter()
    await asyncio.gather(*(run_single(env, c, data, user_info.get("id", 0)) for c in case_id))
    # elapsed = time.perf_counter() - s
    # print(f"async executed in {elapsed:0.2f} seconds.")
    return ArgusResponse.success()


@router.post("/run/sync")
async def execute_case(env: int, case_id: List[int], user_info=Depends(Permission())):
    data = dict()
    task_id = uuid.uuid5(uuid.NAMESPACE_URL, "task")

    # s = time.perf_counter()
    for c in case_id:
        executor = Executor(runtime_user_id=user_info.get("id", 0))
        data[c] = await executor.run(env, c)
    # elapsed = time.perf_counter() - s
    # print(f"sync executed in {elapsed:0.2f} seconds.")
    return ArgusResponse.success(data)


@router.post("/run/multiple")
async def execute_as_report(env: int, case_id: List[int], user_info=Depends(Permission())):
    report_id = await Executor.run_multiple(user_info['id'], env, case_id)
    return ArgusResponse.success(report_id)
    # task = asyncio.create_task(Executor.run_multiple(user_info['id'], env, case_id))
    # random_id = uuid.uuid5(uuid.NAMESPACE_URL, "task")
    # random_dict[random_id] = task
    # return ArgusResponse.success(data=random_id, msg="任务正在后台运行中, 请静静等待🎉")


# @router.post("/cancel")
# async def execute_as_report(random_id: str, user_info=Depends(Permission())):
#     if not random_dict.get(random_id):
#         return ArgusResponse.failed("未找到该任务, 可能已结束")
#     task = random_dict.pop(random_id)
#     # 取消任务
#     task.cancel()
#     return ArgusResponse.success(data=random_id, msg="操作已停止")


async def run_single(env: int, case_id: int, data: Dict[int, tuple], runtime_user_id: int = 0):
    executor = Executor(runtime_user_id=runtime_user_id)
    data[case_id] = await executor.run(env, case_id)
