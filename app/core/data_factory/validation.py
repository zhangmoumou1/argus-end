import json
import math
import re
from datetime import datetime

from .settings import SCENES, settings


def required(data, *keys):
    for key in keys:
        if data.get(key) is None or str(data[key]).strip() == '':
            raise ValueError('请填写参数: ' + key)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', value):
        raise ValueError('数据库名称或指标编码不合法: ' + str(value))
    return value


def finite(value):
    try:
        if not math.isfinite(float(value)):
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ValueError('点位值必须是有限数字: ' + str(value))


def validate(scene, fields):
    if scene not in SCENES:
        raise ValueError('场景不存在')
    if not isinstance(fields, dict):
        raise ValueError('表单必须为对象')
    config = settings()
    if scene == 'create-platform-user':
        required(fields, 'env', 'username', 'password', 'name', 'phone')
        if fields['env'] not in config.get('platforms', {}):
            raise ValueError('请选择已配置的平台环境')
    elif scene == 'create-db-user':
        required(fields, 'mysqlIp', 'mysqlPort', 'mysqlUser', 'mysqlPwd', 'username', 'password', 'targetIp')
        if not 1 <= int(fields['mysqlPort']) <= 65535:
            raise ValueError('数据库端口不合法')
        if fields.get('database', '*') != '*':
            identifier(fields['database'])
    elif scene == 'release-metrics':
        if fields.get('dateType') not in ('周', '月', '季度', '年'):
            raise ValueError('请选择统计维度')
        if not 2000 <= int(fields.get('year', 0)) <= 2100:
            raise ValueError('年份范围为 2000–2100')
        if not fields.get('participateUser'):
            raise ValueError('请选择参与人员')
    elif scene == 'create-gp-sr-table':
        required(fields, 'environment', 'mes', 'repair', 'period')
        if fields['environment'] not in config.get('business', {}):
            raise ValueError('环境不存在')
        identifier(fields['mes'])
        identifier(fields['repair'])
        if not isinstance(fields['period'], list) or not set(fields['period']) <= {'15_minutes', 'hourly', 'daily'}:
            raise ValueError('指标周期不合法')
        if fields.get('isDeleted') not in (None, False, True):
            raise ValueError('删除重建参数不合法')
    elif scene == 'sim-point-tag':
        required(fields, 'module', 'sendWay', 'topicName', 'startTime', 'endTime', 'frequency')
        products = {x['module']: x for x in config.get('products', [])}
        if not config.get('qms', {}).get('enabled', True) and fields['module'] not in products and fields['module'] != 'Custom':
            raise ValueError('请选择已配置的产品功能')
        if fields['sendWay'] not in ('API', 'KAFKA'):
            raise ValueError('发送方式不合法')
        product = products.get(fields['module'])
        if not config.get('qms', {}).get('enabled', True) and product and product['send_way'].upper() != fields['sendWay']:
            raise ValueError('发送方式与产品功能配置不一致')
        start, end = [datetime.strptime(fields[k], '%Y-%m-%d %H:%M:%S') for k in ('startTime', 'endTime')]
        if start > end or (end - start).total_seconds() > 7 * 86400:
            raise ValueError('时间范围必须递增且不超过七天')
        if isinstance(fields['frequency'], bool) or not 1 <= int(fields['frequency']) <= 86400:
            raise ValueError('发送频率范围为 1–86400 秒')
        if fields['module'] == 'Custom':
            required(fields, 'kafkaServer', 'messageFormat', 'tagContent')
            if fields['sendWay'] != 'KAFKA' or fields['messageFormat'] not in ('JSON', 'Protobuf'):
                raise ValueError('自定义消息支持 Kafka JSON / Protobuf')
            content=json.loads(fields['tagContent'])
            if not isinstance(content,(dict,list)) or '{{tag}}' not in fields['tagContent']:
                raise ValueError('自定义消息模板必须为包含 {{tag}} 占位符的 JSON 对象或数组')
        tags = fields.get('tagList')
        if not isinstance(tags, list) or not 1 <= len(tags) <= 10000:
            raise ValueError('请配置 1–10000 个点位或标签')
        names = set()
        for tag in tags:
            required(tag, 'pointTag', 'valueType')
            if tag['pointTag'] in names:
                raise ValueError('点位/标签不能重复')
            names.add(tag['pointTag'])
            kind = tag['valueType']
            if kind not in ('Fixed', 'Increasing', 'Random', 'Alternating'):
                raise ValueError('点位值类型不合法')
            values = str(tag.get('value', '')).replace('，', ',').split(',')
            if kind == 'Random':
                continue
            if kind == 'Alternating' and len(values) != 2:
                raise ValueError('交替值格式为: 0,1')
            if kind == 'Increasing' and len(values) not in (1, 2):
                raise ValueError('递增格式为: 步长 或 起始值,步长')
            for value in values:
                finite(value)
    elif scene == 'sync-harbor-image':
        required(fields, 'targetIp', 'targetService')
        if fields['targetService'] != 'byt-grid-data':
            raise ValueError('当前仅支持 byt-grid-data')
        if fields['targetIp'] not in config.get('deployment', {}).get('targets', {}):
            raise ValueError('目标机器未配置 JumpServer 资产映射')
        tag = fields.get('imageTag')
        if tag and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag):
            raise ValueError('镜像标签不合法')
    return fields
