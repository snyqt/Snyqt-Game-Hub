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
    """获取游戏列表（排除所有封禁游戏和私密游戏）。

    支持 ?tag= 与 ?q= 搜索（q 同时匹配标题、描述、标签、game_uid）。
    私密游戏（access_mode='private'）不显示在公开列表中。
    """
    tag = request.args.get('tag')
    if tag:
        sql = '''SELECT DISTINCT g.* FROM games g
                 JOIN game_tags gt ON g.id = gt.game_id
                 JOIN tags t ON gt.tag_id = t.id
                 WHERE g.is_banned = 0 AND g.access_mode != 'private' AND t.name = %s'''
        args = [tag]
    else:
        sql = "SELECT * FROM games WHERE is_banned = 0 AND access_mode != 'private'"
        args = []

    q = request.args.get('q')
    if q:
        kw = f'%{q}%'
        if tag:
            sql += (' AND (g.title LIKE %s OR g.description LIKE %s '
                    'OR g.tags LIKE %s OR g.game_uid LIKE %s)')
            args.extend([kw, kw, kw, kw])
        else:
            sql += (' AND (title LIKE %s OR description LIKE %s '
                    'OR tags LIKE %s OR game_uid LIKE %s)')
            args.extend([kw, kw, kw, kw])

    if tag:
        sql += ' ORDER BY g.created_at DESC'
    else:
        sql += ' ORDER BY created_at DESC'
    return query(sql, args)


@games_bp.route('/')
def index():
    """首页：游戏展示区 + 管理员认证标签精选栏。"""
    games = _get_filtered_games()
    # 编辑精选：仅展示评鉴员推荐的游戏（editor_picks 表，排除私密游戏）
    editor_picks = query('''
        SELECT g.id, g.title, g.game_uid, g.description, g.cover_image,
               g.hosting_type, g.tags, g.play_count, g.category
        FROM editor_picks ep
        JOIN games g ON ep.game_id = g.id
        WHERE g.is_banned = 0 AND g.status = 'active' AND g.access_mode != 'private'
        ORDER BY ep.sort_order ASC, ep.created_at DESC
        LIMIT 6
    ''')
    # 认证标签精选：仅展示 is_verified=1 且关联了至少1款非封禁游戏的标签
    verified_tags = query('''
        SELECT t.id, t.name, COUNT(g.id) AS game_count
        FROM tags t
        JOIN game_tags gt ON t.id = gt.tag_id
        JOIN games g ON gt.game_id = g.id AND g.is_banned = 0
        WHERE t.is_verified = 1
        GROUP BY t.id, t.name
        HAVING game_count > 0
        ORDER BY game_count DESC, t.name ASC
    ''')
    # 全量标签（用于"更多标签"展开）：仅包含关联了至少1款非封禁游戏的标签
    all_tags = query('''
        SELECT t.id, t.name, t.is_verified, COUNT(g.id) AS game_count
        FROM tags t
        JOIN game_tags gt ON t.id = gt.tag_id
        JOIN games g ON gt.game_id = g.id AND g.is_banned = 0
        GROUP BY t.id, t.name, t.is_verified
        HAVING game_count > 0
        ORDER BY t.is_verified DESC, game_count DESC, t.name ASC
    ''')

    # 社区热帖 Top 5（按点赞数 + 评论数排序）
    # 注：community_posts 表无 views 列，使用 0 AS views 占位以兼容模板字段
    hot_posts = query('''
        SELECT p.id, p.title, p.post_type AS type, p.likes, p.comment_count,
               0 AS views, p.created_at, u.username, u.avatar
        FROM community_posts p
        JOIN users u ON p.user_id = u.id
        WHERE (p.status IS NULL OR p.status != 'banned')
        ORDER BY p.likes DESC, p.comment_count DESC LIMIT 5
    ''')

    # 玩家最新评价 5 条
    recent_reviews = query('''
        SELECT r.id, r.rating, r.comment, r.created_at,
               g.id AS game_id, g.title AS game_title, g.cover_image AS game_cover,
               u.username, u.avatar
        FROM reviews r
        JOIN games g ON r.game_id = g.id
        JOIN users u ON r.user_id = u.id
        WHERE g.is_banned = 0
        ORDER BY r.created_at DESC LIMIT 5
    ''')

    # 最新公告（从 announcements 表查询当前生效的公告，最多 10 条）
    announcements = query(
        """SELECT id, title, type, content, is_pinned, created_at
           FROM announcements
           WHERE status = 'active'
             AND (start_at IS NULL OR start_at <= NOW())
             AND (end_at IS NULL OR end_at >= NOW())
           ORDER BY is_pinned DESC, created_at DESC
           LIMIT 10"""
    ) or []

    return render_template(
        'index.html',
        games=games,
        editor_picks=editor_picks,
        verified_tags=verified_tags,
        all_tags=all_tags,
        hot_posts=hot_posts,
        recent_reviews=recent_reviews,
        announcements=announcements,
    )


@games_bp.route('/api/games')
def api_games():
    """游戏列表 API（JSON）。"""
    games = _get_filtered_games()
    return jsonify({'success': True, 'games': games})


