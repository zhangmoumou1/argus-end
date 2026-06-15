import asyncio
import functools
import importlib
import json
import os
import pkgutil
import sys
import time
from collections import defaultdict
from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime
from typing import Tuple, List, TypeVar, Any, Callable

from sqlalchemy import inspect, select, text, update
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums.OperationEnum import OperationType
from app.enums.ProjectEnum import ProjectRoleEnum
from app.exception.database import DBError
from app.middleware.RedisManager import RedisHelper
from app.models import Base, async_session, async_engine
from app.models.address import ArgusGateway
from app.models.basic import ArgusRelationField, init_relation, ArgusBase
from app.models.environment import Environment
from app.models.gconfig import GConfig
from app.models.operation_log import ArgusOperationLog
from app.models.platform_task import ArgusPlatformAuditLog, ArgusPlatformTask
from app.models.project import Project
from app.models.project_role import ProjectRole
from app.models.redis_config import ArgusRedis
from app.models.runtime_variable import ArgusRuntimeVariable
from app.models.test_case import TestCase
from app.models.test_plan import ArgusTestPlan
from app.models.testcase_asserts import TestCaseAsserts
from app.models.user import User
from app.utils.logger import Log
from config import Config

Transaction = TypeVar("Transaction", bool, Callable)


class ModelWrapper:

    def __init__(self, model, log=None):
        self.__model__ = model
        if log is None:
            self.__log__ = Log(f"{model.__name__}Dao")
        else:
            self.__log__ = log

    def __call__(self, cls):
        setattr(cls, "__model__", self.__model__)
        setattr(cls, "__log__", self.__log__)
        return cls


# 装饰器，支持自动创建session，支持事务
def connect(transaction: Transaction = False):
    """
    自动获取session连接，简化model相关操作
    :param transaction: 是否开启事务，开启则会被session.begin包裹
    :return:
    """
    if callable(transaction):
        # 说明装饰器非参数模式
        @functools.wraps(transaction)
        async def wrap(cls, *args, **kwargs):
            try:
                session: AsyncSession = kwargs.pop("session", None)
                if session is not None:
                    return await transaction(cls, *args, session=session, **kwargs)
                async with async_session() as ss:
                    return await transaction(cls, *args, session=ss, **kwargs)
            except Exception as e:
                # 这边调用cls本身的log参数，写入日志+抛出异常
                cls.__log__.error(f"操作Model: {cls.__model__.__name__}失败: {e}")
                raise DBError(f"操作数据库失败: {e}")

        return wrap

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(cls, *args, **kwargs):
            try:
                session: AsyncSession = kwargs.pop("session", None)
                nb = kwargs.get("not_begin")
                if session is not None:
                    if transaction and not nb:
                        async with session.begin():
                            return await func(cls, *args, session=session, **kwargs)
                    return await func(cls, *args[1:], session=session, **kwargs)
                async with async_session() as ss:
                    if transaction and not nb:
                        async with ss.begin():
                            return await func(cls, *args, session=ss, **kwargs)
                    return await func(cls, *args, session=ss, **kwargs)
            except Exception as e:
                cls.__log__.error(f"操作Model: {cls.__model__.__name__}失败: {e}")
                raise DBError(f"操作数据失败: {e}")

        return wrapper

    return decorator


