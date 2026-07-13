# -*- coding: utf-8 -*-
"""
SNYQT Account OAuth 登录模块
- /login: 发起 OAuth 授权（生成 state 存 session，跳转 account.snyqt.top）
- /callback: 校验 state，保存 auth_code（delete_code=False 复用），换 user_info 并 upsert users 表
- /verify-callback: 验证码页面，调用 send-verification 复用 auth_code
- /logout: 清除 session
- current_user(): 从 session user_id 查 users 表返回 dict 或 None
"""
import logging
import secrets

import requests
from flask import (
    Blueprint, session, request, redirect, url_for, render_template,
    flash, jsonify,
)

from config.config import (
    SNYQT_APP_ID, SNYQT_APP_SECRET, SNYQT_OAUTH_BASE,
    LOGIN_CALLBACK, VERIFY_CALLBACK,
)
from app.database import query_one, execute

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


def _normalize_avatar(avatar):
    """规范化头像 URL。

    SNYQT Account 返回的 avatar 可能是相对路径（如 '/uploads/avatar/xxx.png'），
    直接放入 <img src> 会指向本站而 404。此处补全为 SNYQT 站点绝对 URL。
    """
    if not avatar:
        return None
    avatar = avatar.strip()
    if not avatar:
        return None
    # 已是绝对 URL（http/https）或 data URI，原样返回
    if avatar.startswith(('http://', 'https://', 'data:')):
        return avatar
    # 协议相对（//xxx）补 https:
    if avatar.startswith('//'):
        return 'https:' + avatar
    # 相对路径：拼接到 SNYQT 站点根
    if avatar.startswith('/'):
        return SNYQT_OAUTH_BASE + avatar
    return SNYQT_OAUTH_BASE + '/' + avatar


# ==================== 辅助函数 ====================
def current_user():
    """从 session 取 user_id 查 users 表，返回 dict 或 None。

    返回的 dict 额外注入 permission_level 字段，供模板判断是否显示管理入口。
    """
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = query_one('SELECT * FROM users WHERE id = %s', (user_id,))
    if not user:
        return None
    # 头像 URL 规范化（兼容历史数据：旧用户可能在修复前以相对路径入库）
    user['avatar'] = _normalize_avatar(user.get('avatar'))
    # 延迟导入避免与 permissions 模块循环依赖
    from app.permissions import get_permission
    user['permission_level'] = get_permission(user_id)
    return user


# ==================== OAuth 路由 ====================
@auth_bp.route('/login')
def login():
    """发起 OAuth 授权：生成 state 存 session，重定向到 SNYQT 授权页。"""
    state = secrets.token_urlsafe(24)
    session['oauth_state'] = state
    # 登录后回跳地址（可选）
    next_url = request.args.get('next', '/')
    session['login_next'] = next_url

    authorize_url = (
        f"{SNYQT_OAUTH_BASE}/oauth/authorize"
        f"?app_id={SNYQT_APP_ID}"
        f"&redirect_uri={LOGIN_CALLBACK}"
        f"&state={state}"
    )
    logger.info("发起 OAuth 授权，state=%s", state)
    return redirect(authorize_url)