@games_bp.route('/store')
def store():
    """游戏库：商店精选 + 30天趋势 + 标签筛选 + 游戏网格。"""
    # 商店精选 Banner（取下载量最高的 5 款作为轮播，排除私密游戏）
    banners = query(
        'SELECT id, title, game_uid, description, cover_image, download_count '
        "FROM games WHERE is_banned = 0 AND status = 'active' AND cover_image IS NOT NULL "
        "AND access_mode != 'private' "
        'ORDER BY download_count DESC LIMIT 5'
    )

    # 最近 30 天游戏总量变化趋势（按天聚合，排除私密游戏）
    trend = query('''
        SELECT DATE(created_at) AS date, COUNT(*) AS count
        FROM games
        WHERE is_banned = 0 AND status = "active" AND access_mode != 'private'
          AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY DATE(created_at)
        ORDER BY date ASC
    ''')

    # 全部活跃游戏（排除私密游戏）
    games = query(
        'SELECT id, title, game_uid, description, cover_image, hosting_type, '
        'tags, price, access_mode, download_count, play_count, avg_rating, rating_count, created_at '
        "FROM games WHERE is_banned = 0 AND status = 'active' AND access_mode != 'private' "
        'ORDER BY download_count DESC, created_at DESC'
    )

    # 全部标签（带游戏数）
    all_tags = query('''
        SELECT t.id, t.name, t.is_verified, COUNT(gt.game_id) AS game_count
        FROM tags t
        JOIN game_tags gt ON t.id = gt.tag_id
        JOIN games g ON gt.game_id = g.id AND g.is_banned = 0 AND g.status = "active"
        GROUP BY t.id, t.name, t.is_verified
        HAVING game_count > 0
        ORDER BY game_count DESC, t.name ASC
    ''')

    return render_template(
        'store.html',
        banners=banners,
        trend=trend,
        games=games,
        all_tags=all_tags,
    )


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
    # 私密游戏除开发者本人和合作开发者外不可见
    if game.get('access_mode') == 'private' and game['developer_id'] != user_id:
        co_dev = query_one(
            'SELECT id FROM game_co_devs WHERE game_id = %s AND user_id = %s AND status = %s',
            [gid, user_id, 'accepted']
        )
        if not co_dev:
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

    # 检查是否已被评鉴员推荐为编辑精选（供详情页评鉴员切换按钮使用）
    editor_pick = query_one(
        'SELECT id FROM editor_picks WHERE game_id = %s', [gid]
    )

    return render_template(
        'game_detail.html',
        game=game,
        reviews=reviews,
        in_library=in_library,
        is_editor_picked=(editor_pick is not None),
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
        logger.warning("game_info: 游戏 %s 不存在", gid)
        return jsonify({'error': '游戏不存在'}), 404
    # 详细日志：记录原始字段，便于排查自动填充不生效问题
    logger.info(
        "game_info: gid=%s title=%r description_len=%s description_repr=%r version=%s",
        gid, g.get('title'), len(g.get('description') or ''), g.get('description'), g.get('version')
    )
    return jsonify({
        'game_uid': g.get('game_uid') or '',
        'title': g.get('title') or '',
        'description': g.get('description') or '',
        'version': g.get('version') or '1.0.0',
        'hosting_type': g.get('hosting_type') or 'html',
        'entry_file': g.get('entry_file') or 'index.html',
        'source_open': g.get('source_open', 1),
        'access_mode': g.get('access_mode') or 'public',
        'external_url': g.get('external_url') or '',
        'price': g.get('price', 0),
        'tags': g.get('tags') or '',
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
    # 非邀请码准入模式强制价格为 0（价格仅用于邀请码售卖）
    if access_mode != 'invite':
        price = 0.0

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
    if hosting_type not in ('html', 'python', 'single_html', 'download'):
        return jsonify({'success': False, 'message': '托管类型无效'}), 400

    # 生成 game_uid（如未提供）
    if not game_uid:
        game_uid = uuid.uuid4().hex[:8].upper()

    logger.info("上传开始: user=%s title=%s type=%s uid=%s", user.get('id'), title, hosting_type, game_uid)

    # 初始状态：HTML/单HTML/下载 直接 active，Python 需审核
    if hosting_type in ('html', 'single_html', 'download'):
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

    elif hosting_type == 'download':
        # 不托管（仅下载）模式：可上传安装包/可执行文件，入口文件由用户自定义
        # 不强制上传文件（可后续在控制面板补充）；入口文件由表单 entry_file 提供
        dl_file = request.files.get('game_files')
        if dl_file and dl_file.filename:
            # 保存原始文件名（防止中文乱码）
            filename = dl_file.filename
            dl_file.save(os.path.join(gdir, filename))
            logger.info("下载模式：已保存文件 %s game_id=%s", filename, game_id)
        # 入口文件由用户输入（可执行文件名/启动命令），不再强制 index.html
        entry_file = request.form.get('entry_file', '').strip()
        if not entry_file:
            # 未提供时给空字符串（允许后续在控制面板补充）
            entry_file = ''

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
    # 版本更新场景：未上传新封面/截图时保留原值，避免清空
    if existing:
        # 仅更新实际提供了新值的字段
        if cover_path:
            execute('UPDATE games SET cover_image = %s WHERE id = %s', [cover_path, game_id])
        if screenshots:
            execute('UPDATE games SET screenshots = %s WHERE id = %s', [json.dumps(screenshots), game_id])
        if entry_file:
            execute('UPDATE games SET entry_file = %s WHERE id = %s', [entry_file, game_id])
        if hosting_type == 'python':
            execute(
                'UPDATE games SET python_main = %s, python_command = %s WHERE id = %s',
                [python_main, python_command, game_id]
            )
    else:
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
        'message': '上传成功' if hosting_type in ('html', 'single_html', 'download') else '上传成功，等待管理员审核'
    })


# 上传文件服务基目录（UPLOAD_FOLDER='uploads/games' 的父级，绝对路径）
_UPLOAD_BASE = os.path.abspath(os.path.dirname(UPLOAD_FOLDER))  # e.g. /app/uploads


@games_bp.route('/uploads/<path:path>')
def serve_upload(path):
    """Serve uploaded files (cover images, screenshots, game assets)."""
    return send_from_directory(_UPLOAD_BASE, path)


@games_bp.route('/creator-center')
def creator_center():
    """创作者中心：展示创作活动、草稿、投稿列表。"""
    user = current_user()
    if not user:
        return render_template('view_denied.html', message='请先登录'), 403

    # 获取用户已发布的游戏（按状态分组）
    my_games = query(
        'SELECT id, title, game_uid, status, hosting_type, cover_image, version, '
        'created_at, download_count, play_count, avg_rating, rating_count '
        'FROM games WHERE developer_id = %s ORDER BY created_at DESC',
        [user['id']]
    )

    # 按状态分组统计
    status_counts = {
        'total': len(my_games),
        'active': sum(1 for g in my_games if g.get('status') == 'active'),
        'pending_review': sum(1 for g in my_games if g.get('status') == 'pending_review'),
        'rejected': sum(1 for g in my_games if g.get('status') == 'rejected'),
        'draft': sum(1 for g in my_games if g.get('status') == 'draft'),
    }

    # 草稿（status='draft' 的游戏，7天清理提示）
    drafts = [g for g in my_games if g.get('status') == 'draft']

    # 获取用户已发布的素材
    my_assets = query(
        'SELECT id, title, tagline, category, cover_image, price, version, '
        'download_count, asset_uid, status, updated_at, created_at '
        'FROM assets WHERE author_id = %s ORDER BY updated_at DESC',
        [user['id']]
    )

    # 开发者 SB 收益统计
    earnings_total = query_one(
        'SELECT COALESCE(SUM(amount), 0) AS total FROM game_earnings WHERE developer_id = %s',
        [user['id']]
    )
    earnings_count = query_one(
        'SELECT COUNT(*) AS cnt FROM game_earnings WHERE developer_id = %s',
        [user['id']]
    )

    return render_template(
        'creator_center.html',
        my_games=my_games,
        status_counts=status_counts,
        drafts=drafts,
        my_assets=my_assets,
        earnings_total=float(earnings_total['total']) if earnings_total else 0,
        earnings_count=earnings_count['cnt'] if earnings_count else 0,
    )


@games_bp.route('/submit-game')
def submit_game():
    """提交游戏表单页（新 wakudemo 风格）。"""
    user = current_user()
    if not user:
        return render_template('view_denied.html', message='请先登录'), 403

    # 普通用户仅支持单 HTML 模式，开发者可使用全部托管类型
    is_developer = has_permission(user['id'], 'developer')

    # 如果带 game_uid 参数，则是更新模式：预填游戏信息
    game_uid = request.args.get('game_uid', '').strip()
    existing_game = None
    if game_uid:
        existing_game = query_one(
            'SELECT * FROM games WHERE game_uid = %s AND developer_id = %s',
            [game_uid, user['id']]
        )

    # 获取热门标签（用于推荐）
    popular_tags = query('''
        SELECT t.name FROM tags t
        JOIN game_tags gt ON t.id = gt.tag_id
        JOIN games g ON gt.game_id = g.id AND g.is_banned = 0
        GROUP BY t.name ORDER BY COUNT(gt.game_id) DESC LIMIT 15
    ''')

    return render_template(
        'submit_game.html',
        existing_game=existing_game,
        popular_tags=[t['name'] for t in popular_tags],
        is_developer=is_developer,
        current_user_data=user,
    )


@games_bp.route('/submit-asset')
def submit_asset():
    """提交素材表单页。"""
    user = current_user()
    if not user:
        return render_template('view_denied.html', message='请先登录'), 403

    # 获取热门素材标签（用于推荐）
    popular_tags = ['2D', '3D', '像素风', '低模', 'PBR', '写实', '怪物', '主角',
                    '横版', '俯视角', '卡牌', '背景', 'Tilemap', '动画', 'UI',
                    '音效', 'Unity', 'Unreal']

    return render_template(
        'submit_asset.html',
        popular_tags=popular_tags,
    )


@games_bp.route('/feedback')
def feedback():
    """反馈社区页：功能提案投票 + 反馈列表。"""
    user = current_user()

    # 功能提案和用户反馈暂未持久化，显示空状态
    proposals = []
    feedbacks = []

    return render_template(
        'feedback.html',
        proposals=proposals,
        feedbacks=feedbacks,
    )


def _asset_tags_list(tags_str):
    """把素材 tags 字段（逗号分隔字符串）转为列表。"""
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(',') if t.strip()]


