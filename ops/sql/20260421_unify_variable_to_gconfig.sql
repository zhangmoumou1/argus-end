-- 统一变量存储到 pity_gconfig
-- 执行前请先备份数据库

-- 1) 扩展字段
ALTER TABLE pity_gconfig
    ADD COLUMN type INT NOT NULL DEFAULT 1 COMMENT '1:全局变量 2:运行时变量 3:特殊变量' AFTER key_type,
    ADD COLUMN project_id INT NULL COMMENT '变量来源项目ID' AFTER type,
    ADD COLUMN case_id INT NULL COMMENT '变量来源用例ID' AFTER project_id,
    ADD COLUMN case_name VARCHAR(128) NULL COMMENT '变量来源用例名称' AFTER case_id;

-- 2) 扩容 key，兼容更长变量名
ALTER TABLE pity_gconfig
    MODIFY COLUMN `key` VARCHAR(64) NOT NULL;

-- 3) 历史数据兜底：默认按全局变量处理
UPDATE pity_gconfig
SET type = 1
WHERE type IS NULL;

-- 4) 运行时变量查询索引
CREATE INDEX idx_gconfig_runtime_lookup
    ON pity_gconfig(type, env, project_id, case_id, `key`, deleted_at, id);

-- 5) 调整唯一约束
-- 注意：请先确认你库里旧唯一索引/约束的实际名字，再替换后执行 DROP
-- 常见名字可能是: env / pity_gconfig_env_key_deleted_at / uq_...
-- 示例：
-- ALTER TABLE pity_gconfig DROP INDEX env;

ALTER TABLE pity_gconfig
    ADD UNIQUE KEY uq_gconfig_dim(env, `key`, type, project_id, case_id, deleted_at);

-- 6) pity_runtime_variable 已弃用，可按需删除（确认无回滚需求再执行）
-- DROP TABLE pity_runtime_variable;
