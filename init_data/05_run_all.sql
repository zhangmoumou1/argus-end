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
VALUES('2026-06-16 19:25:13', '2026-06-17 17:24:29', 0, 1, 1, 1, '数据源管理（勿动）', 1, '', '{"layout": "logicalStructure", "root": {"data": {"text": "数据源管理", "case_uid": "case_1781610643964_8179m1x3", "expand": true, "uid": "768a2ec8-7e31-449c-aae2-90d176717327", "isActive": false}, "children": [{"data": {"text": "模块: 数据源管理", "case_uid": "case_1781610643964_egbaws6m", "uid": "aa2d7bc1-4f85-4055-a770-c7f03f309388", "expand": false, "isActive": false}, "children": [{"data": {"text": "功能: 数据源列表", "case_uid": "case_1781610643964_lm89h53n", "uid": "5d01c622-0160-453a-8881-8be11f7a54a8", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "子功能: 查询区", "case_uid": "case_1781610643964_jlyep3nb", "uid": "5a999558-69c9-427b-a1d1-9638a42acbc8", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 数据源名称", "case_uid": "case_1781610643964_bidg2r2h", "uid": "a187886b-98ae-4c9f-97ed-a17cb985fb69", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 数据源名称输入框展示", "icon": ["priority_3", "progress_8"], "case_uid": "case_1781610643964_ojluschf", "uid": "51c33050-aa17-48e7-a295-cee4bf76cf3a", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，无默认值，占位提示文案为“请输入数据源名称模糊查询”", "case_uid": "case_1781610643964_rwlnor9l", "uid": "a0561d5b-6189-464f-a2dc-c91b833de7fa", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称模糊查询成功", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781610643964_63segmtd", "uid": "9092e050-8698-467f-a47c-2b9d4c08f369", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入已存在数据源名称的部分字符，点击查询，列表展示名称包含该字符的所有数据源", "case_uid": "case_1781610643964_8nzn0fij", "uid": "714dfb5f-28ff-4717-baed-115c4270e398", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称查询无结果", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781610643964_yugqt6am", "uid": "f0400b1c-f92f-48f7-8b88-4a39b6a530b2", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入不存在的名称，点击查询，列表展示空数据状态或“暂无数据”类反馈", "case_uid": "case_1781610643964_oyjiucwt", "uid": "db5d0ae9-8f51-4b80-8816-afe0054bea64", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称输入长度上限", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781610643964_09va3e1y", "uid": "4df2682c-c483-4eef-aa84-032199f09c8d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入100字符后可正常输入，点击查询功能正常", "case_uid": "case_1781610643964_ynwyzjvi", "uid": "0f03aac2-5207-477c-b054-1ecab4530d45", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称输入长度超限", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781610643964_p1u2zjtx", "uid": "263b6961-d64a-4792-a097-bbecd043074c", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过100字符时，输入框不再允许输入或截断", "case_uid": "case_1781610643964_ap36enra", "uid": "44cefa30-348e-490a-be1c-757c4a5a6eb7", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 数据源类型", "case_uid": "case_1781610643964_q62xfwrz", "uid": "20fe6a83-0dcf-489f-8a64-a48fcbc5cba8", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 数据源类型下拉框展示", "icon": ["priority_3", "progress_8"], "case_uid": "case_1781610643964_ot0fzqmf", "uid": "d88bd728-7d05-4f24-bac0-4600aaee22e1", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示下拉单选框，无默认值，占位文案为“请选择”，下拉选项包含“Mysql”和“StarRocks”", "case_uid": "case_1781610643965_k2r0dch8", "uid": "201eb367-6a1a-47d4-b82c-ebd5061903b5", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源类型下拉框筛选", "icon": ["priority_2", "progress_8"], "case_uid": "case_1781610643965_lam3tsk1", "uid": "2090262f-e298-4543-a3d2-e6db17c9f670", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 在下拉框中输入“Mysql”或“StarRocks”的部分字符，可筛选出匹配的选项", "case_uid": "case_1781610643965_m13ajkna", "uid": "135fc173-69e1-464d-957d-deb43c0276a8", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 按类型查询成功", "icon": ["priority_1", "progress_8"], "case_uid": "case_1781610643965_aqrylsvc", "uid": "c6f8f6a6-2174-492c-91d4-4a324302eaaa", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 选择“Mysql”，点击查询，列表仅展示类型为Mysql的数据源", "case_uid": "case_1781610643965_kohn0hh6", "uid": "87706c0c-3995-4963-a3fe-db90000d63ff", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 组合查询成功", "icon": ["priority_1", "progress_8"], "case_uid": "case_1781610643965_1tgog1nc", "uid": "0e81e86c-be43-43fb-aa3f-19d57d4ce154", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入数据源名称并选择类型，点击查询，列表展示同时满足两个条件的数据源", "case_uid": "case_1781610643965_rkw6lm9m", "uid": "b5ddb15c-9c57-4371-affe-f45a8774ad6d", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}, {"data": {"text": "子功能: 列表区", "case_uid": "case_1781610643965_u4nle8wt", "uid": "cab8cba0-1e59-400a-ad6c-877cc566e23a", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 列表表头", "case_uid": "case_1781610643965_1k9mfsvp", "uid": "01614bd2-0382-4c62-aa6f-91ba9e0a54ee", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 列表表头展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_aakp8rtp", "uid": "f9917280-c05a-4672-9ec4-e90d26541884", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 按序展示：数据源名称、数据源类型、数据源描述、责任人、更新时间、配置信息、操作", "case_uid": "case_1781610643965_adnadfpf", "uid": "b0f0451f-8b25-4958-8aa4-c1e293f937c1", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 列表数据", "case_uid": "case_1781610643965_9ds4e96s", "uid": "0ebf174d-3b06-4305-a86a-2e78276d2504", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 列表数据展示", "icon": ["priority_2"], "case_uid": "case_1781610643965_ivaz1tr4", "uid": "e7a8ceaf-9f0f-45ba-bec7-ee1eaf99bfb2", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 数据源名称、数据源描述、数据源类型、责任人、更新时间均以文本标签展示对应字段值，名称和描述支持换行", "case_uid": "case_1781610643965_k3h1niq6", "uid": "83077566-d20d-4316-90e6-bc37f85e1f7a", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 列表数据排序校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_9k0ilo01", "uid": "c2985497-c09b-4227-9c2b-824f5072cea3", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 列表数据默认按“更新时间”字段倒序排列", "case_uid": "case_1781610643965_ryiuh3td", "uid": "8a6d85de-babe-49dd-8fc9-46a813658e4b", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 配置信息", "case_uid": "case_1781610643965_taifh4wp", "uid": "d8ab863c-f50e-49f1-9d8f-913877bc93fb", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 配置信息悬浮展示", "icon": ["priority_2"], "case_uid": "case_1781610643965_dvw3qh58", "uid": "55ba4137-1005-46be-8c96-15ccded7b8ee", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 鼠标悬浮在“查看配置信息”文字上，弹框展示当前行的详细配置信息；鼠标移出，弹框消失", "case_uid": "case_1781610643965_ttwkxfrh", "uid": "f27ca5fc-f1d7-4b2a-9b05-f448d03bd446", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 元数据查看", "case_uid": "case_1781610643965_8tvx0d4j", "uid": "d3c109f6-a591-4e3e-8763-7b095e6b5aec", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 元数据查看按钮点击", "icon": ["priority_1"], "case_uid": "case_1781610643965_vg2tatz7", "uid": "9b072646-195f-46a3-82b0-ee59384d055f", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 点击“元数据查看”按钮，弹框展示元数据详情", "case_uid": "case_1781610643965_asivoks4", "uid": "48943471-c8c8-4c6c-87af-25feac915d1a", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 分页", "case_uid": "case_1781610643965_ggkzewkt", "uid": "a36de3cb-27bc-4555-beae-cea5d0c3ffe4", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 分页功能", "icon": ["priority_2"], "case_uid": "case_1781610643965_jjfxke14", "uid": "efcf082f-4c39-430b-b9bb-0b079d7fc380", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 数据总条数超过10条时，底部分页组件展示正常，默认每页展示10条", "case_uid": "case_1781610643965_uskpg1yb", "uid": "bf6141c0-370b-456a-94a0-39042e6cff26", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}]}, {"data": {"text": "功能: 新增数据源", "case_uid": "case_1781610643965_snyjqxdk", "uid": "308210a1-a887-4a1a-b125-e62cb03ed7ca", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "子功能: 第一步-选择数据源类型", "case_uid": "case_1781610643965_g2c69t3k", "uid": "c4d96210-4709-40d1-9585-0ee289a30175", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 分段器Tab", "case_uid": "case_1781610643965_bfdlr6c8", "uid": "855f781f-6c8b-40b6-9c05-fdde688e8871", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 分段器Tab展示与数量统计", "icon": ["priority_2"], "case_uid": "case_1781610643965_ut4a17zk", "uid": "fdf29c30-a49a-4882-bf22-f6ab3494d12b", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示全部(n)、关系型数据库(n)、大数据存储(n)三个分段器tab，n为当前分类下可选数据源的数量", "case_uid": "case_1781610643965_ulzwphvh", "uid": "1365555f-a13c-4fa4-913e-861e4cb20240", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源卡片展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_6khamr0p", "uid": "6ad5ce9e-7831-47ae-bf99-cda6096ac4d5", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 分段器下每行展示4个数据源卡片，关系型数据库展示Mysql，大数据存储展示StarRocks", "case_uid": "case_1781610643965_ecej1zdn", "uid": "9a3d9d20-d8f4-499e-be0f-bb84631b476a", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 选中数据源类型", "icon": ["priority_1"], "case_uid": "case_1781610643965_5m10xc3l", "uid": "fb2c97a7-c1ed-4b42-a08c-913709617545", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 点击数据源卡片，展示选中状态", "case_uid": "case_1781610643965_5dz6q63j", "uid": "bebaf2ca-9ace-4d0b-8bd5-9d584be4b33f", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 下一步按钮", "case_uid": "case_1781610643965_vo78c8x3", "uid": "2a267b87-23e1-4b15-a2b9-12f2d145adc9", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 未选择数据源点击下一步", "icon": ["priority_1"], "case_uid": "case_1781610643965_48t6n05y", "uid": "c22e18fe-6817-43b5-ba0c-ad8eba4e4201", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 未选择任何数据源类型，点击“下一步”时给出提示或不允许跳转", "case_uid": "case_1781610643965_pjduui22", "uid": "4f1da918-f542-4706-8364-0cf332495665", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 已选择数据源点击下一步", "icon": ["priority_1"], "case_uid": "case_1781610643965_4h5cv1rp", "uid": "d1080b30-8c66-4acb-ab51-6604fbfb318f", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 选择数据源类型后，点击“下一步”，弹框切换到第二步，展示配置参数界面", "case_uid": "case_1781610643965_qli9r1g5", "uid": "f94b194a-a1b8-49ee-bd6f-9927934ba035", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 取消按钮", "case_uid": "case_1781610643965_w1phumrq", "uid": "b8a8ec11-cf42-400d-b186-db26baa359cc", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 第一步点击取消", "icon": ["priority_1"], "case_uid": "case_1781610643965_dinv40oy", "uid": "b2fbe7ea-c388-4435-9292-05cd5e830d80", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 点击“取消”按钮，关闭新增数据源弹框", "case_uid": "case_1781610643965_u7jkkmg2", "uid": "c843062b-7709-4ec3-b1dd-191e25e1eec6", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}, {"data": {"text": "子功能: 第二步-配置参数", "case_uid": "case_1781610643965_mk8alcc4", "uid": "6b21fd9c-e0b1-4cf7-9965-130295f6fd7a", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 数据源名称", "case_uid": "case_1781610643965_nr06ym7u", "uid": "ab7fa14f-800b-4863-9cb2-4c6475d2bc3a", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 数据源名称输入框展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_y7cppx8o", "uid": "b8a7583b-8ba1-4128-a630-b08ddbc9758f", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，无默认值，占位提示为“数据源名称唯一，以字母开头并与数字及下划线结合”，最大输入100字符", "case_uid": "case_1781610643965_zmlhp0y4", "uid": "80381ebd-9c5d-4bf4-a409-42d977398e22", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称必填校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_7gjhht04", "uid": "2d9357db-9703-4a28-8a4d-402bdb85f7d2", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 提交时若字段为空，给出必填提示", "case_uid": "case_1781610643965_0dwiuwal", "uid": "fe961bb6-f813-474f-890f-176eeae78ef8", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称长度超限校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_nyfmm7lj", "uid": "d79f8107-a58a-4912-a2fe-b2a4c91b1d1a", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过100字符时，超出部分无法输入或触发限制", "case_uid": "case_1781610643965_hb24gy5v", "uid": "6e331073-67bf-473f-978e-414eb0095a23", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源名称唯一性校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_yh9vyz9m", "uid": "87b8cce7-0166-47db-96cb-78e0aac4a3f7", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入已存在的数据源名称并提交，保存失败并提示名称已存在或唯一性错误", "case_uid": "case_1781610643965_slkohupi", "uid": "84a6ed21-a758-4c2e-a75f-79f3541f7c09", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 数据源类型", "case_uid": "case_1781610643965_wjtihtzv", "uid": "9b1ec13e-6c1a-4acd-b75a-b24837cd04fc", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 数据源类型文本展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_1l4vj08u", "uid": "e295186e-edd6-4e94-a78c-7fa7d43eec6a", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 文本标签展示第一步所选的数据源类型值，不可编辑", "case_uid": "case_1781610643965_oh3d01xi", "uid": "c9667f81-36d5-453d-9b1e-04ac7605f4fe", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 数据源描述", "case_uid": "case_1781610643965_trxskip4", "uid": "44ff64f3-3e6e-4692-9d12-b6040374a7ba", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 数据源描述输入框展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_88k2brb5", "uid": "bff4dbd0-5adf-41bb-8a86-541709f1ab0e", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，无默认值，占位提示为“请输入数据源描述”，最大输入200字符", "case_uid": "case_1781610643965_uiky5587", "uid": "4ba94bb9-b094-48c2-b781-4af9f002c859", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源描述非必填提交", "icon": ["priority_2"], "case_uid": "case_1781610643965_ac8lushw", "uid": "98661068-4964-49e0-b66e-77faeddd2230", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 描述为空时，可以成功新增数据源", "case_uid": "case_1781610643965_02uz2gw1", "uid": "56c68230-e2a1-4a6a-a400-9bbb4120a271", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据源描述长度超限校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_j6cuy9or", "uid": "08fe93d9-3f72-4346-890f-b95937cb63f8", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过200字符，超出部分无法输入或触发限制", "case_uid": "case_1781610643965_kjmy5z6o", "uid": "43132099-4264-4000-b14b-33bcb9736561", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 责任人", "case_uid": "case_1781610643965_d0zrbyhw", "uid": "94a7cf49-2e30-473b-8e1d-cba058a37b02", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 责任人输入框展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_knd2seyd", "uid": "da54e185-8a3d-41c2-865e-18bd49ccc62a", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，无默认值，占位提示为“请填写责任人”，最大输入100字符", "case_uid": "case_1781610643965_1xmj207a", "uid": "5c2019ae-a1fc-4280-a97d-741f7ec4b7e5", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 责任人必填校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_5wkhk1r0", "uid": "77c745a6-4843-4959-994d-d87bc96866d8", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 提交时若字段为空，给出必填提示", "case_uid": "case_1781610643965_owi9pn4t", "uid": "67f386f9-d588-4c62-8185-ac13cf8cdb5f", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 责任人长度超限校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_bs34ezqh", "uid": "445c1aa3-173d-45eb-893d-1f09c9e78a70", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过100字符，超出部分无法输入或触发限制", "case_uid": "case_1781610643965_2iti8prl", "uid": "dbc77c0c-5907-4432-b59e-8ffca3d570ac", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 责任人电话", "case_uid": "case_1781610643965_depb3w85", "uid": "60b35570-9de6-4286-ac23-b5983c08e5d3", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 责任人电话输入框展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_knnkbegc", "uid": "820cb07a-8977-451a-9ea0-005f7d1abd0d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，无默认值，占位提示为“请填写责任人电话，多个电话以英文逗号分隔”，最大输入100字符", "case_uid": "case_1781610643965_b3ouvux6", "uid": "7ab4f965-81fa-4933-8495-8aeee4606bad", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 责任人电话必填校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_0xir2clg", "uid": "2df2bdd6-6c50-478e-a498-b525eb230768", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 提交时若字段为空，给出必填提示", "case_uid": "case_1781610643965_0b6bvcin", "uid": "0a27e5e8-d693-4ec3-b4de-35042a3e8dcc", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 责任人电话长度超限校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_szcpc4zj", "uid": "297e5a77-c756-4c70-a728-96290e6a2b86", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过100字符，超出部分无法输入或触发限制", "case_uid": "case_1781610643965_90edvhky", "uid": "4a7165d9-fc2b-4cca-bd3c-db34bbea61c8", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: URL", "case_uid": "case_1781610643965_pfw68hcv", "uid": "f19be60b-43e6-4a01-b18d-de4c203ef5bd", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: URL输入框展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_is7sghc1", "uid": "b42bf346-f8ed-46f5-82b9-ec5b96e864b2", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，无默认值，占位提示为“请输入URL”，最大输入200字符", "case_uid": "case_1781610643965_v051hv20", "uid": "3a42e329-a2f5-4747-a1e1-dc86d1b377ae", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: URL必填校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_hjbqnqsf", "uid": "83f4297d-1e15-4634-beec-70f3ea3f5d22", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 提交时若字段为空，给出必填提示", "case_uid": "case_1781610643965_1qjukfk6", "uid": "ca83c69d-88b5-4c2a-bae8-7ee6d48ee7a0", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: URL长度超限校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_npdee3co", "uid": "01eea3e8-acf8-44b9-9ea7-547d510af448", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过200字符，超出部分无法输入或触发限制", "case_uid": "case_1781610643965_qvr4ekh3", "uid": "56416ae4-0c96-4068-9df2-ea9a5c5e92dc", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: Driver", "case_uid": "case_1781610643965_zw00mwbw", "uid": "11dbc82c-1a3f-49e5-b06a-2b717b92a6a6", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: Driver输入框展示与默认值", "icon": ["priority_3"], "case_uid": "case_1781610643965_mhqd4v0k", "uid": "e3b3746a-ce63-414f-b603-0d726e99f7c7", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，默认带出当前数据源类型的Driver信息，最大输入200字符", "case_uid": "case_1781610643965_9ca8s382", "uid": "b3abc572-7880-4060-84fa-329e351605d1", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: Driver必填校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_t8qe2j6h", "uid": "f0db8d24-495b-4894-abc0-ef2052353ac5", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 清空Driver值后提交，给出必填提示", "case_uid": "case_1781610643965_58tbaur5", "uid": "a50ffd78-3cb1-4cf0-a038-0ffdee02f84b", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: Driver长度超限校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_5db3r3cy", "uid": "b3eb2b53-5b80-41e1-ba5c-487c448f335d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过200字符，超出部分无法输入或触发限制", "case_uid": "case_1781610643965_28c4gvih", "uid": "cfd4d1f9-4121-4fb3-bc96-20d84f67739f", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: user", "case_uid": "case_1781610643965_zgbefsr4", "uid": "b7317a39-c23d-4cef-b4eb-9eb7394bc99c", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: user输入框展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_oxbaoj38", "uid": "d0ef80d2-eb16-4f29-b484-ccf8d31bcc6e", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，无默认值，占位提示为“请输入user”，最大输入200字符", "case_uid": "case_1781610643965_idvgfz1s", "uid": "f371d715-6c52-4e6c-850e-f3bf1cdabd18", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: user必填校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_zcumx4rd", "uid": "90b7d972-3a83-47a7-97c7-eeb7bfa53232", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 提交时若字段为空，给出必填提示", "case_uid": "case_1781610643965_c92pyjws", "uid": "1b8c3faa-b856-4dc4-b4ef-56a6e94e28a1", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: user长度超限校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_efh8dny1", "uid": "a56a7b7e-918f-45e1-a122-c86d5f0ad9ee", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过200字符，超出部分无法输入或触发限制", "case_uid": "case_1781610643965_vnye62gi", "uid": "1306c027-9200-4e9f-9712-f2c0dbe9def0", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: password", "case_uid": "case_1781610643965_iocnu50h", "uid": "4153bfdc-3bd7-44b4-929d-1415737c078e", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: password输入框展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_699iyvog", "uid": "0d965db9-581f-4892-8461-09daea3df000", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 展示文本输入框，无默认值，占位提示为“请输入password”，最大输入200字符", "case_uid": "case_1781610643965_vck7xsmu", "uid": "2307e1f3-0da2-4ac1-9437-13362c7086f8", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: password必填校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_82a3u9c2", "uid": "ce0245a0-fb83-4270-b771-1df2e9d934ae", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 提交时若字段为空，给出必填提示", "case_uid": "case_1781610643965_zykdo96n", "uid": "ce0b6412-6f6b-48b6-b05a-be7863282bbb", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: password长度超限校验", "icon": ["priority_2"], "case_uid": "case_1781610643965_dipxxn0s", "uid": "5b2b30a5-7718-4e63-8fba-1b26e09a64be", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入超过200字符，超出部分无法输入或触发限制", "case_uid": "case_1781610643965_modenxh3", "uid": "4a4f94f0-1f3d-4a80-a5ae-9f01b646bfb0", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: jdbc连接参数", "case_uid": "case_1781610643965_he27qg2o", "uid": "6b3f7c99-2ee7-4009-bc27-5e8cf7ea8790", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: jdbc连接参数默认值与可编辑", "icon": ["priority_3"], "case_uid": "case_1781610643965_zm87sqic", "uid": "a7948607-6f4a-4c0b-90dc-e1de7de1b2bc", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 文本编译器展示默认值{\\"useSSL\\": \\"false\\", \\"characterEncoding\\": \\"utf8\\"}，内容支持修改", "case_uid": "case_1781610643965_85g37254", "uid": "ad92dfa7-45dd-47f6-9b06-058e81af8937", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: StarRocksHttpPort", "case_uid": "case_1781610643965_o3ljb1c5", "uid": "213d6ef5-7c15-4dcb-bb36-4301263f658d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 选择StarRocks时展示StarRocksHttpPort", "icon": ["priority_2"], "case_uid": "case_1781610643965_0vmzkq91", "uid": "b181d83f-b85a-4cbf-bb00-43e5567453dd", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 数据源类型为StarRocks时，显示StarRocksHttpPort输入框，默认值为8030，最大输入200字符", "case_uid": "case_1781610643965_r17qi8bh", "uid": "e87f8973-bae8-4208-86fb-5aa22339d8a5", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 选择Mysql时隐藏StarRocksHttpPort", "icon": ["priority_2"], "case_uid": "case_1781610643965_btuauehe", "uid": "984a5829-f5ff-4a3b-a2cb-338d5efa19ae", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 数据源类型为Mysql时，不展示StarRocksHttpPort输入框", "case_uid": "case_1781610643965_gc7d9pze", "uid": "70fb8863-9702-470e-b6f9-69ee750948ed", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: StarRocksHttpPort必填校验", "icon": ["priority_1"], "case_uid": "case_1781610643965_7kooy7px", "uid": "a82976c5-3604-405b-af66-ac2c98020de4", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 数据源类型为StarRocks，清空StarRocksHttpPort后提交，给出必填提示", "case_uid": "case_1781610643965_3yey3bl7", "uid": "0d5ece24-da4d-40a6-8662-da0f87e9b388", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 取消按钮", "case_uid": "case_1781610643965_3lncrz6m", "uid": "c4d47909-8e02-46e8-812c-83fac0d82a20", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 第二步点击取消", "icon": ["priority_1"], "case_uid": "case_1781610643965_6nv2a8sr", "uid": "0775aa1f-0f4c-4d36-ac84-a3c4da742596", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 点击“取消”，关闭新增弹框，不保存任何信息", "case_uid": "case_1781610643965_d9sfgu2l", "uid": "d50a76d0-0ad0-4bae-b9f1-c1a67578d86c", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 上一步按钮", "case_uid": "case_1781610643965_rwuhy24v", "uid": "f79c5b63-a183-44f9-8f93-027a3f4cfd94", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 第二步点击上一步", "icon": ["priority_2"], "case_uid": "case_1781610643965_ul3ksz2b", "uid": "91585c6c-8ef6-47fb-a4eb-6798fb1bb719", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 返回第一步选择数据源类型界面，第二步已填写的参数保留", "case_uid": "case_1781610643965_qadxogw3", "uid": "b6fe8c9a-1b40-4b73-812b-cbe96025654c", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 连接测试按钮", "case_uid": "case_1781610643965_oc5rfvzg", "uid": "4ab5cc7d-5df1-4c8f-bb8c-2297d716f578", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 连接测试成功", "icon": ["priority_1"], "case_uid": "case_1781610643965_s0sx5ess", "uid": "9f682aec-8d40-471b-a44a-a1861b0c5474", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 填写有效配置信息，点击“连接测试”，弱提示“连接成功！”", "case_uid": "case_1781610643965_rcgt9zm6", "uid": "21fd38c7-852f-4eba-a847-2103b1b05f9d", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 连接测试失败", "icon": ["priority_1"], "case_uid": "case_1781610643965_p6bcfx87", "uid": "30698c15-673a-452d-b529-4731ef444fef", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 填写无效配置信息，点击“连接测试”，提示“连接失败！失败原因xxxx”", "case_uid": "case_1781610643965_9xdd1cih", "uid": "8f873ac1-4913-4314-8c3f-9fd3aaf95bb5", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 完成按钮", "case_uid": "case_1781610643965_ynijju35", "uid": "233d61ef-366c-48e7-880c-c747bdd2eda5", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 新增数据源成功", "icon": ["priority_1"], "case_uid": "case_1781610643965_n5xwn0m1", "uid": "689786b4-0c57-46bf-863f-34ebe0e1c527", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 填写所有必填项且信息合法，点击“完成”，弱提示“数据源新增成功！”，弹框关闭，列表刷新可见新数据源", "case_uid": "case_1781610643965_7k58ph7o", "uid": "de1cd3de-36ad-4922-9b76-c3317a7419cf", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 新增数据源失败", "icon": ["priority_1"], "case_uid": "case_1781610643965_osc5dosu", "uid": "b96ea1d9-dbc0-4784-843c-10e86cb41f99", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 因服务端异常导致保存失败，点击“完成”后提示“数据源新增失败，失败原因：xxxxx”，弹框不关闭，已填信息保留", "case_uid": "case_1781610643965_88la6n6o", "uid": "3b1d28d9-b878-474d-a5eb-b5cc4246e958", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}]}, {"data": {"text": "功能: 删除数据源", "case_uid": "case_1781610643965_7ooaht67", "uid": "bdfbfc97-f847-498d-94bd-909cbe1a6f12", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "子功能: 二次确认弹窗", "case_uid": "case_1781610643965_mz64ktef", "uid": "dd388379-082f-411b-95dc-18b8d8f486e7", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 未关联任务删除", "case_uid": "case_1781610643965_muytifz1", "uid": "88351298-1041-4870-aa99-a4968e2d7800", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 未关联任务删除确认弹窗文案", "icon": ["priority_2"], "case_uid": "case_1781610643965_j8yet3l3", "uid": "3bf1ed3e-1f7f-4f67-8ebd-4da4e89b7556", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 弹窗文案为：“已检测数据源【aaaawe_182dddd】未关联任务，请确认是否删除当前数据源【aaaawe_182dddd】？删除后不可恢复请谨慎处理。”", "case_uid": "case_1781610643965_39h49zfs", "uid": "7f809fca-cfbb-4c6f-a0f9-b113dc3a62c5", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 确认删除未关联任务数据源", "icon": ["priority_1"], "case_uid": "case_1781610643965_2pvvkt72", "uid": "4550a06c-a142-43de-a4a9-36562cb057f5", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 在确认弹窗中点击“确认”，该数据源删除成功，列表不再显示", "case_uid": "case_1781610643965_jsdaeghs", "uid": "ab3aa2b3-053c-428f-850a-f6c492ba339f", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 取消删除数据源", "icon": ["priority_2"], "case_uid": "case_1781610643965_sul8i2u4", "uid": "40aea3fb-f29a-4307-9dfc-0b7ee4088f68", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 在确认弹窗中点击“取消”，弹窗关闭，数据源仍在列表中", "case_uid": "case_1781610643965_qsyy0qjs", "uid": "d63f35d1-7794-4087-9390-c294bfac9733", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 已关联任务删除", "case_uid": "case_1781610643965_ukm4peil", "uid": "9a920146-7638-4bd6-affc-d4d1c202ee13", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 已关联任务删除反馈", "icon": ["priority_1"], "case_uid": "case_1781610643965_pde7gcj9", "uid": "e99c6566-0738-414c-a559-97a8aa3b7a49", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 点击删除已关联任务的数据源，弹窗提示“已检测数据源【aaaawe_182dddd】关联任务，暂无法删除，请先删除关联任务后再删除当前数据源【aaaawe_182dddd】”，操作被阻止", "case_uid": "case_1781610643965_jft2owz7", "uid": "b176a517-56bd-4b3b-9bf9-a410f4ac00e7", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}]}, {"data": {"text": "功能: 编辑数据源", "case_uid": "case_1781610643965_pefcti7b", "uid": "ef23f688-5707-4d64-a61f-20d5231d8280", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "子功能: 编辑弹窗", "case_uid": "case_1781610643965_jixfm6p8", "uid": "41435d41-8d16-4579-9f8c-48bfd13b953d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 编辑入口", "case_uid": "case_1781610643965_vb2qv52k", "uid": "f7f2aea1-2cf5-459e-a2a3-25337b6203f9", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 点击编辑打开弹框", "icon": ["priority_1"], "case_uid": "case_1781610643965_sf0thmbw", "uid": "12cc46c6-3800-4ca0-9e62-1236e46607b1", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 点击列表操作列“编辑”按钮，直接打开编辑弹框，弹框定位在第二步-配置参数界面", "case_uid": "case_1781610643965_5dg545tw", "uid": "ba5aa552-79e1-40fb-8ec9-b4eaa2f149d6", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 分段器Tab", "case_uid": "case_1781610643965_mm8h547v", "uid": "8cdc8aa3-5a74-42c3-9414-1a8719d92e84", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 编辑时第一步Tab禁用", "icon": ["priority_2"], "case_uid": "case_1781610643965_wnz9fcez", "uid": "5133f2a0-5cce-4928-ada2-6ab47a8425f2", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 弹框顶部“第一步”的Tab或步骤按钮置灰禁用，点击无反应", "case_uid": "case_1781610643965_9keqf8ct", "uid": "1da5c88d-661e-4771-92df-51666e8afd23", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 上一步按钮", "case_uid": "case_1781610643965_g5p6yw0k", "uid": "d91c410b-5b20-4438-860d-4c766324b58d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 编辑时上一步按钮状态与提示", "icon": ["priority_2"], "case_uid": "case_1781610643965_o53gaj6i", "uid": "b7a292b8-e6a6-4b56-9201-0f399124b965", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: “上一步”按钮禁用，鼠标悬浮后提示“数据源类型目前不支持修改，若需修改请删除当前数据源后新增数据源”，鼠标移出后提示消失", "case_uid": "case_1781610643965_0jxhaj7p", "uid": "58419f01-d8d1-4c46-bfc2-b365d9110617", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 基本信息与配置信息编辑", "case_uid": "case_1781610643965_d5wdwoz0", "uid": "19c70f86-a555-4382-82f7-61847073cf6c", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 编辑数据源保存成功", "icon": ["priority_1"], "case_uid": "case_1781610643965_yu3z3jlp", "uid": "5518c7a5-4f39-4601-98be-861eb78da48d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 修改字段值后点击“完成”，保存成功并给出提示，弹框关闭，列表数据更新", "case_uid": "case_1781610643965_dihfkd3s", "uid": "d6b739a4-8690-4d9b-bb07-37fe3b0f5790", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 编辑数据源保存失败", "icon": ["priority_1"], "case_uid": "case_1781610643965_42853o4j", "uid": "3ead154f-991f-4b5a-a547-22b26b030cee", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 修改字段值后因异常导致保存失败，给出失败提示，弹框不关闭，修改内容保留", "case_uid": "case_1781610643965_ez5zu2e1", "uid": "2dbaae4e-325f-4f33-9b97-038db4555db6", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}]}, {"data": {"text": "功能: 查看元数据", "case_uid": "case_1781610643965_vywz1jvl", "uid": "ca2b5f9d-b797-4f3c-8ccc-07784a6a90d9", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "子功能: 左侧数据库表树", "case_uid": "case_1781610643965_793f0443", "uid": "9d30af8b-ae1d-482d-ae86-34f11cd01e63", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 树形菜单", "case_uid": "case_1781610643965_vv9twt29", "uid": "981b0636-73ed-47c3-a24b-2baf28631177", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 树形菜单展示", "icon": ["priority_2"], "case_uid": "case_1781610643965_1uscsrel", "uid": "e6c09df9-b50e-4d58-8afe-1bcfb3a7ef54", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 左侧以树形展示database，database下展示table信息", "case_uid": "case_1781610643965_x4x9zd3n", "uid": "a028e9fe-5afd-4be2-b0cf-dff3ecad79a7", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}, {"data": {"text": "字段: 表名模糊查询", "case_uid": "case_1781610643965_thflx18a", "uid": "e12470f8-4153-498d-9ae8-b147ade48c23", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 表名模糊查询成功", "icon": ["priority_2"], "case_uid": "case_1781610643965_efnop690", "uid": "63aeee01-306a-41f9-8361-45d2e1000b5f", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 在树形菜单的查询框输入表名部分字符，树结构过滤展示匹配的表", "case_uid": "case_1781610643965_233nh16o", "uid": "7dd29095-8a34-4dd5-8bf7-e6f215f1515a", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 表名查询无结果", "icon": ["priority_2"], "case_uid": "case_1781610643965_durj0gwi", "uid": "8fd4f949-1c83-47cb-a875-e82ee576ae6d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 输入不存在的表名，树结构展示空结果", "case_uid": "case_1781610643965_ec5wnhl3", "uid": "9b128d08-3e4d-4f48-b142-c9ccdcca2371", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}, {"data": {"text": "子功能: 右侧Tab-表结构信息", "case_uid": "case_1781610643965_9ndpvli4", "uid": "30150c8c-a429-400c-a2b0-29079ce44d66", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 表结构列表", "case_uid": "case_1781610643965_u8xzqfnb", "uid": "077245b7-c9ee-438d-ad30-65babaeae516", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 表结构信息表头展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_yojdqel2", "uid": "60c2f99c-86e0-497f-b845-c40a30da3193", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 表头按序展示：序号、字段名称、字段类型、字段中文描述", "case_uid": "case_1781610643965_m83g7x7n", "uid": "f7a5824f-e440-4d3d-9295-9529fdce4dc6", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 表结构数据展示", "icon": ["priority_2"], "case_uid": "case_1781610643965_48cut49n", "uid": "d3417ceb-4b36-49c7-bc88-820a3f4e12dc", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 点击某个表后，右侧表结构信息Tab展示该表所有字段，序号为1-n，其他字段文本标签展示对应值，列表不分页", "case_uid": "case_1781610643965_7hrysnb3", "uid": "6c473b99-07e0-41a4-a634-66301f2698ec", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}, {"data": {"text": "子功能: 右侧Tab-数据探查", "case_uid": "case_1781610643965_3t4o8rm4", "uid": "0bc39897-6dbc-4409-bf34-9e219f6f3c57", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "字段: 数据探查提示与列表", "case_uid": "case_1781610643965_f9h2ifvg", "uid": "2db96853-e139-4d55-8977-01240e399f4d", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "用例名称: 数据探查提示语展示", "icon": ["priority_3"], "case_uid": "case_1781610643965_jx6vhg45", "uid": "cb006217-725f-4b65-aaf5-823bd04e5ef4", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 列表上方展示提示语“注：数据探查为随机探查10条数据”", "case_uid": "case_1781610643965_xz44f8n3", "uid": "5e1c4eb1-5890-4fdd-b859-dd8acc20761c", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}, {"data": {"text": "用例名称: 数据探查表头与数据展示", "icon": ["priority_2"], "case_uid": "case_1781610643965_lsqh855g", "uid": "49f5bd7c-0ba2-4bd4-9eb1-c3d401b8f5f0", "expand": true, "isActive": false, "needUpdate": true}, "children": [{"data": {"text": "预期: 列表表头为当前表的所有字段名，列表展示随机探查的数据，数据不分页，所有字段以文本标签展示", "case_uid": "case_1781610643965_83ymi80k", "uid": "9273ce8a-049e-47e1-a63e-6999cc60a394", "expand": true, "isActive": false, "needUpdate": true}, "children": []}]}]}]}]}]}, {"data": {"text": "UI自动化用例", "case_uid": "case_1781658035762_0op0ek7t", "expand": true, "isActive": false, "uid": "1ee106b6-ad9b-4260-a0cb-233e563c46dd"}, "children": [{"data": {"text": "场景配置", "case_uid": "case_1781658035762_2ikuechv", "expand": true, "isActive": false, "uid": "c8bcbe61-e8a8-4a7f-a8ad-03d807ba4f05"}, "children": [{"data": {"text": "渠道: web", "case_uid": "case_1781658035762_s7v5v6lr", "expand": true, "isActive": false, "uid": "9804c614-396c-4ccb-bb46-63c8d6ea67d5"}, "children": []}, {"data": {"text": "浏览器: chromium", "case_uid": "case_1781658035762_khxu8moh", "expand": true, "isActive": false, "uid": "a3d82dc7-e07e-4847-b379-597543e280a8"}, "children": []}, {"data": {"text": "用户名: test1", "case_uid": "case_1781658035762_htesgze4", "expand": true, "isActive": false, "uid": "93c63767-bf36-4f73-b565-a2fce7751d20"}, "children": []}, {"data": {"text": "密码: 123456", "case_uid": "case_1781658035762_0jxvqcuz", "expand": true, "isActive": false, "uid": "4b0e1848-cbd3-46da-ad2f-90621a9541ca"}, "children": []}]}, {"data": {"text": "成功登录", "case_uid": "case_1781658035762_7t5o1bbk", "expand": true, "isActive": false, "uid": "ffa61412-db55-4ebf-9caa-d79727cc1a18"}, "children": [{"data": {"text": "测试步骤", "case_uid": "case_1781658035762_qx4b3kjy", "expand": true, "isActive": false, "uid": "bdb37642-9dca-4c72-b48c-e7b7cc84fea0"}, "children": [{"data": {"text": "1 打开 ${base_url}/#/user/login", "case_uid": "case_1781658035762_9gazzqka", "expand": true, "isActive": false, "uid": "5b43f7f7-6021-4ff1-a829-1312fe7f27e2"}, "children": []}, {"data": {"text": "2 输入 用户名 ${用户名}", "case_uid": "case_1781658035762_gkthlnie", "expand": true, "isActive": false, "uid": "a2f6e372-968c-4ad0-9f5f-e14be7f7e2e4"}, "children": []}, {"data": {"text": "3 输入 密码 ${密码}", "case_uid": "case_1781658035762_kk0i80no", "expand": true, "isActive": false, "uid": "2480b3d8-aa1f-4e79-ab1d-a41dc5baa5ca"}, "children": []}, {"data": {"text": "4 点击 登录按钮", "case_uid": "case_1781658035762_4hfe5ryb", "expand": true, "isActive": false, "uid": "20a4958b-604c-40ad-8d34-d651f1f03c6a"}, "children": []}, {"data": {"text": "5 等待出现 Dashboard", "case_uid": "case_1781658035762_pvevqnjp", "expand": true, "isActive": false, "uid": "5626adb1-a93b-4b01-816b-cb261583bafe"}, "children": []}, {"data": {"text": "6 提取 用户名称 => 测试人员1", "case_uid": "case_1781658035762_bm73uvpk", "expand": true, "isActive": false, "uid": "565abf1c-a9b1-4b01-90e1-bb8602744194"}, "children": []}, {"data": {"text": "7 截图 登录成功页", "case_uid": "case_1781658035762_ruhog6bi", "expand": true, "isActive": false, "uid": "49334234-4fb9-401a-ba57-527752d44974"}, "children": []}]}, {"data": {"text": "执行断言", "case_uid": "case_1781658035762_3pu70cjj", "expand": true, "isActive": false, "uid": "a2209920-4e42-4708-9c57-e845c531e417"}, "children": [{"data": {"text": "页面包含: 项目管理", "case_uid": "case_1781658035762_fcp5fu9h", "expand": true, "isActive": false, "uid": "55958809-114b-415b-923e-ce66444a8bf6"}, "children": []}, {"data": {"text": "元素存在: 接口测试", "case_uid": "case_1781658035762_afjqyerg", "expand": true, "isActive": false, "uid": "cfb2a92e-cb6b-4c88-8ac2-139ef0ce34c1"}, "children": []}]}]}, {"data": {"text": "退出登录", "case_uid": "case_1781658035762_bc9evbj6", "expand": true, "isActive": false, "uid": "ee60bdc6-2cea-4128-9ff1-7c222b7e4b56"}, "children": [{"data": {"text": "测试步骤", "case_uid": "case_1781658035762_8h8ny26g", "expand": true, "isActive": false, "uid": "7c46dbba-af40-4682-9939-37350b19d059"}, "children": [{"data": {"text": "1 打开 ${base_url}/#/", "case_uid": "case_1781658035762_jwnipwst", "expand": true, "isActive": false, "uid": "5ead3f68-abf5-4e13-beb5-70b3827355e5"}, "children": []}, {"data": {"text": "2 点击 测试人员1", "case_uid": "case_1781658035762_p3s4dgxa", "expand": true, "isActive": false, "uid": "faa9928b-78a7-4d31-a78e-a49222e28575"}, "children": []}, {"data": {"text": "3 等待出现 退出登录", "case_uid": "case_1781658035762_zlr1bd98", "expand": true, "isActive": false, "uid": "d03ad622-a378-49b0-8ccc-46d2d7e88116"}, "children": []}, {"data": {"text": "4 点击 退出登录按钮", "case_uid": "case_1781658035762_q2qywowb", "expand": true, "isActive": false, "uid": "f9ce1919-e5a5-46ee-a77c-a08a46044016"}, "children": []}, {"data": {"text": "5 等待出现 立即注册", "case_uid": "case_1781658035762_upm7s9kd", "expand": true, "isActive": false, "uid": "1b0550d0-431c-4da3-80c6-e1a96a0a5c20"}, "children": []}, {"data": {"text": "6 截图 登录注册页", "case_uid": "case_1781658035762_227w3cjw", "expand": true, "isActive": false, "uid": "98cc42ae-3403-4739-b44a-1de76fa931a9"}, "children": []}]}, {"data": {"text": "执行断言", "case_uid": "case_1781658035762_v0wrn69e", "expand": true, "isActive": false, "uid": "d0995003-8d1a-44b1-8cfd-ae409e44d31b"}, "children": [{"data": {"text": "页面包含: 立即注册", "case_uid": "case_1781658035762_g0pc0g0d", "expand": true, "isActive": false, "uid": "401f22c2-05bf-4ce7-ba49-35a8c302653b"}, "children": []}, {"data": {"text": "元素存在: 欢迎来到 ", "case_uid": "case_1781658035762_n6dbqh6o", "expand": true, "isActive": false, "uid": "39e3b8e8-4d28-4435-8c7f-25fee54d9f08"}, "children": []}]}]}]}], "smmVersion": "0.14.0-fix.2"}, "theme": {"template": "default", "config": {}}}', 0);

COMMIT;