def _asset_image_count(asset):
    """统计素材预览图数量（preview_images 是 JSON 数组字符串）。"""
    pi = asset.get('preview_images') if isinstance(asset, dict) else None
    if not pi:
        return 0
    try:
        arr = json.loads(pi) if isinstance(pi, str) else pi
        return len(arr) if isinstance(arr, list) else 0
    except (ValueError, TypeError):
        return 0


def _format_asset_row(row, author_name=''):
    """把数据库返回的素材行整理成模板所需的字典。"""
    return {
        'id': row['id'],
        'title': row.get('title', ''),
        'cover_image': row.get('cover_image') or '',
        'author': author_name or row.get('author_name', '') or '匿名',
        'category': row.get('category', 'other'),
        'tags': _asset_tags_list(row.get('tags', '')),
        'image_count': _asset_image_count(row),
        'updated_at': (str(row.get('updated_at')) or '')[:10],
        'price': float(row.get('price') or 0),
    }


@games_bp.route('/assets')
def assets_library():
    """游戏素材库：精选素材 + 搜索 + 标签筛选 + 素材网格（真实数据）。"""
    # 拉取全部已发布素材 + 作者用户名
    rows = query('''
        SELECT a.*, u.username AS author_name
        FROM assets a
        LEFT JOIN users u ON a.author_id = u.id
        WHERE a.status = 'active'
        ORDER BY a.download_count DESC, a.updated_at DESC
    ''')

    all_assets = [_format_asset_row(r) for r in rows]

    # 精选区：最多 3 条（下载量最高、最新更新、随机一条作为新品）
    featured_assets = []
    if all_assets:
        # weekly_pick：下载量最高
        weekly = max(all_assets, key=lambda x: x.get('image_count', 0)) if all_assets else None
        if weekly:
            ft = dict(weekly)
            ft['feature_type'] = 'weekly_pick'
            featured_assets.append(ft)
        # hot_select：第二多
        if len(all_assets) > 1:
            sorted_by_count = sorted(all_assets, key=lambda x: x.get('image_count', 0), reverse=True)
            hot = dict(sorted_by_count[1])
            hot['feature_type'] = 'hot_select'
            featured_assets.append(hot)
        # new_arrival：最新更新
        if len(all_assets) > 2:
            sorted_by_time = sorted(all_assets, key=lambda x: x.get('updated_at', ''), reverse=True)
            new = dict(sorted_by_time[0])
            new['feature_type'] = 'new_arrival'
            # 去重（避免与 weekly/hot 重复）
            existing_ids = {f['id'] for f in featured_assets}
            if new['id'] not in existing_ids:
                featured_assets.append(new)
            else:
                # 取下一条不重复的
                for r in sorted_by_time[1:]:
                    if r['id'] not in existing_ids:
                        nn = dict(r)
                        nn['feature_type'] = 'new_arrival'
                        featured_assets.append(nn)
                        break

    # 全部可用标签（聚合去重）
    tag_set = set()
    for a in all_assets:
        for t in a['tags']:
            tag_set.add(t)
    all_tags = sorted(tag_set)

    return render_template(
        'assets.html',
        featured_assets=featured_assets,
        all_assets=all_assets,
        all_tags=all_tags,
    )