# Mapper单表类，类似mybatis-plus
class Mapper(object):
    __log__ = Log("ArgusBase")
    __model__ = ArgusBase
    __log_description_max_len__ = 3500

    @classmethod
    def _shrink_log_value(cls, value, max_str_len=600):
        if isinstance(value, str):
            if len(value) <= max_str_len:
                return value
            return f"{value[:max_str_len]}...(已截断, 原长度={len(value)})"
        if isinstance(value, list):
            return [cls._shrink_log_value(item, max_str_len) for item in value]
        if isinstance(value, dict):
            return {k: cls._shrink_log_value(v, max_str_len) for k, v in value.items()}
        return value

    @classmethod
    def _compact_log_diff(cls, diff):
        try:
            compact = cls._shrink_log_value(diff)
            return json.dumps(compact, ensure_ascii=False)
        except Exception:
            return json.dumps([{"name": "日志", "now": "日志内容过长，已省略"}], ensure_ascii=False)

    @classmethod
    @RedisHelper.cache("dao")
    @connect
    async def select_list(cls, *, session: AsyncSession = None, condition: list = None, **kwargs):
        """
        基础model查询条件
        :param session: 查询session
        :param condition: 自定义查询条件
        :param kwargs: 普通查询条件
        :return:
        """
        sql = cls.query_wrapper(condition, **kwargs)
        result = await session.execute(sql)
        return result.scalars().all()

    @staticmethod
    def like(s: str):
        if s:
            return f"%{s}%"
        return s

    @staticmethod
    def rlike(s: str):
        if s:
            return f"{s}%"
        return s

    @staticmethod
    def llike(s: str):
        if s:
            return f"%{s}"
        return s

    @staticmethod
    async def pagination(page: int, size: int, session, sql: str, scalars=True, **kwargs):
        """
        分页查询
        :param scalars:
        :param session:
        :param page:
        :param size:
        :param sql:
        :return:
        """
        data = await session.execute(sql)
        total = data.raw.rowcount
        if total == 0:
            return [], 0
        sql = sql.offset((page - 1) * size).limit(size)
        data = await session.execute(sql)
        if scalars and kwargs.get("_join") is None:
            return data.scalars().all(), total
        return data.all(), total

    @staticmethod
    def update_model(dist, source, update_user=None, not_null=False):
        """
        :param dist:
        :param source:
        :param not_null:
        :param update_user:
        :return:
        """
        changed = []
        if hasattr(source, "__table__"):
            source_items = ((column.name, getattr(source, column.name, None)) for column in source.__table__.columns)
        else:
            source_items = vars(source).items()
        for var, value in source_items:
            if not_null:
                if value is None:
                    continue
                if isinstance(value, bool) or isinstance(value, int) or value:
                    if not hasattr(dist, var):
                        continue
                    if getattr(dist, var) != value:
                        changed.append(var)
                        setattr(dist, var, value)
            else:
                if getattr(dist, var) != value:
                    changed.append(var)
                    setattr(dist, var, value)
        if update_user:
            setattr(dist, 'update_user', update_user)
        setattr(dist, 'updated_at', datetime.now())
        return changed

    @staticmethod
    def delete_model(dist, update_user):
        """
        删除数据，兼容老的deleted_at
        :param dist:
        :param update_user:
        :return:
        """
        if str(dist.__class__.deleted_at.property.columns[0].type) == "DATETIME":
            dist.deleted_at = datetime.now()
        else:
            dist.deleted_at = int(time.time() * 1000)
        dist.updated_at = datetime.now()
        dist.update_user = update_user

    @classmethod
    @RedisHelper.cache("dao")
    @connect
    async def list_with_pagination(cls, page, size, /, *, session=None, **kwargs):
        return await cls.pagination(page, size, session, cls.query_wrapper(**kwargs), **kwargs)

    @classmethod
    def where(cls, param: Any, sentence, condition: list):
        if param is None:
            return cls
        if isinstance(param, bool):
            condition.append(sentence)
            return cls
        if isinstance(param, int):
            condition.append(sentence)
            return cls
        if param:
            condition.append(sentence)
        return cls

    @classmethod
    def query_wrapper(cls, condition=None, **kwargs):
        conditions = condition if condition else list()
        if getattr(cls.__model__, "deleted_at", None):
            conditions.append(getattr(cls.__model__, "deleted_at") == 0)
        _sort = kwargs.pop("_sort", None)
        _select = kwargs.pop("_select", list())
        _join = kwargs.pop("_join", None)
        for k, v in kwargs.items():
            like = isinstance(v, str) and (v.startswith("%") or v.endswith("%"))
            if like and v == "%%":
                continue
            cls.where(v, getattr(cls.__model__, k).like(v) if like else getattr(cls.__model__, k) == v, conditions)
        sql = select(cls.__model__, *_select)
        if isinstance(_join, Iterable):
            for j in _join:
                sql = sql.outerjoin(*j)
        where = sql.where(*conditions)
        if _sort and isinstance(_sort, Iterable):
            for d in _sort:
                where = getattr(where, "order_by")(d)
        return where

    @classmethod
    @connect
    async def query_record(cls, session: AsyncSession = None, **kwargs):
        sql = cls.query_wrapper(**kwargs)
        result = await session.execute(sql)
        return result.scalars().first()

    @classmethod
    @RedisHelper.up_cache("dao")
    @connect(True)
    async def insert(cls, *, model: ArgusBase, session: AsyncSession = None, log=False, not_begin=False):
        session.add(model)
        await session.flush()
        session.expunge(model)
        if log:
            await asyncio.create_task(
                cls.insert_log(session, model.create_user, OperationType.INSERT, model, key=model.id)
            )
        return model

    @classmethod
    @RedisHelper.up_cache("dao")
    @connect(True)
    async def update_by_map(cls, user, *condition, session=None, log=False, key=None, **kwargs):
        old_records = []
        if log:
            query = await session.execute(select(cls.__model__).where(*condition))
            old_records = [deepcopy(item) for item in query.scalars().all()]
        sql = update(cls.__model__).where(*condition).values(**kwargs, updated_at=datetime.now(), update_user=user)
        await session.execute(sql)
        if log and old_records:
            fresh = await session.execute(select(cls.__model__).where(*condition))
            fresh_map = {getattr(item, 'id', None): item for item in fresh.scalars().all()}
            changed_fields = list(kwargs.keys())
            for old in old_records:
                current = fresh_map.get(getattr(old, 'id', None))
                if current is None:
                    continue
                changed = [field for field in changed_fields if getattr(old, field, None) != getattr(current, field, None)]
                if not changed:
                    continue
                await cls.insert_log(
                    session,
                    user,
                    OperationType.UPDATE,
                    current,
                    old,
                    key=getattr(current, 'id', None) or key,
                    changed=changed,
                )

    @classmethod
    @RedisHelper.up_cache("dao")
    @connect(True)
    async def update_record_by_id(cls, user: int, model, not_null=False, log=False, session=None):
        query = cls.query_wrapper(id=model.id)
        result = await session.execute(query)
        now = result.scalars().first()
        if now is None:
            raise Exception("数据不存在")
        old = deepcopy(now)
        changed = cls.update_model(now, model, user, not_null)
        await session.flush()
        session.expunge_all()
        if log:
            await asyncio.create_task(
                cls.insert_log(session, user, OperationType.UPDATE, now, old, model.id, changed=changed)
            )
        return now

    @classmethod
    async def _inner_delete(cls, session, user, value, log, key, exists):
        query = cls.query_wrapper(**{key: value})
        result = await session.execute(query)
        original = result.scalars().first()
        if original is None:
            if exists:
                raise Exception("记录不存在")
            return None
        cls.delete_model(original, user)
        await session.flush()
        session.expunge(original)
        if log:
            await asyncio.create_task(cls.insert_log(session, user, OperationType.DELETE, original, key=value))
            return original

    @classmethod
    @RedisHelper.up_cache("dao")
    async def delete_record_by_id(cls, session, user: int, value: int, log=True, key='id', exists=True, session_begin=False):
        try:
            if session_begin:
                return await cls._inner_delete(session, user, value, log, key, exists)
            async with session.begin():
                return await cls._inner_delete(session, user, value, log, key, exists)
        except Exception as e:
            cls.__log__.exception(f"删除{cls.__model__.__name__}记录失败: \n{e}")
            raise Exception("删除失败")

    @classmethod
    @RedisHelper.up_cache("dao")
    async def delete_records(cls, session, user, id_list: List[int], column="id", log=True):
        try:
            for id_ in id_list:
                query = cls.query_wrapper(**{column: id_})
                result = await session.execute(query)
                original = result.scalars().first()
                if original is None:
                    continue
                cls.delete_model(original, user)
                await session.flush()
                session.expunge(original)
                if log:
                    await asyncio.create_task(cls.insert_log(session, user, OperationType.DELETE, original, key=id_))
        except Exception as e:
            cls.__log__.exception(f"删除{cls.__model__}记录失败, error: {e}")
            raise Exception("删除记录失败")

    @classmethod
    async def insert_log(cls, session, user, mode, now, old=None, key=None, changed=None):
        diff, title = await cls.get_diff(session, mode, now, old, changed)
        tag = getattr(now, Config.TABLE_TAG, '未设置')
        diff_data = json.dumps(diff, ensure_ascii=False)
        if len(diff_data) > cls.__log_description_max_len__:
            diff_data = cls._compact_log_diff(diff)
            if len(diff_data) > cls.__log_description_max_len__:
                diff_data = f"{diff_data[:cls.__log_description_max_len__]}...(日志已截断)"
        model = ArgusOperationLog(user, mode, "&".join(title), tag, diff_data, key)
        session.add(model)

    @classmethod
    async def get_diff(cls, session, mode, now, old, changed):
        fields = getattr(now, Config.FIELD, None)
        fields_number = getattr(now, Config.SHOW_FIELD, 1)
        if fields:
            fields = [f.name for f in fields[:fields_number]]
        else:
            fields = ['id']
        if not changed:
            if mode == OperationType.INSERT:
                changed_fields = await cls.get_fields(now)
            else:
                changed_fields = []
        else:
            changed_fields = changed
        detail_fields = [c for c in changed_fields if c not in fields] if mode != OperationType.UPDATE else changed_fields
        result = []
        title = []
        for f in detail_fields:
            item = await cls.get_field_alias(session, getattr(now, Config.RELATION, None), f, now, old)
            result.append(item)
        for d in fields:
            item = await cls.get_field_alias(session, getattr(now, Config.RELATION, None), d, now, old)
            title.append(f"{item.get('name')}={item.get('now')}")
        return result, title

    @classmethod
    async def get_id_list(cls, ids):
        if ids == "":
            return []
        if isinstance(ids, int):
            id_list = [ids]
        else:
            id_list = list(map(int, ids.split(",")))
        return id_list

    @classmethod
    async def fetch_id_with_name(cls, session, id_field, name_field, old_id, new_id):
        cls_ = id_field.parent.class_
        if old_id is None:
            id_list = await cls.get_id_list(new_id)
            data = await session.execute(select(cls_).where(getattr(cls_, id_field.name).in_(id_list)))
            result = data.scalars().all()
            if result is None:
                return new_id, None
            ans = []
            for r in result:
                ans.append(getattr(r, name_field.name, new_id))
            return ",".join(map(str, ans)), None
        new_list = await cls.get_id_list(new_id)
        old_list = await cls.get_id_list(old_id)
        id_list = old_list + new_list
        data = await session.execute(select(cls_).where(getattr(cls_, id_field.name).in_(id_list)))
        old_ans, new_ans = [], []
        mp = dict()
        for d in data.scalars():
            mp[getattr(d, id_field.name, None)] = getattr(d, name_field.name, None)
        for t in old_list:
            old_ans.append(mp.get(t, t))
        for i in new_list:
            new_ans.append(mp.get(i, i))
        return ",".join(map(str, new_ans)), ",".join(map(str, old_ans))

    @classmethod
    def get_json_field(cls, field):
        if isinstance(field, datetime):
            return field.strftime("%Y-%m-%d %H:%M:%S")
        return field

    @classmethod
    async def get_field_alias(cls, session, relation: Tuple[ArgusRelationField], name, now, old=None):
        alias = getattr(now, Config.ALIAS, {})
        current_value = getattr(now, name, None)
        current_value = cls.get_json_field(current_value)
        old_value = getattr(old, name, None) if old is not None else None
        old_value = cls.get_json_field(old_value)
        if relation is not None:
            for r in relation:
                if r.field.name == name:
                    if r.foreign is None:
                        return dict(name=alias.get(name, name), old=old_value, now=current_value)
                    if callable(r.foreign):
                        real_value = r.foreign(current_value)
                        real_old_value = r.foreign(old_value)
                        return dict(name=alias.get(name, name), old=real_old_value, now=real_value)
                    id_field, name_field = r.foreign
                    current, old = await cls.fetch_id_with_name(session, id_field, name_field, old_value, current_value)
                    return dict(name=alias.get(name, name), old=old, now=current)
        return dict(name=alias.get(name, name), old=old_value, now=current_value)

    @classmethod
    async def get_fields(cls, model):
        ans = []
        fields = getattr(model, Config.FIELD, None)
        fields = [x.name for x in fields] if fields else list()
        for c in model.__table__.columns:
            if c.name in Config.IGNORE_FIELDS or (fields and c.name not in fields):
                continue
            ans.append(c.name)
        return ans

    @classmethod
    @RedisHelper.up_cache("dao")
    @connect(True)
    async def delete_by_id(cls, id, session=None):
        query = cls.query_wrapper(id=id)
        result = await session.execute(query)
        original = result.scalars().first()
        if original is None:
            raise Exception("记录不存在")
        session.delete(original)


