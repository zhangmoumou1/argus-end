# AI 测试重建状态

旧原型 API 已替换，不再读取 data/ai_test_state.json，也不迁移原型示例数据。

## 已实现
- MySQL 持久化项目、版本；事务写入，按登录用户隔离访问。
- 项目/版本树、新建项目、新建版本前端，复用 Umi request 认证链路。
- 未接入 Agent 时分析接口明确返回 HTTP 503。
- 移除旧演示页面入口的虚构统计和假执行响应。

## 部署
先在现有业务数据库执行 init_data/09_ai_test_rebuild.sql，再重启后端和构建前端。
未执行迁移时接口将报错，不会回退到内存或 JSON。
旧的 08 表与 JSON 文件不删除，便于人工核对历史内容。

## 尚未实现
独立 LangGraph Agent、Redis checkpoint、模型接入、文件解析、Git 分析、审核中断恢复、XMind/Excel 资产、真实 API/UI/性能执行、报告与缺陷、共享项目成员权限和 Docker Compose。

## 验证
Python compileall 通过。尚未进行数据库迁移、前端构建及端到端验证，当前不是完整平台交付。