def _asset_dir(asset_id):
    """素材文件存储目录：uploads/assets/<id>/"""
    return os.path.abspath(os.path.join(UPLOAD_FOLDER, '..', 'assets', str(asset_id)))


def _save_asset_file_with_hash(file_obj, dest_dir, filename, asset_id):
    """保存素材包文件，若哈希已存在则复用。

    :return: 文件相对路径（如 uploads/assets/<id>/<filename>）
    """
    content = file_obj.read()
    file_hash = hashlib.sha256(content).hexdigest()
    existing = query_one('SELECT file_path FROM file_hashes WHERE hash = %s', (file_hash,))
    if existing:
        return existing['file_path']
    file_obj.seek(0)
    os.makedirs(dest_dir, exist_ok=True)
    filepath = os.path.join(dest_dir, filename)
    file_obj.save(filepath)
    rel_path = f'uploads/assets/{asset_id}/{filename}'
    execute('INSERT INTO file_hashes (hash, file_path) VALUES (%s, %s)', (file_hash, rel_path))
    return rel_path


@games_bp.route('/api/assets/upload', methods=['POST'])
def upload_asset():
    """素材上传接口（需登录）。

    接收 multipart 表单：
    - title, tagline, description, version
    - license（checkbox 多选）, license_serialized（隐藏字段，逗号分隔）
    - category（radio）, category_value（隐藏字段）
    - price（数字，0 表示免费）
    - tags（隐藏字段，逗号分隔字符串）
    - cover（封面图）, asset_package（素材包文件）, previews（多张预览图）
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    title = request.form.get('title', '').strip()
    tagline = request.form.get('tagline', '').strip()
    description = request.form.get('description', '').strip()
    version = request.form.get('version', '1.0.0').strip() or '1.0.0'
    category = request.form.get('category_value', '').strip() or request.form.get('category', '').strip()
    price = float(request.form.get('price', '0') or 0)
    tags_str = request.form.get('tags', '').strip()
    license_serialized = request.form.get('license_serialized', '').strip()

    # 校验
    if not title or len(title) < 2:
        return jsonify({'success': False, 'message': '标题至少 2 个字符'}), 400
    if not tagline:
        return jsonify({'success': False, 'message': '请填写一句话卖点'}), 400
    if not category:
        return jsonify({'success': False, 'message': '请选择分类'}), 400
    if category not in ('character', 'scene', 'ui', 'audio', 'effect', 'tool', 'other'):
        return jsonify({'success': False, 'message': '分类无效'}), 400
    if not tags_str:
        return jsonify({'success': False, 'message': '请至少选择一个标签'}), 400
    if price < 0:
        return jsonify({'success': False, 'message': '价格不能为负'}), 400

    cover = request.files.get('cover')
    if not cover or not cover.filename:
        return jsonify({'success': False, 'message': '请上传封面图'}), 400

    pkg = request.files.get('asset_package')
    if not pkg or not pkg.filename:
        return jsonify({'success': False, 'message': '请上传素材包'}), 400

    # 生成 asset_uid
    asset_uid = uuid.uuid4().hex[:8].upper()

    # 推导 license_type 与 license_detail
    license_flags = [s.strip() for s in license_serialized.split(',') if s.strip()]
    if 'commercial' in license_flags:
        license_type = 'commercial'
    else:
        license_type = 'cc-by'
    license_detail = '允许署名' if 'attribution' in license_flags else ''
    if 'no-redistribute' in license_flags:
        license_detail = (license_detail + '；' if license_detail else '') + '禁止再分发'

    # 先插入数据拿到 asset_id，再保存文件
    asset_id = execute(
        'INSERT INTO assets (title, tagline, description, author_id, category, tags, '
        'cover_image, asset_file, asset_size, preview_images, price, version, '
        'license_type, license_detail, status, download_count, asset_uid) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s)',
        (title, tagline, description, user['id'], category, tags_str,
         '', '', 0, '[]', price, version,
         license_type, license_detail, 'active', asset_uid)
    )

    # 创建素材目录
    adir = _asset_dir(asset_id)
    os.makedirs(adir, exist_ok=True)

    # 保存封面图
    cover_path = ''
    ext = os.path.splitext(cover.filename)[1].lower()
    if ext in _ALLOWED_IMAGE_EXTS:
        cover_name = f'cover{ext}'
        cover.save(os.path.join(adir, cover_name))
        cover_path = f'uploads/assets/{asset_id}/{cover_name}'
    else:
        # 回滚：删除刚插入的记录
        execute('DELETE FROM assets WHERE id = %s', [asset_id])
        return jsonify({'success': False, 'message': '封面图格式无效，仅支持 jpg/png/gif/webp'}), 400

    # 保存素材包（哈希去重）
    pkg_ext = os.path.splitext(pkg.filename)[1].lower()
    allowed_pkg_exts = ('.zip', '.rar', '.7z', '.unitypackage')
    if pkg_ext not in allowed_pkg_exts:
        execute('DELETE FROM assets WHERE id = %s', [asset_id])
        return jsonify({'success': False, 'message': '素材包格式无效，仅支持 zip/rar/7z/unitypackage'}), 400
    pkg_name = f'package{pkg_ext}'
    pkg_path = _save_asset_file_with_hash(pkg, adir, pkg_name, asset_id)
    pkg_size = os.path.getsize(os.path.join(_UPLOAD_BASE, pkg_path)) if os.path.exists(os.path.join(_UPLOAD_BASE, pkg_path)) else 0

    # 保存预览图（多张）
    previews = []
    for idx, f in enumerate(request.files.getlist('previews')):
        if not f or not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1].lower()
        if ext in _ALLOWED_IMAGE_EXTS:
            fname = f'preview_{idx}{ext}'
            f.save(os.path.join(adir, fname))
            previews.append(f'uploads/assets/{asset_id}/{fname}')

    # 更新素材记录的文件路径
    execute(
        'UPDATE assets SET cover_image = %s, asset_file = %s, asset_size = %s, preview_images = %s '
        'WHERE id = %s',
        (cover_path, pkg_path, pkg_size, json.dumps(previews), asset_id)
    )

    logger.info("素材上传成功 asset_id=%s uid=%s author=%s", asset_id, asset_uid, user.get('id'))

    return jsonify({
        'success': True,
        'asset_id': asset_id,
        'asset_uid': asset_uid,
        'message': '素材上传成功'
    })


@games_bp.route('/asset/<int:aid>')
def asset_detail(aid):
    """素材详情页。"""
    row = query_one('''
        SELECT a.*, u.username AS author_name, u.avatar AS author_avatar
        FROM assets a
        LEFT JOIN users u ON a.author_id = u.id
        WHERE a.id = %s
    ''', [aid])
    if not row:
        abort(404)
    if row.get('status') != 'active':
        abort(404)

    # 解析预览图列表
    try:
        preview_images = json.loads(row.get('preview_images') or '[]')
    except (ValueError, TypeError):
        preview_images = []

    # 当前用户是否已加入素材库
    user = current_user()
    in_library = False
    if user:
        lib = query_one(
            'SELECT id FROM asset_library WHERE user_id = %s AND asset_id = %s',
            [user['id'], aid]
        )
        in_library = lib is not None

    asset = {
        'id': row['id'],
        'title': row.get('title', ''),
        'tagline': row.get('tagline', ''),
        'description': row.get('description', ''),
        'category': row.get('category', 'other'),
        'tags': _asset_tags_list(row.get('tags', '')),
        'cover_image': row.get('cover_image') or '',
        'preview_images': preview_images,
        'price': float(row.get('price') or 0),
        'version': row.get('version', '1.0.0'),
        'license_type': row.get('license_type', 'cc-by'),
        'license_detail': row.get('license_detail', ''),
        'download_count': row.get('download_count', 0),
        'asset_uid': row.get('asset_uid', ''),
        'updated_at': row.get('updated_at'),
        'created_at': row.get('created_at'),
        'author_name': row.get('author_name') or '匿名',
        'author_avatar': row.get('author_avatar') or '',
        'author_id': row.get('author_id'),
        'asset_size': row.get('asset_size', 0),
        'in_library': in_library,
    }

    return render_template('asset_detail.html', asset=asset, current_user_data=user)


@games_bp.route('/api/assets/<int:aid>/acquire', methods=['POST'])
def acquire_asset(aid):
    """获取素材（加入素材库）。若价格 > 0 则扣除积分。

    - 免费素材：直接加入 asset_library，award_points +2
    - 付费素材：校验积分余额 → 扣分 → 加入 asset_library → 给作者 award_points
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    asset = query_one('SELECT id, author_id, price, status, title FROM assets WHERE id = %s', [aid])
    if not asset or asset.get('status') != 'active':
        return jsonify({'success': False, 'message': '素材不存在或已下架'}), 404

    # 已在库中
    existing = query_one(
        'SELECT id FROM asset_library WHERE user_id = %s AND asset_id = %s',
        [user['id'], aid]
    )
    if existing:
        return jsonify({'success': True, 'message': '素材已在库中', 'already': True})

    price = float(asset.get('price') or 0)
    if price > 0:
        # 校验积分余额
        u = query_one('SELECT points FROM users WHERE id = %s', [user['id']])
        if not u or (u.get('points') or 0) < price:
            return jsonify({
                'success': False,
                'message': f'积分不足，需要 {price:.0f} 积分，当前余额 {u.get("points") or 0}'
            }), 403

        # 扣除购买者积分
        from app.points import award_points
        award_points(user['id'], -int(price), 'asset_purchase', f'购买素材 #{aid} {asset.get("title", "")}')
        # 奖励作者（作者获得 90% 积分，平台抽成 10%）
        author_reward = int(price * 0.9)
        if author_reward > 0 and asset['author_id'] != user['id']:
            award_points(asset['author_id'], author_reward, 'asset_sale',
                         f'素材 #{aid} 被购买，分成 {author_reward} 积分')

    # 加入素材库
    execute(
        'INSERT INTO asset_library (user_id, asset_id) VALUES (%s, %s)',
        (user['id'], aid)
    )
    # 获取行为奖励 +2 积分
    from app.points import award_points
    award_points(user['id'], 2, 'asset_acquire', f'获取素材 #{aid}')

    return jsonify({'success': True, 'message': '已加入素材库'})


