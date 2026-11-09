"""Exercise real workers against local HTTP fixtures."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

from app.core.data_factory import runner, store
from app.core.data_factory.settings import settings


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.old_runtime=store.RUNTIME
        store.RUNTIME=self.base/'runtime'
        self.config=settings()
        self.config['qms']['enabled']=False
        self.received=[]
        self.arrived=asyncio.Event()
        self.delay=0
        async def receive(request):
            self.received.append(await request.json())
            self.arrived.set()
            await asyncio.sleep(self.delay)
            return web.json_response({'code':0})
        app=web.Application(); app.router.add_post('/points/{box}',receive)
        self.server=web.AppRunner(app); await self.server.setup()
        site=web.TCPSite(self.server,'127.0.0.1',0); await site.start()
        port=site._server.sockets[0].getsockname()[1]
        self.config['products']=[dict(module='pointXts',product_name='local fixture',send_way='API',route_key='api_point_cloud',address='http://127.0.0.1:'+str(port),api_path='/points')]
        config_path=self.base/'config.json'; config_path.write_text(json.dumps(self.config),encoding='utf-8')
        self.environment=patch.dict(os.environ,{'ARGUS_DATA_FACTORY_CONFIG':str(config_path),'ARGUS_DATA_FACTORY_RUNTIME':str(store.RUNTIME)})
        self.environment.start()

    async def asyncTearDown(self):
        await self.server.cleanup()
        self.environment.stop(); store.RUNTIME=self.old_runtime
        self.temp.cleanup()

    def fields(self):
        return dict(module='pointXts',sendWay='API',topicName='unit',startTime='2026-01-01 00:00:00',endTime='2026-01-01 00:00:00',frequency=1,tagList=[dict(pointTag='IoStatus',valueType='Fixed',value='1')])

    async def test_worker_sends_real_http_and_persists_result(self):
        fields=self.fields(); task_id=store.create('sim-point-tag',1,'test',fields)
        await asyncio.wait_for(runner.execute(task_id,'sim-point-tag',fields),20)
        task=store.get(task_id,1)
        self.assertEqual(task['status'],'success',task)
        self.assertEqual(self.received[0]['rts'][0]['tnm'],'IoStatus')
        self.assertEqual(self.received[0]['rts'][0]['val'],1)
        self.assertTrue(store.logs(task_id))

    async def test_running_worker_can_be_cancelled(self):
        fields=self.fields(); fields['endTime']='2026-01-01 00:02:00'
        task_id=store.create('sim-point-tag',1,'test',fields)
        job=asyncio.create_task(runner.execute(task_id,'sim-point-tag',fields))
        await asyncio.wait_for(self.arrived.wait(),15)
        store.cancel(task_id,1)
        await asyncio.wait_for(job,15)
        self.assertEqual(store.get(task_id,1)['status'],'cancelled')



if __name__=='__main__':
    unittest.main(verbosity=2)
