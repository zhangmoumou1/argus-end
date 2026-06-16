# Argus 部署说明

后端项目路径：`argus-end`  
前端项目路径：`argus-front`

当前访问域名示例：

- `http://zhangyanc.club`
- `http://www.zhangyanc.club`

## 部署前先改配置

### 1. 后端配置 `argus-end/conf/pro.env`

按实际环境修改下面这些字段：

```env
MYSQL_HOST="argus-mysql"
MYSQL_PORT=3306
MYSQL_USER="root"
MYSQL_PWD="19950308zyc."
DBNAME="argus"

REDIS_ON=True
REDIS_HOST="argus-redis"
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=""

OSS_TYPE="s3"
OSS_ENDPOINT="http://argus-rustfs:9000"
OSS_ACCESS_KEY_ID="rustfs"
OSS_ACCESS_KEY_SECRET="susan123"
OSS_BUCKET="argus-end"
OSS_AVATAR_BUCKET="public"

EMAIL_SENDER="wuranxu1993@126.com"
EMAIL_PASSWORD="XCLHTLWLUPMBRSFD"
EMAIL_HOST="smtp.126.com"
EMAIL_TO="测试报告收件人pro"
YAPI_TOKEN="ff"

RABBITMQ_HOST="argus-rabbitmq"
RABBITMQ_PORT=5672
RABBITMQ_USER="admin"
RABBITMQ_PASSWORD="admin"

MOCK_ON=False
PROXY_ON=False
PROXY_PORT=7778
GRAFANA_URL="http://192.168.8.25:3001/"

SERVER_PORT=7777
```

### 2. UI Runner 配置 `argus-end/ui_runner/.env`

最少保留：

```env
UI_RUNNER_BROWSER=chromium
UI_RUNNER_HEADLESS=true
UI_RUNNER_POLL_INTERVAL_MS=5000
```

### 3. 前端接口地址

前端部署前，确认 `argus-front` 的环境配置里接口地址指向后端域名或后端服务地址。

## 后端启动

进入后端部署目录：

```bash
cd ~/argus/argus-end/ops
```

首次部署或镜像需要重建时：

```bash
docker-compose up -d --build
```

只是重启服务：

```bash
docker-compose up -d
```

只发布后端代码：

```bash
docker-compose up -d --build argus-api
```

只发布 UI Runner：

```bash
docker-compose up -d --build argus-ui-runner
```

## 前端启动

进入前端部署目录：

```bash
cd ~/argus/argus-front/ops
```

首次部署或镜像需要重建时：

```bash
docker-compose up -d --build
```

只是重启服务：

```bash
docker-compose up -d
```

## 查看状态

后端：

```bash
cd ~/argus/argus-end/ops
docker-compose ps
```

前端：

```bash
cd ~/argus/argus-front/ops
docker-compose ps
```

## 看日志

后端容器日志：

```bash
cd ~/argus/argus-end/ops
docker-compose logs -f argus-api
```

UI Runner 日志：

```bash
cd ~/argus/argus-end/ops
docker-compose logs -f argus-ui-runner
```

前端日志：

```bash
cd ~/argus/argus-front/ops
docker-compose logs -f argus-front
```

后端启动日志文件：

```bash
tail -f ~/argus/argus-end/logs/startup/argus-api.log
```

UI Runner 启动日志文件：

```bash
tail -f ~/argus/argus-end/logs/startup/argus-ui-runner.log
```

## SSH 断开后怎么处理

重新登录服务器后执行：

```bash
cd ~/argus/argus-front/ops
docker-compose ps
docker-compose logs --tail=100
```

或查看后端：

```bash
cd ~/argus/argus-end/ops
docker-compose ps
docker-compose logs --tail=100
```

如果服务没起来，再重新执行一次：

```bash
docker-compose up -d --build
```

## 当前 docker-compose 包含的服务

后端 `argus-end/ops/docker-compose.yaml` 已包含：

- `argus-mysql`
- `argus-redis`
- `argus-rabbitmq`
- `argus-rustfs`
- `argus-api`
- `argus-ui-runner`