@games_bp.route('/api/assets/<int:aid>/download')
def download_asset(aid):
    """下载素材包（需登录且已加入素材库）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    asset = query_one('SELECT * FROM assets WHERE id = %s', [aid])
    if not asset or asset.get('status') != 'active':
        return jsonify({'success': False, 'message': '素材不存在或已下架'}), 404

    # 校验已入库（作者本人可直接下载）
    if asset['author_id'] != user['id']:
        lib = query_one(
            'SELECT id FROM asset_library WHERE user_id = %s AND asset_id = %s',
            [user['id'], aid]
        )
        if not lib:
            return jsonify({'success': False, 'message': '请先获取素材后再下载'}), 403

    asset_file = asset.get('asset_file') or ''
    if not asset_file:
        return jsonify({'success': False, 'message': '素材包文件缺失'}), 404

    # 下载计数 +1
    execute('UPDATE assets SET download_count = download_count + 1 WHERE id = %s', [aid])

    # 文件实际路径
    abs_path = os.path.join(_UPLOAD_BASE, asset_file)
    if not os.path.exists(abs_path):
        return jsonify({'success': False, 'message': '素材包文件不存在'}), 404

    download_name = f"{asset.get('title', 'asset')}_{asset.get('version', '1.0.0')}{os.path.splitext(asset_file)[1]}"
    return send_from_directory(
        os.path.dirname(abs_path),
        os.path.basename(abs_path),
        as_attachment=True,
        download_name=download_name
    )


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
    # 非邀请码准入模式强制价格为 0（价格仅用于邀请码售卖）
    if access_mode != 'invite':
        price = 0.0
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


@games_bp.route('/api/tags/<int:tid>/verify', methods=['POST'])
def verify_tag(tid):
    """管理员认证/取消认证标签。"""
    user = current_user()
    if not user or not _is_admin(user):
        return jsonify({'success': False, 'message': '权限不足'}), 403
    tag = query_one('SELECT id, name, is_verified FROM tags WHERE id = %s', [tid])
    if not tag:
        return jsonify({'success': False, 'message': '标签不存在'}), 404
    new_val = 0 if tag['is_verified'] else 1
    execute('UPDATE tags SET is_verified = %s WHERE id = %s', [new_val, tid])
    action = '已认证' if new_val else '已取消认证'
    return jsonify({'success': True, 'message': f'标签「{tag["name"]}」{action}', 'is_verified': new_val})


@games_bp.route('/api/tags/search')
def search_tags():
    """标签模糊搜索 API（用于标签弹窗选择器）。

    GET /api/tags/search?q=xxx&limit=20
    返回：{ success: true, tags: [{id, name, is_verified, game_count}], exact_match: bool }
    """
    q = (request.args.get('q') or '').strip()
    try:
        limit = int(request.args.get('limit', '20'))
    except ValueError:
        limit = 20
    limit = max(1, min(limit, 100))

    if not q:
        # 无关键字时返回热门标签（按关联游戏数倒序）
        tags = query('''
            SELECT t.id, t.name, t.is_verified,
                   COUNT(gt.game_id) AS game_count
            FROM tags t
            LEFT JOIN game_tags gt ON t.id = gt.tag_id
            LEFT JOIN games g ON gt.game_id = g.id AND g.is_banned = 0
            GROUP BY t.id, t.name, t.is_verified
            ORDER BY game_count DESC, t.is_verified DESC, t.name ASC
            LIMIT %s
        ''', [limit])
        return jsonify({'success': True, 'tags': tags, 'exact_match': False})

    like = f'%{q}%'
    tags = query('''
        SELECT t.id, t.name, t.is_verified,
               COUNT(gt.game_id) AS game_count
        FROM tags t
        LEFT JOIN game_tags gt ON t.id = gt.tag_id
        LEFT JOIN games g ON gt.game_id = g.id AND g.is_banned = 0
        WHERE t.name LIKE %s
        GROUP BY t.id, t.name, t.is_verified
        ORDER BY (t.name = %s) DESC, t.is_verified DESC, game_count DESC, t.name ASC
        LIMIT %s
    ''', [like, q, limit])

    exact_match = any(t['name'].lower() == q.lower() for t in tags)
    return jsonify({'success': True, 'tags': tags, 'exact_match': exact_match})


@games_bp.route('/admin/tags')
def admin_tags():
    """管理员标签管理页：展示全平台标签，可精选/取消精选，不可删除。"""
    user = current_user()
    if not user or not _is_admin(user):
        abort(403)
    tags = query('''
        SELECT t.id, t.name, t.is_verified, t.created_at,
               COUNT(gt.game_id) AS game_count
        FROM tags t
        LEFT JOIN game_tags gt ON t.id = gt.tag_id
        GROUP BY t.id, t.name, t.is_verified, t.created_at
        ORDER BY t.is_verified DESC, game_count DESC, t.name ASC
    ''')
    return render_template('admin_tags.html', tags=tags)


def _is_admin(user):
    """判断用户是否为管理员（含 super_admin / reviewer）。"""
    if not user:
        return False
    perms = query(
        'SELECT permission_level FROM permissions WHERE user_id = %s AND status = %s',
        [user['id'], 'approved']
    )
    return any(p['permission_level'] in ('super_admin', 'reviewer') for p in perms)


@games_bp.route('/user/<username>')
def user_profile(username):
    """用户个人主页。"""
    user = current_user()
    profile_user = query_one(
        'SELECT id, snyqt_user_id, username, avatar, points, created_at, bio, custom_profile_html '
        'FROM users WHERE username = %s',
        [username]
    )
    if not profile_user:
        abort(404)

    # 检查是否为开发者
    is_developer = query_one(
        "SELECT id FROM permissions WHERE user_id = %s AND permission_level = 'developer' AND status = 'approved'",
        [profile_user['id']]
    ) is not None

    # 获取用户发布的游戏（非本人查看时隐藏私密游戏）
    is_owner = user and user['id'] == profile_user['id']
    if is_owner:
        user_games = query(
            'SELECT id, title, game_uid, cover_image, description, hosting_type, '
            'download_count, play_count, avg_rating, rating_count, created_at '
            'FROM games WHERE developer_id = %s AND is_banned = 0 AND status = "active" '
            'ORDER BY created_at DESC',
            [profile_user['id']]
        )
    else:
        user_games = query(
            'SELECT id, title, game_uid, cover_image, description, hosting_type, '
            'download_count, play_count, avg_rating, rating_count, created_at '
            "FROM games WHERE developer_id = %s AND is_banned = 0 AND status = 'active' "
            "AND access_mode != 'private' "
            'ORDER BY created_at DESC',
            [profile_user['id']]
        )

    # 获取用户的社区帖子
    user_posts = query(
        'SELECT id, title, post_type AS type, 0 AS views, likes, comment_count AS comments_count, created_at '
        'FROM community_posts WHERE user_id = %s AND status != "banned" '
        'ORDER BY created_at DESC LIMIT 10',
        [profile_user['id']]
    )

    # 获取用户发布的素材
    user_assets = query(
        'SELECT id, title, tagline, category, cover_image, price, '
        'download_count, asset_uid, version, updated_at '
        'FROM assets WHERE author_id = %s AND status = "active" '
        'ORDER BY updated_at DESC',
        [profile_user['id']]
    )

    # 统计数据
    followers_count = query_one(
        'SELECT COUNT(*) AS c FROM user_follows WHERE followed_id = %s',
        [profile_user['id']]
    )['c']
    following_count = query_one(
        'SELECT COUNT(*) AS c FROM user_follows WHERE follower_id = %s',
        [profile_user['id']]
    )['c']

    # 当前登录用户是否已关注该用户
    is_following = False
    if user and user['id'] != profile_user['id']:
        is_following = query_one(
            'SELECT 1 FROM user_follows WHERE follower_id = %s AND followed_id = %s LIMIT 1',
            [user['id'], profile_user['id']]
        ) is not None

    stats = {
        'games_count': len(user_games),
        'total_downloads': sum(g.get('download_count', 0) for g in user_games),
        'total_plays': sum(g.get('play_count', 0) for g in user_games),
        'posts_count': len(user_posts),
        'assets_count': len(user_assets),
        'followers': followers_count,
        'following': following_count,
    }

    return render_template(
        'user_profile.html',
        profile_user=profile_user,
        is_developer=is_developer,
        user_games=user_games,
        user_posts=user_posts,
        user_assets=user_assets,
        stats=stats,
        is_owner=is_owner,
        is_following=is_following,
        has_custom_profile=bool(profile_user.get('custom_profile_html')),
    )


@games_bp.route('/api/user/profile', methods=['POST'])
def update_profile():
    """更新个人主页（仅本人）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    bio = (request.get_json(silent=True) or {}).get('bio', '').strip()
    if len(bio) > 2000:
        return jsonify({'success': False, 'message': '简介过长（最多 2000 字符）'}), 400

    execute('UPDATE users SET bio = %s WHERE id = %s', [bio, user['id']])
    return jsonify({'success': True, 'message': '个人主页已更新'})


