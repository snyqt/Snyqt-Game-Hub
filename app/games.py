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
import uuid
import shutil
import hashlib
import zipfile
import logging

from flask import (
    Blueprint, render_template, request, jsonify, abort, send_from_directory,
    redirect, url_for
)

from app.database import query, query_one, execute, query_all
from app.permissions import require_level, has_permission
from app.auth import current_user
from config.config import UPLOAD_FOLDER
from app.helpers import game_dir, stop_python

games_bp = Blueprint('games', __name__)
logger = logging.getLogger(__name__)

# 允许的图片扩展名
_ALLOWED_IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


@games_bp.route('/safe-redirect')
def safe_redirect():
    """外链安全跳转中间页"""
    target_url = request.args.get('url', '')
    if not target_url:
        return redirect(url_for('games.index'))
    # 解析域名用于展示
    from urllib.parse import urlparse
    parsed = urlparse(target_url)
    domain = parsed.hostname or '未知'
    return render_template('safe_redirect.html', target_url=target_url, domain=domain)


def _fix_zip_filename(name):
    """修复 ZIP 中文文件名乱码。

    ZIP 规范对非 ASCII 文件名使用 CP437 编码，但 Windows 中文系统使用 GBK。
    尝试用 CP437 解码再编码为 GBK 来恢复原始中文文件名。
    """
    try:
        name.encode('utf-8')
        return name
    except UnicodeEncodeError:
        pass
    try:
        raw = name.encode('cp437')
        return raw.decode('gbk')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    try:
        raw = name.encode('cp437')
        return raw.decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name


