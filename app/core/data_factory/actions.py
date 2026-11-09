import asyncio
from datetime import datetime, timedelta

import aiohttp
from sqlalchemy import bindparam, text

from .business import get_business_session
from .settings import settings
from .validation import identifier


async def request_json(session, method, url, **kwargs):
    async with session.request(method, url, **kwargs) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def create_platform_user(fields, log):
    config = settings()['platforms'][fields['env']]
    base = config['base_url'].rstrip('/')
    username = fields['username']
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        await log('正在登录平台管理员账号')
        code = await request_json(session, 'GET', base+'/bytserver/admin/user/getUserUnSuccess/'+config['username'])
        token = await request_json(session, 'POST', base+'/bytserver/auth/oauth/token',
            params={'randomStr':'blockPuzzle','code':code['data']['code'],'grant_type':'password','username':config['username'],'password':config['password']},
            headers={'Authorization':'Basic cGlnOnBpZw=='})
        if not token.get('access_token'):
            raise ValueError('平台登录失败')
        headers = {'Authorization':'Bearer '+token['access_token'], 'TENANT-ID':'-1' if config['kind']=='xts' else config['tenant_id']}
        if config['kind']=='xts':
            payload = dict(deptId=[config['dept_id']],email='',lockFlag='0',name=fields['name'],nickname='',password=fields['password'],phone=fields['phone'],role=[config['role_id']],username=username,workChatCode='',watermarkFlag='1')
            path = '/bytserver/admin/user/save?TENANT-ID='
        else:
            payload = dict(deptId=config['dept_id'],deptPostId='',drTeamId='',email='',entryTime='',lockFlag='0',password=fields['password'],phone=fields['phone'],realName=fields['name'],remark='',resignationTime='',role=[config['role_id']],sex='0',username=username,chatCode='',workChatCode='')
            path = '/bytserver/admin/user'
        result = await request_json(session, 'POST', base+path, json=payload, headers=headers)
        if result.get('code') != 0 or result.get('data') is not True:
            raise ValueError('创建失败: '+str(result.get('msg')))
        await log('平台用户已创建: '+username)
        if config['kind']=='xts':
            user = await request_json(session,'GET',base+'/bytserver/admin/user/details/'+username,params={'TENANT-ID':''},headers=headers)
            user_id = user.get('data',{}).get('userId') if isinstance(user.get('data'),dict) else None
            if not user_id:
                raise ValueError('用户已创建，但查询 userId 失败，请检查后再授权')
            roles = await request_json(session,'GET',base+'/bytserver/admin/role/list',params={'TENANT-ID':config['tenant_id']},headers=headers)
            role = next((r for r in roles.get('data',[]) if r.get('roleName')=='超级管理员'),None)
            if not role:
                raise ValueError('用户已创建，但未找到超级管理员角色')
            grant = await request_json(session,'POST',base+'/bytserver/admin/user/platform/choiceSave',params={'TENANT-ID':config['tenant_id']},headers=headers,json={'roleIds':[role['roleId']],'userIds':[user_id]})
            if grant.get('code') != 0 or grant.get('data') is not True:
                raise ValueError('用户已创建，但角色授权失败: '+str(grant.get('msg')))
        return {'status':True,'msg':'用户创建并授权完成: '+username}


async def create_db_user(fields, log):
    import aiomysql
    connection = await aiomysql.connect(host=fields['mysqlIp'],port=int(fields['mysqlPort']),user=fields['mysqlUser'],password=fields['mysqlPwd'],db='mysql',charset='utf8mb4',connect_timeout=15,autocommit=True)
    try:
        database = fields.get('database') or '*'
        scope = '*.*' if database=='*' else chr(96)+identifier(database)+chr(96)+'.*'
        async with connection.cursor() as cursor:
            await cursor.execute('CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s',(fields['username'],fields['targetIp'],fields['password']))
            await log('用户已创建或已存在，正在授予权限')
            await cursor.execute('GRANT ALL PRIVILEGES ON '+scope+' TO %s@%s'+(' WITH GRANT OPTION' if fields.get('grantOption',True) else ''),(fields['username'],fields['targetIp']))
        return {'status':True,'msg':'用户 '+fields['username']+'@'+fields['targetIp']+' 创建并授权完成，请重新连接数据库。已存在用户的密码保持原值。'}
    finally:
        connection.close()


