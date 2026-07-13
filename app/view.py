# -*- coding: utf-8 -*-
"""/view 托管展示与权限校验模块（蓝图 view_bp，url_prefix='/view'）。

依赖契约：
- from app.auth import current_user
- from app.database import query_one
- from app.helpers import ensure_running, game_dir
"""
from flask import (
    Blueprint, render_template, abort, send_from_directory,
    request, Response
)
from werkzeug.exceptions import NotFound
import requests as http_requests

from app.auth import current_user
from app.database import query_one
from app.helpers import ensure_running, game_dir

view_bp = Blueprint('view', __name__, url_prefix='/view')

# 反向代理时需要剔除的请求头（避免冲突）
_EXCLUDED_REQ_HEADERS = ('host', 'content-length', 'connection',
                         'accept-encoding', 'transfer-encoding')

# 回传时需要剔除的响应头（Flask 会自行处理）
_EXCLUDED_RESP_HEADERS = ('content-encoding', 'transfer-encoding',
                          'connection', 'content-length')


@view_bp.route('/<int:gid>/')
@view_bp.route('/<int:gid>/<path:subpath>')
def view_game(gid, subpath=''):
    """托管展示：HTML 直接返回文件，Python 反向代理到子进程端口。

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

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        abort(404)

    # 封禁游戏除开发者本人外不可访问
    if game['is_banned'] and game['developer_id'] != user['id']:
        abort(404)

    # 校验游戏库：必须已入库才能访问
    lib = query_one(
        'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
        [user['id'], gid]
    )
    if not lib:
        return render_template(
            'view_denied.html',
            message='请先获取游戏后即可在线游玩',
            game_id=gid
        ), 403

    # 按托管类型分发
    if game['hosting_type'] == 'html':
        return _serve_html(game, gid, subpath)
    else:
        return _serve_python(game, gid, subpath)


def _serve_html(game, gid, subpath):
    """HTML 模式：从游戏目录直接返回入口文件或子路径资源。"""
    target = subpath if subpath else (game.get('entry_file') or 'index.html')
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
