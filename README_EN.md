[中文说明](./README.md)

# Argus Test Platform

> An AI-first open-source testing platform for API automation, XMind-style functional case design, UI testing, performance testing, Mock services, and shared reports.

![Python](https://img.shields.io/badge/Python-3.8+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=111827)
![License](https://img.shields.io/badge/License-MIT-111827)

Argus is built for teams that want more than a request runner. It brings test assets, execution workflows, and AI assistance into one platform.

## AI Highlights

- `AI-generated API flows`: build executable API scenarios from service assets.
- `AI-generated functional cases`: turn requirements, screenshots, and rule docs into structured cases.
- `Model-aware execution`: UI plans, API flow generation, and functional case generation can run with the selected model.
- `Unified model config`: manage multiple enabled models from the admin panel.

## Core Capabilities

- `API Testing`: request debugging, automation cases, chained scenarios, variables, dependency passing.
- `Functional Case Design`: XMind-style case management with AI generation and structured maintenance.
- `UI Testing`: recording, planning, scheduling, model-based execution, and report playback.
- `Performance Testing`: load activities, result aggregation, and performance reports.
- `Mock & Assets`: service management, API assets, and Mock collaboration.
- `Reports & Platform Config`: API/UI/performance reports, environments, gateways, notifications, and model config.

## Server Deployment

Recommended reading order:

1. [Initialization data guide](./init_data/README.md)
2. [Backend deployment guide](./ops/DEPLOY_BACKEND.md)
3. `argus-front/ops/DEPLOY_FRONTEND.md`

Notes:

- Backend startup automatically prepares database schema changes.
- The sample data in `init_data/` is intended for first-time deployment only. Do not run it again once data already exists.

## Local Start

Recommended local prerequisites:

- `Python 3.8+`
- `Node.js 18+`
- `MySQL 8`
- `Redis 6+`
- `RabbitMQ`
- `RustFS / S3-compatible object storage`

Update backend config first:

```text
argus-end/conf/dev.env
```

At minimum, check database, Redis, RabbitMQ, object storage, and:

```env
SERVER_PORT=7777
SERVER_REPORT=http://localhost:8000
```

Install backend dependencies and start:

```bash
pip install -r requirements.txt
python argus.py
```

Then update frontend config:

```text
argus-front/config/defaultSettings.ts
```

Recommended local value:

```ts
apiUrl: 'localhost:7777/argus'
```

Install frontend dependencies and start:

```bash
npm install
npm run start
```

Default local URL:

```text
http://localhost:8000
```

Backend API:

```text
http://localhost:7777/argus/
```

Backend API docs:

```text
http://localhost:7777/docs
http://localhost:7777/redoc
```

## Links

- Live demo: http://zhangyanc.club/
- Backend API docs: http://zhangyanc.club/docs
- Backend OpenAPI: http://zhangyanc.club/openapi.json
- Backend repo: [zhangmoumou1/argus-end](https://github.com/zhangmoumou1/argus-end)
- Frontend repo: [zhangmoumou1/argus-front](https://github.com/zhangmoumou1/argus-front)

## Architecture

```text
argus-front  -> React / Umi / Ant Design
argus-end    -> FastAPI / SQLAlchemy / Scheduler
storage      -> MySQL / Redis / OSS
runtime      -> API / UI / Performance / Mock / AI
```

Argus is designed to become:

`test assets + AI acceleration + unified collaboration`
