# -*- coding: utf-8 -*-
"""
游戏社区模块（蓝图 community_bp）
Tasks 6, 7, 8 - 帖子浏览、发帖、评论、点赞、星标、置顶、热论榜
"""
import logging
from flask import (
    Blueprint, render_template, request, jsonify, abort, url_for, session
)

from app.database import query, query_one, execute
from app.auth import current_user
from app.permissions import require_level, has_permission, is_super_admin

logger = logging.getLogger(__name__)

community_bp = Blueprint('community', __name__, url_prefix='/community')


def _is_reviewer(user):
    """检查用户是否为评荐人员或超级管理员（支持多权限并存）。"""
    if not user:
        return False
    return has_permission(user['id'], 'reviewer') or has_permission(user['id'], 'super_admin')


def _get_json_or_form(key, default=None):
    """从 JSON body 或 form 中获取字段值。"""
    if request.is_json:
        return (request.get_json(silent=True) or {}).get(key, default)
    return request.form.get(key, default)


@community_bp.route('/')
def index():
    """社区首页 - 支持分页、排序，置顶帖子优先展示。"""
    page = request.args.get('page', '1')
    sort = request.args.get('sort', 'new')

    try:
        page = int(page)
        if page < 1:
            page = 1
    except ValueError:
        page = 1

    per_page = 20
    offset = (page - 1) * per_page

    # 热度排序公式: comment_count * 2 + likes * 1 + (is_starred * 5)
    if sort == 'hot':
        order_sql = 'ORDER BY cp.is_pinned DESC, (cp.comment_count * 2 + cp.likes * 1 + cp.is_starred * 5) DESC, cp.created_at DESC'
    else:  # new
        order_sql = 'ORDER BY cp.is_pinned DESC, cp.created_at DESC'

    # 非管理员过滤掉已封禁帖子
    if is_super_admin(session.get('user_id')):
        ban_filter = ""
        ban_args = []
    else:
        ban_filter = "AND (cp.status IS NULL OR cp.status != 'banned')"
        ban_args = []

    # 查询总数（用于分页）
    total = query_one(
        f'SELECT COUNT(*) as total FROM community_posts cp WHERE 1=1 {ban_filter}',
        ban_args
    )
    total_pages = (total['total'] + per_page - 1) // per_page if total else 1

    # 查询帖子列表（JOIN users 获取发帖者信息）
    posts = query(
        f'''
        SELECT cp.*, u.username, u.avatar
        FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        WHERE 1=1 {ban_filter}
        {order_sql}
        LIMIT %s OFFSET %s
        ''',
        ban_args + [per_page, offset]
    )

    # 计算每条帖子的热度值（用于显示）
    for p in posts:
        p['hot_score'] = p['comment_count'] * 2 + p['likes'] * 1 + (p['is_starred'] * 5)

    return render_template(
        'community_index.html',
        posts=posts,
        page=page,
        total_pages=total_pages,
        sort=sort
    )


@community_bp.route('/new')
def new_post():
    """发帖页 - 支持 game_uid / game_tag / game_id / tag 参数自动填充。"""
    # 优先使用 game_uid（游戏唯一ID），兼容旧参数
    game_tag = request.args.get('game_uid', '') or request.args.get('game_tag', '') or request.args.get('game_id', '')
    return render_template('community_new.html', game_tag=game_tag)


