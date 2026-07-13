# -*- coding: utf-8 -*-
"""
Cloudflare Turnstile 全局人机验证模块
- verify_turnstile(): 向 Cloudflare 校验 token
- is_turnstile_verified(): 检查当前 session 是否已验证且未过期（2 小时）
- register_turnstile_middleware(app): before_request 中间件，未验证跳转 /turnstile-verify
- 蓝图 turnstile_bp: /turnstile-verify, /api/turnstile/verify-form, /api/turnstile/status
"""
import logging
from datetime import datetime, timedelta

import requests
from flask import (
    Blueprint, session, request, redirect, jsonify, url_for, render_template
)

from config.config import (
    TURNSTILE_SECRET, TURNSTILE_SITEKEY,
    TURNSTILE_VERIFY_DURATION_HOURS, TURNSTILE_BYPASS_LOCALHOST,
)

logger = logging.getLogger(__name__)

turnstile_bp = Blueprint('turnstile', __name__)

# Cloudflare Turnstile 站点校验端点
_TURNSTILE_SITEVERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'

# 不需要人机验证的路由前缀（OAuth 回调由 Snyqt 服务端发起，需放行）
EXCLUDED_ROUTES = [
    '/static/',
    '/favicon.ico',
    '/turnstile-verify',
    '/api/turnstile/',
    '/callback',
    '/verify-callback',
]


def verify_turnstile(token, client_ip=None):
    """向 Cloudflare 验证 Turnstile token，返回布尔值。"""
    if not token:
        return False
    data = {'secret': TURNSTILE_SECRET, 'response': token}
    if client_ip:
        data['remoteip'] = client_ip
    try:
        resp = requests.post(_TURNSTILE_SITEVERIFY_URL, data=data, timeout=10)
        return bool(resp.json().get('success', False))
    except Exception as e:
        logger.error("Turnstile 验证请求异常: %s", e)
        return False


def is_turnstile_verified():
    """检查当前 session 是否已完成人机验证且未过期。"""
    if not session.get('turnstile_verified'):
        return False
    verify_at = session.get('turnstile_verified_at')
    if not verify_at:
        return False
    verify_time = datetime.fromtimestamp(verify_at)
    if datetime.now() > verify_time + timedelta(hours=TURNSTILE_VERIFY_DURATION_HOURS):
        # 过期清理
        session.pop('turnstile_verified', None)
        session.pop('turnstile_verified_at', None)
        return False
    return True


def _is_localhost(addr):
    """判断 IP 是否为本地开发地址（127.0.0.1 或 192.168.*）。"""
    if not addr:
        return False
    return addr.startswith('127.0.0.1') or addr.startswith('192.168.')


def register_turnstile_middleware(app):
    """注册 before_request 中间件：未验证用户重定向到 /turnstile-verify。"""

    @app.before_request
    def check_turnstile():
        # 排除路由
        for excluded in EXCLUDED_ROUTES:
            if request.path.startswith(excluded):
                return None

        # 开发期：对 localhost 旁路（便于本地测试）
        if TURNSTILE_BYPASS_LOCALHOST:
            client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            # 取 X-Forwarded-For 第一个 IP 或 remote_addr
            if client_ip and ',' in client_ip:
                client_ip = client_ip.split(',')[0].strip()
            if _is_localhost(client_ip):
                return None

        # 已验证放行
        if is_turnstile_verified():
            return None

        # API 返回 403 JSON
        if request.path.startswith('/api/'):
            return jsonify({
                'success': False,
                'message': '请先完成人机验证',
                'verify_url': url_for('turnstile.turnstile_verify')
            }), 403

        # 页面重定向到验证页
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for('turnstile.turnstile_verify', next=next_url))


# ==================== 路由 ====================
@turnstile_bp.route('/turnstile-verify')
def turnstile_verify():
    """展示全屏验证页面。"""
    if is_turnstile_verified():
        return redirect(request.args.get('next', '/'))
    return render_template(
        'turnstile_verify.html',
        sitekey=TURNSTILE_SITEKEY,
        next_url=request.args.get('next', '/'),
    )


@turnstile_bp.route('/api/turnstile/verify-form', methods=['POST'])
def turnstile_verify_form():
    """接收表单提交的 Turnstile token，校验通过后写入 session。"""
    token = request.form.get('cf-turnstile-response')
    next_url = request.form.get('next', '/')
    if not token:
        return redirect(url_for('turnstile.turnstile_verify', next=next_url))

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip and ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()

    if verify_turnstile(token, client_ip):
        session['turnstile_verified'] = True
        session['turnstile_verified_at'] = datetime.now().timestamp()
        session.permanent = True
        return redirect(next_url)
    else:
        return redirect(url_for('turnstile.turnstile_verify', next=next_url, error='invalid'))


@turnstile_bp.route('/api/turnstile/status')
def turnstile_status():
    """返回当前验证状态与过期时间。"""
    verified = is_turnstile_verified()
    expire_time = None
    if verified:
        verify_at = session.get('turnstile_verified_at')
        if verify_at:
            t = datetime.fromtimestamp(verify_at) + timedelta(hours=TURNSTILE_VERIFY_DURATION_HOURS)
            expire_time = t.strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({'verified': verified, 'expire_time': expire_time})