def _extract_zip_flat(zf, dest_dir):
    """解压 ZIP 到目标目录，自动处理中文文件名和顶层目录扁平化。

    如果 ZIP 内所有文件都在同一个顶层目录下（如 MyGame/index.html），
    则跳过该顶层目录，直接将内容解压到 dest_dir。
    """
    name_map = {}
    for info in zf.infolist():
        fixed = _fix_zip_filename(info.filename)
        if fixed != info.filename:
            logger.debug("ZIP 文件名修复: %s → %s", info.filename, fixed)
        name_map[info.filename] = fixed

    paths = list(name_map.values())
    files_only = [p for p in paths if not p.endswith('/')]
    if files_only:
        first_parts = set()
        for p in files_only:
            parts = p.replace('\\', '/').split('/')
            if parts[0]:
                first_parts.add(parts[0])
        strip_prefix = ''
        if len(first_parts) == 1:
            strip_prefix = list(first_parts)[0] + '/'
            logger.info("ZIP 检测到顶层目录 '%s'，将跳过它进行扁平化解压", strip_prefix.rstrip('/'))

        for info in zf.infolist():
            fixed_name = name_map[info.filename]
            if fixed_name.endswith('/'):
                continue
            if strip_prefix and fixed_name.startswith(strip_prefix):
                rel_path = fixed_name[len(strip_prefix):]
            else:
                rel_path = fixed_name.replace('\\', '/')
            if not rel_path:
                continue
            target = os.path.join(dest_dir, rel_path.replace('/', os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, 'wb') as dst:
                dst.write(src.read())
            logger.debug("ZIP 解压: %s", rel_path)


def _get_filtered_games():
    """获取游戏列表（排除所有封禁游戏）。

    支持 ?tag= 与 ?q= 搜索。
    """
    tag = request.args.get('tag')
    if tag:
        sql = '''SELECT DISTINCT g.* FROM games g 
                 JOIN game_tags gt ON g.id = gt.game_id 
                 JOIN tags t ON gt.tag_id = t.id 
                 WHERE g.is_banned = 0 AND t.name = %s'''
        args = [tag]
    else:
        sql = 'SELECT * FROM games WHERE is_banned = 0'
        args = []

    q = request.args.get('q')
    if q:
        if tag:
            sql += ' AND (g.title LIKE %s OR g.description LIKE %s)'
        else:
            sql += ' AND (title LIKE %s OR description LIKE %s)'
        args.extend([f'%{q}%', f'%{q}%'])

    if tag:
        sql += ' ORDER BY g.created_at DESC'
    else:
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

    # 获取游戏标签
    game_tags = query(
        'SELECT t.name FROM game_tags gt JOIN tags t ON gt.tag_id = t.id WHERE gt.game_id = %s ORDER BY t.name',
        [gid]
    )

    # 获取版本历史（用于详情页展示更新记录）
    versions = query(
        'SELECT * FROM game_versions WHERE game_id = %s ORDER BY created_at DESC',
        [gid]
    )

    return render_template(
        'game_detail.html',
        game=game,
        reviews=reviews,
        in_library=in_library,
        game_tags=game_tags,
        versions=versions,
        cover_ratio=None  # 由前端 JS 检测封面长宽比并切换布局
    )


@games_bp.route('/game/<int:gid>/description')
def game_description(gid):
    """游戏描述详情页 - 展示完整描述。"""
    g = query_one('SELECT id, title, description FROM games WHERE id = %s', (gid,))
    if not g:
        abort(404)
    return render_template('game_description.html', game=g)


@games_bp.route('/html-editor')
def html_editor():
    """在线 HTML 编辑器页面。"""
    user = current_user()
    if not user:
        abort(403)
    return render_template('html_editor.html')


@games_bp.route('/api/games/<int:gid>/play', methods=['POST'])
def record_play(gid):
    """记录游玩次数（前端进入游戏时调用）。"""
    execute('UPDATE games SET play_count = play_count + 1 WHERE id = %s', (gid,))
    return jsonify({'ok': True})


@games_bp.route('/api/games/<int:gid>/info')
def game_info(gid):
    """获取游戏信息（推送更新时前端自动填充用）。"""
    g = query_one('SELECT * FROM games WHERE id = %s', (gid,))
    if not g:
        return jsonify({'error': '游戏不存在'}), 404
    return jsonify({
        'game_uid': g.get('game_uid', ''),
        'title': g.get('title', ''),
        'description': g.get('description', ''),
        'hosting_type': g.get('hosting_type', 'html'),
        'entry_file': g.get('entry_file', 'index.html'),
        'source_open': g.get('source_open', 1),
        'access_mode': g.get('access_mode', 'public'),
        'external_url': g.get('external_url', ''),
        'price': g.get('price', 0),
        'tags': g.get('tags', ''),
    })


def _save_file_with_hash(file_obj, dest_dir, filename, game_id):
    """保存文件，如果哈希已存在则复用。

    :param file_obj: Flask 上传文件对象
    :param dest_dir: 目标目录绝对路径
    :param filename: 目标文件名
    :param game_id: 游戏 ID（用于生成相对路径）
    :return: 文件的相对路径（如 uploads/games/<id>/<filename>）
    """
    # 读取文件内容并计算哈希
    content = file_obj.read()
    file_hash = hashlib.sha256(content).hexdigest()
    # 检查哈希是否已存在
    existing = query_one('SELECT file_path FROM file_hashes WHERE hash = %s', (file_hash,))
    if existing:
        return existing['file_path']
    # 保存新文件
    file_obj.seek(0)
    filepath = os.path.join(dest_dir, filename)
    file_obj.save(filepath)
    rel_path = f'uploads/games/{game_id}/{filename}'
    execute('INSERT INTO file_hashes (hash, file_path) VALUES (%s, %s)', (file_hash, rel_path))
    return rel_path


@games_bp.route('/api/games/upload', methods=['POST'])
def upload_game():
    """游戏上传接口（登录用户均可，普通用户仅限单 HTML 模式）。

    接收 multipart 表单：
    - title, description, tags, hosting_type
    - cover（图片）, screenshots（多图）, game_files（zip 或单文件）
    - hosting_type=html 时需 entry_file
    - hosting_type=python 时需 python_main、python_command
    - hosting_type=single_html 时可传 html_content 或 game_files（单 HTML 文件）
    - game_uid（可选，未提供则自动生成；已存在则视为版本更新）
    - version（默认 1.0.0）, changelog（更新日志）
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    # 判断是否为开发者（普通用户仅限单 HTML 模式）
    is_dev = has_permission(user['id'], 'developer')

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    tag_names = request.form.get('tags', '').strip()
    hosting_type = request.form.get('hosting_type', 'html').strip()
    source_open = request.form.get('source_open', '1').strip()
    access_mode = request.form.get('access_mode', 'public').strip()
    external_url = request.form.get('external_url', '').strip()
    price = float(request.form.get('price', '0'))

    # 新增字段：游戏唯一ID、版本号、更新日志、HTML 编辑器内容
    game_uid = request.form.get('game_uid', '').strip()
    version = request.form.get('version', '1.0.0').strip() or '1.0.0'
    changelog = request.form.get('changelog', '').strip()
    html_content = request.form.get('html_content', '').strip()

    # 平台抽成固定为 1.0（仅展示用，忽略表单传入值）
    platform_share = 1.0

    # 普通用户强制单 HTML 模式
    if not is_dev:
        hosting_type = 'single_html'

    if not title:
        return jsonify({'success': False, 'message': '标题不能为空'}), 400
    if not tag_names:
        return jsonify({'success': False, 'message': '请至少选择一个标签'}), 400
    if hosting_type not in ('html', 'python', 'single_html'):
        return jsonify({'success': False, 'message': '托管类型无效'}), 400

    # 生成 game_uid（如未提供）
    if not game_uid:
        game_uid = uuid.uuid4().hex[:8].upper()

    logger.info("上传开始: user=%s title=%s type=%s uid=%s", user.get('id'), title, hosting_type, game_uid)

    # 初始状态：HTML/单HTML 直接 active，Python 需审核
    if hosting_type in ('html', 'single_html'):
        initial_status = 'active'
    else:
        initial_status = 'pending_review'

    # 检查是否已存在相同 game_uid 的游戏（版本更新场景）
    existing = query_one('SELECT id, version FROM games WHERE game_uid = %s', (game_uid,))

    if existing:
        # 版本更新：保存旧版本到 game_versions 表
        game_id = existing['id']
        execute(
            'INSERT INTO game_versions (game_id, version, changelog, zip_path, entry_file) '
            'VALUES (%s, %s, %s, %s, %s)',
            (game_id, existing.get('version') or '1.0.0', changelog, '', '')
        )
        # 更新游戏表为新版本信息
        execute(
            'UPDATE games SET title=%s, description=%s, version=%s, hosting_type=%s, status=%s, '
            'tags=%s, source_open=%s, access_mode=%s, external_url=%s, price=%s, platform_share=%s '
            'WHERE id=%s',
            (title, description, version, hosting_type, initial_status, tag_names,
             int(source_open), access_mode, external_url, price, platform_share, game_id)
        )
        logger.info("版本更新 game_id=%s version=%s", game_id, version)
    else:
        # 新游戏：插入记录
        game_id = execute(
            'INSERT INTO games '
            '(title, description, developer_id, hosting_type, status, tags, is_banned, '
            'source_open, access_mode, external_url, price, platform_share, game_uid, version) '
            'VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s)',
            (title, description, user['id'], hosting_type, initial_status, tag_names,
             int(source_open), access_mode, external_url, price, platform_share, game_uid, version)
        )
        logger.info("已创建游戏记录 game_id=%s uid=%s", game_id, game_uid)

    # 写入价格表
    execute(
        'INSERT INTO game_pricing (game_id, price, platform_share) VALUES (%s, %s, %s) '
        'ON DUPLICATE KEY UPDATE price = %s, platform_share = %s',
        [game_id, price, platform_share, price, platform_share]
    )

    # 处理标签：分割逗号，创建不存在的标签，关联到 game_tags
    # 版本更新时先清除旧关联再重新插入
    if existing:
        execute('DELETE FROM game_tags WHERE game_id = %s', [game_id])
    tag_list = [t.strip() for t in tag_names.split(',') if t.strip()]
    for tag_name in tag_list:
        execute('INSERT INTO tags (name) VALUES (%s) ON DUPLICATE KEY UPDATE id=id', [tag_name])
        t = query_one('SELECT id FROM tags WHERE name = %s', [tag_name])
        if t:
            execute('INSERT INTO game_tags (game_id, tag_id) VALUES (%s, %s)', [game_id, t['id']])

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

    # 保存截图（多张）- 普通用户忽略截图
    screenshots = []
    if is_dev:
        for f in request.files.getlist('screenshots'):
            if not f or not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            if ext in _ALLOWED_IMAGE_EXTS:
                fname = f'shot_{len(screenshots)}{ext}'
                f.save(os.path.join(gdir, fname))
                screenshots.append(f'uploads/games/{game_id}/{fname}')

    # 托管类型相关字段
    entry_file = None
    python_main = None
    python_command = None

    if hosting_type == 'single_html':
        # 单 HTML 模式：在线编辑器内容或上传单文件
        if html_content:
            # 从在线编辑器保存 HTML 内容为 index.html
            index_path = os.path.join(gdir, 'index.html')
            with open(index_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            entry_file = 'index.html'
            logger.info("单 HTML 模式：已保存在线编辑器内容 game_id=%s", game_id)
        else:
            # 从上传文件保存（使用哈希去重）
            single_file = request.files.get('game_files')
            if single_file and single_file.filename:
                _save_file_with_hash(single_file, gdir, 'index.html', game_id)
                entry_file = 'index.html'
                logger.info("单 HTML 模式：已保存上传文件 game_id=%s", game_id)
        # 单 HTML 模式状态直接 active
        initial_status = 'active'

    elif hosting_type == 'html':
        # HTML 模式：解压 zip（处理中文乱码 + 目录扁平化）
        zip_file = request.files.get('game_files')
        if zip_file and zip_file.filename:
            try:
                zip_file.stream.seek(0)
                with zipfile.ZipFile(zip_file) as zf:
                    _extract_zip_flat(zf, gdir)
                logger.info("ZIP 解压完成 game_id=%s 解压到 %s", game_id, gdir)
            except zipfile.BadZipFile:
                logger.error("ZIP 文件无效 game_id=%s", game_id)
                return jsonify({'success': False, 'message': '无效的 ZIP 文件'}), 400
            except Exception as e:
                logger.exception("ZIP 解压失败 game_id=%s: %s", game_id, e)
                return jsonify({'success': False, 'message': f'解压失败: {e}'}), 500
        else:
            logger.warning("未收到 game_files 字段 game_id=%s", game_id)
        entry_file = request.form.get('entry_file', 'index.html').strip()

    else:
        # Python 模式：解压 zip + 启动命令
        zip_file = request.files.get('game_files')
        if zip_file and zip_file.filename:
            try:
                zip_file.stream.seek(0)
                with zipfile.ZipFile(zip_file) as zf:
                    _extract_zip_flat(zf, gdir)
                logger.info("ZIP 解压完成 game_id=%s 解压到 %s", game_id, gdir)
            except zipfile.BadZipFile:
                logger.error("ZIP 文件无效 game_id=%s", game_id)
                return jsonify({'success': False, 'message': '无效的 ZIP 文件'}), 400
            except Exception as e:
                logger.exception("ZIP 解压失败 game_id=%s: %s", game_id, e)
                return jsonify({'success': False, 'message': f'解压失败: {e}'}), 500
        else:
            logger.warning("未收到 game_files 字段 game_id=%s", game_id)
        python_main = request.form.get('python_main', '').strip()
        python_command = request.form.get('python_command', '').strip()
        if not python_command:
            return jsonify({'success': False, 'message': 'Python 启动命令不能为空'}), 400

    # 更新游戏记录（封面、截图、入口文件、Python 配置）
    execute(
        'UPDATE games SET cover_image = %s, screenshots = %s, entry_file = %s, '
        'python_main = %s, python_command = %s WHERE id = %s',
        (cover_path, json.dumps(screenshots), entry_file,
         python_main, python_command, game_id)
    )

    # Python 托管：插入审核队列（仅新游戏，版本更新不重复插入）
    if hosting_type == 'python' and not existing:
        execute(
            'INSERT INTO python_review_queue (game_id, status) VALUES (%s, %s)',
            (game_id, 'pending')
        )

    return jsonify({
        'success': True,
        'game_id': game_id,
        'status': initial_status,
        'message': '上传成功' if hosting_type in ('html', 'single_html') else '上传成功，等待管理员审核'
    })


# 上传文件服务基目录（UPLOAD_FOLDER='uploads/games' 的父级，绝对路径）
_UPLOAD_BASE = os.path.abspath(os.path.dirname(UPLOAD_FOLDER))  # e.g. /app/uploads


@games_bp.route('/uploads/<path:path>')
def serve_upload(path):
    """Serve uploaded files (cover images, screenshots, game assets)."""
    return send_from_directory(_UPLOAD_BASE, path)


@games_bp.route('/developer')
def developer_panel():
    """开发者面板：展示当前用户的游戏列表与审核状态。"""
    user = current_user()
    if not user:
        abort(403)

    # 判断是否为开发者（用于模板区分功能入口）
    is_dev = has_permission(user['id'], 'developer')

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

    return render_template('developer.html', games=games, review_map=review_map, is_developer=is_dev)


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
    tag_names = request.form.get('tags', game['tags'] or '').strip()
    source_open = request.form.get('source_open', str(game.get('source_open', 1))).strip()
    access_mode = request.form.get('access_mode', game.get('access_mode', 'public')).strip()
    external_url = request.form.get('external_url', game.get('external_url', '')).strip()
    price = float(request.form.get('price', str(game.get('price', 0))))
    platform_share = float(request.form.get('platform_share', str(game.get('platform_share', 30))))
    old_platform_share = float(game.get('platform_share', 30))

    # 平台抽成变更需提交审核，暂不直接更新
    platform_share_effective = old_platform_share
    if platform_share != old_platform_share:
        execute(
            'INSERT INTO config_review_queue (game_id, field_name, old_value, new_value, status) '
            'VALUES (%s, %s, %s, %s, %s)',
            [gid, 'platform_share', str(old_platform_share), str(platform_share), 'pending']
        )
        logger.info("平台抽成变更已提交审核 game_id=%s old=%s new=%s", gid, old_platform_share, platform_share)
        platform_share_effective = old_platform_share

    # Python 模式可更新启动命令
    python_main = game['python_main']
    python_command = game['python_command']
    if game['hosting_type'] == 'python':
        python_main = request.form.get('python_main', python_main or '').strip()
        python_command = request.form.get('python_command', python_command or '').strip()

    execute(
        'UPDATE games SET title = %s, description = %s, tags = %s, '
        'source_open = %s, access_mode = %s, external_url = %s, price = %s, platform_share = %s, '
        'python_main = %s, python_command = %s WHERE id = %s',
        (title, description, tag_names, int(source_open), access_mode, external_url,
         price, platform_share_effective, python_main, python_command, gid)
    )

    # 更新标签：先删除旧关联，再重新插入
    execute('DELETE FROM game_tags WHERE game_id = %s', [gid])
    tag_list = [t.strip() for t in tag_names.split(',') if t.strip()]
    for tag_name in tag_list:
        execute('INSERT INTO tags (name) VALUES (%s) ON DUPLICATE KEY UPDATE id=id', [tag_name])
        t = query_one('SELECT id FROM tags WHERE name = %s', [tag_name])
        if t:
            execute('INSERT INTO game_tags (game_id, tag_id) VALUES (%s, %s)', [gid, t['id']])

    # 更新价格表（平台抽成使用审核通过后的值，变更中则保持旧值）
    execute(
        'INSERT INTO game_pricing (game_id, price, platform_share) VALUES (%s, %s, %s) '
        'ON DUPLICATE KEY UPDATE price = %s, platform_share = %s',
        [gid, price, platform_share_effective, price, platform_share_effective]
    )

    msg = '更新成功'
    if platform_share != old_platform_share:
        msg = '更新成功。平台抽成变更已提交审核，审核通过后生效。'
    return jsonify({'success': True, 'message': msg})


@games_bp.route('/api/tags')
def list_tags():
    """获取所有标签列表（用于搜索建议）。"""
    tags = query('SELECT id, name FROM tags ORDER BY name')
    return jsonify({'success': True, 'tags': tags})


@games_bp.route('/api/tags', methods=['POST'])
def create_tag():
    """创建新标签（登录用户均可）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    name = (request.get_json(silent=True) or {}).get('name', '').strip()
    if not name or len(name) > 50:
        return jsonify({'success': False, 'message': '标签名 1-50 字符'}), 400
    try:
        execute('INSERT INTO tags (name) VALUES (%s)', [name])
        tag_id = query_one('SELECT id FROM tags WHERE name = %s', [name])
        return jsonify({'success': True, 'tag': {'id': tag_id['id'], 'name': name}})
    except Exception:
        # 标签已存在
        tag_id = query_one('SELECT id FROM tags WHERE name = %s', [name])
        return jsonify({'success': True, 'tag': {'id': tag_id['id'], 'name': name}})


def _delete_game(gid):
    """级联删除游戏：评论、游戏库、审核队列、游戏记录、进程、文件。

    调用方需自行校验权限。
    """
    # 停止 Python 进程（如有）
    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if game:
        try:
            stop_python(game)
        except Exception as e:
            logger.warning("停止进程失败 game_id=%s: %s", gid, e)

    # 级联删除数据库记录
    execute('DELETE FROM reviews WHERE game_id = %s', [gid])
    execute('DELETE FROM game_library WHERE game_id = %s', [gid])
    execute('DELETE FROM python_review_queue WHERE game_id = %s', [gid])
    execute('DELETE FROM game_co_devs WHERE game_id = %s', [gid])
    execute('DELETE FROM invite_codes WHERE game_id = %s', [gid])
    execute('DELETE FROM game_pricing WHERE game_id = %s', [gid])
    execute('DELETE FROM game_tags WHERE game_id = %s', [gid])
    execute('DELETE FROM games WHERE id = %s', [gid])

    # 删除游戏目录
    gdir = game_dir(gid)
    if os.path.exists(gdir):
        try:
            shutil.rmtree(gdir)
        except Exception as e:
            logger.warning("删除游戏目录失败 game_id=%s: %s", gid, e)

    logger.info("游戏已删除 game_id=%s", gid)


@games_bp.route('/api/games/<int:gid>', methods=['DELETE'])
@require_level('developer')
def delete_game(gid):
    """删除游戏（仅开发者本人可操作）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404
    if game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '无权删除他人游戏'}), 403

    _delete_game(gid)
    return jsonify({'success': True, 'message': '游戏已删除'})


@games_bp.route('/api/games/<int:gid>/entry-file', methods=['POST'])
@require_level('developer')
def update_entry_file(gid):
    """更新游戏入口文件路径（仅开发者本人可操作）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404
    if game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '无权操作他人游戏'}), 403

    entry_file = (request.get_json(silent=True) or {}).get('entry_file', '').strip()
    if not entry_file:
        return jsonify({'success': False, 'message': '入口文件不能为空'}), 400

    execute('UPDATE games SET entry_file = %s WHERE id = %s', [entry_file, gid])
    logger.info("入口文件已更新 game_id=%s entry_file=%s", gid, entry_file)
    return jsonify({'success': True, 'message': '入口文件已更新'})


