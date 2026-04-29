import json
from typing import Dict, List, Tuple

from sqlalchemy import select, func, desc, or_

from app.crud import Mapper, ModelWrapper
from app.enums.GconfigEnum import GConfigParserEnum, GConfigVariableType
from app.middleware.RedisManager import RedisHelper
from app.models import async_session
from app.models.gconfig import GConfig
from app.models.project import Project
from app.models.user import User
from app.schema.gconfig import GConfigForm


@ModelWrapper(GConfig)
class GConfigDao(Mapper):

    @staticmethod
    def _value_to_text(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    @staticmethod
    def _parse_value(row: GConfig):
        if row is None:
            return None
        if row.key_type == GConfigParserEnum.json:
            try:
                return json.loads(row.value) if row.value is not None else None
            except Exception:
                return row.value
        return row.value

    @classmethod
    @RedisHelper.up_cache("dao", "list_gconfig", "list_gconfig_page")
    async def insert_gconfig(cls, form: GConfigForm, user_id: int) -> None:
        try:
            async with async_session() as session:
                async with session.begin():
                    query = await session.execute(
                        select(GConfig).where(GConfig.env == form.env, GConfig.key == form.key, GConfig.type == form.type,
                                              GConfig.project_id == form.project_id, GConfig.case_id == form.case_id,
                                              GConfig.deleted_at == 0))
                    data = query.scalars().first()
                    if data is not None:
                        raise Exception(f"变量: {data.key}已存在")
                    config = GConfig(**form.dict(), user=user_id)
                    session.add(config)
        except Exception as e:
            cls.__log__.error(f"新增变量失败, {e}")
            raise Exception(f"新增变量失败: {str(e)}")

    @staticmethod
    @RedisHelper.cache("dao", 1800, True)
    async def async_get_gconfig_by_key(key: str, env: int) -> GConfig:
        try:
            filters = [
                GConfig.key == key,
                GConfig.deleted_at == 0,
                GConfig.enable == True,
                GConfig.env == env,
                GConfig.type == int(GConfigVariableType.global_var)
            ]
            async with async_session() as session:
                sql = select(GConfig).where(*filters)
                result = await session.execute(sql)
                return result.scalars().first()
        except Exception as e:
            raise Exception(f"查询全局变量失败: {str(e)}")

    @staticmethod
    @RedisHelper.cache("list_gconfig", 300, True)
    async def list_gconfig(env: int) -> List[GConfig]:
        """
        查询可用全局变量（仅 type=1）
        """
        try:
            filters = [GConfig.deleted_at == 0, GConfig.enable == True,
                       GConfig.type == int(GConfigVariableType.global_var)]
            if env is not None:
                filters.append(GConfig.env == env)
            async with async_session() as session:
                sql = select(GConfig).where(*filters)
                result = await session.execute(sql)
                return result.scalars().all()
        except Exception as e:
            raise Exception(f"查询全局变量失败: {str(e)}")
    @staticmethod
    async def list_gconfig_page(page: int, size: int, env=None, key: str = "", var_type: int = None,
                                project_id: int = None, case_name: str = "", create_user: str = ""):
        """
        gconfig 分页查询（返回原始表字段）
        """
        try:
            filters = [GConfig.deleted_at == 0]
            if env is not None:
                filters.append(GConfig.env == env)
            if key:
                filters.append(GConfig.key.like(f"%{key}%"))
            if var_type is not None:
                filters.append(GConfig.type == var_type)
            if project_id is not None:
                filters.append(GConfig.project_id == project_id)
            if case_name:
                filters.append(GConfig.case_name.like(f"%{case_name}%"))
            if create_user:
                if str(create_user).isdigit():
                    filters.append(GConfig.create_user == int(create_user))
                else:
                    filters.append(or_(User.name.like(f"%{create_user}%"), User.username.like(f"%{create_user}%")))

            async with async_session() as session:
                total_sql = (
                    select(func.count(GConfig.id))
                    .select_from(GConfig)
                    .outerjoin(Project, Project.id == GConfig.project_id)
                    .outerjoin(User, User.id == GConfig.create_user)
                    .where(*filters)
                )
                total = (await session.execute(total_sql)).scalar() or 0

                sql = (
                    select(GConfig, Project.name.label("project_name"), User.name.label("create_user_name"))
                    .outerjoin(Project, Project.id == GConfig.project_id)
                    .outerjoin(User, User.id == GConfig.create_user)
                    .where(*filters)
                    .order_by(GConfig.id.desc())
                    .offset((page - 1) * size)
                    .limit(size)
                )
                result = await session.execute(sql)
                rows = []
                for gconfig, project_name, create_user_name in result.all():
                    item = json.loads(gconfig.serialize())
                    item["project_name"] = project_name
                    item["create_user_name"] = create_user_name
                    rows.append(item)
                return rows, total
        except Exception as e:
            raise Exception(f"分页查询全局变量失败: {str(e)}")


    @classmethod
    async def upsert_runtime_variables(cls, env: int, project_id: int, case_id: int, case_name: str, variables: dict,
                                       user_id: int = 0):
        if not variables:
            return
        async with async_session() as session:
            async with session.begin():
                for name, value in variables.items():
                    key_type = int(
                        GConfigParserEnum.json if isinstance(value, (dict, list, tuple))
                        else GConfigParserEnum.string
                    )
                    query = await session.execute(
                        select(GConfig).where(
                            GConfig.deleted_at == 0,
                            GConfig.type == int(GConfigVariableType.runtime_var),
                            GConfig.env == env,
                            GConfig.project_id == project_id,
                            GConfig.case_id == case_id,
                            GConfig.key == name
                        )
                    )
                    row = query.scalars().first()
                    text_val = cls._value_to_text(value)
                    if row is None:
                        row = GConfig(
                            env=env,
                            key=name,
                            value=text_val,
                            key_type=key_type,
                            enable=True,
                            user=user_id or 0,
                            type=int(GConfigVariableType.runtime_var),
                            project_id=project_id,
                            case_id=case_id,
                            case_name=case_name
                        )
                        session.add(row)
                        continue
                    row.value = text_val
                    row.key_type = key_type
                    row.case_name = case_name
                    row.enable = True
                    row.update_user = user_id or row.update_user

    @staticmethod
    async def latest_runtime_variable_map(env: int, project_id: int, case_id: int, limit: int = 1000) -> Dict[str, str]:
        result_map = dict()
        if case_id is None:
            return result_map
        project_filter = (
            or_(GConfig.project_id == project_id, GConfig.project_id.is_(None))
            if project_id is not None else GConfig.project_id.is_(None)
        )
        async with async_session() as session:
            query = await session.execute(
                select(GConfig)
                .where(
                    GConfig.deleted_at == 0,
                    GConfig.enable == True,
                    GConfig.type == int(GConfigVariableType.runtime_var),
                    GConfig.env == env,
                    project_filter,
                    GConfig.case_id == case_id
                )
                .order_by(desc(GConfig.id))
                .limit(limit)
            )
            rows = query.scalars().all()
            for row in rows:
                if row.key not in result_map:
                    result_map[row.key] = GConfigDao._parse_value(row)
        return result_map

    @staticmethod
    async def latest_case_variables(env: int, project_id: int, pairs: List[Tuple[int, str]], limit: int = 3000) -> Dict[Tuple[int, str], str]:
        if not pairs:
            return {}
        case_ids = list({cid for cid, _ in pairs})
        var_names = list({name for _, name in pairs})
        result = {}
        project_filter = (
            or_(GConfig.project_id == project_id, GConfig.project_id.is_(None))
            if project_id is not None else GConfig.project_id.is_(None)
        )
        async with async_session() as session:
            query = await session.execute(
                select(GConfig)
                .where(
                    GConfig.deleted_at == 0,
                    GConfig.enable == True,
                    GConfig.type == int(GConfigVariableType.runtime_var),
                    GConfig.env == env,
                    project_filter,
                    GConfig.case_id.in_(case_ids),
                    GConfig.key.in_(var_names)
                )
                .order_by(desc(GConfig.id))
                .limit(limit)
            )
            rows = query.scalars().all()
            for row in rows:
                key = (row.case_id, row.key)
                if key not in result:
                    result[key] = GConfigDao._parse_value(row)
        return result