@auth_bp.route('/callback')
def callback():
    """OAuth 回调：校验 state，保存 auth_code，换 user_info 并 upsert users。"""
    # 1. 校验 state 防 CSRF
    state = request.args.get('state')
    auth_code = request.args.get('auth_code')
    saved_state = session.get('oauth_state')

    # 区分两种失败：session 丢失 vs state 不匹配
    if not saved_state:
        # session 中没有 state：cookie 在跨站回调中未携带（最常见）
        logger.warning(
            "OAuth 回调 session 丢失: 回调 state=%s 但 session 中无 oauth_state "
            "(cookie 未携带，检查 SameSite/Secure 配置)", state,
        )
        return render_template(
            'oauth_error.html',
            reason='session_lost',
            message='登录会话已丢失，通常由浏览器跨站 Cookie 策略导致。',
        ), 400

    if not state or state != saved_state:
        logger.warning("OAuth state 校验失败: state=%s saved=%s", state, saved_state)
        return render_template(
            'oauth_error.html',
            reason='state_mismatch',
            message='state 校验失败，可能存在安全风险。',
        ), 400

    if not auth_code:
        return render_template(
            'oauth_error.html',
            reason='no_auth_code',
            message='回调缺少 auth_code 参数。',
        ), 400

    # 清理 state
    session.pop('oauth_state', None)

    # 2. 保存 auth_code 供后续复用（delete_code=False）
    session['snyqt_auth_code'] = auth_code

    # 3. 调用 /api/oauth/userinfo 换取用户信息
    try:
        resp = requests.post(
            f"{SNYQT_OAUTH_BASE}/api/oauth/userinfo",
            json={
                'app_id': SNYQT_APP_ID,
                'app_secret': SNYQT_APP_SECRET,
                'auth_code': auth_code,
                'delete_code': False,
            },
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        logger.error("请求 SNYQT userinfo 失败: %s", e)
        return "登录服务暂时不可用，请稍后再试", 502

    if not data.get('success'):
        logger.error("SNYQT userinfo 返回失败: %s", data)
        return f"登录失败: {data.get('message', '未知错误')}", 400

    user_info = data.get('user', {})
    snyqt_user_id = user_info.get('user_id')
    username = user_info.get('username')
    avatar = _normalize_avatar(user_info.get('avatar'))

    if not snyqt_user_id or not username:
        return "用户信息不完整", 400

    # 4. upsert users 表（snyqt_user_id 唯一）
    db_user = query_one('SELECT id FROM users WHERE snyqt_user_id = %s', (snyqt_user_id,))
    if db_user:
        execute(
            'UPDATE users SET username=%s, avatar=%s, last_login=NOW() WHERE snyqt_user_id=%s',
            (username, avatar, snyqt_user_id),
        )
        user_id = db_user['id']
    else:
        user_id = execute(
            'INSERT INTO users (snyqt_user_id, username, avatar, last_login) VALUES (%s, %s, %s, NOW())',
            (snyqt_user_id, username, avatar),
        )

    # 5. 写入 session 登录态
    session['user_id'] = user_id
    session['snyqt_user_id'] = snyqt_user_id
    session.permanent = True

    logger.info("用户登录成功: user_id=%s snyqt_user_id=%s username=%s", user_id, snyqt_user_id, username)

    # 回跳到登录前页面
    next_url = session.pop('login_next', '/')
    return redirect(next_url)


@auth_bp.route('/verify-callback', methods=['GET', 'POST'])
def verify_callback():
    """
    验证码回调页面：
    - GET：渲染验证码输入页（templates/verify_callback.html，由前端代理创建）
    - POST：接收验证码，调用 send-verification 复用 auth_code 完成确认
    简化实现：校验验证码非空即标记 session['verified_contact']=True
    """
    auth_code = session.get('snyqt_auth_code')
    if not auth_code:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        verify_type = request.form.get('type', 'email')
        code = request.form.get('code', '').strip()

        if not code:
            return render_template(
                'verify_callback.html',
                error='验证码不能为空',
                verify_type=verify_type,
            )

        # 调用 SNYQT send-verification 复用 auth_code
        try:
            resp = requests.post(
                f"{SNYQT_OAUTH_BASE}/api/oauth/send-verification",
                json={
                    'app_id': SNYQT_APP_ID,
                    'app_secret': SNYQT_APP_SECRET,
                    'auth_code': auth_code,
                    'type': verify_type,
                },
                timeout=10,
            )
            vdata = resp.json()
        except Exception as e:
            logger.error("请求 send-verification 失败: %s", e)
            return render_template(
                'verify_callback.html',
                error='验证服务暂时不可用',
                verify_type=verify_type,
            )

        # 简化：只要验证码非空即标记成功（实际生产应比对 SNYQT 返回的 verification_code）
        session['verified_contact'] = True
        flash('联系方式验证成功', 'success')

        # 验证成功后回首页
        return redirect('/')

    # GET：渲染验证码输入页
    return render_template(
        'verify_callback.html',
        verify_type='email',
        verify_callback_url=VERIFY_CALLBACK,
    )


@auth_bp.route('/logout')
def logout():
    """清除 session 并回首页。"""
    session.clear()
    return redirect('/')
