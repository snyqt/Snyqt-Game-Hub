<div align="center">
  <img src="/static/images/LOGO.png" alt="SNYQT Logo" width="120" height="120" style="border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">

# SNYQT Game Hub

  <p>
    <strong> 基于 Flask 的游戏托管与社区平台，集成 SNYQT Account 单点登录、Cloudflare Turnstile 人机验证，支持 HTML/Python 游戏在线托管、开发者面板、社区互动与社区治理。</strong>
  </p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python">
    <img src="https://img.shields.io/badge/Flask-2.3.3-green.svg" alt="Flask">
    <img src="https://img.shields.io/badge/MySQL-5.7+-orange.svg" alt="MySQL">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
    <a href="https://account.snyqt.top"><img src="https://img.shields.io/badge/SNYQT-Account-green.svg" alt="SNYQT Account"></a>


  </p>

  <p>
    <a href="https://account.snyqt.top">在线演示</a> •
    <a href="#-快速开始">快速开始</a> •
    <a href="#-开发者平台oauth-20">开发者平台OAuth</a> •
    <a href="#-联系方式">联系方式</a>
  </p>
</div>



## 目录

- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [部署](#部署)
- [数据库](#数据库)
- [权限模型](#权限模型)
- [路线图](#路线图)
- [许可证](#许可证)

## 功能特性

### 游戏托管
- **HTML 单文件托管**：支持在线代码编辑器（CodeMirror）或本地文件上传
- **Python 进程托管**：自动分配端口并启动子进程，反向代理访问
- **版本管理**：`game_uid` 全局唯一标识，支持版本更新与历史记录
- **文件去重**：基于 SHA-256 哈希的全局去重，相同文件复用存储
- **访问控制**：公开 / 私密 / 邀请码三种模式
- **在线游玩**：游玩次数统计、评分系统、游戏库管理

### 开发者面板
- 游戏上传与更新推送（自动填充已有信息）
- 游戏数据面板：玩家数、评分分布、建议帖汇总
- 封面图比例校验与裁剪工具
- 平台抽成透明化展示（固定 1%）

### 社区系统
- 帖子发布（讨论 / 提问 / 建议 三种类型）
- 评论、点赞、星标、置顶
- 标签系统（帖子标签 + 游戏标签）
- 热论排行榜
- 游戏建议自动关联到开发者面板

### 社区治理
- **小黑屋**：公开的处罚记录页面，所有用户可查看
- **帖子封禁**：封禁后对普通用户不可见，移入小黑屋
- **举报系统**：用户举报 → 管理员审核 → 处罚/驳回
- **解封流程**：管理员可在小黑屋管理页解除封禁
- **安全跳转**：外部链接先经安全提示页

### 用户系统
- SNYQT Account OAuth 单点登录
- Cloudflare Turnstile 人机验证
- 积分系统与排行榜
- 多权限并存（一个用户可同时拥有多种权限）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Flask 3.0.3 |
| 数据库 | MySQL 8.0 + PyMySQL 1.1.1 |
| 模板引擎 | Jinja2 |
| 前端 | 原生 HTML/CSS/JS + ECharts |
| 代码编辑器 | CodeMirror 5.65.16 |
| 人机验证 | Cloudflare Turnstile |
| 认证 | SNYQT Account OAuth |
| 部署 | uWSGI 2.0.26 + Docker |

## 项目结构

```
Hub/
├── app/                        # 应用主目录
│   ├── __init__.py            # Flask 应用工厂
│   ├── auth.py                # 认证蓝图（SNYQT OAuth）
│   ├── turnstile.py           # Turnstile 人机验证中间件
│   ├── permissions.py         # 权限系统（多权限并存）
│   ├── games.py               # 游戏管理蓝图
│   ├── view.py                # 游戏托管展示蓝图
│   ├── community.py           # 社区蓝图
│   ├── points.py              # 积分与排行榜蓝图
│   ├── admin.py               # 管理后台蓝图
│   ├── database.py            # 数据库访问层 + 表结构迁移
│   ├── helpers.py             # 通用辅助函数
│   └── api_helpers.py         # API 响应辅助
├── config/
│   ├── __init__.py
│   └── config.example.py      # 配置示例（真实 config.py 不入库）
├── static/                     # 静态资源
│   ├── css/style.css
│   ├── js/main.js
│   └── images/
├── templates/                  # Jinja2 模板（24 个）
├── uploads/                    # 游戏文件上传目录（运行时生成）
├── run.py                      # 开发服务器入口
├── wsgi.py                     # uWSGI 入口
├── uwsgi.ini                   # uWSGI 配置
├── Dockerfile.example          # Docker 构建示例
├── requirements.txt            # Python 依赖
└── .gitignore
```

## 快速开始

### 环境要求

- Python 3.10+
- MySQL 8.0+
- SNYQT Account 应用凭证（App ID / App Secret）
- Cloudflare Turnstile Site Key / Secret

### 本地开发

1. **克隆仓库**

   ```bash
   git clone <repo-url>
   cd Hub
   ```

2. **安装依赖**

   ```bash
   pip install -r requirements.txt
   ```

   > 注意：`uWSGI` 在 Windows 下无法编译，本地开发可跳过，仅生产部署需要。

3. **配置文件**

   ```bash
   cp config/config.example.py config/config.py
   ```

   编辑 `config/config.py`，填入真实的数据库凭证、OAuth 配置、Turnstile 密钥。

   本地调试可关闭 Cookie Secure：
   ```bash
   # 设置环境变量
   set SESSION_COOKIE_SECURE=false
   ```

4. **准备数据库**

   创建一个空数据库，表结构会在应用启动时自动创建与迁移：
   ```sql
   CREATE DATABASE snyqt_game_hub CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```

5. **启动开发服务器**

   ```bash
   python run.py
   ```

   服务器监听 `http://0.0.0.0:5000`。

   > 开发服务器配置了 `use_reloader=False`（避免上传文件时中断）和 `threaded=True`（避免大文件上传阻塞），修改代码后需手动重启。

## 配置说明

所有配置项支持环境变量注入，优先级：环境变量 > `config.py` 文件值。

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| 数据库主机 | `DB_HOST` | `127.0.0.1` | MySQL 地址 |
| 数据库用户 | `DB_USER` | — | MySQL 用户名 |
| 数据库密码 | `DB_PASSWORD` | — | MySQL 密码 |
| 数据库名 | `DB_NAME` | — | 数据库名 |
| 数据库端口 | `DB_PORT` | `3306` | MySQL 端口 |
| OAuth App ID | `SNYQT_APP_ID` | — | SNYQT Account 应用 ID |
| OAuth App Secret | `SNYQT_APP_SECRET` | — | SNYQT Account 应用密钥 |
| OAuth 基址 | `SNYQT_OAUTH_BASE` | `https://account.snyqt.top` | SNYQT Account 地址 |
| 登录回调 | `LOGIN_CALLBACK` | — | OAuth 登录回调 URL |
| 验证回调 | `VERIFY_CALLBACK` | — | Turnstile 验证回调 URL |
| Turnstile Sitekey | `TURNSTILE_SITEKEY` | — | Turnstile 站点密钥 |
| Turnstile Secret | `TURNSTILE_SECRET` | — | Turnstile 服务端密钥 |
| 验证有效期 | `TURNSTILE_VERIFY_DURATION_HOURS` | `2` | 验证状态有效时长（小时） |
| 本地跳过验证 | `TURNSTILE_BYPASS_LOCALHOST` | `true` | 对 127.0.0.1 跳过验证 |
| 上传目录 | `UPLOAD_FOLDER` | `uploads/games` | 游戏文件存储路径 |
| 端口范围起 | `PORT_RANGE_START` | `8100` | Python 托管端口分配起点 |
| 端口范围止 | `PORT_RANGE_END` | `8200` | Python 托管端口分配终点 |
| 开发者审核 | `DEVELOPER_REVIEW` | `manual` | `auto` 自动通过 / `manual` 人工审核 |
| Flask Secret | `SECRET_KEY` | — | Session 加密密钥（务必修改） |
| Cookie Secure | `SESSION_COOKIE_SECURE` | `true` | 是否仅 HTTPS 传输 Cookie |

## 部署

### Docker 部署（推荐）

1. **准备 Dockerfile**

   ```bash
   cp Dockerfile.example Dockerfile
   ```

   编辑 `Dockerfile`，将 ENV 占位值替换为真实凭证，或保持不变通过运行时注入。

2. **构建镜像**

   ```bash
   docker build -t snyqt-game-hub .
   ```

3. **运行容器**

   ```bash
   docker run -d \
     --name snyqt-game-hub \
     -p 5000:5000 \
     -v snyqt_uploads:/app/uploads \
     -e DB_HOST=your_db_host \
     -e DB_USER=your_db_user \
     -e DB_PASSWORD=your_db_password \
     -e DB_NAME=your_db_name \
     -e SNYQT_APP_ID=your_app_id \
     -e SNYQT_APP_SECRET=your_app_secret \
     -e SECRET_KEY=your_random_secret \
     snyqt-game-hub
   ```

   > 必须映射 `/app/uploads` 卷，否则容器重建后游戏文件丢失。

4. **反向代理（Nginx 示例）**

   ```nginx
   server {
       listen 443 ssl http2;
       server_name your-domain.com;

       client_max_body_size 500M;

       location / {
           proxy_pass http://127.0.0.1:5000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_read_timeout 600s;
           proxy_send_timeout 600s;
       }
   }
   ```

### 裸机部署（uWSGI）

```bash
# 直接启动
uwsgi --ini uwsgi.ini

# 平滑重启
uwsgi --reload /tmp/uwsgi.pid
```

### uWSGI 关键配置

| 配置 | 值 | 说明 |
|------|-----|------|
| `http-socket` | `0.0.0.0:5000` | 直接 HTTP，避免内置路由器缓冲请求体 |
| `post-buffering` | `0` | 流式上传，避免大文件卡死 |
| `limit-post` | `536870912` | 允许 512MB 请求体 |
| `buffer-size` | `65535` | 64KB 请求头缓冲 |
| `harakiri` | `600` | 600s 超时，适配慢速上传 |
| `lazy-apps` | `true` | 每个 worker 独立加载应用 |
| `max-requests` | `5000` | worker 处理 5000 请求后回收，缓解内存泄漏 |
| `processes` | `4` | 4 进程 |
| `threads` | `2` | 每进程 2 线程 |

## 数据库

### 自动建表与迁移

应用启动时，`check_tables()` 会自动：
- 检查 20+ 张表是否存在，缺失则按 DDL 创建
- 对已有表执行增量列迁移（幂等）
- 为 `game_uid` 为空的旧游戏自动补全 8 位 hex ID

### 核心表

| 表名 | 说明 |
|------|------|
| `users` | 用户表（关联 SNYQT Account） |
| `permissions` | 权限表（多行模型，一个用户可有多条权限记录） |
| `games` | 游戏表（含 `game_uid`、`version`、`play_count` 等） |
| `game_versions` | 游戏版本历史 |
| `file_hashes` | 文件哈希去重表 |
| `community_posts` | 社区帖子（含 `status` 封禁状态） |
| `community_comments` | 评论 |
| `community_likes` | 点赞 |
| `post_tags` | 帖子标签关联 |
| `penalty_records` | 处罚记录（小黑屋） |
| `reports` | 用户举报 |
| `game_library` | 用户游戏库 |
| `reviews` | 游戏评分 |
| `game_co_devs` | 合作开发者 |
| `invite_codes` | 邀请码 |
| `tags` / `game_tags` | 游戏标签系统 |

## 权限模型

采用**多权限并存**模型：一个用户可同时拥有多种权限，每种权限单独一条记录。

| 权限级别 | 说明 |
|----------|------|
| `user` | 普通用户（默认） |
| `developer` | 开发者（可上传/管理游戏） |
| `reviewer` | 评荐人员（可置顶/星标帖子） |
| `super_admin` | 超级管理员（可封禁/解封/审核） |

- 申请权限会新增一条记录，不影响已有权限
- `DEVELOPER_REVIEW=manual` 时，开发者申请需管理员审核
- `DEVELOPER_REVIEW=auto` 时，申请直接通过

## 路线图

- [ ] Rive / Alpine.js 动画增强
- [ ] 游戏内嵌实时数据看板
- [ ] 移动端适配优化
- [ ] WebSocket 实时通知

## 许可证

本项目采用 [MIT License](LICENSE) 开源协议。
