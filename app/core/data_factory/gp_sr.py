import asyncio
import re
from sqlalchemy import text, bindparam
from typing import List, Tuple, Dict, Optional
from .business import get_business_session, print_connection_pool_status
from .validation import identifier
from app import init_logging

from pymysql.err import InterfaceError

logger = init_logging()

# 定义对象类型枚举
OBJECT_TYPES = {
    'TABLE': r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?',
    'VIEW': r'CREATE\s+OR\s+REPLACE\s+VIEW\s+',
}

# 通用标识符匹配（支持反引号、双引号、无引号）
IDENTIFIER_PATTERN = r'`?([\w-]+)`?'
SCHEMA_TABLE_PATTERN = f'(?:{IDENTIFIER_PATTERN}\\.){IDENTIFIER_PATTERN}'  # schema.table
ORPHAN_TABLE_PATTERN = IDENTIFIER_PATTERN  # standalone table/view name


def parse_ddl_object(ddl: str) -> Dict[str, Optional[str]]:
    ddl_clean = re.sub(r'/\*.*?\*/|--.*?\n', '', ddl, flags=re.DOTALL)  # 移除注释
    ddl_upper = re.sub(r'\s+', ' ', ddl_clean.strip()).upper()

    result = {"type": None, "schema": None, "name": None}

    # 更精确的匹配
    if re.match(r'CREATE\s+OR\s+REPLACE\s+VIEW\s+', ddl_upper):
        result["type"] = "视图"
        pattern = r'CREATE\s+OR\s+REPLACE\s+VIEW\s+(?:`?([\w-]+)`?\.)?`?([\w-]+)`?'
    elif re.match(r'CREATE(?:\s+OR\s+REPLACE)?(?:\s+TEMPORARY)?\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+', ddl_upper):
        result["type"] = "表"
        pattern = (
            r'CREATE(?:\s+OR\s+REPLACE)?(?:\s+TEMPORARY)?\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+'
            r'(?:`?([\w-]+)`?\.)?`?([\w-]+)`?'
        )
    else:
        result["name"] = "未知对象"
        return result

    match = re.search(pattern, ddl_upper)
    if match:
        result["schema"] = match.group(1).lower() if match.group(1) else None
        result["name"] = match.group(2).lower()
    else:
        # fallback: 提取第一个标识符
        identifiers = re.findall(IDENTIFIER_PATTERN, ddl_upper)
        result["name"] = identifiers[0].lower() if identifiers else "未知对象"

    return result


