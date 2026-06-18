SET NAMES utf8mb4;

START TRANSACTION;

INSERT INTO argus_functional_case_skill_doc
(created_at, updated_at, deleted_at, create_user, update_user, title, description, doc_type, content, is_shared)
VALUES('2026-06-16 19:31:29', '2026-06-17 17:11:42', 0, 1, 1, '功能用例-生成用例', '', 'skill_md', '# 生成功能测试用例

## 目标

用于在 `<需求目录>` 下生成可交付的 `功能测试用例.md`。

本文件只定义生成策略，不重复定义通用写法规则。  
结构、优先级、异常范围、禁止项统一以《测试用例编写规范.md》为准。

## 输入优先级

按以下顺序读取并裁决：

1. 用户本次提供的需求说明、截图、原型、链接
2. `<需求目录>\\需求文档`
3. `<需求目录>\\规范与标准\\测试用例编写规范.md`
4. `<需求目录>\\规范与标准\\测试用例模板.md`
5. `<需求目录>\\skills\\审查用例.md`
6. 其他补充文档（如有）

若材料冲突，以更高优先级为准。

## 生成原则

1. 直接输出可交付初稿
2. 不输出分析过程
3. 不输出解释性文字
4. 不复述需求原文
5. 直接提炼测试点并组织成标准层级
6. 默认补齐最小必要异常集
7. 不无限扩展低价值异常场景
8. 若需求未明确提示文案或业务规则，只写行为性预期，不得虚构细节

## 生成范围

必须覆盖需求或原型中明确出现的对象，包括但不限于：

- 页面区域
- 表单字段
- 按钮
- 弹窗
- 查询区
- 列表区
- 操作分支
- 结果反馈
- 状态变化

## 最小异常集

在需求未额外约束时，默认至少补齐以下异常：

1. 必填为空
2. 长度超限
3. 关键操作失败反馈
4. 查询无结果
5. 删除失败或不可删除（存在删除场景时）

## 优先级生成规则

- `P0`：主流程、关键操作、关键提交、关键结果
- `P1`：重要校验、关键异常、关键边界
- `P2`：一般展示、提示文案、低风险补充项

## 输出要求

1. 仅输出最终 Markdown
2. 严格使用固定层级
3. 单条用例只包含一个检查点
4. 每条用例必须带且仅带一个优先级
5. 每条预期必须具体、可验证
6. 禁止使用进度图标或额外状态标记

## 固定输出结构
- 模块: xxx
  - 功能: xxx
    - 子功能: xxx
      - 字段: xxx
        - 用例名称: xxx（P0/P1/P2）
          - 预期: xxx', 1);
INSERT INTO argus_functional_case_skill_doc
(created_at, updated_at, deleted_at, create_user, update_user, title, description, doc_type, content, is_shared)
VALUES('2026-06-16 19:32:52', '2026-06-17 17:11:58', 0, 1, 1, '功能用例-审查用例', '', 'skill_md', '# 审查要求

## 目标

用于快速判定 `功能测试用例.md` 是否达到可交付标准。

审查目标：

1. 结构正确
2. 覆盖完整
3. 优先级合理
4. 预期可验证
5. 无明显虚构内容

## 输入优先级

按以下顺序读取并裁决：

1. 用户本次提供的需求说明、截图、原型、链接
2. `<需求目录>\\需求文档`
3. `<需求目录>\\规范与标准\\测试用例编写规范.md`
4. `<需求目录>\\规范与标准\\测试用例模板.md`
5. `<需求目录>\\功能测试用例.md`

若材料冲突，以更高优先级为准。

## 审查结论

仅允许输出以下两种结论之一：

- `通过：可交付`
- `不通过：需修正`

## 一票否决项

命中任一项，直接判定为 `不通过：需修正`：

1. 未按模板层级输出
2. 存在未标注优先级的用例
3. 单条用例包含多个检查点
4. 预期描述模糊、不可验证
5. 关键流程仅覆盖成功路径，未覆盖必要异常
6. 需求明确对象缺失覆盖
7. 存在明显虚构的业务规则、校验规则或反馈结果
8. 使用进度图标或非规定格式标记用例状态

## 固定审查维度

### 1. 结构审查

检查是否严格使用以下层级：

`模块 -> 功能 -> 子功能 -> 字段 -> 用例名称 -> 预期`

不允许跳层、并层、错层。

### 2. 覆盖审查

检查需求或原型中明确出现的对象是否已覆盖，包括但不限于：

- 页面区域
- 字段
- 按钮
- 操作入口
- 分支流程
- 状态结果
- 反馈信息

### 3. 优先级审查

检查每条用例是否标注 `P0 / P1 / P2`，且分配合理：

- `P0`：主流程、关键操作、关键提交、关键结果
- `P1`：重要校验、关键异常、关键边界
- `P2`：一般展示、提示文案、非关键补充场景

禁止：

- 缺失优先级
- 一个用例多个优先级
- 使用进度图标替代优先级

### 4. 异常审查

至少检查是否覆盖最小必要异常集：

- 必填为空
- 长度超限
- 关键操作失败反馈
- 查询无结果
- 删除失败或不可删除（存在删除场景时）

若需求明确存在其他异常分支，也必须覆盖。

### 5. 预期审查

每条 `预期` 必须满足：

1. 描述具体
2. 能直接验证
3. 不使用“正确显示”“正常处理”等空泛措辞
4. 不混入多个判断点

### 6. 虚构审查

不得补充需求未明确说明的内容，包括但不限于：

- 权限规则
- 默认值规则
- 格式校验规则
- 成功/失败提示文案
- 后端容错逻辑
- 业务判定条件

若需求未说明，只允许写行为性预期，不得自行发明业务细节。

## 不通过输出格式

若结论为 `不通过：需修正`，每条问题必须包含以下 4 项：

1. 问题类型
2. 问题位置
3. 问题说明
4. 修正建议

位置格式：

`模块 -> 功能 -> 子功能 -> 字段`

示例：

`新增数据源 -> 第二步-配置信息 -> 连接测试 -> 测试连接按钮`

## 通过输出格式

若结论为 `通过：可交付`，必须输出：

1. 已覆盖的核心功能范围
2. 已覆盖的关键异常范围
3. 低风险残留项（如有，无则写“无”）

## 审查后动作

1. 通过：允许交付
2. 不通过：必须修正后复审
3. 禁止跳过复审直接交付', 1);
INSERT INTO argus_functional_case_skill_doc
(created_at, updated_at, deleted_at, create_user, update_user, title, description, doc_type, content, is_shared)
VALUES('2026-06-16 19:34:06', '2026-06-17 17:13:23', 0, 1, 1, '功能用例-用例编写规范', '', 'skill_md', '# 测试用例编写规范

## 1. 作用

本规范是功能测试用例生成与审查的统一规则基准。

适用范围：

- AI 生成测试用例
- 人工补充或修改测试用例
- AI 审查测试用例

若材料冲突，按以下原则处理：

1. 用户本次明确提供的需求说明、截图、原型优先
2. 需求文档优先于本规范
3. 本规范优先于模板和其他补充文档

## 2. 材料优先级

按以下顺序读取并裁决：

1. 用户本次明确提供的需求文本、截图、原型、链接
2. `<需求目录>\\需求文档`
3. 本规范
4. `测试用例模板.md`
5. 其他技能文档或补充材料

## 3. 输出基本要求

1. 输出格式必须为 Markdown
2. 严格按模板层级输出
3. 单条用例只允许表达一个检查点
4. 每条用例必须标注且仅标注一个优先级：`P0 / P1 / P2`
5. 优先级只能写在 `用例名称` 中
6. `预期` 必须具体、可验证、可判定
7. 禁止使用进度图标、状态图标或额外符号标记用例
8. 禁止虚构需求未声明的业务规则、提示文案、默认值或校验逻辑
9. 默认补齐必要的异常场景与边界场景，但不得无限扩展

## 4. 标准结构

```md
- 模块: xxx
  - 功能: xxx
    - 子功能: xxx
      - 字段: xxx
        - 用例名称: xxx（P0/P1/P2）
          - 预期: xxx', 1);
INSERT INTO argus_functional_case_skill_doc
(created_at, updated_at, deleted_at, create_user, update_user, title, description, doc_type, content, is_shared)
VALUES('2026-06-16 19:35:02', '2026-06-17 17:12:50', 0, 1, 1, '功能用例-用例模板', '', 'skill_md', '# 测试用例模板

以下模板用于统一功能测试用例的输出层级与写法。

- 模块: 示例模块
  - 功能: 示例功能
    - 子功能: 示例子功能

      - 字段: 示例字段
        - 用例名称: 示例字段展示（P2）
          - 预期: 展示需求定义的字段名称、默认值或占位信息

        - 用例名称: 示例字段必填校验（P0）
          - 预期: 字段为空时给出必填提示，且不允许继续提交或保存

        - 用例名称: 示例字段长度上限校验（P1）
          - 预期: 输入最大允许长度时可正常输入，并允许继续保存或提交

        - 用例名称: 示例字段长度超限校验（P1）
          - 预期: 输入超过最大允许长度时超出部分无法输入或触发长度限制反馈

      - 字段: 示例按钮
        - 用例名称: 示例按钮点击成功（P0）
          - 预期: 点击后执行对应操作，并返回成功结果

        - 用例名称: 示例按钮点击失败反馈（P1）
          - 预期: 点击失败时展示失败反馈，且当前页面或弹窗状态保持可预期

  - 功能: 示例列表
    - 子功能: 查询区

      - 字段: 查询条件
        - 用例名称: 查询条件展示（P2）
          - 预期: 展示需求定义的查询字段、默认值和占位提示

        - 用例名称: 查询无结果反馈（P1）
          - 预期: 输入无匹配条件执行查询后，列表展示空结果状态或无数据反馈

    - 子功能: 列表区

      - 字段: 列表表头
        - 用例名称: 列表表头展示（P2）
          - 预期: 展示需求定义的表头信息，且字段顺序正确

      - 字段: 列表数据
        - 用例名称: 列表数据展示（P1）
          - 预期: 列表展示正确的数据内容、格式和状态信息

      - 字段: 提示文案
        - 用例名称: 提示文案展示（P2）
          - 预期: 展示需求定义的提示文案，且位置正确', 1);
INSERT INTO argus_functional_case_skill_doc
(created_at, updated_at, deleted_at, create_user, update_user, title, description, doc_type, content, is_shared)
VALUES('2026-06-17 17:13:08', '2026-06-17 17:13:08', 0, 1, 1, '接口用例-流程场景生成规范', '', 'skill_md', '# 接口用例流程场景生成技能

## 角色
你是测试平台的接口自动化用例生成助手。你需要根据用户选择的接口链路、接口定义、录制实例数据和业务目标，生成可直接保存到“场景测试-接口用例”的流程性接口场景。

## 输出格式
只输出 1 个严格 JSON 对象，不输出解释、分析、Markdown、代码块或额外文本。

固定结构：
{
  "scenario_name": "",
  "summary": "",
  "warnings": [],
  "cases": []
}

每个 case 固定字段：
{
  "name": "",
  "priority": "P1",
  "method": "GET|POST|PUT|DELETE|PATCH",
  "url": "",
  "headers": {},
  "body_type": 0,
  "body": {},
  "asserts": [],
  "out_parameters": [],
  "pre_steps": [],
  "tags": ["AI生成", "流程场景"],
  "reason": ""
}

## 基础字段规则
- `name` 使用明确业务动作命名，如“新增维度”“查询最新维度”“删除维度”。
- `priority` 默认 `P1`。
- `method` 必须大写。
- `GET/DELETE` 默认 `body_type=0`，`body={}` 或 `body=null`。
- `POST/PUT/PATCH` 默认 `body_type=1`，`body` 必须是 JSON 对象。
- `headers` 必须是对象，不能是字符串。
- `url` 优先使用接口录制实例的真实路径和 query 参数；没有实例时使用接口管理中的 path/full_url。
- 不要生成平台无法识别的字段；不要输出 `constructor`，当前 AI 流程保存不会落库构造器。

## 真实实例优先级
- 当接口上下文存在 `sample_url`、`sample_request`、`sample_response` 时，必须优先使用录制实例。
- `sample_url` 是真实请求地址依据，优先保留真实 path 和 query 结构。
- `sample_request.query` 是 GET/DELETE 参数依据。
- `sample_request.body` 是 POST/PUT/PATCH 请求体依据。
- `sample_response` 是断言和出参提取依据。
- 接口定义中的 `request_schema`、`response_schema` 只作为没有录制实例时的兜底。
- 禁止根据字段名臆造不存在的响应路径。

## 流程依赖规则
- 生成流程时按业务目标排序，例如：新增 -> 列表查询 -> 详情/编辑 -> 删除。
- 后续步骤需要依赖前序步骤数据时，必须通过 `out_parameters` 提取变量，并在后续 `url/query/body/header` 中用 `${变量名}` 引用。
- `pre_steps` 只用于前端预览说明依赖关系，不是真正可执行前置条件。
- 真正的依赖必须体现在 `out_parameters` 和 `${变量名}` 引用上。
- 禁止使用 `${【case3】xxx}`、`${【case6】token}` 这类旧 case_id 依赖写法。
- 不要写死 id、token、编码、随机名称等动态值，除非它来自用户明确要求或录制实例中可复用的常量。

## 变量引用规则
平台执行时会在 `body`、`url`、`request_headers` 中替换变量。

允许变量写法：
- 接口出参变量：`${entityId}`、`${entityName}`、`${token}`
- 复杂结构变量：`${user.id}`、`${list[0].id}`
- 固定接口变量：`${response}`、`${status_code}`
- 全局变量：`${authorization}`、`${tenant_id}`、`${baseToken}`，名称来自系统全局变量配置
- 特殊变量：`${【snowflake_id】}`、`${【phone】}`、`${【rand_4】}`、`${【cur_ymdhms】}`

特殊变量规则：
- `${【phone】}` 生成手机号。
- `${【rand_4】}` 生成 4 位随机数字，数字长度可变，如 `${【rand_8】}`。
- `${【snowflake_id】}` 生成雪花 ID。
- 时间变量支持 `${【cur_ymdhms】}`、`${【cur_ymd】}`、`${【pre_1d_ymd】}`、`${【fut_2h_ymdhms】}`。
- 时间粒度支持 `ymdhms`、`ymdm`、`ymdh`、`ymd`、`ym`、`y`。
- 时间偏移单位支持 `s`、`min`、`h`、`d`、`m`、`y`。

## 出参提取规则
当 `include_extractors=false` 时，所有 case 的 `out_parameters=[]`。

当 `include_extractors=true` 时：
- 只提取后续步骤真正需要的变量。
- 变量名只能使用英文、数字、下划线，且建议驼峰命名，如 `entityId`、`entityName`、`latestId`、`token`。
- `out_parameters` 格式固定：
{
  "name": "entityId",
  "expression": "data.list.0.id",
  "source": 1,
  "match_index": "0"
}

source 枚举：
- `0`：响应文本正则
- `1`：响应 JSON
- `2`：响应 Header
- `3`：Cookie
- `4`：HTTP 状态码
- `5`：请求 Body 正则
- `6`：请求 Body JSON
- `7`：请求 Header

表达式规则：
- 响应 JSON 提取优先使用不带 `$` 的表达式，如 `data.id`、`data.list.0.id`、`data.records.0.name`。
- 不要写 `$.data.id`；后端虽会兼容清洗，但标准输出必须用 `data.id`。
- Header/Cookie/RequestHeader 使用 JSONPath 风格时可用 `$..token` 或 `$.authorization`。
- 正则提取必须填写 `match_index`，常用 `0`、`random`、`all`。
- `source=4` 状态码提取不需要表达式，可使用 `expression=""`，变量名如 `httpStatus`。

匹配索引：
- `"0"`：取第一个匹配项。
- `"1"`：取第二个匹配项。
- `"random"`：随机取一个匹配项。
- `"all"`：取全部匹配结果。

## 断言规则
当 `include_asserts=false` 时，所有 case 的 `asserts=[]`。

允许断言类型：
`equal`、`not_equal`、`contain`、`not_contain`、`in`、`not_in`、`length_eq`、`length_gt`、`length_ge`、`length_le`、`length_lt`、`json_equal`、`text_in`、`text_not_in`

禁止输出：
`eq`、`equals`、`contains`、`not_null`、`not_empty`、`exists`

断言格式：
{
  "name": "业务码成功",
  "assert_type": "equal",
  "actually": "${response.code}",
  "expected": "0"
}

断言生成策略：
- 优先基于 `sample_response` 真实字段生成断言。
- 普通成功响应至少生成 2-3 条断言：状态码/业务码、核心 data 字段、业务语义字段。
- 如果实例数据的响应为 `{"code":0,"msg":null,"data":"success","pageFlag":null}`，断言只需要配置 1 条完整响应断言：
{
  "name": "校验内容为全部响应",
  "assert_type": "equal",
  "actually": "${response}",
  "expected": "{\\"code\\":0,\\"msg\\":null,\\"data\\":\\"success\\",\\"pageFlag\\":null}"
}
- 该完整响应断言的含义是：校验内容为全部响应，类型为等于，预期结果为 `{"code":0,"msg":null,"data":"success","pageFlag":null}`，实际结果为 `${response}`。
- 列表接口优先断言列表存在或长度大于 0，如 `${response.data.list}`、`${response.data.records}`。
- 新增/编辑/删除接口优先断言 `code=0`、`data=success`、`msg` 包含成功语义。
- 不要对响应中不存在的字段生成断言。

## CRUD 链路生成规则
当业务目标包含“新增/创建/保存 + 查询/列表/详情 + 删除/移除”时：
- 新增步骤生成唯一名称或编码，优先使用 `${【snowflake_id】}`、`${【rand_4】}`、`${【cur_ymdhms】}` 组合。
- 新增步骤提取可用于查询的变量，如 `entityName`、`entityCode`、`createdId`。
- 查询/列表步骤必须使用新增步骤变量过滤最新数据。
- 查询/列表步骤必须提取删除/编辑需要的 id，如 `entityId`、`latestId`。
- 删除/编辑/详情步骤必须引用 `${entityId}` 或 `${latestId}`，禁止写死 id。
- 如果新增响应直接返回 id，也可以提取 `createdId`；但删除前仍优先通过列表/详情提取最新 id，避免误删。

## 请求参数生成规则
- 保留录制实例中的真实字段名、层级和类型。
- 对明显动态字段做变量化：名称、编码、手机号、id、token、时间。
- 认证信息优先来自业务目标或全局变量，如 `authorization: "Bearer ${token}"`、`tenant-id: "81010000"`。
- 不要把 `Host`、`Content-Length`、`Proxy-Connection` 等录制环境头作为必要业务 header，除非业务目标明确要求。
- POST/PUT/PATCH 的 body 必须是对象，不要输出 JSON 字符串。
- GET/DELETE 参数优先放在 URL query 中。

## 前后置条件说明
平台支持前置/后置构造器类型：用例、SQL、Redis、Python 脚本、HTTP。
但“AI生成流程场景”当前保存的是多个接口 case，不保存构造器。
因此本功能中不要生成可执行构造器；如果需要说明依赖，请写入 `pre_steps`。
跨步骤执行依赖必须通过 `out_parameters` 和 `${变量名}` 实现。

## 最终输出质量要求
- cases 顺序必须与业务流程一致。
- 每个后续步骤使用的变量，必须由前面某个步骤的 `out_parameters` 提供，或是全局/特殊变量。
- 不输出旧语法 `${【caseX】变量}`。
- 不输出不存在的响应路径。
- 不输出单纯只有 `code=0` 的弱断言，除非完整响应断言已经覆盖。
- 不输出解释文字，只输出最终 JSON。', 1);

INSERT INTO argus_gconfig
(created_at, updated_at, deleted_at, create_user, update_user, env, `key`, value, `key_type`, `type`, project_id, case_id, case_name, enable)
VALUES('2026-06-16 19:49:49', '2026-06-17 14:46:37', 0, 1, 1, 0, '__ai_model_config__', '{"active_model_id":"qwen_2","providers":[{"id":"qwen_2","provider_type":"qwen","provider":"qwen","provider_name":"qwen","name":"qwen","base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1","model":"qwen3.6-plus","model_options":["qwen3.6-plus"],"models":["qwen3.6-plus"],"api_key":"sk-f69eb070ffaa4174af6d85b408833aa4","wire_api":"chat_completions","enabled":true}]}', 1, 3, NULL, NULL, NULL, 1);

INSERT INTO argus_project
(created_at, updated_at, deleted_at, create_user, update_user, name, owner, app, description, avatar, dingtalk_url)
VALUES('2026-06-16 19:19:55', '2026-06-17 16:14:05', 0, 1, 1, '测试项目', 2, 'argus-end', '', NULL, NULL);

INSERT INTO argus_functional_case_directory
(created_at, updated_at, deleted_at, create_user, update_user, project_id, name, parent, sort_index)
VALUES('2026-06-16 19:24:58', '2026-06-16 19:24:58', 0, 1, 1, 1, '数据源管理', NULL, 0);

INSERT INTO argus_functional_case_file
(created_at, updated_at, deleted_at, create_user, update_user, project_id, title, directory_id, file_path, case_data, sort_index)
VALUES('2026-06-16 19:25:13', '2026-06-18 09:52:25', 0, 1, 1, 1, '数据源管理（勿动）', 1, '', '{"layout": "logicalStructure", "root": {"data": {"text": "数据源管理（勿动）", "case_uid": "case_1781747103242_ttrucdjl", "expand": true, "uid": "5b08e917-4ae0-4098-8e98-91b62e18d88a", "isActive": false}, "children": [{"data": {"text": "功能用例", "case_uid": "case_1781747103242_uqzye9n8", "uid": "cd2b07d4-cd9c-4aed-b69b-765e977fc086", "expand": true, "isActive": false}, "children": [{"data": {"text": "功能: 查询区", "case_uid": "case_1781747103242_h178ztui", "uid": "d59aca54-8fcf-4845-aaa4-afe8642a872c", "expand": true, "isActive": false}, "children": [{"data": {"text": "子功能: 查询条件", "case_uid": "case_1781747103242_57jyk5a8", "uid": "dd2f2d09-e6a4-4802-bab8-c290563097f7", "expand": true, "isActive": false}, "children": [{"data": {"text": "字段: 数据源名称", "case_uid": "case_1781747103242_lvv97gsw", "uid": "68721e3b-ada0-446c-af84-297580c2a4c5", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 数据源名称输入框展示", "icon": ["priority_3", "progress_8"], "case_uid": "case_1781747103242_jo255pvv", "uid": "924760bd-c55d-4ec1-9ed2-9ea19258c72c", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 文本框无默认值，默认展示占位提示“请输入数据源名称模糊查询”", "case_uid": "case_1781747103242_hksgmjtj", "uid": "d73c26e8-afea-4a58-9567-d093dd7fbeec", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称特殊字符输入", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781747103242_n3kz0gbr", "uid": "1ba09b5d-5acc-426b-8799-a70d456fd20c", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 输入特殊字符可正常显示，且不限制输入", "case_uid": "case_1781747103242_1o9k9ods", "uid": "c0eddc65-8622-402f-8ce2-1c4805de1bad", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称最大长度输入", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781747103242_jt239v33", "uid": "848813c8-2884-4921-b9c8-60d38f457d11", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 输入100个字符可正常输入并显示", "case_uid": "case_1781747103242_22fzpf3p", "uid": "6939e734-db63-47a0-b188-150e67d689be", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称超长输入", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781747103242_5cp5qtr3", "uid": "0373ee14-9ca5-4063-ab88-b7c283dc00bf", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 输入超过100个字符时，超出部分无法输入或被截断", "case_uid": "case_1781747103242_ckdlhy8k", "uid": "33875212-e9fe-4d2f-8a8c-1a65653b9ccc", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称模糊查询", "icon": ["priority_1", "progress_8"], "case_uid": "case_1781747103242_mtw4n8bs", "uid": "3e6d4f89-ac07-4401-90f0-171088b38d42", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 输入部分名称点击查询，列表展示包含该名称的所有数据源记录", "case_uid": "case_1781747103242_zwbbrn7d", "uid": "5978e0b7-c48f-4f44-9e69-3346f31d395a", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 数据源类型", "case_uid": "case_1781747103242_pitme8n0", "uid": "edad341c-fcc8-422c-a1ac-7a9d38375ec2", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 数据源类型下拉框展示", "icon": ["priority_3", "progress_8"], "case_uid": "case_1781747103242_lj7nrr4h", "uid": "0d3b9037-f341-426c-872f-70cf0b824516", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 下拉框无默认值，默认展示占位提示“请选择”，点击展开显示选项", "case_uid": "case_1781747103242_4ib6whod", "uid": "5e7324b7-ea26-4b62-9204-be4b1d69fb58", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 数据源类型选项验证", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781747103242_grr0hbwk", "uid": "909c3268-8d6a-4f29-8720-6f71d4a0a51b", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 下拉选项包含“Mysql”和“StarRocks”", "case_uid": "case_1781747103242_8cf5wm63", "uid": "8218be6b-23d6-4481-8479-d7d530791a9a", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 数据源类型模糊筛选", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781747103242_aa3zax53", "uid": "3a65430b-c8c1-4167-9184-cc05e5a6cde0", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 在下拉框内输入关键字（如“y”），可筛选出匹配的选项（如“Mysql”）", "case_uid": "case_1781747103242_32ily9q6", "uid": "424a6558-47b2-4612-930c-179c0ad0ce30", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 数据源类型查询", "icon": ["priority_1", "progress_8"], "case_uid": "case_1781747103242_bp26dlp5", "uid": "b0170ba4-eb33-4155-ae0e-296a73134da7", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 选择具体类型点击查询，列表仅展示该类型的数据源记录", "case_uid": "case_1781747103242_5uzqsz3h", "uid": "e9837dd0-03c3-42bf-b6f1-4467dfc15021", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 查询按钮", "case_uid": "case_1781747103242_i9msyopq", "uid": "11141463-be70-498a-9758-9092f0602aa6", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 查询按钮展示与点击", "icon": ["priority_1"], "case_uid": "case_1781747103242_89tmd0jm", "uid": "eb77debb-f5d0-4698-9f14-1964d1ebda5c", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 按钮展示“查询”，点击后根据输入条件刷新列表数据", "case_uid": "case_1781747103242_4gzeslq5", "uid": "21012104-1d15-4ed3-8579-78b55007a17e", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 重置按钮", "case_uid": "case_1781747103242_mffryio4", "uid": "17bca713-9833-4ac2-afb7-632d1ebf9f1e", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 重置按钮展示与点击", "icon": ["priority_2"], "case_uid": "case_1781747103242_vzaz2dwn", "uid": "a3716060-88a5-43be-9060-1efee2782fc1", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 按钮展示“重置”，点击后清空查询条件，列表恢复默认展示", "case_uid": "case_1781747103242_drlk99t8", "uid": "e81ddf80-8fe8-44f3-b424-b18897315a3d", "expand": true, "isActive": false}, "children": []}]}]}]}]}, {"data": {"text": "功能: 列表区", "case_uid": "case_1781747103242_q39zsdke", "uid": "b4d71405-c048-4aa4-abdd-b7239af98c61", "expand": true, "isActive": false}, "children": [{"data": {"text": "子功能: 列表展示", "case_uid": "case_1781747103242_dp1yq843", "uid": "9af466a3-53f3-4f3c-9cd0-c33bd1c9e913", "expand": true, "isActive": false}, "children": [{"data": {"text": "字段: 列表表头", "case_uid": "case_1781747103242_ropky600", "uid": "891e923c-5e47-4ced-a225-f8464d3c4be7", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 列表表头展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_pnjnmuyx", "uid": "0f717fee-24e0-4873-8071-1693313ef904", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 依次展示“数据源名称、数据源类型、数据源描述、责任人、更新时间、配置信息、操作”", "case_uid": "case_1781747103242_5x90yho4", "uid": "a28b26af-11e5-4532-a159-718e73c776f8", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 数据源名称", "case_uid": "case_1781747103242_ra06hp2t", "uid": "e6f8ec7e-9c7f-471e-b7b0-2da47e5992de", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 数据源名称展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_tx392dil", "uid": "842e47bf-991c-4c7f-b3ab-603493a4e4d1", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 文本标签展示字段值，内容过长时支持换行展示", "case_uid": "case_1781747103242_ckjc4x6w", "uid": "1999dfcf-f95a-48bc-b0d3-0f9ef3d89f7a", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 数据源类型", "case_uid": "case_1781747103242_upgmydew", "uid": "e38e70d7-3e5f-4b81-bd88-780c361dfb1e", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 数据源类型展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_ji9gitdj", "uid": "33239253-2ccc-4487-9683-628beec96e1d", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 文本标签展示“Mysql”或“StarRocks”", "case_uid": "case_1781747103242_ay4ut5p1", "uid": "b974749e-13be-4138-a397-7905cbb5f142", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 数据源描述", "case_uid": "case_1781747103242_2b9pr5zc", "uid": "96338a15-3ccd-4940-82f9-4d31cf4c9a88", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 数据源描述展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_a78mi3xl", "uid": "7cdea101-fa31-47c7-ac30-8319747bca95", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 文本标签展示字段值，内容过长时支持换行展示", "case_uid": "case_1781747103242_0e86qyyp", "uid": "faa9f6a3-3467-4a9c-8486-072559839aa9", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 责任人", "case_uid": "case_1781747103242_lz9nn510", "uid": "8b1ab025-6daf-4988-b3d1-c300df61964a", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 责任人展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_zjadp6pr", "uid": "1f825d5b-6fd0-44df-bfa6-c1b1304889be", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 文本标签展示责任人姓名", "case_uid": "case_1781747103242_jh6xl7br", "uid": "75f4bc21-4e5c-41aa-a00b-26ce76b11025", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 更新时间", "case_uid": "case_1781747103242_l0do86zu", "uid": "12bfca40-a656-4eb0-8e0b-d12c7cd19e83", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 更新时间展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_u98nx6r3", "uid": "1a20750e-095e-4739-8a94-b35f60a05857", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 文本标签展示具体时间，格式如“2025-05-13 13:47:32”", "case_uid": "case_1781747103242_8953iten", "uid": "31225842-86b3-439d-bd56-0cce8c60f3df", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 配置信息", "case_uid": "case_1781747103242_pk5lxjkg", "uid": "5867992b-07c4-4374-bd9c-fee7537f9527", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 配置信息文本展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_xjlva8ks", "uid": "b56ac478-0052-4650-9703-c678aca475ef", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 展示文字链接“查看配置信息”", "case_uid": "case_1781747103242_03649zbq", "uid": "7fb6dca6-d18f-48a7-ad60-bf68cca028ca", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 配置信息悬浮展示", "icon": ["priority_2"], "case_uid": "case_1781747103242_m727htak", "uid": "04dd8e44-6ebf-4cbe-9084-aca6c21381ec", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 鼠标悬浮在“查看配置信息”上，弹框展示相应的配置详细信息", "case_uid": "case_1781747103242_jaqqxw2z", "uid": "b829fd66-803f-42b5-a13a-c71fa7964125", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 配置信息移出隐藏", "icon": ["priority_2"], "case_uid": "case_1781747103242_10x7l3y0", "uid": "1eee15fe-c067-4abf-9ccc-d8d07119a8e8", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 鼠标移出弹框区域后，配置信息弹框自动消失", "case_uid": "case_1781747103242_wa5bgnns", "uid": "8dab41e5-981a-4e82-8a54-d550cb660312", "expand": true, "isActive": false}, "children": []}]}]}, {"data": {"text": "字段: 操作列", "case_uid": "case_1781747103242_afkjpcr1", "uid": "309a5093-e74e-4462-aa2d-2fa3b359cc0a", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 操作列按钮展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_3jjidfcf", "uid": "d5b799fa-310a-4d97-90b4-422190cca3fa", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 每条数据操作列展示“删除”、“编辑”、“元数据查看”按钮（基于截图补充）", "case_uid": "case_1781747103242_05iz8trp", "uid": "7fa4cd8a-a445-4aea-9363-88857bf43e3f", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 元数据查看按钮点击", "icon": ["priority_1"], "case_uid": "case_1781747103242_xafckekn", "uid": "d2255136-0279-4105-a23f-fde651ef82cb", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 点击“元数据查看”按钮，弹框展示该数据源的具体元数据信息", "case_uid": "case_1781747103242_76i7fmzo", "uid": "240f1549-265e-4ec8-8806-981f6bba538e", "expand": true, "isActive": false}, "children": []}]}]}]}]}, {"data": {"text": "功能: 新增功能", "case_uid": "case_1781747103242_fgpouftc", "uid": "f709f267-762c-4fdb-8acd-7e344295e315", "expand": true, "isActive": false}, "children": [{"data": {"text": "子功能: 新增入口", "case_uid": "case_1781747103242_rra2ds50", "uid": "942da62a-41a1-45fd-a09d-05e84ccc3a02", "expand": true, "isActive": false}, "children": [{"data": {"text": "字段: 新增数据源按钮", "case_uid": "case_1781747103242_wptz5mq1", "uid": "01419b16-b2d8-47c2-b837-5d82ca5324be", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 新增数据源按钮展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_maiqxmja", "uid": "a46af9a7-8baf-401f-bea4-c8967bf8de9d", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 列表右上角展示蓝色“新增数据源”按钮（基于截图补充）", "case_uid": "case_1781747103242_sg91xys1", "uid": "d173ddf5-5367-43b4-b669-04ef3a159e1d", "expand": true, "isActive": false}, "children": []}]}]}]}]}, {"data": {"text": "功能: 分页区", "case_uid": "case_1781747103242_m59n05fp", "uid": "12b71539-a835-4e38-a8b7-c0f1a0022b3a", "expand": true, "isActive": false}, "children": [{"data": {"text": "子功能: 分页控件", "case_uid": "case_1781747103242_au9b9j3r", "uid": "21d92f90-b756-4f2f-8c8d-05472bc0a454", "expand": true, "isActive": false}, "children": [{"data": {"text": "字段: 分页展示", "case_uid": "case_1781747103242_qii32qmm", "uid": "f8462525-672c-4ec3-94b6-cad2f3a9de25", "expand": true, "isActive": false}, "children": [{"data": {"text": "用例名称: 分页默认展示", "icon": ["priority_3"], "case_uid": "case_1781747103242_e8ndr6te", "uid": "cabb05b4-0fa2-470d-8011-ee9b5889965e", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 列表默认每页展示10条数据，底部显示总记录数及页码信息", "case_uid": "case_1781747103242_nf1mp504", "uid": "51edd9a2-8e90-477f-bf55-029c1d0228b2", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 列表默认排序", "icon": ["priority_1"], "case_uid": "case_1781747103242_gmcqvexx", "uid": "2310e6cc-d1a9-4b69-ac58-0320ad585c13", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 列表数据默认按“更新时间”倒序排列，最新的数据展示在最上方", "case_uid": "case_1781747103242_j0z4w4ft", "uid": "121e33d5-7724-4ec9-a0b2-0c687ac7da53", "expand": true, "isActive": false}, "children": []}]}, {"data": {"text": "用例名称: 分页切换", "icon": ["priority_2"], "case_uid": "case_1781747103242_5r3o3fgw", "uid": "2a3cd01d-4240-4dc1-bdf0-63de1b455db2", "expand": true, "isActive": false}, "children": [{"data": {"text": "预期: 点击页码或上下页按钮，列表数据正确切换，且保持每页10条", "case_uid": "case_1781747103242_dl9oxeo6", "uid": "f7c235f0-e9fc-4510-b9d7-e82851cf3e72", "expand": true, "isActive": false}, "children": []}]}]}]}]}]}, {"data": {"text": "UI自动化用例", "case_uid": "case_1781747145029_nfyun4gt", "expand": true, "isActive": false, "uid": "619cf455-2a6f-4a63-bdd0-722013935d2b"}, "children": [{"data": {"text": "场景配置", "case_uid": "case_1781747145029_s0bvi2c2", "expand": true, "isActive": false, "uid": "d5a1d2c0-1d92-4274-aab0-05da617f38b6"}, "children": [{"data": {"text": "渠道: web", "case_uid": "case_1781747145029_a0k1bemc", "expand": true, "isActive": false, "uid": "f5a80a53-6e0e-4e7a-9d6b-2d35eac640be"}, "children": []}, {"data": {"text": "浏览器: chromium", "case_uid": "case_1781747145029_1303u8ao", "expand": true, "isActive": false, "uid": "614c166c-fa8d-44ca-aba7-f7210127fe08"}, "children": []}, {"data": {"text": "用户名: test1", "case_uid": "case_1781747145029_hp4az6y7", "expand": true, "isActive": false, "uid": "b7ac7027-0626-42da-a179-2613f1f63e38"}, "children": []}, {"data": {"text": "密码: 123456", "case_uid": "case_1781747145029_rleu8olt", "expand": true, "isActive": false, "uid": "44e0b6fa-fbd3-457b-b0ed-cba8923f19ba"}, "children": []}]}, {"data": {"text": "成功登录", "case_uid": "case_1781747145029_u76koflb", "expand": true, "isActive": false, "uid": "8eb6cdd9-1d4b-4a34-b086-3a9ea681a88b"}, "children": [{"data": {"text": "测试步骤", "case_uid": "case_1781747145029_4mcn3i7i", "expand": true, "isActive": false, "uid": "b174a07d-df75-4db9-9a8f-9d54562451eb"}, "children": [{"data": {"text": "1 打开 ${base_url}/#/user/login", "case_uid": "case_1781747145029_kthfmuj5", "expand": true, "isActive": false, "uid": "c5918997-0dde-4f7e-8a0e-61bebd47654d"}, "children": []}, {"data": {"text": "2 输入 用户名 ${用户名}", "case_uid": "case_1781747145029_f0c10spq", "expand": true, "isActive": false, "uid": "25054d02-cb2b-402c-a4be-ec1efbeedc3b"}, "children": []}, {"data": {"text": "3 输入 密码 ${密码}", "case_uid": "case_1781747145029_sibued22", "expand": true, "isActive": false, "uid": "f80e1e5a-00b1-4272-ab43-8ef0fda08d41"}, "children": []}, {"data": {"text": "4 点击 登录按钮", "case_uid": "case_1781747145029_7r1r9hdb", "expand": true, "isActive": false, "uid": "cee5e1f8-c58b-4f84-ab14-a8797abfc587"}, "children": []}, {"data": {"text": "5 等待出现 Dashboard", "case_uid": "case_1781747145029_p3iwc9yq", "expand": true, "isActive": false, "uid": "375729ad-32f3-482c-b1ab-aefe31f0df45"}, "children": []}, {"data": {"text": "6 提取 用户名称 => 测试人员1", "case_uid": "case_1781747145029_vnfkkenx", "expand": true, "isActive": false, "uid": "1d79265c-0048-4299-b462-2504d52ad799"}, "children": []}, {"data": {"text": "7 截图 登录成功页", "case_uid": "case_1781747145029_blifb6lj", "expand": true, "isActive": false, "uid": "7b653e55-4c92-4a31-ac13-47b1ca766939"}, "children": []}]}, {"data": {"text": "执行断言", "case_uid": "case_1781747145029_l41ktrw4", "expand": true, "isActive": false, "uid": "c4af1add-6bad-468d-8d16-1259722f8c4a"}, "children": [{"data": {"text": "页面包含: 项目管理", "case_uid": "case_1781747145029_jf71otcx", "expand": true, "isActive": false, "uid": "4714d4d0-e61f-4cf1-ad2f-4056aba53700"}, "children": []}, {"data": {"text": "元素存在: 接口测试", "case_uid": "case_1781747145029_hbcz3dm8", "expand": true, "isActive": false, "uid": "9afbb785-47c6-498e-b468-58e4d386d985"}, "children": []}]}]}, {"data": {"text": "退出登录", "case_uid": "case_1781747145029_i7puki53", "expand": true, "isActive": false, "uid": "844cf818-f211-4792-87d8-ba918c16454c"}, "children": [{"data": {"text": "测试步骤", "case_uid": "case_1781747145029_mathsa5q", "expand": true, "isActive": false, "uid": "e53da085-da83-4d09-9821-3da843556e47"}, "children": [{"data": {"text": "1 打开 ${base_url}/#/dashboard/workspace", "case_uid": "case_1781747145029_6kyf5kl5", "expand": true, "isActive": false, "uid": "12fb227b-dd73-4128-ac11-c6fcf8891e56"}, "children": []}, {"data": {"text": "2 点击 测试人员1", "case_uid": "case_1781747145029_mkiop0jj", "expand": true, "isActive": false, "uid": "85470f04-4506-408a-949d-c10a98d8686f"}, "children": []}, {"data": {"text": "3 等待出现 退出登录", "case_uid": "case_1781747145029_wk7bcid4", "expand": true, "isActive": false, "uid": "33341bce-34bf-40b3-98fe-3fe1126c3398"}, "children": []}, {"data": {"text": "4 点击 退出登录按钮", "case_uid": "case_1781747145029_pgmdzqca", "expand": true, "isActive": false, "uid": "a74afb1a-e4eb-49bb-a2c3-b0522b38d572"}, "children": []}, {"data": {"text": "5 等待出现 立即注册", "case_uid": "case_1781747145029_lylq1iat", "expand": true, "isActive": false, "uid": "797a5d44-2051-44d8-b719-3473dd1e98cc"}, "children": []}, {"data": {"text": "6 截图 退出登录页", "case_uid": "case_1781747145029_5xcafigb", "expand": true, "isActive": false, "uid": "2e530a44-c2ac-49de-bb0d-73d118e64a28"}, "children": []}]}, {"data": {"text": "执行断言", "case_uid": "case_1781747145029_jxjj83sb", "expand": true, "isActive": false, "uid": "48610308-2d18-4c9e-9ed9-90ecc193d31e"}, "children": [{"data": {"text": "页面包含: 立即注册", "case_uid": "case_1781747145029_xw2d11vn", "expand": true, "isActive": false, "uid": "e69f1314-2d09-4a2d-9dc5-bde490e2ea3c"}, "children": []}, {"data": {"text": "元素存在: 欢迎来到", "case_uid": "case_1781747145029_2qmhedwf", "expand": true, "isActive": false, "uid": "409af56f-a9ac-4ba7-9147-e72a88d634fe"}, "children": []}]}]}]}], "smmVersion": "0.14.0-fix.2"}, "theme": {"template": "default", "config": {}}}', 0);

INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_jo255pvv', '数据源管理（勿动）', '用例名称: 数据源名称输入框展示', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源名称 / 用例名称: 数据源名称输入框展示', '3', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_n3kz0gbr', '数据源管理（勿动）', '用例名称: 数据源名称特殊字符输入', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源名称 / 用例名称: 数据源名称特殊字符输入', '2', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_jt239v33', '数据源管理（勿动）', '用例名称: 数据源名称最大长度输入', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源名称 / 用例名称: 数据源名称最大长度输入', '2', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_5cp5qtr3', '数据源管理（勿动）', '用例名称: 数据源名称超长输入', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源名称 / 用例名称: 数据源名称超长输入', '2', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_mtw4n8bs', '数据源管理（勿动）', '用例名称: 数据源名称模糊查询', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源名称 / 用例名称: 数据源名称模糊查询', '1', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_lj7nrr4h', '数据源管理（勿动）', '用例名称: 数据源类型下拉框展示', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源类型 / 用例名称: 数据源类型下拉框展示', '3', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_grr0hbwk', '数据源管理（勿动）', '用例名称: 数据源类型选项验证', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源类型 / 用例名称: 数据源类型选项验证', '2', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_aa3zax53', '数据源管理（勿动）', '用例名称: 数据源类型模糊筛选', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源类型 / 用例名称: 数据源类型模糊筛选', '2', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_bp26dlp5', '数据源管理（勿动）', '用例名称: 数据源类型查询', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 数据源类型 / 用例名称: 数据源类型查询', '1', 1);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_89tmd0jm', '数据源管理（勿动）', '用例名称: 查询按钮展示与点击', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 查询按钮 / 用例名称: 查询按钮展示与点击', '1', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_vzaz2dwn', '数据源管理（勿动）', '用例名称: 重置按钮展示与点击', '数据源管理（勿动） / 功能用例 / 功能: 查询区 / 子功能: 查询条件 / 字段: 重置按钮 / 用例名称: 重置按钮展示与点击', '2', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_pnjnmuyx', '数据源管理（勿动）', '用例名称: 列表表头展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 列表表头 / 用例名称: 列表表头展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_tx392dil', '数据源管理（勿动）', '用例名称: 数据源名称展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 数据源名称 / 用例名称: 数据源名称展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_ji9gitdj', '数据源管理（勿动）', '用例名称: 数据源类型展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 数据源类型 / 用例名称: 数据源类型展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_a78mi3xl', '数据源管理（勿动）', '用例名称: 数据源描述展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 数据源描述 / 用例名称: 数据源描述展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_zjadp6pr', '数据源管理（勿动）', '用例名称: 责任人展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 责任人 / 用例名称: 责任人展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_u98nx6r3', '数据源管理（勿动）', '用例名称: 更新时间展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 更新时间 / 用例名称: 更新时间展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_xjlva8ks', '数据源管理（勿动）', '用例名称: 配置信息文本展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 配置信息 / 用例名称: 配置信息文本展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_m727htak', '数据源管理（勿动）', '用例名称: 配置信息悬浮展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 配置信息 / 用例名称: 配置信息悬浮展示', '2', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_10x7l3y0', '数据源管理（勿动）', '用例名称: 配置信息移出隐藏', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 配置信息 / 用例名称: 配置信息移出隐藏', '2', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_3jjidfcf', '数据源管理（勿动）', '用例名称: 操作列按钮展示', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 操作列 / 用例名称: 操作列按钮展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_xafckekn', '数据源管理（勿动）', '用例名称: 元数据查看按钮点击', '数据源管理（勿动） / 功能用例 / 功能: 列表区 / 子功能: 列表展示 / 字段: 操作列 / 用例名称: 元数据查看按钮点击', '1', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_maiqxmja', '数据源管理（勿动）', '用例名称: 新增数据源按钮展示', '数据源管理（勿动） / 功能用例 / 功能: 新增功能 / 子功能: 新增入口 / 字段: 新增数据源按钮 / 用例名称: 新增数据源按钮展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_e8ndr6te', '数据源管理（勿动）', '用例名称: 分页默认展示', '数据源管理（勿动） / 功能用例 / 功能: 分页区 / 子功能: 分页控件 / 字段: 分页展示 / 用例名称: 分页默认展示', '3', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_gmcqvexx', '数据源管理（勿动）', '用例名称: 列表默认排序', '数据源管理（勿动） / 功能用例 / 功能: 分页区 / 子功能: 分页控件 / 字段: 分页展示 / 用例名称: 列表默认排序', '1', 0);
INSERT INTO argus_functional_case_item
(created_at, updated_at, deleted_at, create_user, update_user, project_id, directory_id, file_id, case_uid, file_title, case_name, case_path, case_priority, case_pass)
VALUES('2026-06-18 09:45:13', '2026-06-18 09:52:25', 0, 1, 1, 1, 1, 1, 'case_1781747103242_5r3o3fgw', '数据源管理（勿动）', '用例名称: 分页切换', '数据源管理（勿动） / 功能用例 / 功能: 分页区 / 子功能: 分页控件 / 字段: 分页展示 / 用例名称: 分页切换', '2', 0);

COMMIT;