@community_bp.route('/api/community/posts', methods=['POST'])
def create_post():
    """创建新帖子接口。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    title = _get_json_or_form('title')
    content = _get_json_or_form('content')
    post_type = _get_json_or_form('post_type')
    game_tag = _get_json_or_form('game_tag', '')

    if not title or not title.strip():
        return jsonify({'success': False, 'message': '标题不能为空'}), 400
    if not content or not content.strip():
        return jsonify({'success': False, 'message': '内容不能为空'}), 400

    title = title.strip()
    content = content.strip()

    # 处理 post_type 默认值
    if not post_type:
        if game_tag and game_tag.strip():
            post_type = 'suggestion'
        else:
            post_type = 'discussion'
    else:
        post_type = post_type.strip().lower()
        if post_type not in ('discussion', 'question', 'suggestion'):
            post_type = 'discussion'

    game_tag = (game_tag or '').strip()

    post_id = execute(
        '''
        INSERT INTO community_posts (user_id, title, content, post_type, game_tag)
        VALUES (%s, %s, %s, %s, %s)
        ''',
        [user['id'], title, content, post_type, game_tag]
    )

    # 保存帖子标签到 post_tags 关联表
    tags_str = _get_json_or_form('tags', '')
    if tags_str:
        tag_names = [t.strip() for t in tags_str.split(',') if t.strip()]
        for tn in tag_names[:20]:  # 最多 20 个标签
            tn = tn[:50]
            execute(
                'INSERT INTO post_tags (post_id, tag_name) VALUES (%s, %s)',
                [post_id, tn]
            )

    logger.info("新帖子创建: post_id=%s user_id=%s title=%s", post_id, user['id'], title[:30])
    return jsonify({'success': True, 'post_id': post_id})


@community_bp.route('/post/<int:pid>')
def post_detail(pid):
    """帖子详情页 - 显示帖子内容和评论列表。"""
    # 查询帖子详情
    post = query_one(
        '''
        SELECT cp.*, u.username, u.avatar
        FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        WHERE cp.id = %s
        ''',
        [pid]
    )
    if not post:
        abort(404)

    user = current_user()

    # 如果帖子已封禁且当前用户不是超级管理员，则隐藏内容
    is_banned = (post.get('status') == 'banned')
    is_admin = is_super_admin(user['id']) if user else False
    if is_banned and not is_admin:
        return render_template(
            'community_post.html',
            post=post,
            comments=[],
            user_liked=False,
            is_reviewer=False,
            is_banned=True,
            is_admin=False
        )

    # 查询评论列表（按时间正序）
    comments = query(
        '''
        SELECT cc.*, u.username, u.avatar
        FROM community_comments cc
        JOIN users u ON cc.user_id = u.id
        WHERE cc.post_id = %s
        ORDER BY cc.created_at ASC
        ''',
        [pid]
    )

    # 检查当前用户是否已点赞
    user_liked = False
    if user:
        like = query_one(
            '''
            SELECT id FROM community_likes
            WHERE user_id = %s AND post_id = %s
            ''',
            [user['id'], pid]
        )
        user_liked = like is not None

    # 检查当前用户是否为 reviewer（显示管理按钮）
    is_reviewer = _is_reviewer(user)

    return render_template(
        'community_post.html',
        post=post,
        comments=comments,
        user_liked=user_liked,
        is_reviewer=is_reviewer,
        is_banned=is_banned,
        is_admin=is_admin
    )


@community_bp.route('/api/community/posts/<int:pid>/comments', methods=['POST'])
def add_comment(pid):
    """发表评论接口。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    # 检查帖子是否存在
    post = query_one('SELECT id, game_tag FROM community_posts WHERE id = %s', [pid])
    if not post:
        return jsonify({'success': False, 'message': '帖子不存在'}), 404

    content = _get_json_or_form('content')
    if not content or not content.strip():
        return jsonify({'success': False, 'message': '评论内容不能为空'}), 400

    content = content.strip()

    # 检测是否为开发者回复
    # 注意：game_tag 现在存储的是 game_uid（8位hex），需先解析为数字 game.id
    is_developer_reply = 0
    if post.get('game_tag') and post['game_tag'].strip():
        tag = post['game_tag'].strip()
        # 优先按 game_uid 匹配，兼容旧的数字 id
        game = query_one('SELECT id, developer_id FROM games WHERE game_uid = %s', [tag])
        if not game:
            # 兼容旧数据：game_tag 直接存了数字 id
            try:
                numeric_id = int(tag)
                game = query_one('SELECT id, developer_id FROM games WHERE id = %s', [numeric_id])
            except (ValueError, TypeError):
                game = None
        if game:
            numeric_game_id = game['id']
            is_dev = game['developer_id'] == user['id']
            if not is_dev:
                co_dev = query_one(
                    'SELECT id FROM game_co_devs WHERE game_id = %s AND user_id = %s AND status = %s',
                    [numeric_game_id, user['id'], 'accepted']
                )
                is_dev = co_dev is not None
            if is_dev:
                is_developer_reply = 1

    # 插入评论
    cid = execute(
        '''
        INSERT INTO community_comments (post_id, user_id, content, is_developer_reply)
        VALUES (%s, %s, %s, %s)
        ''',
        [pid, user['id'], content, is_developer_reply]
    )

    # 更新帖子评论计数
    execute(
        '''
        UPDATE community_posts SET comment_count = comment_count + 1
        WHERE id = %s
        ''',
        [pid]
    )

    # 返回新评论（带用户信息）
    comment = query_one(
        '''
        SELECT cc.*, u.username, u.avatar
        FROM community_comments cc
        JOIN users u ON cc.user_id = u.id
        WHERE cc.id = %s
        ''',
        [cid]
    )

    logger.info("新评论: comment_id=%s post_id=%s user_id=%s", cid, pid, user['id'])
    return jsonify({'success': True, 'comment': comment})


