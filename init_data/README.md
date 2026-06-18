# 初始化数据

目录说明：
- `01_functional_case_skill_docs.sql`：场景设计/接口流程场景的技能文档初始化 SQL
- `02_ai_model_config.sql`：后台管理-模型配置初始化 SQL
- `03_project_and_directory.sql`：测试项目与功能用例目录初始化 SQL
- `04_functional_case_file.sql`：功能用例文件初始化 SQL
- `05_run_all.sql`：按顺序合并后的总 SQL
- `skill_docs/`：对应技能文档的 Markdown 源文件

导入顺序：
1. `01_functional_case_skill_docs.sql`
2. `02_ai_model_config.sql`
3. `03_project_and_directory.sql`
4. `04_functional_case_file.sql`

说明：
- 这批数据用于项目首次发布初始化；后续库里已存在时，不要再执行覆盖。
- 当前示例目录与文件 SQL 默认使用 `project_id = 1`、`directory_id = 1`。
- 如果部署环境自增 ID 不一致，请先调整 SQL 再执行。
- 建议导入前确认目标库已完成表结构升级。
