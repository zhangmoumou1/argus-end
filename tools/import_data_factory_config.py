"""Import local QMS environment values without loading its application."""
import ast
import json
import sys
from pathlib import Path


def migrate(source):
    target = Path(__file__).resolve().parents[1] / 'conf/data_factory.json'
    if target.exists():
        raise SystemExit('配置已存在，未覆盖: ' + str(target))
    values = {}
    for line in (source / 'conf/dev.env').read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        try:
            value = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            value = value.strip()
        values[key.strip()] = value
    def val(key, default=''):
        return values.get(key, default)
    def db(prefix, suffix):
        return dict(host=val(prefix+'_HOST_'+suffix), port=int(val(prefix+'_PORT_'+suffix, 3306)), username=val(prefix+'_USER_'+suffix), password=val(prefix+'_PWD_'+suffix))
    config = {'business': {}, 'platforms': {}, 'products': []}
    for name, suffix in [('新天山（测试）','V1'), ('研发审计系统','V2'), ('边缘端（开发）','V3'), ('上海大区（测试）','V4')]:
        config['business'][name] = {'mysql': db('MYSQL', suffix)}
    for name, suffix in [('新天山（测试）','V1'), ('上海大区（测试）','V2')]:
        config['business'][name].update(greenplum=db('GREENPLUM', suffix), starrocks=db('StarRocks', suffix))
    for name, suffix, kind in [('8.87新天山测试环境','XTS','xts'), ('8.178上海南方测试环境','SH','sh')]:
        config['platforms'][name] = {'kind':kind, 'base_url':val('BASEURL_'+suffix), 'username':val('USERNAME_'+suffix), 'password':val('PASSWORD_'+suffix), 'tenant_id':str(val('TENANTID_'+suffix)), 'role_id':str(val('ROLE_'+suffix)), 'dept_id':str(val('DEPTID_'+suffix))}
    kafka = val('KAFKA_BOOTSTRAP_SERVERS_V1')
    products = [('模型标签（新天山测试）','tagXts','KAFKA','kafka',kafka,''), ('模型标签（上海南方测试）','tagSh','KAFKA','kafka',kafka,''), ('盒子点位（新天山测试）','pointXts','API','api_point_cloud',val('BASEURL_XTS'),'/bytserver/symlink-box/tag/saveData'), ('盒子点位（上海南方测试）','pointSh','API','api_point_cloud',val('BASEURL_SH'),'/bytserver/symlink-box/tag/saveData'), ('盒子点位（边缘端开发）','pointByd','API','api_point_byd','http://192.168.8.25','')]
    for name, mod, way, route, address, path in products:
        config['products'].append(dict(product_name=name,module=mod,send_way=way,route_key=route,address=address,api_path=path))
    config['qms'] = {'enabled':True, 'host':val('MYSQL_HOST'), 'port':int(val('MYSQL_PORT',3306)), 'username':val('MYSQL_USER'), 'password':val('MYSQL_PWD'), 'database':val('DBNAME','qms')}
    config['audit'] = {'environment':'研发审计系统','database':'byt_mes_ops_2'}
    config['deployment'] = {'harbor_url':val('HB_LOGIN_URL'), 'harbor_username':val('HB_USERNAME'), 'harbor_password':val('HB_PASSWORD'), 'jumpserver_url':val('JP_BASE_URL'), 'jumpserver_username':val('JP_USERNAME'), 'jumpserver_password':val('JP_PASSWORD'), 'org_id':val('JP_ORG_ID'), 'policy_name':'byt-grid-data', 'repository':'e-platform/byt-grid-data', 'registry':'192.168.8.226:8092', 'compose_path':'/data/docker/docker-compose-tmp.yml', 'targets':{'192.168.8.202':{'asset_id':'a912fc9e-4870-4479-802c-36c66bd6d68b','system_user_id':'1e963b1d-e60e-43ad-9c1f-1f41dce5f9cc'}}}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    print('环境配置已导入: ' + str(target))


if __name__ == '__main__':
    migrate(Path(sys.argv[1]).resolve())