class GeneratorGpSrManager:
    """
    根据mysql指标表，生成GP和SR的周期指标表
    """
    @classmethod
    async def get_quota_config(cls, async_session) -> dict:
        """
        根据quota_config表生成gp和sr数据
        :param async_session: SQLAlchemy异步会话
        :return: 处理后的指标字典
        """

        async def fetch_data(session, data_types: List[str]) -> List[dict]:
            """
            查询quota_config表并关联work_procedure表
            """
            try:
                stmt = text("""
                    SELECT 
                        qc.code AS quota_code, 
                        qc.quota, 
                        qc.quota_value_type, 
                        wp.code AS procedure_code
                    FROM quota_config qc
                    LEFT JOIN work_procedure wp ON qc.procedure_id = wp.id
                    WHERE qc.data_type IN :data_types 
                       OR (qc.data_type IS NULL AND 'GreenPlum/StarRocks' IN :data_types)
                """).bindparams(bindparam('data_types', expanding=True))

                result = await session.execute(stmt, {'data_types': data_types})
                return [dict(row) for row in result.mappings()]
            except Exception as e:
                logger.error(f"查询quota_config表失败: {str(e)}")
                raise

        try:
            conditions = {
                "GreenPlum/StarRocks": [None, 'GreenPlum/StarRocks'],
                "StarRocks": ['StarRocks', 'GreenPlum/StarRocks']
            }

            result = {}
            process_logs = []

            for key, data_types in conditions.items():
                raw_data = await fetch_data(async_session, data_types)
                processed_data = []
                process_logs.append(f"处理 {key} 数据: 获取到 {len(raw_data)} 条记录")

                for item in raw_data:
                    quota_code = item.get('quota_code', '')
                    quota = str(item.get('quota') or '').replace(chr(39), chr(39)*2)
                    procedure_code = item.get('procedure_code', '')
                    quota_value_type = item.get('quota_value_type', '')

                    combined_code = f"{procedure_code}_{quota_code}" if procedure_code else quota_code
                    combined_code = identifier(combined_code.lower())

                    if quota_value_type == '时间':
                        code_quota_pairs = [
                            (f"{combined_code}_plan_stop", f"{quota}计划停止时长"),
                            (f"{combined_code}_run", f"{quota}运行时长"),
                            (f"{combined_code}_fault_stop", f"{quota}故障停机时长")
                        ]
                        process_logs.append(f"时间类型指标处理: {combined_code} -> 生成3个子指标")
                    else:
                        code_quota_pairs = [(combined_code, quota)]
                        process_logs.append(f"普通指标处理: {combined_code}")

                    processed_data.extend(code_quota_pairs)

                result[key] = processed_data
                process_logs.append(f"{key} 数据处理完成: 共生成 {len(processed_data)} 个指标")

            return result

        except Exception as e:
            logger.error(f"处理指标配置失败: {str(e)}")
            raise

    @classmethod
    async def generator_gp_sr_table(cls, user_id, fields: dict) -> dict:
        """
        生成GP和SR指标表
        :param fields: 包含环境和周期等参数的字典
        :return: 包含执行状态的字典
        """
        try:
            environment = fields.get("environment")['value']
            db_config = {
                "gp_db_mes": fields.get("mes")["value"],
                "gp_db_repair": fields.get("repair")["value"],
                "sr_db_mes": fields.get("mes")["value"],
                "sr_db_repair": fields.get("repair")["value"]
            }
            is_deleted = fields.get("isDeleted")["value"]
            # 删除指标表
            if is_deleted == '是':
                async with await get_business_session(environment, 'byt', 'greenplum') as gp_session:
                    ddls_view = [f"DROP VIEW IF EXISTS {db_config['gp_db_mes']}.dws_15_minutes", f"DROP VIEW IF EXISTS {db_config['gp_db_mes']}.dws_15_minutes_meter",
                                 f"DROP VIEW IF EXISTS {db_config['gp_db_mes']}.dws_daily", f"DROP VIEW IF EXISTS {db_config['gp_db_mes']}.dws_daily_meter",
                                 f"DROP VIEW IF EXISTS {db_config['gp_db_mes']}.dws_hourly", f"DROP VIEW IF EXISTS {db_config['gp_db_mes']}.dws_hourly_meter"]
                    for ddl_view in ddls_view:
                        await gp_session.execute(text(ddl_view))
                    await gp_session.commit()
                    ddls = [f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_15_minutes_ht;", f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_15_minutes_ht_meter;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_15_minutes_rt;", f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_15_minutes_rt_meter;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_daily_ht;", f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_daily_ht_meter;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_daily_rt;", f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_daily_rt_meter;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_hourly_ht;", f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_hourly_ht_meter;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_hourly_rt;", f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.dws_hourly_rt_meter;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_mes']}.halt_record;", f"DROP TABLE IF EXISTS {db_config['gp_db_repair']}.dws_15_minutes;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_repair']}.dws_15_minutes_meter;", f"DROP TABLE IF EXISTS {db_config['gp_db_repair']}.dws_daily;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_repair']}.dws_daily_meter;", f"DROP TABLE IF EXISTS {db_config['gp_db_repair']}.dws_hourly;",
                           f"DROP TABLE IF EXISTS {db_config['gp_db_repair']}.dws_hourly_meter;"]
                    for ddl in ddls:
                        await gp_session.execute(text(ddl))
                    await gp_session.commit()
                async with await get_business_session(environment, db_config['sr_db_mes'], 'starrocks') as sr_mes_session:
                    ddls = [f"DROP TABLE IF EXISTS dws_15_minutes;", f"DROP TABLE IF EXISTS dws_15_minutes_meter;",
                            f"DROP TABLE IF EXISTS dws_daily;", f"DROP TABLE IF EXISTS dws_daily_meter;",
                            f"DROP TABLE IF EXISTS dws_hourly;", f"DROP TABLE IF EXISTS dws_hourly_meter;",
                            f"DROP TABLE IF EXISTS halt_record;"]
                    for ddl in ddls:
                        await sr_mes_session.execute(text(ddl))

                async with await get_business_session(environment, db_config['sr_db_repair'], 'starrocks') as sr_repair_session:
                    ddls = [f"DROP TABLE IF EXISTS dws_15_minutes;", f"DROP TABLE IF EXISTS dws_15_minutes_meter;",
                            f"DROP TABLE IF EXISTS dws_daily;", f"DROP TABLE IF EXISTS dws_daily_meter;",
                            f"DROP TABLE IF EXISTS dws_hourly;", f"DROP TABLE IF EXISTS dws_hourly_meter;"]
                    for ddl in ddls:
                        await sr_repair_session.execute(text(ddl))

                logger.info(f'删除gp和sr指标表成功')

            async with await get_business_session(environment, 'byt_grid_data', 'Mysql') as mysql_session:
                quota_result = await GeneratorGpSrManager.get_quota_config(mysql_session)
                if not quota_result:
                    return {"status": False, "msg": "指标配置获取失败"}

                if not all(k in quota_result for k in ['GreenPlum/StarRocks', 'StarRocks']):
                    return {"status": False, "msg": "指标配置不完整"}

                execute_result = await DynamicTableExecutor.generate_and_execute(fields, quota_result, db_config)

                # 构建详细结果消息
                details = []
                summary = execute_result["summary"]

                # 添加周期结果
                for period, period_result in execute_result["details"].items():
                    period_header = f"\n============ 周期 {period} ============"
                    period_details = []

                    # 处理Greenplum结果
                    gp_result = period_result.get("Greenplum", {})
                    gp_status = gp_result.get("status", False)
                    gp_msg = f"Greenplum 表创建: {'成功' if gp_status else '失败'}"
                    period_details.append(gp_msg)

                    if not gp_status:
                        period_details.append(f"失败原因: {gp_result.get('error', '未知错误')}")

                    # 处理StarRocks结果
                    sr_result = period_result.get("StarRocks", {})
                    sr_status = sr_result.get("status", False)
                    sr_msg = f"StarRocks 表创建: {'成功' if sr_status else '失败'}"
                    period_details.append(sr_msg)

                    if not sr_status:
                        period_details.append(f"失败原因: {sr_result.get('error', '未知错误')}")

                    # 添加表创建详情
                    for db_type in ["Greenplum", "StarRocks"]:
                        db_result = period_result.get(db_type, {})
                        if "tables" in db_result and db_result["tables"]:
                            table_list = []
                            for table in db_result["tables"]:
                                table_name = table.get('name', '未知对象')
                                table_type = table.get('type', 'UNKNOWN')  # 新增：获取类型
                                table_status = "成功" if table.get("status", False) else "失败"
                                table_list.append(f"- [{table_type}] {table_name}: {table_status}")  # 显示类型

                            if table_list:
                                period_details.append(f"\n{db_type} 创建的表:")
                                period_details.extend(table_list)

                    # 将周期结果添加到总详情
                    details.append(period_header)
                    details.extend(period_details)

                # 添加停机记录表状态
                halt_header = "\n=== 停机记录表 ==="
                halt_details = []

                halt_status = execute_result.get("halt_record", {})  # ✅ 修复字段名：原为 halt_table
                gp_halt_msg = f"Greenplum 停机表: {'成功' if halt_status.get('gp', False) else '失败'}"
                sr_halt_msg = f"StarRocks 停机表: {'成功' if halt_status.get('sr', False) else '失败'}"

                halt_details.extend([halt_header, gp_halt_msg, sr_halt_msg])
                details.extend(halt_details)

                # ✅ 修正最终状态和消息
                if execute_result["status"]:
                    msg = (
                            f"✅ 所有操作成功 (共 {summary['total']} 个周期)\n"
                            "详细结果:\n" + "\n".join(details))
                else:
                    msg = (
                            f"❌ 部分操作失败 (成功 {summary['success']}/{summary['total']} 个周期)\n"
                            "详细结果:\n" + "\n".join(details))

                logger.info({
                    "status": execute_result["status"],
                    "msg": msg
                })
                return {
                    "status": execute_result["status"],
                    "msg": msg
                }

        except Exception as e:
            logger.error(f"生成表结构失败: {str(e)}", exc_info=True)
            return {
                "status": False,
                "msg": f"生成表结构失败: {str(e)}"
            }


