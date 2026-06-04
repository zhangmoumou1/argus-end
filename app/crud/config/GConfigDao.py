import json
from datetime import datetime
from copy import deepcopy
from typing import Dict, List, Tuple

from sqlalchemy import select, func, desc, or_

from app.crud import Mapper, ModelWrapper
from app.enums.OperationEnum import OperationType
from app.enums.GconfigEnum import GConfigParserEnum, GConfigVariableType
from app.middleware.RedisManager import RedisHelper
from app.models import async_session
from app.models.ai_model import AI_MODEL_CONFIG_KEY as DEFAULT_AI_MODEL_CONFIG_KEY
from app.models.ai_model import AI_MODEL_DEFAULTS as DEFAULT_AI_MODEL_DEFAULTS
from app.models.ai_model import AI_MODEL_PRESETS as DEFAULT_AI_MODEL_PRESETS
from app.models.ai_model import AI_MODEL_PRESET_ORDER as DEFAULT_AI_MODEL_PRESET_ORDER
from app.models.gconfig import GConfig
from app.models.project import Project
from app.models.user import User
from app.schema.gconfig import GConfigForm


@ModelWrapper(GConfig)
class GConfigDao(Mapper):
    AI_MODEL_CONFIG_KEY = DEFAULT_AI_MODEL_CONFIG_KEY
    AI_MODEL_DEFAULTS = DEFAULT_AI_MODEL_DEFAULTS
    AI_MODEL_PRESETS = DEFAULT_AI_MODEL_PRESETS
    AI_MODEL_PRESET_ORDER = DEFAULT_AI_MODEL_PRESET_ORDER

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

    @staticmethod
    def _mask_api_key(api_key: str):
        value = str(api_key or "")
        if len(value) <= 10:
            return value
        return f"{value[:6]}***{value[-4:]}"

    @staticmethod
    def _normalize_ai_wire_api(wire_api: str, provider_type: str = "", model: str = "", base_url: str = ""):
        value = str(wire_api or "").strip().lower().replace("-", "_").replace("/", "_")
        if value in ("chat_completions", "responses"):
            return value
        base_url_value = str(base_url or "").strip().lower().rstrip("/")
        if base_url_value.endswith("/chat/completions"):
            return "chat_completions"
        if base_url_value.endswith("/responses"):
            return "responses"
        provider_value = str(provider_type or "").strip().lower()
        model_value = str(model or "").strip().lower()
        if model_value.startswith("gpt") and provider_value in ("openai", "custom"):
            return "responses"
        return "chat_completions"

    @classmethod
    def _get_ai_preset(cls, provider_type: str):
        provider_key = str(provider_type or "").strip().lower() or "custom"
        return deepcopy(cls.AI_MODEL_PRESETS.get(provider_key) or cls.AI_MODEL_PRESETS.get("custom") or {})

    @classmethod
    def _normalize_ai_model_item(cls, item=None, index: int = 0):
        raw_item = item if isinstance(item, dict) else {}
        provider_type = str(raw_item.get("provider_type") or raw_item.get("provider") or "custom").strip().lower() or "custom"
        preset = cls._get_ai_preset(provider_type)
        provider_name = str(raw_item.get("provider_name") or raw_item.get("name") or preset.get("provider_name") or "自定义供应商").strip() or "自定义供应商"
        base_url = str(raw_item.get("base_url") or preset.get("base_url") or "").strip()
        model = str(raw_item.get("model") or preset.get("model") or "").strip()
        model_options = raw_item.get("model_options") if isinstance(raw_item.get("model_options"), list) else raw_item.get("models")
        if not isinstance(model_options, list):
            model_options = preset.get("model_options") or []
        model_options = [str(v).strip() for v in model_options if str(v or "").strip()]
        if provider_type == "deepseek":
            model_options = ["deepseek-v4-pro" if value == "deepseek-v4" else value for value in model_options]
            if model == "deepseek-v4":
                model = "deepseek-v4-pro"
        if model and model not in model_options:
            model_options = [model] + model_options
        model_options = list(dict.fromkeys(model_options))
        if not model and model_options:
            model = model_options[0]
        item_id = str(raw_item.get("id") or f"{provider_type}_{index + 1}").strip() or f"{provider_type}_{index + 1}"
        api_key = str(raw_item.get("api_key") or preset.get("api_key") or "").strip()
        wire_api = cls._normalize_ai_wire_api(raw_item.get("wire_api") or preset.get("wire_api"), provider_type, model, base_url)
        normalized = {
            "id": item_id,
            "provider_type": provider_type,
            "provider": provider_type,
            "provider_name": provider_name,
            "name": provider_name,
            "base_url": base_url,
            "model": model,
            "model_options": model_options,
            "models": model_options,
            "api_key": api_key,
            "wire_api": wire_api,
            "enabled": bool(raw_item.get("enabled")),
        }
        return normalized

    @classmethod
    def _normalize_ai_model_config(cls, value=None):
        saved = value if isinstance(value, dict) else {}
        providers = []

        if isinstance(saved.get("providers"), list):
            for index, item in enumerate(saved.get("providers") or []):
                providers.append(cls._normalize_ai_model_item(item, index))
            active_model_id = str(saved.get("active_model_id") or "").strip()
        else:
            saved_models = saved.get("models") if isinstance(saved.get("models"), dict) else {}
            active_provider = str(saved.get("active_provider") or "").strip().lower()
            ordered_keys = [
                *[key for key in cls.AI_MODEL_PRESET_ORDER if key in saved_models],
                *[key for key in saved_models.keys() if key not in cls.AI_MODEL_PRESET_ORDER],
            ]
            if not ordered_keys:
                ordered_keys = [key for key in cls.AI_MODEL_PRESET_ORDER if key != "custom"]
            for index, provider in enumerate(ordered_keys):
                saved_item = saved_models.get(provider) if isinstance(saved_models.get(provider), dict) else {}
                legacy_item = dict(saved_item)
                legacy_item.setdefault("provider_type", provider if provider in cls.AI_MODEL_PRESETS else "custom")
                legacy_item.setdefault("provider_name", legacy_item.get("name") or provider)
                providers.append(cls._normalize_ai_model_item(legacy_item, index))
            active_model_id = ""
            if active_provider:
                matched = next((item for item in providers if item.get("provider_type") == active_provider), None)
                if matched:
                    active_model_id = matched.get("id") or ""

        if not providers:
            providers = [cls._normalize_ai_model_item({"provider_type": "kimi"}, 0)]

        current_active_id = str(saved.get("active_model_id") or "").strip()
        if current_active_id:
            active_model_id = current_active_id
        if not active_model_id:
            matched_enabled = next((item for item in providers if item.get("enabled")), None)
            active_model_id = str((matched_enabled or providers[0]).get("id") or "")
        if not any(str(item.get("id") or "") == active_model_id for item in providers):
            active_model_id = str(providers[0].get("id") or "")

        for item in providers:
            item["enabled"] = str(item.get("id") or "") == active_model_id

        return {
            "active_model_id": active_model_id,
            "providers": providers,
        }

    @classmethod
    def _public_ai_model_config(cls, config):
        normalized = cls._normalize_ai_model_config(config)
        public_config = {
            "active_model_id": normalized["active_model_id"],
            "providers": [],
        }
        for item in normalized["providers"]:
            public_item = dict(item)
            public_item["api_key_masked"] = cls._mask_api_key(public_item.get("api_key"))
            public_item["has_api_key"] = bool(public_item.get("api_key"))
            public_item.pop("api_key", None)
            public_config["providers"].append(public_item)
        return public_config

    @classmethod
    def get_ai_model_provider_options(cls):
        options = []
        for provider_type in cls.AI_MODEL_PRESET_ORDER:
            preset = cls._get_ai_preset(provider_type)
            options.append({
                "provider_type": provider_type,
                "provider_name": str(preset.get("provider_name") or provider_type).strip() or provider_type,
                "base_url": str(preset.get("base_url") or "").strip(),
                "model": str(preset.get("model") or "").strip(),
                "model_options": [str(v).strip() for v in (preset.get("model_options") or []) if str(v or "").strip()],
                "wire_api": cls._normalize_ai_wire_api(preset.get("wire_api"), provider_type, str(preset.get("model") or "").strip(), str(preset.get("base_url") or "").strip()),
                "builtin": bool(preset.get("builtin")),
            })
        return options

    @classmethod
    async def get_ai_model_config(cls, include_secret: bool = False):
        async with async_session() as session:
            result = await session.execute(
                select(GConfig).where(
                    GConfig.deleted_at == 0,
                    GConfig.type == int(GConfigVariableType.special_var),
                    GConfig.key == cls.AI_MODEL_CONFIG_KEY,
                ).order_by(desc(GConfig.id))
            )
            row = result.scalars().first()
        saved_config = cls._parse_value(row) if row is not None else None
        config = cls._normalize_ai_model_config(saved_config)
        return config if include_secret else cls._public_ai_model_config(config)

    @classmethod
    async def get_active_ai_model_config(cls):
        config = await cls.get_ai_model_config(include_secret=True)
        active_model_id = str(config.get("active_model_id") or "").strip()
        model_config = next(
            (item for item in (config.get("providers") or []) if str(item.get("id") or "") == active_model_id),
            None,
        )
        if not model_config or not model_config.get("api_key"):
            raise Exception("请先到后台管理-模型配置配置并启用AI模型")
        return model_config

    @classmethod
    @RedisHelper.up_cache("dao", "list_gconfig", "list_gconfig_page")
    async def update_ai_model_config(cls, form: dict, user_id: int):
        current = await cls.get_ai_model_config(include_secret=True)
        incoming_providers = form.get("providers") if isinstance(form.get("providers"), list) else current.get("providers") or []
        active_model_id = str(form.get("active_model_id") or current.get("active_model_id") or "").strip()
        current_provider_map = {
            str(item.get("id") or ""): item
            for item in (current.get("providers") or [])
            if isinstance(item, dict)
        }

        next_config = cls._normalize_ai_model_config({
            "active_model_id": active_model_id,
            "providers": incoming_providers,
        })
        for item in next_config["providers"]:
            current_item = current_provider_map.get(str(item.get("id") or ""))
            if current_item and not str(item.get("api_key") or "").strip():
                item["api_key"] = str(current_item.get("api_key") or "").strip()

        if not next_config["providers"]:
            raise Exception("请至少保留一个模型配置")
        if not next_config.get("active_model_id"):
            raise Exception("请先启用一个模型配置")
        async with async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(GConfig).where(
                        GConfig.deleted_at == 0,
                        GConfig.type == int(GConfigVariableType.special_var),
                        GConfig.key == cls.AI_MODEL_CONFIG_KEY,
                    ).order_by(desc(GConfig.id))
                )
                row = result.scalars().first()
                text_value = json.dumps(next_config, ensure_ascii=False)
                if row is None:
                    row = GConfig(
                        env=0,
                        key=cls.AI_MODEL_CONFIG_KEY,
                        value=text_value,
                        key_type=int(GConfigParserEnum.json),
                        enable=True,
                        user=user_id,
                        type=int(GConfigVariableType.special_var),
                    )
                    session.add(row)
                    await session.flush()
                    await cls.insert_log(session, user_id, OperationType.INSERT, row, key=row.id)
                else:
                    old = deepcopy(row)
                    row.value = text_value
                    row.key_type = int(GConfigParserEnum.json)
                    row.enable = True
                    row.update_user = user_id
                    row.updated_at = datetime.now()
                    await session.flush()
                    await cls.insert_log(
                        session,
                        user_id,
                        OperationType.UPDATE,
                        row,
                        old,
                        row.id,
                        changed=["value", "key_type", "enable"],
                    )
        return cls._public_ai_model_config(next_config)

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
                    await session.flush()
                    await cls.insert_log(session, user_id, OperationType.INSERT, config, key=config.id)
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

                # 运行时变量按“每个用户一份最新值”展示（同维度去重，只保留最新一条）
                if var_type is None or int(var_type) == int(GConfigVariableType.runtime_var):
                    latest_rows = []
                    runtime_keys = set()
                    for item in rows:
                        if int(item.get("type", 0)) != int(GConfigVariableType.runtime_var):
                            latest_rows.append(item)
                            continue
                        dedupe_key = (
                            item.get("env"),
                            item.get("project_id"),
                            item.get("case_id"),
                            item.get("key"),
                            item.get("create_user"),
                        )
                        if dedupe_key in runtime_keys:
                            continue
                        runtime_keys.add(dedupe_key)
                        latest_rows.append(item)
                    rows = latest_rows
                    total = len(rows)

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
                    var_name = str(name or "").strip()
                    if not var_name:
                        continue
                    key_type = int(
                        GConfigParserEnum.json if isinstance(value, (dict, list, tuple))
                        else GConfigParserEnum.string
                    )
                    text_val = cls._value_to_text(value)

                    # 命中历史同维度数据时，先软删除旧记录，再插入新记录，保证每次执行都有新增版本
                    query = await session.execute(
                        select(GConfig).where(
                            GConfig.deleted_at == 0,
                            GConfig.type == int(GConfigVariableType.runtime_var),
                            GConfig.env == env,
                            GConfig.project_id == project_id,
                            GConfig.case_id == case_id,
                            GConfig.key == var_name,
                            GConfig.create_user == (user_id or 0),
                        )
                    )
                    row = query.scalars().first()

                    if row is None:
                        fallback = await session.execute(
                            select(GConfig).where(
                                GConfig.deleted_at == 0,
                                GConfig.type == int(GConfigVariableType.runtime_var),
                                GConfig.env == env,
                                GConfig.case_id == case_id,
                                GConfig.key == var_name,
                                GConfig.create_user == (user_id or 0),
                            ).order_by(desc(GConfig.id))
                        )
                        row = fallback.scalars().first()

                    if row is not None:
                        row.deleted_at = int(datetime.now().timestamp())
                        row.update_user = user_id or row.update_user
                        row.updated_at = datetime.now()

                    new_row = GConfig(
                        env=env,
                        key=var_name,
                        value=text_val,
                        key_type=key_type,
                        enable=True,
                        user=user_id or 0,
                        type=int(GConfigVariableType.runtime_var),
                        project_id=project_id,
                        case_id=case_id,
                        case_name=case_name
                    )
                    session.add(new_row)

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

    @staticmethod
    async def latest_runtime_values_by_names(env: int, project_id: int, names: List[str], limit: int = 5000) -> Dict[str, str]:
        """
        按变量名跨case取最近值（同环境、同项目优先），用于${var}不依赖case_id的场景。
        """
        if not names:
            return {}
        normalized_names = [str(name).strip() for name in names if str(name).strip()]
        if not normalized_names:
            return {}
        project_filter = (
            or_(GConfig.project_id == project_id, GConfig.project_id.is_(None))
            if project_id is not None else GConfig.project_id.is_(None)
        )
        result = {}
        async with async_session() as session:
            query = await session.execute(
                select(GConfig)
                .where(
                    GConfig.deleted_at == 0,
                    GConfig.enable == True,
                    GConfig.type == int(GConfigVariableType.runtime_var),
                    GConfig.env == env,
                    project_filter,
                    GConfig.key.in_(normalized_names),
                )
                .order_by(desc(GConfig.id))
                .limit(limit)
            )
            rows = query.scalars().all()
            for row in rows:
                if row.key not in result:
                    result[row.key] = GConfigDao._parse_value(row)
        return result