@games_bp.route('/api/games/<int:gid>/versions', methods=['POST'])
@require_level('developer')
def push_version(gid):
    """推送游戏新版本（仅开发者本人或合作开发者可操作）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404

    # 检查权限：开发者本人或合作开发者
    is_dev = game['developer_id'] == user['id']
    if not is_dev:
        co_dev = query_one(
            'SELECT id FROM game_co_devs WHERE game_id = %s AND user_id = %s AND status = %s',
            [gid, user['id'], 'accepted']
        )
        if not co_dev:
            return jsonify({'success': False, 'message': '无权操作他人游戏'}), 403

    version = request.form.get('version', '').strip()
    changelog = request.form.get('changelog', '').strip()
    entry_file = request.form.get('entry_file', '').strip()

    if not version:
        return jsonify({'success': False, 'message': '版本号不能为空'}), 400

    # 解压新版本到游戏目录
    zip_file = request.files.get('game_files')
    if zip_file and zip_file.filename:
        gdir = game_dir(gid)
        old_entry = game.get('entry_file')
        try:
            zip_file.stream.seek(0)
            with zipfile.ZipFile(zip_file) as zf:
                _extract_zip_flat(zf, gdir)
            logger.info("版本更新 ZIP 解压完成 game_id=%s version=%s", gid, version)
        except zipfile.BadZipFile:
            return jsonify({'success': False, 'message': '无效的 ZIP 文件'}), 400
        except Exception as e:
            logger.exception("版本更新 ZIP 解压失败 game_id=%s: %s", gid, e)
            return jsonify({'success': False, 'message': '解压失败: %s' % e}), 500

        # 更新入口文件（如提供）
        if entry_file:
            execute('UPDATE games SET entry_file = %s WHERE id = %s', [entry_file, gid])
        elif game['hosting_type'] == 'html' and old_entry:
            # 如果旧入口文件存在则保留
            pass

    # 记录版本历史
    execute(
        'INSERT INTO game_versions (game_id, version, changelog) VALUES (%s, %s, %s)',
        [gid, version, changelog]
    )

    # 更新游戏 updated_at
    execute('UPDATE games SET updated_at = NOW() WHERE id = %s', [gid])

    return jsonify({'success': True, 'message': '版本更新成功'})


@games_bp.route('/api/games/<int:gid>/versions', methods=['GET'])
def get_versions(gid):
    """获取游戏版本历史列表。"""
    versions = query(
        'SELECT * FROM game_versions WHERE game_id = %s ORDER BY created_at DESC',
        [gid]
    )
    return jsonify({'success': True, 'versions': versions})


# ==================== Part A: 合作开发者机制 ====================

@games_bp.route('/api/games/<int:gid>/co-dev', methods=['GET', 'POST'])
@require_level('developer')
def invite_co_dev(gid):
    """GET 查看合作开发者列表，POST 邀请合作开发者（仅主开发者可操作）。"""
    user = current_user()
    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game or game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '无权操作'}), 403

    if request.method == 'GET':
        co_devs = query(
            """SELECT cd.*, u.username, u.avatar
               FROM game_co_devs cd
               JOIN users u ON cd.user_id = u.id
               WHERE cd.game_id = %s
               ORDER BY cd.invited_at DESC""",
            [gid]
        )
        return jsonify({'success': True, 'co_devs': co_devs})

    # POST：邀请
    username = (request.get_json(silent=True) or {}).get('username', '').strip()
    if not username:
        return jsonify({'success': False, 'message': '请输入用户名'}), 400

    target = query_one('SELECT id FROM users WHERE username = %s', [username])
    if not target:
        return jsonify({'success': False, 'message': '用户不存在'}), 404
    if target['id'] == user['id']:
        return jsonify({'success': False, 'message': '不能邀请自己'}), 400

    # 检查是否已有记录
    existing = query_one(
        'SELECT id, status FROM game_co_devs WHERE game_id = %s AND user_id = %s',
        [gid, target['id']]
    )
    if existing:
        return jsonify({'success': False, 'message': '已邀请过该用户'}), 400

    execute(
        'INSERT INTO game_co_devs (game_id, user_id, status) VALUES (%s, %s, %s)',
        [gid, target['id'], 'pending']
    )
    return jsonify({'success': True, 'message': '邀请已发送'})


@games_bp.route('/api/games/<int:gid>/co-dev/<int:uid>', methods=['DELETE'])
@require_level('developer')
def remove_co_dev(gid, uid):
    """移除合作开发者（仅主开发者可操作）。"""
    user = current_user()
    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game or game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '无权操作'}), 403

    execute('DELETE FROM game_co_devs WHERE game_id = %s AND user_id = %s', [gid, uid])
    return jsonify({'success': True, 'message': '已移除'})


@games_bp.route('/api/co-dev/invitations', methods=['GET'])
def my_invitations():
    """查看我的合作开发者邀请。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    invitations = query(
        """SELECT cd.*, g.title as game_title
           FROM game_co_devs cd
           JOIN games g ON cd.game_id = g.id
           WHERE cd.user_id = %s AND cd.status = 'pending'
           ORDER BY cd.invited_at DESC""",
        [user['id']]
    )
    return jsonify({'success': True, 'invitations': invitations})


