# -*- coding: utf-8 -*-
"""
SNYQT Game Hub 配置示例文件（开源可见）

使用方法：
1. 复制本文件为 config.py：cp config.example.py config.py
2. 将下方所有示例值替换为你自己的真实凭证
3. 也可不修改 config.py，而是通过环境变量注入（Docker / systemd 推荐）

注意：config.py 已被 .gitignore 屏蔽，不会上传 Git；本文件作为开源示例保留。
"""
import os

# ==================== 数据库配置 ====================
DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
DB_USER = os.getenv('DB_USER', 'your_db_user')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'your_db_password')
DB_NAME = os.getenv('DB_NAME', 'your_db_name')
DB_PORT = int(os.getenv('DB_PORT', '3306'))
DB_CHARSET = 'utf8mb4'


# ==================== SNYQT Account OAuth ====================
SNYQT_APP_ID = os.getenv('SNYQT_APP_ID', 'your_snyqt_app_id')
SNYQT_APP_SECRET = os.getenv('SNYQT_APP_SECRET', 'your_snyqt_app_secret')
SNYQT_OAUTH_BASE = os.getenv('SNYQT_OAUTH_BASE', 'https://account.snyqt.top')

# OAuth 登录回调地址
LOGIN_CALLBACK = os.getenv('LOGIN_CALLBACK', 'https://your-domain.example.com/callback')
# 验证码回调地址
VERIFY_CALLBACK = os.getenv('VERIFY_CALLBACK', 'https://your-domain.example.com/verify-callback')


# ==================== Cloudflare Turnstile ====================
TURNSTILE_SITEKEY = os.getenv('TURNSTILE_SITEKEY', 'your_turnstile_sitekey')
TURNSTILE_SECRET = os.getenv('TURNSTILE_SECRET', 'your_turnstile_secret')
# 验证有效期（小时）
TURNSTILE_VERIFY_DURATION_HOURS = int(os.getenv('TURNSTILE_VERIFY_DURATION_HOURS', '2'))
# 开发期：对 127.0.0.1 / 192.168.* 跳过人机验证
TURNSTILE_BYPASS_LOCALHOST = os.getenv('TURNSTILE_BYPASS_LOCALHOST', 'true').lower() == 'true'


# ==================== 文件上传与端口 ====================
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'uploads/games')
# Python 托管子进程端口分配范围
PORT_RANGE_START = int(os.getenv('PORT_RANGE_START', '8100'))
PORT_RANGE_END = int(os.getenv('PORT_RANGE_END', '8200'))


# ==================== 权限审核规则 ====================
# developer_review: 开发者申请审核规则
#   auto   = 申请直接通过（status=approved）
#   manual = 需管理员审核（status=pending）
DEVELOPER_REVIEW = os.getenv('DEVELOPER_REVIEW', 'manual')


# ==================== Flask 安全 ====================
# 生产环境务必通过环境变量注入随机字符串
SECRET_KEY = os.getenv('SECRET_KEY', 'please-change-this-to-a-random-secret')
