# -*- coding: utf-8 -*-
"""游戏上传与 HTML 托管模块（蓝图 games_bp）。

依赖契约：
- from app.database import query, query_one, execute
- from app.permissions import require_level
- from app.auth import current_user
- from config.config import UPLOAD_FOLDER
- from app.helpers import game_dir
"""
import os
import json
import zipfile
import logging

from flask import (
    Blueprint, render_template, request, jsonify, abort
)

from app.database import query, query_one, execute
from app.permissions import require_level
from app.auth import current_user
from config.config import UPLOAD_FOLDER
from app.helpers import game_dir

games_bp = Blueprint('games', __name__)
logger = logging.getLogger(__name__)

# 允许的图片扩展名
_ALLOWED_IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


def _get_filtered_games():
    """获取游戏列表（排除封禁游戏，除非是当前用户自己开发的）。

    支持 ?tag= 与 ?q= 搜索。
    """
    user = current_user()
    user_id = user['id'] if user else 0

    sql = 'SELECT * FROM games WHERE (is_banned = 0 OR developer_id = %s)'
    args = [user_id]

    tag = request.args.get('tag')
    if tag:
        sql += ' AND tags LIKE %s'
        args.append(f'%{tag}%')

    q = request.args.get('q')
    if q:
        sql += ' AND (title LIKE %s OR description LIKE %s)'
        args.extend([f'%{q}%', f'%{q}%'])

    sql += ' ORDER BY created_at DESC'
    return query(sql, args)


@games_bp.route('/')
def index():
    """首页：游戏展示区。"""
    games = _get_filtered_games()
    return render_template('index.html', games=games)


@games_bp.route('/api/games')
def api_games():
    """游戏列表 API（JSON）。"""
    games = _get_filtered_games()
    return jsonify({'success': True, 'games': games})


@games_bp.route('/game/<int:gid>')
def game_detail(gid):
    """游戏详情页。"""
    user = current_user()
    user_id = user['id'] if user else 0

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        abort(404)
    # 封禁游戏除开发者本人外不可见
    if game['is_banned'] and game['developer_id'] != user_id:
        abort(404)

    # 获取评论列表
    reviews = query(
        'SELECT r.*, u.username, u.avatar '
        'FROM reviews r JOIN users u ON r.user_id = u.id '
        'WHERE r.game_id = %s ORDER BY r.created_at DESC',
        [gid]
    )

    # 检查是否已在游戏库
    in_library = False
    if user:
        lib = query_one(
            'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
            [user['id'], gid]
        )
        in_library = lib is not None

    return render_template(
        'game_detail.html',
        game=game,
        reviews=reviews,
        in_library=in_library
    )