@games_bp.route('/api/co-dev/invitations/<int:inv_id>', methods=['POST'])
def handle_invitation(inv_id):
    """接受/拒绝合作开发者邀请。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    inv = query_one(
        'SELECT * FROM game_co_devs WHERE id = %s AND user_id = %s AND status = %s',
        [inv_id, user['id'], 'pending']
    )
    if not inv:
        return jsonify({'success': False, 'message': '邀请不存在或已处理'}), 404

    action = (request.get_json(silent=True) or {}).get('action', 'accept')
    if action == 'accept':
        execute(
            'UPDATE game_co_devs SET status = %s, accepted_at = NOW() WHERE id = %s',
            ['accepted', inv_id]
        )
        return jsonify({'success': True, 'message': '已接受邀请'})
    else:
        execute('DELETE FROM game_co_devs WHERE id = %s', [inv_id])
        return jsonify({'success': True, 'message': '已拒绝邀请'})


# ==================== Part B: 邀请码 & 访问权限体系 ====================

@games_bp.route('/api/games/<int:gid>/invite-codes', methods=['GET', 'POST'])
@require_level('developer')
def manage_invite_codes(gid):
    """管理邀请码：GET 查看列表，POST 批量生成。"""
    user = current_user()
    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404

    # 权限：开发者本人或合作开发者
    is_dev = game['developer_id'] == user['id']
    if not is_dev:
        co_dev = query_one(
            'SELECT id FROM game_co_devs WHERE game_id = %s AND user_id = %s AND status = %s',
            [gid, user['id'], 'accepted']
        )
        if not co_dev:
            return jsonify({'success': False, 'message': '无权操作'}), 403

    if request.method == 'GET':
        codes = query(
            'SELECT * FROM invite_codes WHERE game_id = %s ORDER BY created_at DESC',
            [gid]
        )
        return jsonify({'success': True, 'codes': codes})

    # POST：批量生成
    count = int((request.get_json(silent=True) or {}).get('count', 1))
    if count < 1 or count > 100:
        return jsonify({'success': False, 'message': '数量范围 1-100'}), 400

    import secrets
    import string
    new_codes = []
    for _ in range(count):
        code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))
        execute(
            'INSERT INTO invite_codes (game_id, code, created_by) VALUES (%s, %s, %s)',
            [gid, code, user['id']]
        )
        new_codes.append(code)

    return jsonify({'success': True, 'codes': new_codes, 'message': f'已生成 {count} 个邀请码'})


@games_bp.route('/api/invite/redeem', methods=['POST'])
def redeem_invite():
    """兑换邀请码。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    code = (request.get_json(silent=True) or {}).get('code', '').strip().upper()
    if not code:
        return jsonify({'success': False, 'message': '请输入邀请码'}), 400

    inv = query_one('SELECT * FROM invite_codes WHERE code = %s', [code])
    if not inv:
        return jsonify({'success': False, 'message': '无效的邀请码'}), 404
    if inv['is_used']:
        return jsonify({'success': False, 'message': '邀请码已被使用'}), 400

    # 标记已使用
    execute(
        'UPDATE invite_codes SET is_used = 1, used_by = %s, used_at = NOW() WHERE id = %s',
        [user['id'], inv['id']]
    )

    # 自动加入游戏库（如果还没有）
    lib = query_one(
        'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
        [user['id'], inv['game_id']]
    )
    if not lib:
        execute(
            'INSERT INTO game_library (user_id, game_id) VALUES (%s, %s)',
            [user['id'], inv['game_id']]
        )

    game = query_one('SELECT title FROM games WHERE id = %s', [inv['game_id']])
    return jsonify({
        'success': True,
        'message': f'兑换成功！已获得「{game["title"]}」的游玩权限',
        'game_id': inv['game_id']
    })


