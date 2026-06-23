# Argus 后端部署文档

后端项目路径：`argus-end`

## 访问地址

- 平台首页：`http://你的域名或IP/`
- 后端接口：`http://你的域名或IP/argus/`
- 后端接口文档：`http://你的域名或IP/docs`
- OpenAPI：`http://你的域名或IP/openapi.json`

默认端口：

- 前端容器：`127.0.0.1:8000`
- 后端容器：`127.0.0.1:7777`
- 宿主机统一入口：Nginx

## 本地启动

本机建议先安装：

- `Python 3.8+`
- `Node.js 18+`
- `MySQL 8`
- `Redis 6+`
- `RabbitMQ`
- `RustFS / S3 兼容对象存储`

后端改这里：

```text
argus-end/conf/dev.env
```

至少确认：

```env
MYSQL_HOST=你的本机数据库地址
MYSQL_PORT=3306
MYSQL_USER=你的数据库账号
MYSQL_PWD=你的数据库密码
DBNAME=argus

REDIS_HOST=你的本机Redis地址
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

RABBITMQ_HOST=你的本机RabbitMQ地址
RABBITMQ_PORT=5672
RABBITMQ_USER=你的RabbitMQ账号
RABBITMQ_PASSWORD=你的RabbitMQ密码

PUBLIC_BASE_URL=http://localhost:8000

OSS_TYPE=s3
OSS_ENDPOINT=http://你的本机RustFS地址:9000
OSS_ACCESS_KEY_ID=你的对象存储AccessKey
OSS_ACCESS_KEY_SECRET=你的对象存储SecretKey
OSS_BUCKET=argus-end
OSS_AVATAR_BUCKET=public

SERVER_PORT=7777
```

前端改这里：

```text
argus-front/config/defaultSettings.ts
```

本机联调推荐：

```ts
apiUrl: 'localhost:7777/argus'
```

启动命令：

```bash
cd ~/argus/argus-end
pip install -r requirements.txt
python argus.py
```

```bash
cd ~/argus/argus-front
npm install
npm run start
```

## 服务器部署

### 服务器部署前要改的地方

1. `argus-end/conf/pro.env`
2. `argus-end/ops/nginx.conf`
3. `argus-front/config/defaultSettings.ts`
4. `argus-front/ops/nginx.frontend.conf`

`conf/pro.env` 至少确认这些字段：

```env
MYSQL_HOST=argus-mysql
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PWD=your_password
DBNAME=argus

REDIS_ON=True
REDIS_HOST=argus-redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

PUBLIC_BASE_URL=http://你的域名或IP

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

说明：

- `SERVER_REPORT` 默认继承 `PUBLIC_BASE_URL`
- `OSS_PUBLIC_ENDPOINT` 默认按 `PUBLIC_BASE_URL + OSS_PORT` 推导
- 如果对象存储外网地址不是 `http://你的域名或IP:9000`，再单独显式配置 `OSS_PUBLIC_ENDPOINT`

如果你用域名部署：

- `ops/nginx.conf` 的 `server_name` 改成你的域名
- `argus-front/ops/nginx.frontend.conf` 的 `server_name` 改成你的域名
- `conf/pro.env` 的 `PUBLIC_BASE_URL` 改成 `http://你的域名` 或 `https://你的域名`
- 如果对象存储外网地址和 `你的域名:9000` 不一致，再单独配置 `OSS_PUBLIC_ENDPOINT`

如果你用 IP 部署：

- `ops/nginx.conf` 的 `server_name` 改成 `_`
- `argus-front/ops/nginx.frontend.conf` 的 `server_name` 改成 `_`
- `conf/pro.env` 的 `PUBLIC_BASE_URL` 改成 `http://服务器IP`
- 如果对象存储外网地址和 `http://服务器IP:9000` 不一致，再单独配置 `OSS_PUBLIC_ENDPOINT`

`ui_runner/.env` 最少保留：

```env
UI_RUNNER_BROWSER=chromium
UI_RUNNER_HEADLESS=true
UI_RUNNER_POLL_INTERVAL_MS=5000
```

## 首次初始化

- 后端启动时会自动做表结构初始化
- `init_data/` 里的示例数据只在首次发布时执行一次，已有数据不要重复覆盖

参考：

```text
argus-end/init_data/README.md
```

### 宿主机准备

```bash
sudo apt update
sudo apt install -y nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

### 方式一：服务器本机构建

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml up -d argus-mysql argus-redis argus-rabbitmq argus-rustfs
docker-compose -f docker-compose.yaml up -d --build --no-deps argus-api
docker-compose -f docker-compose.yaml up -d --build --no-deps argus-ui-runner
```

后续更新：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml up -d --build --no-deps argus-api
docker-compose -f docker-compose.yaml up -d --build --no-deps argus-ui-runner
```

### 方式二：使用公有镜像

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.image.yaml up -d argus-mysql argus-redis argus-rabbitmq argus-rustfs argus-ui-runner
docker-compose -f docker-compose.image.yaml pull argus-api
docker-compose -f docker-compose.image.yaml up -d --no-deps argus-api
```

说明：

- `argus-api` 使用公有镜像

后续更新：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.image.yaml pull argus-api
docker-compose -f docker-compose.image.yaml up -d --no-deps argus-api
```

### Nginx

加载配置：

```bash
sudo cp ~/argus/argus-end/ops/nginx.conf /etc/nginx/conf.d/argus.conf
sudo nginx -t
sudo systemctl reload nginx
```

部署完成后在对象存储里创建两个 bucket：

- `argus-end`
- `public`

### 验证

查看状态：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml ps
docker-compose -f docker-compose.image.yaml ps
```

查看日志：

```bash
cd ~/argus/argus-end/ops
docker-compose -f docker-compose.yaml logs -f argus-api
docker-compose -f docker-compose.image.yaml logs -f argus-api
tail -f ~/argus/argus-end/logs/startup/argus-api.log
tail -f ~/argus/argus-end/logs/startup/argus-ui-runner.log
```

能正常打开下面这些地址，就说明部署基本成功：

- `http://你的域名或IP/`
- `http://你的域名或IP/docs`
- `http://你的域名或IP/openapi.json`
