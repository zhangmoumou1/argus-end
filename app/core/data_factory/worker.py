"""An isolated, cancellable process. Credentials arrive only through stdin."""
import asyncio
import json
import sys

from .settings import scrub, scrub_result, settings
from .validation import validate


async def main():
    payload = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    config = settings()
    async def log(value):
        print(scrub(value,config,payload['fields']),flush=True)
    try:
        fields = validate(payload['scene'],payload['fields'])
        from .actions import run_scene
        result = await run_scene(payload['scene'],fields,payload['task_id'],log)
    except Exception as error:
        result = {'status':False,'msg':scrub(str(error),config,payload['fields'])}
    print('DF_RESULT:'+json.dumps(scrub_result(result,config,payload['fields']),ensure_ascii=False,default=str),flush=True)


if __name__=='__main__':
    asyncio.run(main())
