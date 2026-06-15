from fastapi import APIRouter, Depends

from app.crud.config.DbConfigDao import DbConfigDao, ArgusSQLHistoryDao
from app.handler.fatcory import ArgusResponse
from app.models.database import ArgusDatabase
from app.models.environment import Environment
from app.models.sql_log import ArgusSQLHistory
from app.routers import Permission
from app.schema.database import DatabaseForm
from app.schema.online_sql import OnlineSQLForm

router = APIRouter(prefix="/online")


@router.post("/sql")
async def execute_sql(data: OnlineSQLForm, user=Depends(Permission())):
    try:
        result, elapsed = await DbConfigDao.online_sql(data.id, data.sql)
        columns, result = ArgusResponse.parse_sql_result(result)
        await ArgusSQLHistoryDao.insert(model=ArgusSQLHistory(data.sql, elapsed, data.id, user['id']), log=True)
        return ArgusResponse.success(data=dict(result=result, columns=columns, elapsed=elapsed))
    except Exception as err:
        return ArgusResponse.failed(err)


@router.get("/history/query", summary="获取sql执行历史记录")
async def query_sql_history(page: int = 1, size: int = 4, _=Depends(Permission())):
    data, total = await ArgusSQLHistoryDao.list_with_pagination(page, size,
                                                               _sort=[ArgusSQLHistory.created_at.desc()],
                                                               _select=[ArgusDatabase, Environment],
                                                               _join=[(ArgusDatabase,
                                                                       ArgusDatabase.id == ArgusSQLHistory.database_id),
                                                                      (Environment, Environment.id == ArgusDatabase.env)
                                                                      ])
    ans = []
    for history, database, env in data:
        database.env_info = env
        history.database = database
        ans.append(history)
    return ArgusResponse.success(dict(data=ans, total=total))


@router.get("/tables")
async def list_tables(_=Depends(Permission())):
    try:
        result, table_map = await DbConfigDao.query_database_and_tables()
        return ArgusResponse.success(dict(database=result, tables=table_map))
    except Exception as err:
        return ArgusResponse.failed(err)


@router.get("/database/list")
async def list_databases(_=Depends(Permission())):
    try:
        result = await DbConfigDao.query_database_tree()
        return ArgusResponse.success(result)
    except Exception as err:
        return ArgusResponse.failed(err)


@router.post("/tables/list", summary="获取数据库表和字段")
async def list_tables(form: DatabaseForm, _=Depends(Permission())):
    try:
        children, tables = await DbConfigDao.get_tables(form)
        return ArgusResponse.success(dict(children=children, tables=tables))
    except Exception as err:
        return ArgusResponse.failed(err)
