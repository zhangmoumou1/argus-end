"""Harbor replication and JumpServer deployment with checked command results."""
import asyncio
import base64
import json
import re
import shlex
import time
from urllib.parse import quote

import aiohttp

from .settings import settings


async def deploy(fields, log):
    config = settings()['deployment']
    target = config['targets'][fields['targetIp']]
    harbor = config['harbor_url'].rstrip('/')
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        async with session.post(harbor+'/c/login', data={'principal':config['harbor_username'],'password':config['harbor_password']}) as response:
            response.raise_for_status()
            await response.read()
        await log('Harbor 登录完成，正在查找同步策略')
        async with session.get(harbor+'/api/replication/policies',params={'name':config['policy_name']}) as response:
            response.raise_for_status()
            policies = await response.json()
        policy = next((x for x in policies if x.get('name')==config['policy_name']),None)
        if not policy:
            raise ValueError('Harbor 同步策略不存在')
        async with session.get(harbor+'/api/repositories/'+config['repository']+'/tags',params={'detail':1}) as response:
            response.raise_for_status()
            tags = await response.json()
        tags = sorted(tags,key=lambda x:x.get('created',''),reverse=True)
        tag = fields.get('imageTag') or (tags[0]['name'] if tags else '')
        if not tag or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',tag):
            raise ValueError('没有有效镜像标签')
        if not any(x.get('name')==tag for x in tags):
            raise ValueError('源 Harbor 中未找到指定镜像: '+tag)
        async with session.post(harbor+'/api/replication/executions',json={'policy_id':policy['id']}) as response:
            response.raise_for_status()
            location = response.headers.get('Location','')
            await response.read()
        if not location:
            raise ValueError('Harbor 未返回同步执行 ID，无法确认同步状态，已停止部署')
        execution_url = location if location.startswith('http') else harbor+'/'+location.lstrip('/')
        await log('同步已触发，等待 Harbor 同步完成，镜像: '+tag)
        deadline = time.monotonic()+600
        while True:
            async with session.get(execution_url) as response:
                response.raise_for_status()
                execution = await response.json()
            status = str(execution.get('status','')).lower()
            if status in ('succeed','succeeded','success'):
                break
            if status in ('failed','error','stopped','cancelled'):
                raise ValueError('Harbor 镜像同步失败: '+status)
            if time.monotonic()>deadline:
                raise ValueError('Harbor 镜像同步等待超时，已停止部署')
            await asyncio.sleep(3)
    await log('镜像同步完成，正在通过 JumpServer 连接目标机器 '+fields['targetIp'])
    gateway = await jumpserver_gateway(config,target)
    image = config['registry'].rstrip('/')+'/'+config['repository']+':'+tag
    output = await asyncio.to_thread(ssh_deploy, gateway, config, image) if hasattr(asyncio,'to_thread') else await asyncio.get_running_loop().run_in_executor(None,ssh_deploy,gateway,config,image)
    for line in output:
        await log(line)
    return {'status':True,'msg':'镜像同步并部署完成: '+image,'image':image,'target':fields['targetIp']}


async def jumpserver_gateway(config,target):
    base = config['jumpserver_url'].rstrip('/')
    login = base+'/core/auth/login/'
    headers = {'X-JMS-ORG':str(config.get('org_id','')),'Referer':login,'X-TZ':'Asia/Shanghai'}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),cookie_jar=aiohttp.CookieJar(unsafe=True),headers=headers) as session:
        async with session.get(login) as response:
            response.raise_for_status()
            await response.read()
        csrf = next((c.value for c in session.cookie_jar if c.key=='jms_csrftoken'),None)
        if not csrf:
            raise ValueError('JumpServer CSRF 令牌获取失败')
        async with session.post(login,data={'username':config['jumpserver_username'],'password':config['jumpserver_password'],'csrfmiddlewaretoken':csrf}) as response:
            response.raise_for_status()
            await response.read()
        params={'asset':target['asset_id'],'system_user':target['system_user_id'],'protocol':'ssh','full_screen':'1'}
        async with session.get(base+'/api/v1/authentication/connection-token/client-url/',params=params) as response:
            response.raise_for_status()
            data = await response.json()
        encoded = data.get('url','').removeprefix('jms://') if hasattr(str,'removeprefix') else data.get('url','').replace('jms://','',1)
        if not encoded:
            raise ValueError('JumpServer 未返回连接令牌')
        decoded = json.loads(base64.b64decode(encoded+'='*((-len(encoded))%4)).decode('utf-8'))
        token = decoded['token']
        return json.loads(token) if isinstance(token,str) else token


def ssh_deploy(gateway,config,image):
    import paramiko
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    outputs=[]
    try:
        client.connect(hostname=gateway['ip'],port=int(gateway['port']),username=gateway['username'],password=gateway['password'],timeout=15,banner_timeout=15,auth_timeout=15)
        path=config['compose_path']
        if not path.startswith('/') or '\n' in path:
            raise ValueError('部署 compose 文件路径不合法')
        service='byt-grid-data'
        # Update exactly the requested service and use the same file for all compose commands.
        script = "import re,sys; p=sys.argv[1]; s=open(p).read(); pattern=r'(?ms)(^  byt-grid-data:\\s*\\n)(.*?)(?=^  [A-Za-z0-9_-]+:|\\Z)'; m=re.search(pattern,s); assert m, 'service missing'; body,n=re.subn(r'(?m)^(\\s+image:)\\s*.*$',lambda x:x.group(1)+' '+sys.argv[2],m.group(2),count=1); assert n, 'image missing'; out=s[:m.start(2)]+body+s[m.end(2):]; open(p+'.argus-backup','w').write(s); open(p,'w').write(out)"
        commands=[
            'docker pull '+shlex.quote(image),
            'python3 -c '+shlex.quote(script)+' '+shlex.quote(path)+' '+shlex.quote(image),
            'docker-compose -f '+shlex.quote(path)+' config --quiet',
            'docker-compose -f '+shlex.quote(path)+' up -d --no-deps '+service,
            "test \"$(docker inspect --format '{{.State.Running}}' byt-grid-data)\" = true",
            'docker logs --tail 100 '+service,
        ]
        for command in commands:
            stdin,stdout,stderr=client.exec_command(command,timeout=120)
            stdout.channel.settimeout(120)
            out=stdout.read().decode('utf-8',errors='replace')
            err=stderr.read().decode('utf-8',errors='replace')
            code=stdout.channel.recv_exit_status()
            outputs.append(command+'\n'+out+err)
            if code:
                raise ValueError('部署命令失败（退出码 '+str(code)+'）: '+command+'\n'+out+err)
        return outputs
    finally:
        client.close()
