# UI Runner

这个目录提供平台 UI 自动化的独立执行器，职责是：

- 从平台领取 `/ui-test/runner/claim` 任务
- 用 `Playwright + Midscene` 执行平台 DSL
- 回写步骤结果到 `/ui-test/runner/step/save`
- 回写最终结果到 `/ui-test/runner/run/save`

## 依赖

参考 Midscene 官方 Playwright 集成文档，需要安装：

```bash
npm install
npx playwright install chromium
```

官方文档：

- <https://midscenejs.com/zh/introduction>
- <https://midscenejs.com/zh/integrate-with-playwright>
- <https://midscenejs.com/zh/api>

## 配置

复制 `.env.example` 中的变量并注入当前环境，或者直接修改当前目录下的 `.env`。

现在 runner 支持优先从 `ui_runner/.runner-bootstrap.json` 自动读取平台下发的启动信息。这个文件会在你从平台点击：

- UI 用例试运行
- UI 计划执行
- UI 运行重试

时由后端自动生成，里面会带上：

- `server`
- `project_id`
- `plan_id`
- `run_id`
- `token`

- `UI_RUNNER_SERVER`: 平台地址
- `UI_RUNNER_TOKEN`: 平台登录 token，可选
- `UI_RUNNER_USERNAME`: 平台登录用户名，未提供 token 时使用
- `UI_RUNNER_PASSWORD`: 平台登录密码，未提供 token 时使用
- `UI_RUNNER_PROJECT_ID`: 领取任务的项目 ID
- `UI_RUNNER_RUN_ID`: 可选，指定只领取某个 run_id
- `UI_RUNNER_PLAN_ID`: 可选，限制只领取某个计划的任务
- `UI_RUNNER_POLL_INTERVAL_MS`: 空闲轮询间隔
- `UI_RUNNER_BROWSER`: 浏览器类型，默认 `chromium`
- `UI_RUNNER_HEADLESS`: 是否无头
Runner 启动时会自动调用：

- `GET http://127.0.0.1:7777/config/ai-model/config`

并把平台当前启用的模型配置同步成 Midscene 运行时环境变量。

优先级如下：

1. `.env` 显式配置
2. `.runner-bootstrap.json` 自动注入
3. 代码默认值

所以日常通常不需要再手填：

- `UI_RUNNER_SERVER`
- `UI_RUNNER_PROJECT_ID`

如果 bootstrap 不存在，才需要回退到：

- `UI_RUNNER_TOKEN` 或 `UI_RUNNER_USERNAME/UI_RUNNER_PASSWORD`
- `UI_RUNNER_PROJECT_ID`

## 启动

```bash
npm run start
```

启动时会先做自检，并明确输出以下状态之一：

- 账号没填
- 登录失败
- 模型配置读取失败
- 当前 Midscene 版本不支持平台启用的模型
- 任务领取失败
- 当前没有可领取的 UI 任务

## 产物约束

Runner 会按平台下发的对象路径写回这些字段：

- `artifact_bucket=argus-end`
- `artifact_prefix=autowebcase/{project_id}/{plan_id}/{run_id}`
- `screenshot_dir`
- `video_path`
- `trace_path`
- `report_path`
- `result_json_path`

当前 Runner 负责把本地产物路径和平台路径对应起来；实际上传 OSS 的动作可以继续由平台统一接管，或在 Runner 中补充 SDK 上传逻辑。
