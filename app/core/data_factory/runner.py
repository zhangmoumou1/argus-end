import asyncio
import json
import os
import signal
import sys

from . import store
from .settings import ROOT, scrub, scrub_result, settings

ACTIVE = set()


async def terminate(process):
    if process.returncode is not None:
        return
    if os.name == 'nt':
        killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(process.pid), '/T', '/F',
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                                                       creationflags=0x08000000)
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def execute(task_id, scene, fields):
    process = None
    result = None
    config = {}
    async def read_output():
        nonlocal result
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            value = line.decode('utf-8', errors='replace').rstrip()
            if value.startswith('DF_RESULT:'):
                result = scrub_result(json.loads(value[10:]), config, fields)
            else:
                store.log(task_id, scrub(value, config, fields))
    reader = None
    try:
        config = settings()
        if store.should_cancel(task_id):
            store.update(task_id, 'cancelled', {'status': False, 'msg': '任务已取消'})
            return
        store.update(task_id, 'running')
        store.log(task_id, '开始执行任务 ' + task_id)
        kwargs = {'creationflags': 0x08000000} if os.name == 'nt' else {'start_new_session': True}
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'app.core.data_factory.worker',
                        cwd=str(ROOT), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT, env=environment, limit=2 * 1024 * 1024, **kwargs)
        process.stdin.write(json.dumps({'scene': scene, 'fields': fields, 'task_id': task_id}, ensure_ascii=False).encode('utf-8'))
        await process.stdin.drain()
        process.stdin.close()
        reader = asyncio.create_task(read_output())
        while process.returncode is None:
            store.update(task_id)
            if store.should_cancel(task_id):
                await terminate(process)
                await reader
                store.log(task_id, '任务已停止；已经发送的数据或已经执行的业务操作不会回滚。')
                store.update(task_id, 'cancelled', {'status': False, 'msg': '任务已停止'})
                return
            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), timeout=1)
            except asyncio.TimeoutError:
                pass
        await reader
        if result is None:
            result = {'status': False, 'msg': '执行进程异常退出，退出码: ' + str(process.returncode)}
        store.update(task_id, 'success' if result.get('status') and process.returncode == 0 else 'failed', result)
        store.log(task_id, result.get('msg', '执行结束'))
    except asyncio.CancelledError:
        if process:
            await terminate(process)
        store.update(task_id, 'interrupted', {'status': False, 'msg': '服务关闭，任务已停止，请检查业务侧状态'})
        raise
    except Exception as error:
        if process:
            await terminate(process)
        result = {'status': False, 'msg': scrub(str(error), config, fields)}
        store.update(task_id, 'failed', result)
        store.log(task_id, result['msg'])
    finally:
        if reader and not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


def start(task_id, scene, fields):
    job = asyncio.create_task(execute(task_id, scene, fields))
    ACTIVE.add(job)
    job.add_done_callback(ACTIVE.discard)


async def shutdown():
    jobs = list(ACTIVE)
    for job in jobs:
        job.cancel()
    await asyncio.gather(*jobs, return_exceptions=True)