@community_bp.route('/api/community/comments/<int:cid>/best', methods=['POST'])
def mark_best_answer(cid):
    """标记最佳答案 - 仅帖子作者（提问者）可操作。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    # 查询评论所属帖子
    comment = query_one('SELECT * FROM community_comments WHERE id = %s', [cid])
    if not comment:
        return jsonify({'success': False, 'message': '评论不存在'}), 404

    # 查询帖子检查是否为作者
    post = query_one('SELECT * FROM community_posts WHERE id = %s', [comment['post_id']])
    if not post:
        return jsonify({'success': False, 'message': '帖子不存在'}), 404

    if post['user_id'] != user['id']:
        return jsonify({'success': False, 'message': '仅帖子作者可操作'}), 403

    # 取消该帖子内所有最佳答案
    execute(
        '''
        UPDATE community_comments SET is_best_answer = 0
        WHERE post_id = %s
        ''',
        [post['id']]
    )

    # 设置当前评论为最佳答案
    execute(
        '''
        UPDATE community_comments SET is_best_answer = 1
        WHERE id = %s
        ''',
        [cid]
    )

    logger.info("标记最佳答案: comment_id=%s post_id=%s user_id=%s", cid, post['id'], user['id'])
    return jsonify({'success': True, 'message': '已标记为最佳答案'})


@community_bp.route('/api/community/posts/<int:pid>/like', methods=['POST'])
def toggle_like(pid):
    """切换点赞状态 - 已点赞则取消，否则点赞。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    # 检查帖子是否存在
    post = query_one('SELECT id FROM community_posts WHERE id = %s', [pid])
    if not post:
        return jsonify({'success': False, 'message': '帖子不存在'}), 404

    # 检查是否已点赞
    existing = query_one(
        '''
        SELECT id FROM community_likes
        WHERE user_id = %s AND post_id = %s
        ''',
        [user['id'], pid]
    )

    if existing:
        # 取消点赞
        execute(
            '''
            DELETE FROM community_likes
            WHERE id = %s
            ''',
            [existing['id']]
        )
        execute(
            '''
            UPDATE community_posts SET likes = likes - 1
            WHERE id = %s
            ''',
            [pid]
        )
        liked = False
    else:
        # 添加点赞
        execute(
            '''
            INSERT INTO community_likes (user_id, post_id)
            VALUES (%s, %s)
            ''',
            [user['id'], pid]
        )
        execute(
            '''
            UPDATE community_posts SET likes = likes + 1
            WHERE id = %s
            ''',
            [pid]
        )
        liked = True

    # 获取最新点赞数
    updated = query_one(
        'SELECT likes FROM community_posts WHERE id = %s',
        [pid]
    )

    return jsonify({
        'success': True,
        'liked': liked,
        'likes': updated['likes'] if updated else 0
    })