@games_bp.route('/u/<username>/custom')
def custom_profile(username):
    """用户个性化个人主页（免费托管 HTML）。

    若用户未设置个性化主页，重定向回自带个人主页。
    """
    profile_user = query_one(
        'SELECT id, username, avatar, custom_profile_html FROM users WHERE username = %s',
        [username]
    )
    if not profile_user:
        abort(404)

    custom_html = profile_user.get('custom_profile_html')
    if not custom_html:
        return redirect(url_for('games.user_profile', username=username))

    return render_template(
        'custom_profile.html',
        profile_user=profile_user,
        custom_html=custom_html,
    )


@games_bp.route('/api/user/custom_profile', methods=['GET', 'POST'])
def custom_profile_api():
    """获取/保存个性化个人主页 HTML（仅本人）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    if request.method == 'GET':
        row = query_one('SELECT custom_profile_html FROM users WHERE id = %s', [user['id']])
        return jsonify({'success': True, 'html': row.get('custom_profile_html') or ''})

    # POST: 保存个性化主页 HTML
    html = (request.get_json(silent=True) or {}).get('html', '')
    if len(html) > 500 * 1024:
        return jsonify({'success': False, 'message': 'HTML 内容过大（最多 500KB）'}), 400

    execute('UPDATE users SET custom_profile_html = %s WHERE id = %s', [html or None, user['id']])
    logger.info("用户个性化主页已保存 user_id=%s length=%s", user['id'], len(html))
    return jsonify({'success': True, 'message': '个性化主页已保存'})


@games_bp.route('/api/user/bio', methods=['POST'])
@require_level('user')
def update_bio():
    """更新当前用户简介（bio 字段，最多 500 字符）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    bio = (request.get_json(silent=True) or {}).get('bio', '')
    if bio is None:
        bio = ''
    bio = bio.strip()
    if len(bio) > 500:
        return jsonify({'success': False, 'message': '简介过长（最多 500 字符）'}), 400

    execute('UPDATE users SET bio = %s WHERE id = %s', [bio, user['id']])
    return jsonify({'success': True})