class DynamicSQLGenerator:
    @staticmethod
    def time_dict():
        return {
            '15分钟': '15_minutes',
            '每小时': 'hourly',
            '自然日': 'daily'
        }

    @staticmethod
    def generate_mes_halt_table(env: str, db_config: dict) -> dict:
        """
        生成停机记录表的SQL语句
        :param env: 环境标识
        :return: 包含GP和SR表SQL的字典
        """
        if '上海大区' in env:
            gp_sql = f"""CREATE TABLE {db_config['gp_db_mes']}.halt_record (
                    line_id int4 NULL,
                    quota_code varchar(20) NULL,
                    start_time timestamp(0) NULL,
                    end_time timestamp(0) NULL,
                    halt_type varchar(20) NULL,
                    halt_reason varchar(255) NULL,
                    work_produce_code varchar(20) NULL,
                    id bigserial NOT NULL,
                    update_time varchar NULL,
                    update_by varchar NULL
                );"""

            sr_sql = """CREATE TABLE `halt_record` (
                  `line_id` varchar(65533) NOT NULL COMMENT "产线id",
                  `start_time` datetime NOT NULL COMMENT "停机开始时间",
                  `quota_code` varchar(65533) NOT NULL COMMENT "指标编码",
                  `work_produce_code` varchar(65533) NOT NULL COMMENT "工序编码",
                  `end_time` datetime NULL COMMENT "停机结束时间",
                  `halt_type` varchar(65533) NULL COMMENT "停机类型",
                  `halt_reason` varchar(65533) NULL COMMENT "停机原因",
                  `update_time` varchar(65533) NULL COMMENT "更新时间",
                  `update_by` varchar(65533) NULL COMMENT "更新着"
                ) ENGINE=OLAP 
                PRIMARY KEY(`line_id`, `start_time`, `quota_code`, `work_produce_code`)
                COMMENT "停机记录"
                PARTITION BY date_trunc('year', start_time)
                DISTRIBUTED BY HASH(`line_id`) BUCKETS 4 
                PROPERTIES (
                    "replication_num" = "3",
                    "in_memory" = "false",
                    "enable_persistent_index" = "false",
                    "replicated_storage" = "true",
                    "compression" = "LZ4"
                );"""
        else:
            gp_sql = f"""CREATE TABLE {db_config['gp_db_mes']}.halt_record (
                    id serial4 NOT NULL,
                    line_id int4 NULL,
                    quota_code varchar(20) NULL,
                    start_time timestamp(0) NULL,
                    end_time timestamp(0) NULL,
                    halt_type varchar(20) NULL,
                    halt_reason varchar(255) NULL,
                    work_produce_code varchar(20) NULL,
                    line_code varchar(225) NOT NULL,
                    tenant_id varchar(225) NOT NULL,
                    update_time varchar NULL,
                    update_by varchar NULL,
                    CONSTRAINT halt_record_pkey PRIMARY KEY (id)
                );"""

            sr_sql = """CREATE TABLE `halt_record` (
                  `line_id` varchar(65533) NOT NULL COMMENT "产线id",
                  `line_code` varchar(225) NOT NULL,
                  `tenant_id` varchar(225) NOT NULL,
                  `start_time` datetime NOT NULL COMMENT "停机开始时间",
                  `quota_code` varchar(65533) NOT NULL COMMENT "指标编码",
                  `work_produce_code` varchar(65533) NOT NULL COMMENT "工序编码",
                  `end_time` datetime NULL COMMENT "停机结束时间",
                  `halt_type` varchar(65533) NULL COMMENT "停机类型",
                  `halt_reason` varchar(65533) NULL COMMENT "停机原因",
                  `update_time` varchar(65533) NULL COMMENT "更新时间",
                  `update_by` varchar(65533) NULL COMMENT "更新着"
                ) ENGINE=OLAP 
                PRIMARY KEY(`line_id`,`line_code`,`tenant_id`, `start_time`, `quota_code`, `work_produce_code`)
                COMMENT "停机记录"
                PARTITION BY date_trunc('year', start_time)
                DISTRIBUTED BY HASH(`line_id`) BUCKETS 4 
                PROPERTIES (
                    "replication_num" = "3",
                    "in_memory" = "false",
                    "enable_persistent_index" = "false",
                    "replicated_storage" = "true",
                    "compression" = "LZ4"
                );"""
        return {"gp": gp_sql, "sr": sr_sql}

    @staticmethod
    def generate_mes_gp_view(period: str, is_meter: bool, env: str, fields: list, db_config: dict) -> str:
        period_suffix = DynamicSQLGenerator.time_dict()[period]
        type_suffix = '_meter' if is_meter else ''
        table_name = f"dws_{period_suffix}{type_suffix}"
        gp_db_mes = db_config['gp_db_mes']

        if is_meter:
            field_columns_rt = "\n    ".join(
                [f"dws_{period_suffix}_rt{type_suffix}.er_impep_{i:03d}," for i in range(1, 1001)])
            field_columns_ht = "\n    ".join(
                [f"dws_{period_suffix}_ht{type_suffix}.er_impep_{i:03d}," for i in range(1, 1001)])
        else:
            field_columns_rt = "\n    ".join([f"dws_{period_suffix}_rt{type_suffix}.{field[0]}," for field in fields])
            field_columns_ht = "\n    ".join([f"dws_{period_suffix}_ht{type_suffix}.{field[0]}," for field in fields])

        if '上海大区' in env:
            sql = f"""CREATE OR REPLACE VIEW {gp_db_mes}.{table_name}
                AS SELECT 
                    dws_{period_suffix}_rt{type_suffix}.line_id,
                    dws_{period_suffix}_rt{type_suffix}.id,
                    dws_{period_suffix}_rt{type_suffix}.date,
                    {field_columns_rt.rstrip(',')}
                FROM {gp_db_mes}.dws_{period_suffix}_rt{type_suffix}
                UNION ALL
                SELECT 
                    dws_{period_suffix}_ht{type_suffix}.line_id,
                    dws_{period_suffix}_ht{type_suffix}.id,
                    dws_{period_suffix}_ht{type_suffix}.date,
                    {field_columns_ht.rstrip(',')}
                FROM {gp_db_mes}.dws_{period_suffix}_ht{type_suffix};
                """
        else:
            sql = f"""CREATE OR REPLACE VIEW {gp_db_mes}.{table_name}
                AS SELECT 
                    dws_{period_suffix}_rt{type_suffix}.line_id,
                    dws_{period_suffix}_rt{type_suffix}.line_code,
                    dws_{period_suffix}_rt{type_suffix}.tenant_id,
                    dws_{period_suffix}_rt{type_suffix}.id,
                    dws_{period_suffix}_rt{type_suffix}.date,
                    {field_columns_rt.rstrip(',')}
                FROM {gp_db_mes}.dws_{period_suffix}_rt{type_suffix}
                UNION ALL
                SELECT 
                    dws_{period_suffix}_ht{type_suffix}.line_id,
                    dws_{period_suffix}_ht{type_suffix}.line_code,
                    dws_{period_suffix}_ht{type_suffix}.tenant_id,
                    dws_{period_suffix}_ht{type_suffix}.id,
                    dws_{period_suffix}_ht{type_suffix}.date,
                    {field_columns_ht.rstrip(',')}
                FROM {gp_db_mes}.dws_{period_suffix}_ht{type_suffix};
                """
        return sql

    @staticmethod
    def generate_mes_gp_table(period: str, table_type: str, env: str, fields: list, db_config: dict) -> str:
        period_suffix = DynamicSQLGenerator.time_dict()[period]
        table_name = f"dws_{period_suffix}_{table_type}"
        is_meter = table_type.endswith('_meter')
        gp_db_mes = db_config['gp_db_mes']

        if is_meter:
            field_columns = "\n    ".join([f"er_impep_{i:03d} numeric(18, 3) NULL," for i in range(1, 1001)])
        else:
            field_columns = "\n    ".join(
                [f"{field[0]} numeric(18, 3) DEFAULT NULL::numeric NULL," for field in fields])

        if '上海大区' in env:
            sql = f"""CREATE TABLE {gp_db_mes}.{table_name} (
                    id serial4 NOT NULL, 
                    date timestamp(0) NOT NULL, 
                    line_id int4 NOT NULL, 
                    {field_columns.rstrip(',')},
                    CONSTRAINT {table_name}_date_line_id_key UNIQUE (date, line_id)
                );"""
        else:
            sql = f"""CREATE TABLE {gp_db_mes}.{table_name} (
                    id serial4 NOT NULL, 
                    date timestamp(0) NOT NULL, 
                    line_id int4 NOT NULL, 
                    line_code varchar(20) NOT NULL, 
                    tenant_id varchar(20) NOT NULL,
                    {field_columns.rstrip(',')},
                    CONSTRAINT {table_name}_date_line_id_line_code_tenant_id_key UNIQUE (date, line_id, line_code, tenant_id)
                );"""
        return sql

    @staticmethod
    def generate_repair_gp_table(period: str, is_meter: bool, env: str, fields: list, db_config: dict) -> str:
        period_suffix = DynamicSQLGenerator.time_dict()[period]
        table_name = f"dws_{period_suffix}{'_meter' if is_meter else ''}"
        gp_db_repair = db_config['gp_db_repair']
        if is_meter:
            field_columns = "\n    ".join([f"er_impep_{i:03d} numeric(18, 3) NULL," for i in range(1, 1001)])
        else:
            field_columns = "\n    ".join(
                [f"{field[0]} numeric(18, 3) DEFAULT NULL::numeric NULL," for field in fields])

        if '上海大区' in env:
            sql = f"""CREATE TABLE {gp_db_repair}.{table_name} (
                    id serial4 NOT NULL, 
                    date timestamp(0) NOT NULL, 
                    line_id int4 NOT NULL, 
                    task_id int4 NOT NULL,
                    {field_columns.rstrip(',')},
                    CONSTRAINT {table_name}_date_line_id_task_id_key UNIQUE (date, line_id, task_id)
                );"""
        else:
            sql = f"""CREATE TABLE {gp_db_repair}.{table_name} (
                    id serial4 NOT NULL, 
                    date timestamp(0) NOT NULL, 
                    line_id int4 NOT NULL, 
                    line_code varchar(20) NOT NULL, 
                    tenant_id varchar(20) NOT NULL,
                    task_id int4 NOT NULL,
                    {field_columns.rstrip(',')},
                    CONSTRAINT {table_name}_date_line_id_line_code_tenant_id_task_id_key UNIQUE (date, line_id, line_code, tenant_id, task_id)
                );"""
        return sql

    @staticmethod
    def generate_mes_sr_table(period: str, is_meter: bool, env: str, fields: list) -> str:
        period_suffix = DynamicSQLGenerator.time_dict()[period]
        table_name = f"dws_{period_suffix}{'_meter' if is_meter else ''}"

        if is_meter:
            field_columns = "\n    ".join(
                [f"`er_impep_{i:03d}` decimal(18, 3) NULL COMMENT 'er_impep_{i:03d}'," for i in range(1, 1001)])
        else:
            field_columns = "\n    ".join(
                [f"`{field[0]}` decimal(18, 3) NULL COMMENT '{field[1]}'," for field in fields])

        if '上海大区' in env:
            sql = f"""CREATE TABLE `{table_name}` (
                    `date` datetime NOT NULL COMMENT "",
                    `line_id` int(11) NOT NULL COMMENT "",
                    {field_columns.rstrip(',')}
                    ) ENGINE=OLAP 
                    PRIMARY KEY(`date`,`line_id`)
                    DISTRIBUTED BY HASH(`date`,`line_id`) BUCKETS 4 
                    PROPERTIES (
                        "replication_num" = "3",
                        "in_memory" = "false",
                        "enable_persistent_index" = "false",
                        "replicated_storage" = "true",
                        "compression" = "LZ4"
                    );"""

        else:
            sql = f"""CREATE TABLE `{table_name}` (
                    `date` datetime NOT NULL COMMENT "",
                    `line_code` varchar(65533) NOT NULL COMMENT "",
                    `line_id` int(11) NOT NULL COMMENT "",
                    `tenant_id` varchar(65533) NOT NULL COMMENT "",
                    {field_columns.rstrip(',')}
                    ) ENGINE=OLAP 
                    PRIMARY KEY(`date`, `line_code`, `line_id`, `tenant_id`)
                    DISTRIBUTED BY HASH(`date`, `line_code`, `line_id`, `tenant_id`) BUCKETS 4 
                    PROPERTIES (
                        "replication_num" = "3",
                        "in_memory" = "false",
                        "enable_persistent_index" = "false",
                        "replicated_storage" = "true",
                        "compression" = "LZ4"
                    );"""
        return sql

    @staticmethod
    def generate_repair_sr_table(period: str, is_meter: bool, env: str, fields: list) -> str:
        period_suffix = DynamicSQLGenerator.time_dict()[period]
        table_name = f"dws_{period_suffix}{'_meter' if is_meter else ''}"

        if is_meter:
            field_columns = "\n    ".join(
                [f"`er_impep_{i:03d}` decimal(18, 3) NULL COMMENT 'er_impep_{i:03d}'," for i in range(1, 1001)])
        else:
            field_columns = "\n    ".join(
                [f"`{field[0]}` decimal(18, 3) NULL COMMENT '{field[1]}'," for field in fields])

        if '上海大区' in env:
            sql = f"""CREATE TABLE `{table_name}` (
                        `date` datetime NOT NULL COMMENT "",
                        `line_id` int(11) NOT NULL COMMENT "",
                        `task_id` int(11) NOT NULL COMMENT "",
                        {field_columns.rstrip(',')}
                        ) ENGINE=OLAP 
                        PRIMARY KEY(`date`,`line_id`, `task_id`)
                        DISTRIBUTED BY HASH(`date`,`line_id`, `task_id`) BUCKETS 4 
                        PROPERTIES (
                            "replication_num" = "3",
                            "in_memory" = "false",
                            "enable_persistent_index" = "false",
                            "replicated_storage" = "true",
                            "compression" = "LZ4"
                        );"""
        else:
            sql = f"""CREATE TABLE `{table_name}` (
                        `date` datetime NOT NULL COMMENT "",
                        `line_code` varchar(65533) NOT NULL COMMENT "",
                        `line_id` int(11) NOT NULL COMMENT "",
                        `tenant_id` varchar(65533) NOT NULL COMMENT "",
                        `task_id` int(11) NOT NULL COMMENT "",
                        {field_columns.rstrip(',')}
                        ) ENGINE=OLAP 
                        PRIMARY KEY(`date`, `line_code`, `line_id`, `tenant_id`, `task_id`)
                        DISTRIBUTED BY HASH(`date`, `line_code`, `line_id`, `tenant_id`, `task_id`) BUCKETS 4 
                        PROPERTIES (
                            "replication_num" = "3",
                            "in_memory" = "false",
                            "enable_persistent_index" = "false",
                            "replicated_storage" = "true",
                            "compression" = "LZ4"
                        );"""
        return sql