@community_bp.route('/api/community/posts/<int:pid>/star', methods=['POST'])
def toggle_star(pid):
    """切换星标状态 - 仅 reviewer 或 super_admin 可操作。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    if not _is_reviewer(user):
        return jsonify({'success': False, 'message': '权限不足'}), 403

    # 检查帖子是否存在
    post = query_one('SELECT * FROM community_posts WHERE id = %s', [pid])
    if not post:
        return jsonify({'success': False, 'message': '帖子不存在'}), 404

    # 切换状态
    new_state = 0 if post['is_starred'] else 1
    execute(
        '''
        UPDATE community_posts SET is_starred = %s
        WHERE id = %s
        ''',
        [new_state, pid]
    )

    logger.info("星标切换: post_id=%s user_id=%s new_state=%s", pid, user['id'], new_state)
    return jsonify({
        'success': True,
        'is_starred': new_state == 1
    })


@community_bp.route('/api/community/posts/<int:pid>/pin', methods=['POST'])
def toggle_pin(pid):
    """切换置顶状态 - 仅 reviewer 或 super_admin 可操作。"""
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    if not _is_reviewer(user):
        return jsonify({'success': False, 'message': '权限不足'}), 403

    # 检查帖子是否存在
    post = query_one('SELECT * FROM community_posts WHERE id = %s', [pid])
    if not post:
        return jsonify({'success': False, 'message': '帖子不存在'}), 404

    # 切换状态
    new_state = 0 if post['is_pinned'] else 1
    execute(
        '''
        UPDATE community_posts SET is_pinned = %s
        WHERE id = %s
        ''',
        [new_state, pid]
    )

    logger.info("置顶切换: post_id=%s user_id=%s new_state=%s", pid, user['id'], new_state)
    return jsonify({
        'success': True,
        'is_pinned': new_state == 1
    })


@community_bp.route('/hot')
def hot():
    """热论排行榜 - 按热度公式排序取前 50。"""
    # 非管理员过滤掉已封禁帖子
    if is_super_admin(session.get('user_id')):
        ban_filter = ""
        ban_args = []
    else:
        ban_filter = "AND (cp.status IS NULL OR cp.status != 'banned')"
        ban_args = []
    # 热度公式: comment_count * 2 + likes * 1 + (is_starred * 5)
    posts = query(
        f'''
        SELECT cp.*, u.username, u.avatar,
               (cp.comment_count * 2 + cp.likes * 1 + cp.is_starred * 5) as hot_score
        FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        WHERE 1=1 {ban_filter}
        ORDER BY hot_score DESC
        LIMIT 50
        ''',
        ban_args
    )

    return render_template('community_hot.html', posts=posts)


# ==================== 帖子封禁与举报 ====================
@community_bp.route('/api/community/post/<int:pid>/ban', methods=['POST'])
def ban_post(pid):
    """管理员封禁帖子。"""
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    reason = (request.get_json(silent=True) or {}).get('reason', '违反社区规范')
    # 更新帖子状态为封禁
    execute('UPDATE community_posts SET status=%s WHERE id=%s', ('banned', pid))
    # 获取帖子标题
    post = query_one('SELECT title FROM community_posts WHERE id=%s', (pid,))
    target_title = post['title'] if post else ''
    # 创建处罚记录
    execute(
        'INSERT INTO penalty_records (target_type, target_id, target_title, reason, action, admin_id) '
        'VALUES (%s,%s,%s,%s,%s,%s)',
        ('post', pid, target_title, reason, 'ban', session.get('user_id'))
    )
    logger.info("帖子封禁: post_id=%s reason=%s admin_id=%s", pid, reason, session.get('user_id'))
    return jsonify({'ok': True})


@community_bp.route('/api/community/post/<int:pid>/report', methods=['POST'])
def report_post(pid):
    """用户举报帖子。"""
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': '请先登录'}), 401
    reason = (request.get_json(silent=True) or {}).get('reason', '')
    if not reason:
        return jsonify({'error': '请填写举报理由'}), 400
    # 检查帖子是否存在
    post = query_one('SELECT id FROM community_posts WHERE id=%s', (pid,))
    if not post:
        return jsonify({'error': '帖子不存在'}), 404
    # 检查是否已举报过该帖子（pending 状态）
    existing = query_one(
        'SELECT id FROM reports WHERE reporter_id=%s AND target_type=%s AND target_id=%s AND status=%s',
        (uid, 'post', pid, 'pending')
    )
    if existing:
        return jsonify({'error': '您已举报过该帖子，请等待管理员处理'}), 400
    execute(
        'INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (%s,%s,%s,%s)',
        (uid, 'post', pid, reason)
    )
    logger.info("帖子举报: post_id=%s reporter_id=%s", pid, uid)
    return jsonify({'ok': True})


# ==================== 公开小黑屋页面 ====================
@community_bp.route('/blacklist')
def blacklist_public():
    """公开小黑屋页面：所有用户均可查看处罚记录（类似 B 站小黑屋）。

    支持按 target_type 筛选与分页。
    """
    # 筛选参数
    target_type = request.args.get('type', '').strip()
    # 分页参数
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page

    # 构造查询
    where_sql = ""
    where_args = []
    if target_type in ('game', 'post'):
        where_sql = "WHERE pr.target_type = %s"
        where_args = [target_type]

    # 总数
    total_row = query_one(
        f'SELECT COUNT(*) AS cnt FROM penalty_records pr {where_sql}',
        where_args
    )
    total = total_row['cnt'] if total_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page)

    # 列表（JOIN users 拿管理员名称）
    penalties = query(
        f'''
        SELECT pr.*, u.username AS admin_name
        FROM penalty_records pr
        LEFT JOIN users u ON pr.admin_id = u.id
        {where_sql}
        ORDER BY pr.created_at DESC
        LIMIT %s OFFSET %s
        ''',
        where_args + [per_page, offset]
    )

    return render_template(
        'blacklist_public.html',
        penalties=penalties,
        target_type=target_type,
        page=page,
        total_pages=total_pages,
        total=total
    )
