import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.data_factory import runner, store
from app.core.data_factory.business import get_business_session, session_for
from app.core.data_factory.settings import SCENES, settings, redact, save_settings, check_database, scrub
from app.core.data_factory.products import enabled_products, product_configs
from app.core.data_factory.validation import validate
from app.handler.fatcory import ArgusResponse
from app.routers import Permission

router = APIRouter(prefix='/datafactory',tags=['数据工厂'])


class RunForm(BaseModel):
    fields: Dict[str,Any] = Field(default_factory=dict)


def owned(task_id,user):
    task=store.get(task_id,int(user['id']))
    if not task:
        raise HTTPException(404,'任务不存在')
    return task


@router.get('/catalog')
async def catalog(user=Depends(Permission())):
    config=settings()
    counts=store.statistics()
    products=[]
    products_error=''
    try:
        products=await enabled_products()
    except Exception as error:
        products_error=scrub(str(error),config)
    return ArgusResponse.success({'scenes':[{'id':key,'title':title,'executions':counts.get(key,0)} for key,title in SCENES.items()],
        'platforms':list(config.get('platforms',{})),
        'environments':[name for name,value in config.get('business',{}).items() if 'greenplum' in value and 'starrocks' in value],
        'products':products,
        'productsError':products_error,
        'targets':list(config.get('deployment',{}).get('targets',{}))})


@router.get('/members')
async def members(user=Depends(Permission())):
    config=settings()['audit']
    async with await get_business_session(config['environment'],config['database']) as session:
        rows=(await session.execute(text('SELECT id,member_name FROM qa_member ORDER BY member_name'))).mappings().all()
    return ArgusResponse.success([{'id':str(row['id']),'name':row['member_name']} for row in rows])


@router.post('/scenes/{scene}/run')
async def run(scene: str, data: RunForm, user=Depends(Permission())):
    try:
        fields=validate(scene,data.fields)
    except (ValueError,TypeError,KeyError) as error:
        return ArgusResponse.success(code=101,msg=str(error))
    task_id=store.create(scene,int(user['id']),user.get('name',''),fields)
    runner.start(task_id,scene,fields)
    return ArgusResponse.success(store.get(task_id,int(user['id'])),msg='任务已提交')


@router.get('/tasks')
async def tasks(scene: str=None,page: int=Query(1,ge=1),size: int=Query(20,ge=1,le=100),
                status: str=None,started_after: datetime=None,started_before: datetime=None,user=Depends(Permission())):
    if status and status not in store.TERMINAL | {'queued', 'running'}:
        return ArgusResponse.success(code=101,msg='任务状态不合法')
    def timestamp(value):
        if value is None:
            return None
        return (value if value.tzinfo else value.replace(tzinfo=timezone(timedelta(hours=8)))).timestamp()
    return ArgusResponse.success(store.history(int(user['id']),scene,page,size,status,
        timestamp(started_after),timestamp(started_before)))


@router.get('/tasks/{task_id}')
async def task(task_id: str,after: int=Query(0,ge=0),user=Depends(Permission())):
    value=owned(task_id,user)
    value['logs']=store.logs(task_id,after)
    value['cursor']=value['logs'][-1]['id'] if value['logs'] else after
    return ArgusResponse.success(value)


@router.post('/tasks/{task_id}/stop')
async def stop(task_id: str,user=Depends(Permission())):
    value=owned(task_id,user)
    if value['status'] not in store.TERMINAL:
        store.cancel(task_id,int(user['id']))
    return ArgusResponse.success(msg='停止请求已提交')


@router.get('/tasks/{task_id}/events')
async def events(task_id: str,request: Request,after: int=Query(0,ge=0),user=Depends(Permission())):
    owned(task_id,user)
    async def stream():
        cursor=after
        while not await request.is_disconnected():
            value=owned(task_id,user)
            batch=store.logs(task_id,cursor)
            if batch:
                cursor=batch[-1]['id']
            value.update(logs=batch,cursor=cursor)
            yield 'data: '+json.dumps(value,ensure_ascii=False)+'\n\n'
            if value['status'] in store.TERMINAL and len(batch)<500:
                break
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@router.on_event('shutdown')
async def shutdown():
    await runner.shutdown()


@router.get('/config')
async def get_config(user=Depends(Permission())):
    return ArgusResponse.success(redact(settings()))


@router.put('/config')
async def put_config(data: RunForm,user=Depends(Permission())):
    try:
        return ArgusResponse.success(redact(save_settings(data.fields)),msg='工具配置已保存')
    except (ValueError,TypeError,KeyError) as error:
        return ArgusResponse.success(code=101,msg=str(error))


@router.post('/config/test-database')
async def test_database(data: RunForm,user=Depends(Permission())):
    fields=dict(data.fields)
    config=settings()
    if fields.get('password')=='***':
        if fields.get('source')=='qms':
            fields['password']=config.get('qms',{}).get('password')
        else:
            fields['password']=config.get('business',{}).get(fields.get('environment'),{}).get(fields.get('kind','mysql'),{}).get('password')
    try:
        check_database(fields)
        async with session_for(fields,fields.get('database') or None,fields.get('kind','mysql')) as session:
            await session.execute(text('SELECT 1'))
        return ArgusResponse.success(msg='数据库连接成功')
    except Exception as error:
        return ArgusResponse.success(code=101,msg=scrub(str(error),config,fields))


@router.get('/products')
async def get_products(user=Depends(Permission())):
    return ArgusResponse.success(await product_configs())


@router.put('/products')
async def put_product(data: RunForm,user=Depends(Permission())):
    fields=dict(data.fields)
    for key in ('product_name','module','send_way','route_key','address'):
        if not fields.get(key):
            return ArgusResponse.success(code=101,msg='请填写 '+key)
    if fields['send_way'] not in ('API','KAFKA') or fields['route_key'] not in ('kafka','api_point_cloud','api_point_byd'):
        return ArgusResponse.success(code=101,msg='发送协议或路由不合法')
    source=settings()['qms']
    fields.update(api_path=fields.get('api_path') or '',remark=fields.get('remark') or '',is_enabled=int(bool(fields.get('is_enabled',1))),user=str(user['id']))
    async with session_for(source,source.get('database','qms')) as session:
        duplicate=(await session.execute(text('SELECT id FROM product_function_config WHERE module=:module AND deleted_at=0'),fields)).scalar()
        if duplicate and int(duplicate)!=int(fields.get('id') or 0):
            return ArgusResponse.success(code=101,msg='模块编码已存在')
        if fields.get('id'):
            await session.execute(text('''UPDATE product_function_config SET product_name=:product_name,module=:module,send_way=:send_way,
                route_key=:route_key,address=:address,api_path=:api_path,is_enabled=:is_enabled,remark=:remark,update_user=:user,updated_at=NOW()
                WHERE id=:id AND deleted_at=0'''),fields)
        else:
            await session.execute(text('''INSERT INTO product_function_config(product_name,module,send_way,route_key,address,api_path,is_enabled,remark,create_user,update_user,created_at,updated_at,deleted_at)
                VALUES(:product_name,:module,:send_way,:route_key,:address,:api_path,:is_enabled,:remark,:user,:user,NOW(),NOW(),0)'''),fields)
        await session.commit()
    return ArgusResponse.success(msg='产品功能配置已保存')