def get_dao_path():
    for f in os.listdir(Config.DAO_PATH):
        file_path = os.path.join(Config.DAO_PATH, f)
        if os.path.isdir(file_path) and '__pycache__' not in f:
            path_dict = defaultdict(list)
            for py_file in os.listdir(file_path):
                if py_file.endswith('.py') and '__init__' not in py_file:
                    path_dict[f].append(py_file.split('.')[0])
            yield path_dict


for path in get_dao_path():
    for file, pys in path.items():
        son_dao_path = os.path.join(Config.DAO_PATH, file)
        sys.path.append(son_dao_path)
        for py in pys:
            importlib.import_module(py)


async def create_table():
    models_pkg_path = os.path.join(os.path.dirname(__file__), "..", "models")
    for module_info in pkgutil.iter_modules([os.path.abspath(models_pkg_path)]):
        module_name = module_info.name
        if module_name.startswith("_"):
            continue
        importlib.import_module(f"app.models.{module_name}")

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_sync_missing_model_columns)

    await _run_runtime_schema_patches()


def _render_mysql_default(column):
    default = getattr(column, "default", None)
    if default is None or getattr(default, "is_scalar", False) is False:
        return ""
    value = default.arg
    if value is None:
        return ""
    if isinstance(value, bool):
        return f" DEFAULT {1 if value else 0}"
    if isinstance(value, (int, float)):
        return f" DEFAULT {value}"
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f" DEFAULT '{escaped}'"