def time_ranges(kind, year, include_current, now=None):
    now = now or datetime.utcnow()+timedelta(hours=8)
    start, stop = datetime(year,1,1), datetime(year+1,1,1)
    ranges = []
    current = start
    while current < stop:
        if kind=='周':
            following = current+timedelta(days=7-current.weekday())
        elif kind=='月':
            following = datetime(current.year+(current.month==12),current.month%12+1,1)
        elif kind=='季度':
            month = ((current.month-1)//3+1)*3+1
            following = datetime(current.year+((month-1)//12),(month-1)%12+1,1)
        else:
            following = stop
        following = min(following,stop)
        if current<=now and (include_current or following<=now):
            ranges.append((current, following))
        current = following
    return list(reversed(ranges))


async def release_metrics(fields, log):
    config = settings()['audit']
    ids = [str(x['id']) for x in fields['participateUser']]
    ranges = time_ranges(fields['dateType'], int(fields['year']), fields.get('includeCurrent',True))
    periods = []
    async with await get_business_session(config['environment'],config['database']) as session:
        members = (await session.execute(text('SELECT id, member_name FROM qa_member'))).mappings().all()
        names = {str(x['id']):x['member_name'] for x in members}
        for start,end in ranges:
            await log('正在统计 '+start.strftime('%Y-%m-%d')+' 至 '+(end-timedelta(seconds=1)).strftime('%Y-%m-%d'))
            query = text('SELECT id,project_name,production_name,production_version,project_manager,project_submmiter,project_submit,update_date,publish_status FROM qa_publish WHERE update_date>=:start AND update_date<:end AND project_manager IN :ids ORDER BY update_date DESC').bindparams(bindparam('ids',expanding=True))
            records = (await session.execute(query,dict(start=start,end=end,ids=ids))).mappings().all()
            demands = {}
            if records:
                demand_query = text('SELECT parent_id,project_demand FROM qa_publish_demand WHERE parent_id IN :ids').bindparams(bindparam('ids',expanding=True))
                for row in (await session.execute(demand_query,dict(ids=[str(r['id']) for r in records]))).mappings():
                    if row['project_demand']:
                        demands.setdefault(str(row['parent_id']),[]).append(row['project_demand'])
            details=[]
            for row in records:
                details.append({'project':row['project_name'],'version':str(row['production_name'] or '')+str(row['production_version'] or ''),'manager':names.get(str(row['project_manager']),str(row['project_manager'])),'tester':names.get(str(row['project_submmiter']),str(row['project_submmiter'])),'developers':', '.join(names.get(x.strip(),x.strip()) for x in str(row['project_submit'] or '').split(',')),'published_at':str(row['update_date']),'status':str(row['publish_status']),'demands':'、'.join(demands.get(str(row['id']),[]))})
            periods.append({'label':start.strftime('%Y-%m-%d')+' 至 '+(end-timedelta(seconds=1)).strftime('%Y-%m-%d'),'versions':len(records),'demand_count':len({x for v in demands.values() for x in v}),'details':details})
    return {'status':True,'msg':'需求发布统计完成','periods':periods}


async def run_scene(scene, fields, task_id, log):
    if scene=='create-platform-user':
        return await create_platform_user(fields,log)
    if scene=='create-db-user':
        return await create_db_user(fields,log)
    if scene=='release-metrics':
        return await release_metrics(fields,log)
    if scene=='sim-point-tag':
        if fields['module'] != 'Custom':
            from .products import enabled_products
            product=next((p for p in await enabled_products() if p['module']==fields['module']),None)
            if not product:
                raise ValueError('产品功能未启用或已删除')
            if product['send_way'].upper()!=fields['sendWay']:
                raise ValueError('产品功能发送方式已更新，请刷新页面后执行')
        from .point_sender import MessageSender
        async with MessageSender(fields,log) as sender:
            await sender.run()
        await asyncio.sleep(0)
        return {'status':True,'msg':'点位/标签发送完成'}
    if scene=='create-gp-sr-table':
        from .gp_sr import GeneratorGpSrManager
        data = {key:{'value':value} for key,value in fields.items()}
        names={'15_minutes':'15分钟','hourly':'每小时','daily':'自然日'}
        data['period']=[{'value':names[x]} for x in fields['period']]
        data['isDeleted']={'value':'是' if fields.get('isDeleted') else '否'}
        await log('开始读取指标配置并创建 GP / SR 表')
        return await GeneratorGpSrManager.generator_gp_sr_table('',data)
    if scene=='sync-harbor-image':
        from .deployment import deploy
        return await deploy(fields,log)
    raise ValueError('场景不存在')
