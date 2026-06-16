# Argus 后端部署说明

后端项目路径：`argus-end`

## 访问地址总览

前端访问地址：

```text
http://域名/
```

后端访问地址：

```text
http://域名/argus/
```

后端接口文档地址：

```text
http://域名/docs
```

## 部署后初始化对象存储

RabbitMQ 管理后台访问地址：

```text
http://域名:15672/#/
```

RustFS 对象存储控制台访问地址：

```text
http://域名:9091/
```

RustFS / S3 对象存储中应在部署完成后创建 2 个 bucket：

- `argus-end`
- `public`

## 后端配置文件

部署前先修改：

```text
argus-end/conf/pro.env
```

公有镜像版的数据库、Redis、RabbitMQ、RustFS、对象存储等账密信息，统一放在 `conf/pro.env`，不直接写在 `ops/docker-compose.image.yaml`。
两份 compose 都统一读取 `conf/pro.env` 作为容器运行配置。

最少关注这些字段：

```env
MYSQL_HOST=argus-mysql
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PWD=19950308zyc.
DBNAME=argus

REDIS_ON=True
REDIS_HOST=argus-redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

OSS_TYPE=s3
OSS_ENDPOINT=http://argus-rustfs:9000
OSS_ACCESS_KEY_ID=rustfs
OSS_ACCESS_KEY_SECRET=susan123
OSS_BUCKET=argus-end
OSS_AVATAR_BUCKET=public

RABBITMQ_HOST=argus-rabbitmq
RABBITMQ_PORT=5672
RABBITMQ_USER=admin
RABBITMQ_PASSWORD=admin

SERVER_PORT=7777
ARGUS_API_WORKERS=2
```

UI Runner 最小配置：

```text
argus-end/ui_runner/.env
```

```env
UI_RUNNER_BROWSER=chromium
UI_RUNNER_HEADLESS=true
UI_RUNNER_POLL_INTERVAL_MS=5000
```

## 共享网络初始化

首次部署前先执行一次：

```bash
docker network create argus_shared
```

如果网络已存在，Docker 会提示已存在，可直接忽略。

## 两套部署方式

### 方案一：服务器自己构建

适合：

- 代码刚改完，想直接在服务器构建
- 不依赖腾讯云公有镜像

使用文件：

```text
ops/docker-compose.yaml
```

首次部署：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml up -d argus-mysql argus-redis argus-rabbitmq argus-rustfs
docker-compose -f docker-compose.yaml up -d --build argus-api
```

后端更新发布：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml up -d --build argus-api
```

UI Runner 更新发布：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml up -d --build argus-ui-runner
```

### 方案二：直接拉腾讯云公有镜像

适合：

- 对外部署
- 服务器配置较低，不想本机构建
- 只想 pull + up

使用文件：

```text
ops/docker-compose.image.yaml
```

当前公有镜像：

```bash
docker pull ccr.ccs.tencentyun.com/zhangyancheng/argus-end:1.0
```

首次部署：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.image.yaml up -d argus-mysql argus-redis argus-rabbitmq argus-rustfs
docker-compose -f docker-compose.image.yaml pull argus-api
docker-compose -f docker-compose.image.yaml up -d argus-api
```

后端更新发布：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.image.yaml pull argus-api
docker-compose -f docker-compose.image.yaml up -d argus-api
```

UI Runner 更新发布：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.image.yaml up -d --build argus-ui-runner
```

## 查看状态

自己构建版：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml ps
```

公有镜像版：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.image.yaml ps
```

## 看日志

自己构建版：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml logs -f argus-api
```

公有镜像版：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.image.yaml logs -f argus-api
```

后端启动日志文件：

```bash
tail -f ~/argus/argus-end/logs/startup/argus-api.log
```

UI Runner 启动日志文件：

```bash
tail -f ~/argus/argus-end/logs/startup/argus-ui-runner.log
```

## 说明

- `docker-compose.yaml`：服务器自己构建 `argus-api`
- `docker-compose.image.yaml`：直接拉腾讯云公有镜像 `argus-api`
- `argus-mysql`、`argus-redis`、`argus-rabbitmq`、`argus-rustfs` 都是基础依赖服务
- `argus-ui-runner` 当前仍按本地 Dockerfile 单独构建
- `2核2G` 机器更推荐使用“公有镜像版”