@games_bp.route('/api/games/upload', methods=['POST'])
@require_level('developer')
def upload_game():
    """游戏上传接口（仅开发者）。

    接收 multipart 表单：
    - title, description, tags, hosting_type
    - cover（图片）, screenshots（多图）, game_files（zip）
    - hosting_type=html 时需 entry_file
    - hosting_type=python 时需 python_main、python_command
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    tags = request.form.get('tags', '').strip()
    hosting_type = request.form.get('hosting_type', 'html').strip()

    if not title:
        return jsonify({'success': False, 'message': '标题不能为空'}), 400
    if hosting_type not in ('html', 'python'):
        return jsonify({'success': False, 'message': '托管类型无效'}), 400

    logger.info("上传开始: user=%s title=%s type=%s", user.get('id'), title, hosting_type)

    # 初始状态：HTML 直接 active，Python 需审核
    initial_status = 'pending_review' if hosting_type == 'python' else 'active'

    # 先插入获取游戏 ID
    game_id = execute(
        'INSERT INTO games '
        '(title, description, developer_id, hosting_type, status, tags, is_banned) '
        'VALUES (%s, %s, %s, %s, %s, %s, 0)',
        (title, description, user['id'], hosting_type, initial_status, tags)
    )
    logger.info("已创建游戏记录 game_id=%s", game_id)

    # 创建游戏文件目录
    gdir = game_dir(game_id)
    os.makedirs(gdir, exist_ok=True)

    # 保存封面图片
    cover_path = ''
    cover = request.files.get('cover')
    if cover and cover.filename:
        ext = os.path.splitext(cover.filename)[1].lower()
        if ext in _ALLOWED_IMAGE_EXTS:
            cover_name = f'cover{ext}'
            cover.save(os.path.join(gdir, cover_name))
            cover_path = f'uploads/games/{game_id}/{cover_name}'

    # 保存截图（多张）
    screenshots = []
    for f in request.files.getlist('screenshots'):
        if not f or not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1].lower()
        if ext in _ALLOWED_IMAGE_EXTS:
            fname = f'shot_{len(screenshots)}{ext}'
            f.save(os.path.join(gdir, fname))
            screenshots.append(f'uploads/games/{game_id}/{fname}')

    # 解压游戏文件 zip
    zip_file = request.files.get('game_files')
    if zip_file and zip_file.filename:
        try:
            zip_file.stream.seek(0)
            with zipfile.ZipFile(zip_file) as zf:
                zf.extractall(gdir)
            logger.info("ZIP 解压完成 game_id=%s 解压到 %s", game_id, gdir)
        except zipfile.BadZipFile:
            logger.error("ZIP 文件无效 game_id=%s", game_id)
            return jsonify({'success': False, 'message': '无效的 ZIP 文件'}), 400
        except Exception as e:
            logger.exception("ZIP 解压失败 game_id=%s: %s", game_id, e)
            return jsonify({'success': False, 'message': f'解压失败: {e}'}), 500
    else:
        logger.warning("未收到 game_files 字段 game_id=%s", game_id)

    # 托管类型相关字段
    entry_file = None
    python_main = None
    python_command = None
    if hosting_type == 'html':
        entry_file = request.form.get('entry_file', 'index.html').strip()
    else:
        python_main = request.form.get('python_main', '').strip()
        python_command = request.form.get('python_command', '').strip()
        if not python_command:
            return jsonify({'success': False, 'message': 'Python 启动命令不能为空'}), 400

    # 更新游戏记录
    execute(
        'UPDATE games SET cover_image = %s, screenshots = %s, entry_file = %s, '
        'python_main = %s, python_command = %s WHERE id = %s',
        (cover_path, json.dumps(screenshots), entry_file,
         python_main, python_command, game_id)
    )

    # Python 托管：插入审核队列
    if hosting_type == 'python':
        execute(
            'INSERT INTO python_review_queue (game_id, status) VALUES (%s, %s)',
            (game_id, 'pending')
        )

    return jsonify({
        'success': True,
        'game_id': game_id,
        'status': initial_status,
        'message': '上传成功' if hosting_type == 'html' else '上传成功，等待管理员审核'
    })


@games_bp.route('/developer')
@require_level('developer')
def developer_panel():
    """开发者面板：展示当前用户的游戏列表与审核状态。"""
    user = current_user()
    if not user:
        abort(403)

    games = query(
        'SELECT * FROM games WHERE developer_id = %s ORDER BY created_at DESC',
        [user['id']]
    )

    # 查询 Python 游戏的审核状态
    review_map = {}
    if games:
        game_ids = [g['id'] for g in games]
        placeholders = ','.join(['%s'] * len(game_ids))
        reviews = query(
            f'SELECT * FROM python_review_queue WHERE game_id IN ({placeholders})',
            game_ids
        )
        for r in reviews:
            review_map[r['game_id']] = r

    return render_template('developer.html', games=games, review_map=review_map)


@games_bp.route('/api/games/<int:gid>/edit', methods=['GET', 'POST'])
def edit_game(gid):
    """游戏编辑接口（仅开发者本人）。

    GET 返回游戏信息（JSON），POST 更新游戏元数据。
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        abort(404)
    if game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '无权操作他人游戏'}), 403

    if request.method == 'GET':
        return jsonify({'success': True, 'game': game})

    # POST：更新可编辑字段
    title = request.form.get('title', game['title']).strip()
    description = request.form.get('description', game['description'] or '').strip()
    tags = request.form.get('tags', game['tags'] or '').strip()

    # Python 模式可更新启动命令
    python_main = game['python_main']
    python_command = game['python_command']
    if game['hosting_type'] == 'python':
        python_main = request.form.get('python_main', python_main or '').strip()
        python_command = request.form.get('python_command', python_command or '').strip()

    execute(
        'UPDATE games SET title = %s, description = %s, tags = %s, '
        'python_main = %s, python_command = %s WHERE id = %s',
        (title, description, tags, python_main, python_command, gid)
    )

    return jsonify({'success': True, 'message': '更新成功'})
