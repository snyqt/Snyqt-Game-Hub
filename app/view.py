# -*- coding: utf-8 -*-
"""/view 托管展示与权限校验模块（蓝图 view_bp，url_prefix='/view'）。

依赖契约：
- from app.auth import current_user
- from app.database import query_one
- from app.helpers import ensure_running, game_dir
"""
from flask import (
    Blueprint, render_template, abort, send_from_directory,
    request, Response, url_for
)
from werkzeug.exceptions import NotFound
import requests as http_requests

from app.auth import current_user
from app.database import query_one, execute
from app.helpers import ensure_running, game_dir

view_bp = Blueprint('view', __name__, url_prefix='/view')

# 反向代理时需要剔除的请求头（避免冲突）
_EXCLUDED_REQ_HEADERS = ('host', 'content-length', 'connection',
                         'accept-encoding', 'transfer-encoding')

# 回传时需要剔除的响应头（Flask 会自行处理）
_EXCLUDED_RESP_HEADERS = ('content-encoding', 'transfer-encoding',
                          'connection', 'content-length')


def _resolve_game(gid):
    """解析 URL 中的游戏标识，兼容 game_uid（8位hex）和旧数字 id。

    返回 game dict 或 None。
    """
    if not gid:
        return None
    gid = str(gid).strip()
    # 优先按 game_uid 查询
    game = query_one('SELECT * FROM games WHERE game_uid = %s', [gid])
    if game:
        return game
    # 兼容旧数字 id 链接
    try:
        numeric_id = int(gid)
        return query_one('SELECT * FROM games WHERE id = %s', [numeric_id])
    except (ValueError, TypeError):
        return None


def _check_access(game, user):
    """检查访问模式权限，返回 (denied_response, status_code) 或 None（通过）。"""
    # 开发者本人始终有权限
    if game['developer_id'] == user['id']:
        return None

    # 合作开发者检查
    is_co_dev = False
    co_dev = query_one(
        'SELECT id FROM game_co_devs WHERE game_id = %s AND user_id = %s AND status = %s',
        [game['id'], user['id'], 'accepted']
    )
    if co_dev:
        is_co_dev = True

    access_mode = game.get('access_mode') or 'public'

    if access_mode == 'private':
        if not is_co_dev:
            return render_template(
                'view_denied.html',
                message='此游戏为私密模式，仅开发者可访问',
                game_id=game['id']
            ), 403

    elif access_mode == 'invite':
        if is_co_dev:
            return None
        invite = query_one(
            'SELECT id FROM invite_codes WHERE game_id = %s AND used_by = %s',
            [game['id'], user['id']]
        )
        if not invite:
            return render_template(
                'view_denied.html',
                message='此游戏需要邀请码才能访问，请输入有效的邀请码',
                game_id=game['id']
            ), 403

    # public 模式无额外限制
    return None


@view_bp.route('/<gid>/')
@view_bp.route('/<gid>/<path:subpath>')
def view_game(gid, subpath=''):
    """托管展示：HTML 直接返回文件，Python 反向代理到子进程端口。

    URL 中的 gid 支持 game_uid（8位hex）或旧数字 id。
    权限校验：current_user 必须存在且 game_library 含 (user_id, gid)。
    封禁游戏除开发者本人外返回 404。
    """
    user = current_user()
    if not user:
        return render_template(
            'view_denied.html',
            message='请先登录后再访问游戏内容',
            game_id=gid
        ), 403

    game = _resolve_game(gid)
    if not game:
        abort(404)

    # 内部统一使用数字 id
    numeric_gid = game['id']

    # 封禁游戏除开发者本人外不可访问
    if game['is_banned'] and game['developer_id'] != user['id']:
        abort(404)

    # 校验游戏库：必须已入库才能访问
    lib = query_one(
        'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
        [user['id'], numeric_gid]
    )
    if not lib:
        return render_template(
            'view_denied.html',
            message='请先获取游戏后即可在线游玩',
            game_id=numeric_gid
        ), 403

    # 访问控制检查
    denied = _check_access(game, user)
    if denied:
        return denied

    # 记录游玩次数（每次访问游戏页面时累加）
    try:
        execute('UPDATE games SET play_count = play_count + 1 WHERE id = %s', (numeric_gid,))
    except Exception:
        # 游玩次数统计失败不应阻断游戏访问
        pass

    # 按托管类型分发（single_html 与 html 均走静态文件服务）
    if game['hosting_type'] in ('html', 'single_html'):
        return _serve_html(game, numeric_gid, subpath)
    else:
        return _serve_python(game, numeric_gid, subpath)


