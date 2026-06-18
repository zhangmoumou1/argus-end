[English](./README_EN.md)

# Argus 测试平台-后端

> 一个以 AI 为核心的开源测试平台，覆盖接口测试、功能用例设计、UI 测试、性能测试、Mock 服务与统一测试报告。

![Python](https://img.shields.io/badge/Python-3.8+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=111827)
![License](https://img.shields.io/badge/License-MIT-111827)

Argus 不只是一个请求调试工具，而是一套把测试资产、执行链路和 AI 能力串起来的协作平台。

## AI 亮点

- `AI 生成流程场景`：从接口资产生成可执行的接口流程场景。
- `AI 生成功能用例`：结合需求、截图、规则文档，生成结构化测试用例。
- `模型可选执行`：UI 测试、接口流程场景、功能用例生成都可以按所选模型执行。
- `统一模型配置`：后台管理里集中维护模型，不限制只启用一个。

## 主要功能

- `接口测试`：接口调试、自动化用例、流程编排、变量提取、依赖传参。
- `功能用例设计`：基于 XMind 风格脑图管理功能用例，支持 AI 生成、复制粘贴、结构化维护。
- `UI 测试`：录制、计划、调度、按模型执行、报告回放。
- `性能测试`：压测活动、结果汇总、性能报告查看。
- `Mock 与资产管理`：服务管理、接口资产、Mock 协作联动。
- `测试报告与平台配置`：接口/UI/性能报告、环境、网关、通知、模型配置。

## 服务器部署

服务器部署建议按这个顺序：

1. [初始化数据说明](./init_data/README.md)
2. [后端部署文档](./ops/DEPLOY_BACKEND.md)
3. [前端部署文档](https://github.com/zhangmoumou1/argus-front/ops/DEPLOY_FRONTEND.md)

说明：

- 后端启动时会自动完成表结构初始化。
- `init_data/` 里的首批示例数据用于首次发布初始化，库里已存在数据时不要重复覆盖。

## 本地启动

本机建议先准备：

- `Python 3.8+`
- `Node.js 18+`
- `MySQL 8`
- `Redis 6+`
- `RabbitMQ`
- `RustFS / S3 兼容对象存储`

先改后端配置：

```text
argus-end/conf/dev.env
```

至少确认数据库、Redis、RabbitMQ、对象存储和：

```env
SERVER_PORT=7777
SERVER_REPORT=http://localhost:8000
```

安装后端依赖并启动：

```bash
cd ~/argus/argus-end
pip install -r requirements.txt
python argus.py
```

再改前端配置：

```text
argus-front/config/defaultSettings.ts
```

本机联调时推荐：

```ts
apiUrl: 'localhost:7777/argus'
```

安装前端依赖并启动：

```bash
cd ~/argus/argus-front
npm install
npm run start
```

本地默认访问地址：

```text
http://localhost:8000
```

后端接口地址：

```text
http://localhost:7777/argus/
```

后端接口文档：

```text
http://localhost:7777/docs
http://localhost:7777/redoc
```

## 相关链接

- 在线体验：http://zhangyanc.club/
- 后端接口文档：http://zhangyanc.club/docs
- 后端 OpenAPI：http://zhangyanc.club/openapi.json
- 后端代码：[zhangmoumou1/argus-end](https://github.com/zhangmoumou1/argus-end)
- 前端代码：[zhangmoumou1/argus-front](https://github.com/zhangmoumou1/argus-front)

## 架构概览

```text
argus-front  -> React / Umi / Ant Design
argus-end    -> FastAPI / SQLAlchemy / Scheduler
storage      -> MySQL / Redis / OSS
runtime      -> API / UI / Performance / Mock / AI
```

Argus 想做的是把测试平台真正沉淀成：

`测试资产 + AI 提效 + 统一协作`

