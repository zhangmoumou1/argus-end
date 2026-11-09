from contextlib import asynccontextmanager

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from .settings import settings


class BusinessSession:
    """Own an engine for exactly one operation, including failed connections."""
    def __init__(self, engine):
        self.engine = engine
        self.session = AsyncSession(engine, expire_on_commit=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    def __getattr__(self, key):
        return getattr(self.session, key)

    async def close(self):
        try:
            await self.session.close()
        finally:
            await self.engine.dispose()


async def get_business_session(env, database, db_type='mysql'):
    config = settings().get('business', {}).get(env, {}).get(db_type.lower())
    if not config:
        raise ValueError('未配置业务数据库: %s / %s' % (env, db_type))
    return session_for(config, database, db_type)


def session_for(config, database, db_type='mysql'):
    postgres = db_type.lower() in ('greenplum', 'postgresql')
    url = URL.create('postgresql+asyncpg' if postgres else 'mysql+aiomysql',
                     username=config['username'], password=config['password'],
                     host=config['host'], port=int(config['port']), database=database)
    options = {'timeout': 15} if postgres else {'connect_timeout': 15, 'charset': 'utf8mb4'}
    engine = create_async_engine(url, poolclass=NullPool, connect_args=options)
    return BusinessSession(engine)


async def print_connection_pool_status():
    pass