def _render_mysql_column_sql(column):
    dialect = mysql.dialect()
    type_sql = column.type.compile(dialect=dialect)
    nullable_sql = " NULL" if column.nullable else " NOT NULL"
    default_sql = _render_mysql_default(column)
    comment_sql = ""
    if getattr(column, "comment", None):
        escaped = str(column.comment).replace("\\", "\\\\").replace("'", "\\'")
        comment_sql = f" COMMENT '{escaped}'"
    return f"`{column.name}` {type_sql}{nullable_sql}{default_sql}{comment_sql}"


def _sync_missing_model_columns(sync_conn):
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {item["name"] for item in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            column_sql = _render_mysql_column_sql(column)
            sync_conn.execute(text(f"ALTER TABLE `{table.name}` ADD COLUMN {column_sql}"))


async def _run_runtime_schema_patches():
    from app.core.interface_sample import ensure_interface_sample_schema
    from app.core.mock_rule import ensure_mock_config_schema
    from app.routers.config.mq_config import ensure_mq_schema
    from app.routers.performance import ensure_performance_schema
    from app.routers.testcase.functional_case import ensure_functional_case_schema
    from app.routers.testcase.functional_case_skill import ensure_skill_task_schema
    from app.routers.testcase.interface_manage import ensure_interface_schema
    from app.routers.ui_test import ensure_ui_test_gateway_schema, ensure_ui_test_schema

    async with async_session() as session:
        await ensure_ui_test_schema(session)
        await ensure_ui_test_gateway_schema(session)
        await ensure_interface_schema(session)
        await ensure_interface_sample_schema(session)
        await ensure_functional_case_schema(session)
        await ensure_skill_task_schema(session)
        await ensure_performance_schema(session)
        await ensure_mq_schema(session)
        await ensure_mock_config_schema(session)


init_relation(ProjectRole, ArgusRelationField(ProjectRole.user_id, (User.id, User.name)),
              ArgusRelationField(ProjectRole.project_id, (Project.id, Project.name)),
              ArgusRelationField(ProjectRole.project_role, ProjectRoleEnum.name))

init_relation(ArgusRedis, ArgusRelationField(ArgusRedis.env, (Environment.id, Environment.name)))

init_relation(ArgusTestPlan, ArgusRelationField(ArgusTestPlan.env, (Environment.id, Environment.name)),
              ArgusRelationField(ArgusTestPlan.project_id, (Project.id, Project.name)),
              ArgusRelationField(ArgusTestPlan.msg_type, ArgusTestPlan.get_msg_type),
              ArgusRelationField(ArgusTestPlan.receiver, (User.id, User.name)))

init_relation(TestCase)

init_relation(TestCaseAsserts, ArgusRelationField(TestCaseAsserts.case_id, (TestCase.id, TestCase.name)))

init_relation(ArgusGateway, ArgusRelationField(ArgusGateway.env, (Environment.id, Environment.name)))

init_relation(GConfig, ArgusRelationField(GConfig.env, (Environment.id, Environment.name)))
