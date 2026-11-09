from sqlalchemy import text

from .business import session_for
from .settings import settings


async def product_configs():
    config = settings()
    source = config.get('qms', {})
    if not source.get('enabled', True):
        return config.get('products', [])
    async with session_for(source, source.get('database', 'qms')) as session:
        rows = (await session.execute(text('''SELECT id,product_name,module,send_way,route_key,address,api_path,is_enabled,remark
            FROM product_function_config WHERE deleted_at=0 ORDER BY id'''))).mappings().all()
    return [dict(row) for row in rows]


async def enabled_products():
    return [row for row in await product_configs() if row.get('is_enabled', 1)]