@games_bp.route('/api/user/<int:user_id>/follow', methods=['POST'])
@require_level('user')
def follow_user(user_id):
    """关注指定用户。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    if user['id'] == user_id:
        return jsonify({'success': False, 'message': '不能关注自己'}), 400

    target = query_one('SELECT id FROM users WHERE id = %s', [user_id])
    if not target:
        return jsonify({'success': False, 'message': '用户不存在'}), 404

    execute(
        'INSERT IGNORE INTO user_follows (follower_id, followed_id) VALUES (%s, %s)',
        [user['id'], user_id]
    )
    followers = query_one(
        'SELECT COUNT(*) AS c FROM user_follows WHERE followed_id = %s',
        [user_id]
    )['c']
    return jsonify({'success': True, 'followers': followers})


@games_bp.route('/api/user/<int:user_id>/unfollow', methods=['POST'])
@require_level('user')
def unfollow_user(user_id):
    """取消关注指定用户。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    execute(
        'DELETE FROM user_follows WHERE follower_id = %s AND followed_id = %s',
        [user['id'], user_id]
    )
    followers = query_one(
        'SELECT COUNT(*) AS c FROM user_follows WHERE followed_id = %s',
        [user_id]
    )['c']
    return jsonify({'success': True, 'followers': followers})