@games_bp.route('/developer/<int:gid>/data')
@require_level('developer')
def developer_data(gid):
    """开发者数据面板：查看游戏运营数据。"""
    user = current_user()
    if not user:
        abort(403)

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        abort(404)

    # 权限检查：开发者本人或合作开发者
    is_dev = game['developer_id'] == user['id']
    if not is_dev:
        co_dev = query_one(
            'SELECT id FROM game_co_devs WHERE game_id = %s AND user_id = %s AND status = %s',
            [gid, user['id'], 'accepted']
        )
        if not co_dev:
            return jsonify({'success': False, 'message': '无权访问'}), 403

    # 统计数据
    player_count = query_one(
        'SELECT COUNT(*) as cnt FROM game_library WHERE game_id = %s', [gid]
    )['cnt']

    # 评分分布
    ratings = query(
        'SELECT rating, COUNT(*) as cnt FROM reviews WHERE game_id = %s GROUP BY rating ORDER BY rating',
        [gid]
    )

    # 建议帖列表（按 game_uid 关联）
    game_uid = game.get('game_uid') or str(gid)
    suggestions = query(
        """SELECT p.*, u.username, u.avatar 
           FROM community_posts p 
           JOIN users u ON p.user_id = u.id 
           WHERE p.game_tag = %s 
           ORDER BY p.created_at DESC""",
        [game_uid]
    )

    return render_template(
        'developer_data.html',
        game=game,
        player_count=player_count,
        ratings=ratings,
        suggestions=suggestions
    )


