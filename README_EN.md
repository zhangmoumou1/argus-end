[中文文档](https://github.com/wuranxu/argus/blob/main/README.md)

![png](https://img.shields.io/badge/Python-3.5+-green)
![png](https://img.shields.io/badge/React-17+-blue)
![png](https://img.shields.io/badge/FastApi-green)
![png](https://img.shields.io/badge/contributors-3-green)


## 🎉 Getting Started

1. clone code

```bash
$ git clone https://github.com/wuranxu/argus
$ cd argus
```

2. install dependencies

```bash
# 可换豆瓣源或者清华源安装依赖
$ pip install -r requirements.txt
```

3. install and start redis

4. install and start mysql

5. edit config.py

  edit connection info about redis and mysql

6. start server

```bash
$ python argus.py
```

7. registry

  Open your browser, enter url: `http://localhost:7777`, then you will see the page.

  First people will be `ADMIN`

![](https://static.argus.fun/picture/2022-1-2/1641092636428-image.png)

  Sign in and enjoy `argus`！

## 🖕 Overview 

[Documents 🍚](http://argus.readthedocs.org/)

[Demo 🍍](https://argus.fun/)

### 😢 About argus 

argus is an auto test tool based on `Python`+`FastApi`+`React` for api test. It's not an absolute production right now.

### ❤️ Heart 

I hope argus can help someone still uses robotframework or writes script for apitest.argus can help you a lot.

### 😊 Features

+ [x] 🔥 absolute auth rule, support login with github

- [x] 🀄 absolute project management

* [x] 🚴 fast with FastApi

- [x] 📝 many options for data dependencies, you can make and use data so easy
- [x] 🎨 online http request like postman
- [x] 🍷 global variable for you
- [x] 🐍 redis online
- [x] 🐎 test plan
- [x] 🙈 online database manager
- [x] 📰 beautiful email notification
- [x] 😹 cronjob for case
- [x] 🐧 beautiful test report

## 🙋 Coming soon 

- [ ] 🐘 Micro Services
- [ ] 🐄 DataFactory for developing data
- [ ] 🐸 support har/jmx to argus case
- [ ] 👍 CI/CD，like pipeline, provide openapi
- [ ] 🌼 notification
- [ ] 🌛 support dubbo/grpc
- [ ] 🐛 yapi
- [ ] 🌽 and so on

  You can open issues to communicate with me, if you like the project, give a star will make me happy.

## 🎨 Wechat communicate group

  you can ask anything in my wechat group.

![](https://static.argus.fun/picture/2022-1-2/1641097484952-ddff5bf23bdccaaf23fa227aa2e9957.jpg)