@games_bp.route('/api/user/<int:user_id>/followers')
@require_level('user')
def list_followers(user_id):
    """获取关注该用户的列表。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    followers = query(
        'SELECT u.id, u.username, u.avatar, u.snyqt_user_id, uf.created_at '
        'FROM user_follows uf JOIN users u ON u.id = uf.follower_id '
        'WHERE uf.followed_id = %s ORDER BY uf.created_at DESC',
        [user_id]
    )
    return jsonify({'success': True, 'followers': followers, 'count': len(followers)})


@games_bp.route('/api/user/<int:user_id>/following')
@require_level('user')
def list_following(user_id):
    """获取该用户关注的列表。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    following = query(
        'SELECT u.id, u.username, u.avatar, u.snyqt_user_id, uf.created_at '
        'FROM user_follows uf JOIN users u ON u.id = uf.followed_id '
        'WHERE uf.follower_id = %s ORDER BY uf.created_at DESC',
        [user_id]
    )
    return jsonify({'success': True, 'following': following, 'count': len(following)})


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
    description = request.form.get('description', '').strip()

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

    # 同步更新游戏描述（如有提供）+ 更新 updated_at
    if description:
        execute(
            'UPDATE games SET description = %s, version = %s, updated_at = NOW() WHERE id = %s',
            [description, version, gid]
        )
    else:
        execute(
            'UPDATE games SET version = %s, updated_at = NOW() WHERE id = %s',
            [version, gid]
        )

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


# ==================== Part C: 评鉴员推荐（编辑精选） ====================

@games_bp.route('/api/editor/picks', methods=['GET'])
def list_editor_picks():
    """获取编辑精选列表（公开接口）。"""
    picks = query('''
        SELECT ep.id, ep.game_id, ep.reason, ep.created_at,
               g.title, g.game_uid, g.cover_image, g.hosting_type, g.tags,
               u.username AS reviewer_name
        FROM editor_picks ep
        JOIN games g ON ep.game_id = g.id
        JOIN users u ON ep.reviewer_id = u.id
        WHERE g.is_banned = 0 AND g.status = 'active'
        ORDER BY ep.sort_order ASC, ep.created_at DESC
    ''')
    return jsonify({'success': True, 'picks': picks})


@games_bp.route('/api/editor/pick', methods=['POST', 'DELETE'])
@require_level('reviewer')
def manage_editor_pick():
    """评鉴员推荐/取消推荐游戏（仅 reviewer 及以上权限）。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    data = request.get_json(silent=True) or {}
    game_id = data.get('game_id')
    if not game_id:
        return jsonify({'success': False, 'message': '缺少游戏ID'}), 400

    game = query_one('SELECT id, title FROM games WHERE id = %s', [game_id])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404

    if request.method == 'POST':
        existing = query_one('SELECT id FROM editor_picks WHERE game_id = %s', [game_id])
        if existing:
            return jsonify({'success': False, 'message': '已推荐过该游戏'}), 400

        reason = (data.get('reason') or '').strip()
        execute(
            'INSERT INTO editor_picks (game_id, reviewer_id, reason) VALUES (%s, %s, %s)',
            [game_id, user['id'], reason]
        )
        logger.info("评鉴员推荐 game_id=%s by user=%s", game_id, user.get('id'))
        return jsonify({'success': True, 'message': f'已推荐「{game["title"]}」'})

    # DELETE：取消推荐
    execute('DELETE FROM editor_picks WHERE game_id = %s', [game_id])
    logger.info("取消推荐 game_id=%s by user=%s", game_id, user.get('id'))
    return jsonify({'success': True, 'message': '已取消推荐'})


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


@games_bp.route('/privacy')
def privacy():
    """隐私条款页。"""
    return render_template(
        'privacy.html',
        updated_date='2026-07-20',
    )


@games_bp.route('/store/rankings')
def store_rankings():
    """试玩库排行榜：6 种实时排行榜 + 数据概览 + Top 30 列表。"""
    user = current_user()

    # 默认显示热门游戏榜
    rank_type = request.args.get('type', 'hot')

    # 6 种排行榜的查询逻辑
    rank_queries = {
        'hot': 'ORDER BY (play_count * 2 + download_count + rating_count * 3) DESC',
        'rating': 'ORDER BY avg_rating DESC, rating_count DESC',
        'click_rate': 'ORDER BY (CASE WHEN play_count > 0 THEN download_count * 1.0 / play_count ELSE 0 END) DESC',
        'downloads': 'ORDER BY download_count DESC',
        'views': 'ORDER BY play_count DESC',
        'library': 'ORDER BY rating_count DESC',
    }

    order_clause = rank_queries.get(rank_type, rank_queries['hot'])

    # Top 30 游戏
    top_games = query(f'''
        SELECT g.id, g.title, g.game_uid, g.cover_image, g.tags, g.price,
               g.download_count, g.play_count, g.avg_rating, g.rating_count,
               g.access_mode, g.hosting_type,
               (SELECT COUNT(*) FROM reviews r WHERE r.game_id = g.id) AS review_count,
               (SELECT COUNT(*) FROM game_library gl WHERE gl.game_id = g.id) AS library_count
        FROM games g
        WHERE g.is_banned = 0 AND g.status = 'active'
        {order_clause}
        LIMIT 30
    ''')

    # 数据概览
    overview = query_one('''
        SELECT COUNT(*) AS total_games,
               COALESCE(SUM(download_count), 0) AS total_downloads,
               COALESCE(SUM(play_count), 0) AS total_clicks
        FROM games
        WHERE is_banned = 0 AND status = 'active'
    ''')

    # 排行榜类型定义
    rank_types = [
        {'key': 'hot', 'name': '热门游戏榜', 'desc': '综合热度排名'},
        {'key': 'rating', 'name': '游戏排行榜', 'desc': '按评分排名'},
        {'key': 'click_rate', 'name': '高点击率游戏', 'desc': '下载/游玩比'},
        {'key': 'downloads', 'name': '最多点击游戏', 'desc': '按下载量排名'},
        {'key': 'views', 'name': '最多浏览游戏', 'desc': '按游玩量排名'},
        {'key': 'library', 'name': '玩家入库榜', 'desc': '按入库数排名'},
    ]

    # 当前时间（用于"数据更新于"展示）
    from datetime import datetime
    now = datetime.now()

    return render_template(
        'store_rankings.html',
        top_games=top_games,
        overview=overview,
        rank_types=rank_types,
        current_rank=rank_type,
        now=now,
    )