@games_bp.route('/api/developer/<int:gid>/suggestions')
@require_level('developer')
def api_suggestions(gid):
    """API：筛选建议帖（支持 ?sort=likes&starred=1&q=关键词）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404

    is_dev = game['developer_id'] == user['id']
    if not is_dev:
        co_dev = query_one(
            'SELECT id FROM game_co_devs WHERE game_id = %s AND user_id = %s AND status = %s',
            [gid, user['id'], 'accepted']
        )
        if not co_dev:
            return jsonify({'success': False, 'message': '无权访问'}), 403

    sql = """SELECT p.*, u.username, u.avatar 
             FROM community_posts p 
             JOIN users u ON p.user_id = u.id 
             WHERE p.game_tag = %s"""
    # 按 game_uid 关联建议帖
    game_uid = game.get('game_uid') or str(gid)
    args = [game_uid]

    sort = request.args.get('sort', 'new')
    starred = request.args.get('starred')
    q = request.args.get('q')

    if starred == '1':
        sql += ' AND p.is_starred = 1'
    if q:
        sql += ' AND (p.title LIKE %s OR p.content LIKE %s)'
        args.extend([f'%{q}%', f'%{q}%'])

    if sort == 'likes':
        sql += ' ORDER BY p.likes DESC'
    else:
        sql += ' ORDER BY p.created_at DESC'

    suggestions = query(sql, args)
    return jsonify({'success': True, 'suggestions': suggestions})
