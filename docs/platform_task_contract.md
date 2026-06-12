# Platform Task Contract

## API Response

All new platform APIs should use the existing response wrapper:

```json
{
  "code": 0,
  "msg": "操作成功",
  "data": {}
}
```

Paged responses should use:

```json
{
  "code": 0,
  "msg": "操作成功",
  "data": {
    "list": [],
    "total": 0,
    "page": 1,
    "size": 20
  }
}
```

## Task Types

- `api_test_run`: 接口测试执行
- `ui_test_run`: UI 测试执行
- `performance_test_run`: 性能测试执行
- `ai_functional_case`: AI 生成功能用例
- `notification`: 通知任务

## Task Status

- `queued`: 已入队
- `claimed`: 已领取
- `running`: 执行中
- `cancelling`: 停止中
- `success`: 执行成功
- `failed`: 执行失败
- `cancelled`: 已停止
- `skipped`: 已跳过
- `partial_success`: 部分成功

## Result Status

- `none`: 暂无结果
- `test_success`: 测试成功
- `test_failed`: 测试失败
- `partial_success`: 部分成功
- `skipped`: 已跳过

## Sequential Execution

RabbitMQ queue name is derived from:

```text
{RABBITMQ_QUEUE_PREFIX}.{task_type}.{resource_key}
```

Executions that must remain sequential should use the same `resource_key`.

Recommended resource keys:

- 接口测试计划: `api_plan_{plan_id}`
- UI 测试计划: `ui_plan_{plan_id}`
- 性能测试计划: `performance_plan_{plan_id}`

## OSS Bucket Policy

Default business bucket is `argus-end`.

Existing explicit public/avatar bucket usage should not be changed. Other generated artifacts, uploaded business files, knowledge files, UI reports, and functional-case attachments should use the default business bucket unless a feature explicitly requires a public bucket.