class DynamicTableExecutor:

    @staticmethod
    async def execute_ddl(
            environment: str,
            db_type: str,
            schema: str,
            ddl_statements: List[str],
            execution_timeout: int = 20
    ) -> dict:
        result = {
            "type": "UNKNOWN",
            "status": True,
            "tables": [],
            "error": None
        }

        session = None
        try:
            session = await get_business_session(environment, schema, db_type.lower())

            for ddl in ddl_statements:
                # 使用新解析函数
                obj_info = parse_ddl_object(ddl)
                object_name = obj_info["name"]
                object_type = obj_info["type"] or "OBJECT"
                object_schema = obj_info["schema"] or schema  # fallback 到传入的 schema

                full_name = f"{object_schema}.{object_name}"

                table_record = {
                    "name": full_name,
                    "type": object_type,
                    "status": False,
                    "error": None
                }

                logger.info(f"正在创建 {db_type} {object_type.lower()}: {full_name}")

                try:
                    await asyncio.wait_for(
                        session.execute(text(ddl)),
                        timeout=execution_timeout
                    )
                    await session.commit()

                    table_record["status"] = True

                except asyncio.TimeoutError:
                    error_msg = f"DDL 执行超时（>{execution_timeout}s）"
                    table_record["error"] = error_msg
                    result["status"] = False
                    await session.rollback()

                except asyncio.CancelledError:
                    raise
                    error_msg = "DDL 执行被取消（可能已在服务端成功）"
                    table_record["error"] = error_msg
                    result["status"] = False

                except Exception as e:
                    error_msg = str(e)
                    table_record["error"] = error_msg
                    result["status"] = False
                    await session.rollback()

                result["tables"].append(table_record)

            return result

        except Exception as e:
            error_msg = f"{db_type} 连接或会话初始化失败: {type(e).__name__} - {str(e)}"
            logger.error(error_msg)
            result["status"] = False
            result["error"] = error_msg
            return result

        finally:
            if session is not None:
                try:
                    close_task = session.close()
                    if asyncio.iscoroutine(close_task):
                        await asyncio.shield(close_task)
                    await print_connection_pool_status()
                except Exception as e:
                    logger.warning(f"关闭 session 时出错: {e}")

        return result

    @classmethod
    async def generate_and_execute(cls, fields: dict, quota_result: dict, db_config: dict) -> dict:
        environment = fields.get("environment")["value"]
        periods = [item["value"] for item in fields.get("period")]

        result = {
            "status": True,
            "details": {},
            "summary": {
                "total": len(periods),
                "success": 0,
                "failed": 0
            },
            "halt_record": {
                "gp": False,
                "sr": False
            }
        }

        # 处理每个周期
        for period in periods:
            period_result = {
                "Greenplum": {"status": False, "tables": []},
                "StarRocks": {"status": False, "tables": []}
            }
            result["details"][period] = period_result

            try:
                # === Greenplum ===
                gp_ddls = [
                    ("mes_rt", DynamicSQLGenerator.generate_mes_gp_table(period, "rt", environment, quota_result['GreenPlum/StarRocks'], db_config)),
                    ("mes_rt_meter", DynamicSQLGenerator.generate_mes_gp_table(period, "rt_meter", environment, [], db_config)),
                    ("mes_ht", DynamicSQLGenerator.generate_mes_gp_table(period, "ht", environment, quota_result['GreenPlum/StarRocks'], db_config)),
                    ("mes_ht_meter", DynamicSQLGenerator.generate_mes_gp_table(period, "ht_meter", environment, [], db_config)),
                    ("repair", DynamicSQLGenerator.generate_repair_gp_table(period, False, environment, quota_result['GreenPlum/StarRocks'], db_config)),
                    ("repair_meter", DynamicSQLGenerator.generate_repair_gp_table(period, True, environment, [], db_config)),
                    ("mes_view", DynamicSQLGenerator.generate_mes_gp_view(period, False, environment, quota_result['GreenPlum/StarRocks'], db_config)),
                    ("mes_view_meter", DynamicSQLGenerator.generate_mes_gp_view(period, True, environment, [], db_config))
                ]
                gp_execution = await cls.execute_ddl(environment, "Greenplum", "byt", [ddl for _, ddl in gp_ddls])
                period_result["Greenplum"] = gp_execution

                # === StarRocks ===
                sr_ddls = [
                    ("mes", DynamicSQLGenerator.generate_mes_sr_table(period, False, environment, quota_result['GreenPlum/StarRocks'])),
                    ("mes_meter", DynamicSQLGenerator.generate_mes_sr_table(period, True, environment, [])),
                    ("repair", DynamicSQLGenerator.generate_repair_sr_table(period, False, environment, quota_result['GreenPlum/StarRocks'])),
                    ("repair_meter", DynamicSQLGenerator.generate_repair_sr_table(period, True, environment, []))
                ]

                sr_mes_execution = await cls.execute_ddl(environment, "StarRocks", db_config['sr_db_mes'], [ddl for name, ddl in sr_ddls if "mes" in name])
                sr_repair_execution = await cls.execute_ddl(environment, "StarRocks", db_config['sr_db_repair'], [ddl for name, ddl in sr_ddls if "repair" in name])

                sr_combined = {
                    "status": sr_mes_execution["status"] and sr_repair_execution["status"],
                    "tables": sr_mes_execution["tables"] + sr_repair_execution["tables"],
                    "error": sr_mes_execution.get("error") or sr_repair_execution.get("error")
                }
                period_result["StarRocks"] = sr_combined

                # ✅ 判断周期是否成功：Greenplum 和 StarRocks 都必须成功
                period_success = gp_execution["status"] and sr_combined["status"]
                if period_success:
                    result["summary"]["success"] += 1
                else:
                    result["summary"]["failed"] += 1
                    result["status"] = False  # 至少一个周期失败，则整体失败

            except Exception as e:
                result["summary"]["failed"] += 1
                result["status"] = False
                logger.error(f"处理周期 {period} 时发生异常: {str(e)}", exc_info=True)

        # 处理停机记录表
        try:
            halt_sqls = DynamicSQLGenerator.generate_mes_halt_table(environment, db_config)
            gp_halt_result = await cls.execute_ddl(environment, "Greenplum", "byt", [halt_sqls["gp"]])
            result["halt_record"]["gp"] = gp_halt_result["status"]

            sr_halt_result = await cls.execute_ddl(environment, "StarRocks", db_config['sr_db_mes'], [halt_sqls["sr"]])
            result["halt_record"]["sr"] = sr_halt_result["status"]

            if not (gp_halt_result["status"] and sr_halt_result["status"]):
                result["status"] = False
        except Exception as e:
            result["status"] = False
            logger.error(f"处理停机记录表时发生异常: {str(e)}", exc_info=True)

        return result