# Argus 后端部署说明

后端项目路径：`argus-end`

## 域名

当前前端访问域名：

- `http://zhangyanc.club`
- `http://www.zhangyanc.club`

后端部署时请确保该域名最终可以正常访问到前端页面，并且前端配置的接口地址能够连到当前后端服务。

## 本地调试和 Docker 部署

- `conf/dev.env`：本地调试用，默认走 `127.0.0.1`
- `conf/pro.env`：Docker 部署用，默认走容器服务名

## 首次部署只需要改 2 个文件

### 1. 改后端配置 `conf/pro.env`

可直接按下面示例填写：

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

# system config
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

### 2. 改 UI Runner 最小配置 `ui_runner/.env`

```env
UI_RUNNER_BROWSER=chromium
UI_RUNNER_HEADLESS=true
UI_RUNNER_POLL_INTERVAL_MS=5000
```

## 启动

首次部署或 `requirements.txt` 变更时执行：

```bash
docker compose -f ops/docker-compose.yaml up -d --build
```

日常发布如果只是代码变更、依赖没变，可执行：

```bash
docker compose -f ops/docker-compose.yaml up -d
```

## 检查

查看容器：

```bash
docker compose -f ops/docker-compose.yaml ps
```

查看后端日志：

```bash
docker compose -f ops/docker-compose.yaml logs -f argus-api
```

查看 UI Runner 日志：

```bash
docker compose -f ops/docker-compose.yaml logs -f argus-ui-runner
```

查看后端启动日志文件：

```bash
tail -f logs/startup/argus-api.log
```

查看 UI Runner 启动日志文件：

```bash
tail -f logs/startup/argus-ui-runner.log
```

## 说明

- `ops/docker-compose.yaml` 已包含：MySQL、Redis、RabbitMQ、RustFS(S3兼容)、argus-api、argus-ui-runner
- 前端请到 `argus-front` 仓库单独部署