def _serve_html(game, gid, subpath):
    """HTML 模式：从游戏目录直接返回入口文件或子路径资源。

    gid 为数字 id（内部目录定位用）；URL 生成使用 game_uid。
    """
    # URL 标识优先使用 game_uid
    url_gid = game.get('game_uid') or gid
    if not subpath:
        # 无子路径：返回美化框架模板
        entry = game.get('entry_file') or 'index.html'
        iframe_src = url_for('view.view_raw', gid=url_gid, subpath=entry)
        return render_template('view_frame.html', game=game, iframe_src=iframe_src)

    target = subpath
    directory = game_dir(gid)
    try:
        return send_from_directory(directory, target)
    except (FileNotFoundError, NotFound):
        abort(404)


def _serve_python(game, gid, subpath):
    """Python 模式：确保进程运行后反向代理到 python_port。"""
    if game['status'] != 'active':
        return render_template(
            'view_denied.html',
            message='游戏尚未激活，暂时无法游玩',
            game_id=gid
        ), 403

    # 确保进程运行（必要时启动）
    ensure_running(game)

    port = game.get('python_port')
    if not port:
        return render_template(
            'view_denied.html',
            message='游戏端口未分配，请联系管理员',
            game_id=gid
        ), 503

    # 构造目标 URL
    url = f'http://127.0.0.1:{port}/{subpath}'

    # 转发请求头（剔除冲突项）
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _EXCLUDED_REQ_HEADERS
    }

    try:
        resp = http_requests.request(
            method=request.method,
            url=url,
            headers=fwd_headers,
            params=request.args,
            data=request.get_data(),
            allow_redirects=False,
            timeout=60
        )
    except http_requests.exceptions.RequestException:
        return render_template(
            'view_denied.html',
            message='游戏服务暂时不可用，请稍后重试',
            game_id=gid
        ), 502

    # 构造回传响应头
    headers = [
        (k, v) for k, v in resp.headers.items()
        if k.lower() not in _EXCLUDED_RESP_HEADERS
    ]

    return Response(resp.content, status=resp.status_code, headers=headers)


@view_bp.route('/<gid>/_raw/<path:subpath>')
def view_raw(gid, subpath):
    """提供原始文件（iframe 内嵌用），需通过完整的权限校验。

    URL 中的 gid 支持 game_uid（8位hex）或旧数字 id。
    """
    user = current_user()
    if not user:
        return render_template(
            'view_denied.html',
            message='请先登录后再访问游戏内容',
            game_id=gid
        ), 403

    game = _resolve_game(gid)
    if not game:
        abort(404)

    numeric_gid = game['id']

    if game['is_banned'] and game['developer_id'] != user['id']:
        abort(404)

    lib = query_one(
        'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
        [user['id'], numeric_gid]
    )
    if not lib:
        return render_template(
            'view_denied.html',
            message='请先获取游戏后即可在线游玩',
            game_id=numeric_gid
        ), 403

    denied = _check_access(game, user)
    if denied:
        return denied

    directory = game_dir(numeric_gid)
    try:
        return send_from_directory(directory, subpath)
    except (FileNotFoundError, NotFound):
        abort(404)
