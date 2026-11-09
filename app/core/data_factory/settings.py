import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(os.environ.get('ARGUS_DATA_FACTORY_CONFIG', str(ROOT / 'conf' / 'data_factory.json')))
RUNTIME = Path(os.environ.get('ARGUS_DATA_FACTORY_RUNTIME', str(ROOT / 'var' / 'data_factory')))
SCENES = {
    'create-platform-user': '创建平台用户',
    'create-db-user': '创建数据库用户并赋权',
    'release-metrics': '需求发布统计',
    'sim-point-tag': '模拟发送数仓点位和标签值',
    'create-gp-sr-table': '创建GP和SR指标表',
    'sync-harbor-image': '自动同步harbor镜像并部署',
}
SECRET_KEY = re.compile(r'password|passwd|pwd|secret|token|authorization', re.I)


def settings():
    if not CONFIG_PATH.exists():
        raise ValueError('数据工厂环境配置不存在，请配置 conf/data_factory.json')
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig'))


def check_database(data):
    for key in ('host', 'port', 'username', 'password'):
        if data.get(key) is None or str(data[key]) == '':
            raise ValueError('数据库连接缺少参数: ' + key)
    if not 1 <= int(data['port']) <= 65535:
        raise ValueError('数据库端口不合法')


def save_settings(data):
    previous = settings() if CONFIG_PATH.exists() else {}
    def restore(value, old):
        if value == '***':
            return old
        if isinstance(value, dict):
            return {k: restore(v, old.get(k) if isinstance(old, dict) else None) for k, v in value.items()}
        if isinstance(value, list):
            previous_items = old if isinstance(old, list) else []
            restored = []
            for index, item in enumerate(value):
                match = previous_items[index] if index < len(previous_items) else None
                restored.append(restore(item, match))
            return restored
        return value
    value = restore(data, previous)
    for name, group in value.get('business', {}).items():
        if not name or not isinstance(group, dict):
            raise ValueError('业务数据库环境配置不合法')
        for kind, db in group.items():
            if kind not in ('mysql', 'greenplum', 'starrocks'):
                raise ValueError('数据库类型不合法')
            check_database(db)
    check_database(value.get('qms', {}))
    for name, platform in value.get('platforms', {}).items():
        if platform.get('kind') not in ('xts', 'sh') or not str(platform.get('base_url', '')).startswith(('http://', 'https://')):
            raise ValueError('平台环境配置不合法: ' + name)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix='.json.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, str(CONFIG_PATH))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def redact(value):
    if isinstance(value, dict):
        return {k: '***' if SECRET_KEY.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def scrub(text, *sources):
    secrets = []
    def collect(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if SECRET_KEY.search(key) and isinstance(value, (str, int)) and str(value):
                    secrets.append(str(value))
                else:
                    collect(value)
        elif isinstance(obj, list):
            for value in obj:
                collect(value)
    for source in sources:
        collect(source)
    result = str(text)
    for secret in sorted(set(secrets), key=len, reverse=True):
        result = result.replace(secret, '***')
    return re.sub(r'(://[^:/\s]+:)[^@\s]+@', r'\1***@', result)


def scrub_result(value, *sources):
    if isinstance(value, str):
        return scrub(value, *sources)
    if isinstance(value, dict):
        return {k: scrub_result(v, *sources) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_result(v, *sources) for v in value]
    return value
