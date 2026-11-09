"""Local regression tests; no business writes or external message sends."""
import asyncio
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core.data_factory import actions, settings as cfg, store, runner
from app.core.data_factory.validation import validate
from app.core.data_factory.point_sender import MessageSender, DynamicProtobufBuilder
from app.core.data_factory.gp_sr import DynamicSQLGenerator


class DataFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.original=cfg.CONFIG_PATH
        self.original_runtime=store.RUNTIME
        self.config=copy.deepcopy(cfg.settings())
        cfg.CONFIG_PATH=Path(self.temporary.name)/'config.json'
        cfg.CONFIG_PATH.write_text(json.dumps(self.config),encoding='utf-8')
        store.RUNTIME=Path(self.temporary.name)/'runtime'

    def tearDown(self):
        cfg.CONFIG_PATH=self.original
        store.RUNTIME=self.original_runtime
        self.temporary.cleanup()

    def point_fields(self):
        return dict(module='tagXts',sendWay='KAFKA',topicName='unit-test',startTime='2026-01-01 00:00:00',endTime='2026-01-01 00:00:00',frequency=1,tagList=[dict(pointTag='test',valueType='Fixed',value='1')])

    def test_passwords_retained_after_masked_save(self):
        value=cfg.redact(self.config)
        value['qms']['host']='127.0.0.1'
        saved=cfg.save_settings(value)
        self.assertEqual(saved['qms']['password'],self.config['qms']['password'])
        for environment, connections in self.config['business'].items():
            for kind, connection in connections.items():
                self.assertEqual(saved['business'][environment][kind]['password'],connection['password'])

    def test_invalid_config_does_not_replace_working_file(self):
        original=cfg.CONFIG_PATH.read_text(encoding='utf-8')
        value=cfg.redact(self.config); value['qms']['port']=0
        with self.assertRaises(ValueError): cfg.save_settings(value)
        self.assertEqual(original,cfg.CONFIG_PATH.read_text(encoding='utf-8'))

    def test_redaction_preserves_json_structure(self):
        result=cfg.scrub_result({'status':True,'msg':'password secret-value','number':10},{'password':'secret-value'})
        self.assertIs(result['status'],True)
        self.assertNotIn('secret-value',json.dumps(result))

    def test_parameter_validation(self):
        data=self.point_fields()
        validate('sim-point-tag',data)
        for key,value in [('frequency',0),('endTime','2025-01-01 00:00:00'),('sendWay','unsupported')]:
            invalid=copy.deepcopy(data); invalid[key]=value
            with self.assertRaises(ValueError): validate('sim-point-tag',invalid)

    def test_nonfinite_values_and_duplicate_tags_rejected(self):
        data=self.point_fields(); data['tagList'][0]['value']='nan'
        with self.assertRaises(ValueError): validate('sim-point-tag',data)
        data=self.point_fields(); data['tagList']*=2
        with self.assertRaises(ValueError): validate('sim-point-tag',data)

    def test_schema_injection_rejected(self):
        with self.assertRaises(ValueError): validate('create-gp-sr-table',dict(environment=next(iter(self.config['business'])),mes='mes; DROP TABLE x',repair='repair',period=['daily']))

    def test_unconfigured_deployment_rejected(self):
        with self.assertRaises(ValueError): validate('sync-harbor-image',dict(targetIp='unknown',targetService='byt-grid-data'))

    def test_natural_week_includes_final_day(self):
        values=actions.time_ranges('周',2026,True,datetime(2026,11,6,12))
        self.assertTrue(any(start<=datetime(2026,11,8,23,59,59)<end for start,end in values))
        self.assertTrue(all(start.year==2026 and end<=datetime(2027,1,1) for start,end in values))

    def test_incomplete_periods_excluded(self):
        values=actions.time_ranges('月',2026,False,datetime(2026,11,6))
        self.assertEqual(len(values),10)

    def test_record_is_owned_and_secrets_not_saved(self):
        task=store.create('create-db-user',1,'unit',{'password':'private'})
        self.assertEqual(store.get(task,1)['fields']['password'],'***')
        self.assertIsNone(store.get(task,2))
        self.assertEqual(store.history(2)['total'],0)

    def test_logs_replayed_with_cursor(self):
        task=store.create('sim-point-tag',1,'unit',{})
        store.log(task,'one'); store.log(task,'two')
        rows=store.logs(task)
        self.assertEqual([row['message'] for row in store.logs(task,rows[0]['id'])],['two'])

    def test_history_filters_before_pagination_and_preserves_ownership(self):
        earlier=store.create('sim-point-tag',1,'unit',{})
        later=store.create('sim-point-tag',1,'unit',{})
        other=store.create('sim-point-tag',2,'other',{})
        store.update(earlier,'success',{})
        store.update(later,'failed',{})
        store.update(other,'success',{})
        with store.connection() as db:
            db.execute('UPDATE tasks SET created=? WHERE id=?',(100,earlier))
            db.execute('UPDATE tasks SET created=? WHERE id=?',(200,later))
        result=store.history(1,'sim-point-tag',1,1,status='success')
        self.assertEqual(result['total'],1)
        self.assertEqual(result['list'][0]['id'],earlier)
        result=store.history(1,started_after=150,started_before=250)
        self.assertEqual([row['id'] for row in result['list']],[later])
        self.assertEqual(store.history(1,status='success',started_after=150)['total'],0)

    def test_cancel_does_not_affect_other_user(self):
        task=store.create('sim-point-tag',1,'unit',{})
        store.cancel(task,2); self.assertFalse(store.should_cancel(task))
        store.cancel(task,1); self.assertTrue(store.should_cancel(task))

    def test_increment_and_alternation(self):
        data=self.point_fields(); data['tagList'][0].update(valueType='Increasing',value='10,0.5')
        sender=MessageSender(data)
        self.assertEqual([sender._update_value('test') for _ in range(3)],[10,10.5,11])
        data['tagList'][0].update(valueType='Alternating',value='0,1')
        sender=MessageSender(data)
        self.assertEqual([sender._update_value('test') for _ in range(4)],[0,1,0,1])

    def test_protobuf_roundtrip(self):
        cls=DynamicProtobufBuilder.create_outer_descriptor(); msg=cls()
        msg.tags.add(name='IoStatus',time='20260101000000',value='1')
        other=cls(); other.ParseFromString(msg.SerializeToString())
        self.assertEqual(other.tags[0].name,'IoStatus')

    def test_gp_sr_all_period_templates(self):
        db=dict(gp_db_mes='mes',gp_db_repair='repair')
        for period in ['15分钟','每小时','自然日']:
            gp=DynamicSQLGenerator.generate_mes_gp_table(period,'rt','新天山（测试）',[('test_quota','测试指标')],db)
            sr=DynamicSQLGenerator.generate_mes_sr_table(period,False,'新天山（测试）',[('test_quota','测试指标')])
            self.assertIn('test_quota',gp); self.assertIn('test_quota',sr)

    def test_http_failure_propagates(self):
        sender=MessageSender(self.point_fields())
        with patch.object(sender,'_post_request_raw',new=AsyncMock(side_effect=RuntimeError('503'))):
            with self.assertRaises(RuntimeError): asyncio.run(sender._post_request('http://localhost',{}))

    def test_stopped_before_start_has_terminal_status(self):
        task=store.create('sim-point-tag',1,'unit',{})
        store.cancel(task,1)
        asyncio.run(runner.execute(task,'sim-point-tag',{}))
        self.assertEqual(store.get(task,1)['status'],'cancelled')


if __name__=='__main__':
    unittest.main(verbosity=2)
