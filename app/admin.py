# -*- coding: utf-8 -*-
"""管理工具模块（蓝图 admin_bp，全部需 super_admin 权限）。

依赖契约：
- from app.database import query, query_one, execute
- from app.permissions import require_level, set_permission
- from app.auth import current_user
- from app.helpers import allocate_port
"""
import logging
from flask import (
    Blueprint, render_template, request, jsonify, abort, session,
    redirect, url_for,
)

from app.database import query, query_one, execute, query_all
from app.permissions import require_level, set_permission, is_super_admin, is_reviewer
from app.auth import current_user
from app.helpers import allocate_port
from app.games import _delete_game

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)


def _get_json_or_form(key, default=None):
    """从 JSON body 或 form 中获取字段值。"""
    if request.is_json:
        return (request.get_json(silent=True) or {}).get(key, default)
    return request.form.get(key, default)


@admin_bp.route('/admin')
@require_level('super_admin')
def admin_panel():
    """管理面板：用户权限列表、Python 审核队列、游戏管理。"""
    # 用户与权限列表（GROUP_CONCAT 聚合每个用户的多条权限记录）
    users_perms = query(
        'SELECT u.id, u.username, u.avatar, u.points, u.status AS account_status, '
        'GROUP_CONCAT(DISTINCT p.permission_level SEPARATOR ",") AS permission_level, '
        'GROUP_CONCAT(DISTINCT p.status SEPARATOR ",") AS perm_status '
        'FROM users u LEFT JOIN permissions p ON u.id = p.user_id '
        'GROUP BY u.id '
        'ORDER BY u.id'
    )

    # Python 审核队列（全部记录）
    review_queue = query(
        'SELECT q.*, g.title, g.developer_id, g.python_main, g.python_command '
        'FROM python_review_queue q JOIN games g ON q.game_id = g.id '
        'ORDER BY q.created_at DESC'
    )

    # 全部权限记录（附带用户信息），按 pending 优先、创建时间倒序排列
    applications = query(
        'SELECT p.id, p.user_id, u.username, u.avatar, u.points, '
        'p.permission_level, p.status, p.reason, p.created_at '
        'FROM permissions p LEFT JOIN users u ON p.user_id = u.id '
        "ORDER BY p.status='pending' DESC, p.created_at DESC"
    )

    # 游戏列表
    games = query('SELECT * FROM games ORDER BY created_at DESC')

    return render_template(
        'admin.html',
        users_perms=users_perms,
        review_queue=review_queue,
        applications=applications,
        games=games
    )


@admin_bp.route('/api/admin/permission', methods=['POST'])
@require_level('super_admin')
def update_permission():
    """修改用户权限级别。body: {user_id, level}。"""
    admin = current_user()
    user_id = _get_json_or_form('user_id')
    level = _get_json_or_form('level')

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'user_id 无效'}), 400

    if level not in ('user', 'developer', 'super_admin', 'reviewer'):
        return jsonify({'success': False, 'message': '无效权限级别'}), 400

    set_permission(user_id, level, granted_by=admin['id'])
    return jsonify({'success': True, 'message': '权限已更新'})


@admin_bp.route('/api/admin/applications/<int:record_id>', methods=['POST'])
@require_level('super_admin')
def review_application(record_id):
    """审核权限申请。body: {action: approve|reject}。

    按 permissions 表主键 id 定位单条记录，仅修改该行：
    - approve：将该行 status 置为 approved，记录 granted_by
    - reject：将该行 status 置为 rejected
    不影响该用户的其他权限记录。
    """
    admin = current_user()
    action = _get_json_or_form('action')

    row = query_one(
        'SELECT id, user_id, permission_level, status FROM permissions WHERE id = %s',
        [record_id]
    )
    if not row:
        return jsonify({'success': False, 'message': '申请记录不存在'}), 404

    if action == 'approve':
        execute(
            'UPDATE permissions SET status = %s, granted_by = %s WHERE id = %s',
            ('approved', admin['id'], record_id)
        )
        logger.info("权限申请已批准: record_id=%s user_id=%s level=%s by=%s",
                    record_id, row['user_id'], row['permission_level'], admin['id'])
        return jsonify({'success': True, 'message': f"已批准 {row['permission_level']} 申请"})

    elif action == 'reject':
        execute(
            'UPDATE permissions SET status = %s WHERE id = %s',
            ('rejected', record_id)
        )
        logger.info("权限申请已拒绝: record_id=%s user_id=%s", record_id, row['user_id'])
        return jsonify({'success': True, 'message': '已拒绝申请'})

    return jsonify({'success': False, 'message': '无效操作'}), 400


@admin_bp.route('/api/admin/python-review')
@require_level('super_admin')
def list_python_reviews():
    """列出 python_review_queue 中 pending 记录。"""
    rows = query(
        'SELECT q.*, g.title, g.developer_id, g.python_main, g.python_command '
        'FROM python_review_queue q JOIN games g ON q.game_id = g.id '
        'WHERE q.status = %s ORDER BY q.created_at DESC',
        ['pending']
    )
    return jsonify({'success': True, 'reviews': rows})


@admin_bp.route('/api/admin/python-review/<int:rid>', methods=['POST'])
@require_level('super_admin')
def review_python(rid):
    """审核 Python 托管游戏。body: {action: approve|reject, reason?}。"""
    admin = current_user()
    action = _get_json_or_form('action')
    reason = _get_json_or_form('reason', '') or ''

    row = query_one('SELECT * FROM python_review_queue WHERE id = %s', [rid])
    if not row:
        return jsonify({'success': False, 'message': '审核记录不存在'}), 404

    if action == 'approve':
        # 分配端口
        port = allocate_port()
        execute(
            'UPDATE python_review_queue SET status = %s, reviewer_id = %s, '
            'reason = %s WHERE id = %s',
            ('approved', admin['id'], reason, rid)
        )
        # 游戏状态置 active 并分配端口
        execute(
            'UPDATE games SET status = %s, python_port = %s WHERE id = %s',
            ('active', port, row['game_id'])
        )
        return jsonify({
            'success': True,
            'port': port,
            'message': '已批准，游戏已激活'
        })

    elif action == 'reject':
        execute(
            'UPDATE python_review_queue SET status = %s, reviewer_id = %s, '
            'reason = %s WHERE id = %s',
            ('rejected', admin['id'], reason, rid)
        )
        execute(
            'UPDATE games SET status = %s WHERE id = %s',
            ('rejected', row['game_id'])
        )
        return jsonify({'success': True, 'message': '已拒绝'})

    return jsonify({'success': False, 'message': '无效操作'}), 400


@admin_bp.route('/api/admin/config-review')
@require_level('super_admin')
def list_config_reviews():
    """列出 config_review_queue 中 pending 记录。"""
    rows = query(
        'SELECT q.*, g.title, g.developer_id '
        'FROM config_review_queue q JOIN games g ON q.game_id = g.id '
        'WHERE q.status = %s ORDER BY q.created_at DESC',
        ['pending']
    )
    return jsonify({'success': True, 'reviews': rows})


@admin_bp.route('/api/admin/config-review/<int:rid>', methods=['POST'])
@require_level('super_admin')
def review_config(rid):
    """审核配置变更。body: {action: approve|reject, reason?}。"""
    admin = current_user()
    action = _get_json_or_form('action')
    reason = _get_json_or_form('reason', '') or ''

    row = query_one('SELECT * FROM config_review_queue WHERE id = %s', [rid])
    if not row:
        return jsonify({'success': False, 'message': '审核记录不存在'}), 404
    if row['status'] != 'pending':
        return jsonify({'success': False, 'message': '该审核已处理'}), 400

    if action == 'approve':
        execute(
            'UPDATE config_review_queue SET status = %s, reviewer_id = %s, '
            'reason = %s WHERE id = %s',
            ('approved', admin['id'], reason, rid)
        )
        # 应用变更：更新 games 和 game_pricing 的 platform_share
        execute(
            'UPDATE games SET %s = %s WHERE id = %s',
            [row['field_name'], row['new_value'], row['game_id']]
        )
        execute(
            'INSERT INTO game_pricing (game_id, %s) VALUES (%s, %s) '
            'ON DUPLICATE KEY UPDATE %s = %s',
            [row['field_name'], row['game_id'], row['new_value'],
             row['field_name'], row['new_value']]
        )
        return jsonify({'success': True, 'message': '已批准，配置已生效'})

    elif action == 'reject':
        execute(
            'UPDATE config_review_queue SET status = %s, reviewer_id = %s, '
            'reason = %s WHERE id = %s',
            ('rejected', admin['id'], reason, rid)
        )
        return jsonify({'success': True, 'message': '已拒绝'})

    return jsonify({'success': False, 'message': '无效操作'}), 400


@admin_bp.route('/api/admin/games/<int:gid>/ban', methods=['POST'])
@require_level('super_admin')
def ban_game(gid):
    """封禁游戏（必须填写封禁理由，支持时长与公开开关）。"""
    data = request.get_json(silent=True) or {}
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({'success': False, 'error': '请填写封禁理由'}), 400
    duration_days = data.get('duration_days')  # None=永久, 整数=天数
    is_public = 1 if data.get('is_public', True) else 0
    game = query_one('SELECT title, developer_id FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'error': '游戏不存在'}), 404
    execute('UPDATE games SET is_banned = 1 WHERE id = %s', [gid])
    # 计算到期时间
    expires_at = _compute_expiry(duration_days)
    execute(
        'INSERT INTO penalty_records (target_type, target_id, target_title, target_user_id, reason, action, '
        'duration_days, expires_at, is_public, admin_id) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        ('game', gid, game['title'], game.get('developer_id'), reason, 'ban',
         duration_days, expires_at, is_public, session.get('user_id'))
    )
    logger.info("游戏封禁: game_id=%s reason=%s duration=%s public=%s admin_id=%s",
                gid, reason, duration_days, is_public, session.get('user_id'))
    return jsonify({'success': True, 'message': '游戏已封禁'})


def _compute_expiry(duration_days):
    """根据天数计算到期时间，None 表示永久封禁。"""
    if duration_days is None:
        return None
    from datetime import datetime, timedelta
    return datetime.now() + timedelta(days=int(duration_days))


@admin_bp.route('/api/admin/games/<int:gid>/unban', methods=['POST'])
@require_level('super_admin')
def unban_game(gid):
    """解封游戏。"""
    execute('UPDATE games SET is_banned = 0 WHERE id = %s', [gid])
    return jsonify({'success': True, 'message': '游戏已解封'})


@admin_bp.route('/api/admin/games/<int:gid>/view-eligibility', methods=['POST'])
@require_level('super_admin')
def view_eligibility(gid):
    """审核/取消游戏 view 资格。body: {action: grant|revoke}。"""
    action = _get_json_or_form('action')

    if action == 'grant':
        execute(
            'UPDATE games SET status = %s WHERE id = %s',
            ('active', gid)
        )
        return jsonify({'success': True, 'message': '已授予 view 资格'})
    elif action == 'revoke':
        execute(
            'UPDATE games SET status = %s WHERE id = %s',
            ('disabled', gid)
        )
        return jsonify({'success': True, 'message': '已取消 view 资格'})

    return jsonify({'success': False, 'message': '无效操作'}), 400


@admin_bp.route('/api/admin/games/<int:gid>', methods=['DELETE'])
@require_level('super_admin')
def delete_game(gid):
    """管理员删除游戏（级联删除）。"""
    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404

    _delete_game(gid)
    return jsonify({'success': True, 'message': '游戏已删除'})


# ==================== 小黑屋管理 ====================
@admin_bp.route('/admin/blacklist')
def blacklist_page():
    """小黑屋管理页面：展示处罚记录与待处理举报。"""
    if not is_super_admin(session.get('user_id')):
        abort(403)
    # 获取全部处罚记录（JOIN users 拿到操作管理员名称）
    penalties = query_all(
        '''
        SELECT pr.*, u.username as admin_name
        FROM penalty_records pr
        LEFT JOIN users u ON pr.admin_id = u.id
        ORDER BY pr.created_at DESC
        '''
    )
    # 获取待处理举报（JOIN users 拿举报人名称、JOIN community_posts 拿帖子标题）
    reports = query_all(
        '''
        SELECT r.*, u.username as reporter_name, cp.title as post_title
        FROM reports r
        LEFT JOIN users u ON r.reporter_id = u.id
        LEFT JOIN community_posts cp ON r.target_id = cp.id
        WHERE r.status = 'pending'
        ORDER BY r.created_at DESC
        '''
    )
    return render_template('blacklist.html', penalties=penalties, reports=reports)


@admin_bp.route('/api/admin/reports/<int:rid>', methods=['POST'])
def handle_report(rid):
    """处理举报：action=valid 标记属实并封禁目标，action=invalid 标记不属实并删除。

    标记属实时强制要求管理员填写封禁理由（可基于举报理由补充）。
    """
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    if action == 'valid':
        # 强制要求管理员填写封禁理由
        ban_reason = (data.get('reason') or '').strip()
        if not ban_reason:
            return jsonify({'error': '请填写封禁理由'}), 400
        duration_days = data.get('duration_days')
        is_public = 1 if data.get('is_public', True) else 0
        # 标记举报属实，封禁目标帖子
        report = query_one('SELECT * FROM reports WHERE id=%s', (rid,))
        if report:
            execute(
                'UPDATE reports SET status=%s, admin_id=%s WHERE id=%s',
                ('valid', session.get('user_id'), rid)
            )
            # 封禁帖子
            execute(
                'UPDATE community_posts SET status=%s WHERE id=%s',
                ('banned', report['target_id'])
            )
            # 创建处罚记录（使用管理员填写的理由）
            post = query_one('SELECT title, user_id FROM community_posts WHERE id=%s', (report['target_id'],))
            target_title = post['title'] if post else ''
            target_user_id = post.get('user_id') if post else None
            expires_at = _compute_expiry(duration_days)
            execute(
                'INSERT INTO penalty_records (target_type, target_id, target_title, target_user_id, reason, action, '
                'duration_days, expires_at, is_public, admin_id) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                ('post', report['target_id'], target_title, target_user_id, ban_reason, 'ban',
                 duration_days, expires_at, is_public, session.get('user_id'))
            )
            logger.info("举报处理-属实: report_id=%s target_id=%s reason=%s duration=%s admin_id=%s",
                        rid, report['target_id'], ban_reason, duration_days, session.get('user_id'))
    elif action == 'invalid':
        # 标记不属实，删除举报记录
        execute('DELETE FROM reports WHERE id=%s', (rid,))
        logger.info("举报处理-不属实: report_id=%s admin_id=%s", rid, session.get('user_id'))
    else:
        return jsonify({'error': '无效操作'}), 400
    return jsonify({'ok': True})


@admin_bp.route('/api/admin/unban', methods=['POST'])
def unban_target():
    """解除封禁（帖子或游戏）。"""
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    data = request.get_json(silent=True) or {}
    target_type = data.get('target_type')  # 'post' 或 'game'
    target_id = data.get('target_id')
    if not target_type or not target_id:
        return jsonify({'error': '参数缺失'}), 400

    if target_type == 'post':
        execute('UPDATE community_posts SET status=%s WHERE id=%s', ('active', target_id))
    elif target_type == 'game':
        execute('UPDATE games SET status=%s WHERE id=%s', ('active', target_id))
    elif target_type == 'user':
        execute("UPDATE users SET status = 'active' WHERE id = %s", [target_id])
    else:
        return jsonify({'error': '未知目标类型'}), 400

    # 删除对应的处罚记录
    execute('DELETE FROM penalty_records WHERE target_type=%s AND target_id=%s',
            (target_type, target_id))
    logger.info("解除封禁: type=%s id=%s admin_id=%s", target_type, target_id, session.get('user_id'))
    return jsonify({'ok': True})


# ==================== 用户封禁/解封 ====================
@admin_bp.route('/api/admin/users/<int:uid>/ban', methods=['POST'])
def ban_user(uid):
    """封禁用户（必须填写理由，支持时长与公开开关）。"""
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    data = request.get_json(silent=True) or {}
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': '请填写封禁理由'}), 400
    duration_days = data.get('duration_days')  # None=永久, 整数=天数
    is_public = 1 if data.get('is_public', True) else 0
    user = query_one('SELECT id, username FROM users WHERE id = %s', [uid])
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    execute("UPDATE users SET status = 'banned' WHERE id = %s", [uid])
    expires_at = _compute_expiry(duration_days)
    execute(
        'INSERT INTO penalty_records (target_type, target_id, target_title, target_user_id, reason, action, '
        'duration_days, expires_at, is_public, admin_id) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        ('user', uid, user['username'], uid, reason, 'ban',
         duration_days, expires_at, is_public, session.get('user_id'))
    )
    logger.info("用户封禁: user_id=%s reason=%s duration=%s public=%s admin_id=%s",
                uid, reason, duration_days, is_public, session.get('user_id'))
    return jsonify({'ok': True})


@admin_bp.route('/api/admin/users/<int:uid>/unban', methods=['POST'])
def unban_user(uid):
    """解除用户封禁。"""
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    execute("UPDATE users SET status = 'active' WHERE id = %s", [uid])
    execute("DELETE FROM penalty_records WHERE target_type = 'user' AND target_id = %s", [uid])
    logger.info("用户解封: user_id=%s admin_id=%s", uid, session.get('user_id'))
    return jsonify({'ok': True})


# ==================== 删除帖子 ====================
@admin_bp.route('/api/admin/posts/<int:pid>/delete', methods=['POST'])
def delete_post(pid):
    """管理员永久删除帖子（级联删除评论、点赞、标签、处罚记录）。"""
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    post = query_one('SELECT id, title FROM community_posts WHERE id = %s', [pid])
    if not post:
        return jsonify({'error': '帖子不存在'}), 404
    execute('DELETE FROM community_likes WHERE post_id = %s', [pid])
    execute('DELETE FROM community_comments WHERE post_id = %s', [pid])
    execute('DELETE FROM post_tags WHERE post_id = %s', [pid])
    execute('DELETE FROM penalty_records WHERE target_type = %s AND target_id = %s', ('post', pid))
    execute('DELETE FROM reports WHERE target_type = %s AND target_id = %s', ('post', pid))
    execute('DELETE FROM community_posts WHERE id = %s', [pid])
    logger.info("帖子删除: post_id=%s title=%s admin_id=%s", pid, post['title'], session.get('user_id'))
    return jsonify({'ok': True})


# ==================== 评鉴员投票封禁系统 ====================
# 规则：
# - 评鉴员可对 game/post/user 投"应封禁"票
# - 帖子/游戏：≥3 票一致 → 自动执行封禁
# - 用户：≥3 票一致 → 推送至管理员小黑屋面板，待管理员确认后执行封禁
# - 管理员直接封禁：无需投票
REVIEWER_VOTE_THRESHOLD = 3


@admin_bp.route('/api/reviewer/vote', methods=['POST'])
def reviewer_vote():
    """评鉴员投票封禁目标。

    body: {target_type: game|post|user, target_id: int, reason: str}
    """
    user = current_user()
    if not user:
        return jsonify({'error': '请先登录'}), 401
    if not is_reviewer(user['id']) and not is_super_admin(user['id']):
        return jsonify({'error': '需要评鉴员权限'}), 403
    data = request.get_json(silent=True) or {}
    target_type = data.get('target_type')
    target_id = data.get('target_id')
    reason = (data.get('reason') or '').strip()
    if target_type not in ('game', 'post', 'user') or not target_id:
        return jsonify({'error': '参数无效'}), 400
    if not reason:
        return jsonify({'error': '请填写投票理由'}), 400

    # 插入投票记录（已存在则忽略）
    try:
        execute(
            'INSERT INTO reviewer_votes (target_type, target_id, reviewer_id, reason, status) '
            'VALUES (%s, %s, %s, %s, %s)',
            (target_type, target_id, user['id'], reason, 'voting')
        )
    except Exception:
        return jsonify({'error': '您已对该目标投过票'}), 400

    # 统计投票数
    votes = query_one(
        'SELECT COUNT(*) AS cnt FROM reviewer_votes '
        'WHERE target_type = %s AND target_id = %s AND status = %s',
        (target_type, target_id, 'voting')
    )
    vote_count = votes['cnt'] if votes else 0

    if vote_count >= REVIEWER_VOTE_THRESHOLD:
        if target_type in ('post', 'game'):
            # 帖子/游戏：自动执行封禁
            _auto_ban_by_reviewers(target_type, target_id, reason)
            # 标记所有投票为 auto_banned
            execute(
                'UPDATE reviewer_votes SET status = %s, confirmed_at = NOW() '
                'WHERE target_type = %s AND target_id = %s AND status = %s',
                ('auto_banned', target_type, target_id, 'voting')
            )
            return jsonify({
                'ok': True,
                'message': f'已达 {REVIEWER_VOTE_THRESHOLD} 票一致，已自动执行封禁',
                'vote_count': vote_count,
                'auto_banned': True
            })
        else:
            # 用户：推送至管理员面板
            execute(
                'UPDATE reviewer_votes SET status = %s '
                'WHERE target_type = %s AND target_id = %s AND status = %s',
                ('pending_admin', target_type, target_id, 'voting')
            )
            return jsonify({
                'ok': True,
                'message': f'已达 {REVIEWER_VOTE_THRESHOLD} 票一致，已推送至管理员确认',
                'vote_count': vote_count,
                'pending_admin': True
            })

    return jsonify({
        'ok': True,
        'message': f'投票成功，当前 {vote_count}/{REVIEWER_VOTE_THRESHOLD} 票',
        'vote_count': vote_count,
        'threshold': REVIEWER_VOTE_THRESHOLD
    })


def _auto_ban_by_reviewers(target_type, target_id, reason):
    """评鉴员一致投票后自动封禁帖子或游戏（不创建公开处罚记录，待管理员审核公开）。"""
    admin_id = None  # 评鉴员集体决策，无单一管理员
    if target_type == 'post':
        post = query_one('SELECT title, user_id FROM community_posts WHERE id = %s', [target_id])
        target_title = post['title'] if post else ''
        target_user_id = post.get('user_id') if post else None
        execute('UPDATE community_posts SET status = %s WHERE id = %s', ('banned', target_id))
    elif target_type == 'game':
        game = query_one('SELECT title, developer_id FROM games WHERE id = %s', [target_id])
        target_title = game['title'] if game else ''
        target_user_id = game.get('developer_id') if game else None
        execute('UPDATE games SET is_banned = 1 WHERE id = %s', [target_id])
    else:
        return
    # 创建处罚记录（默认不公开，待管理员决定是否公开）
    execute(
        'INSERT INTO penalty_records (target_type, target_id, target_title, target_user_id, reason, action, '
        'duration_days, expires_at, is_public, admin_id) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (target_type, target_id, target_title, target_user_id,
         f'[评鉴员一致投票] {reason}', 'ban', None, None, 0, admin_id)
    )
    logger.info("评鉴员一致封禁: type=%s id=%s reason=%s", target_type, target_id, reason)


@admin_bp.route('/api/admin/reviewer-queue')
def reviewer_queue():
    """管理员查看待确认的用户封禁投票队列。"""
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    pending = query_all(
        '''
        SELECT rv.*, u.username as target_username, u.avatar as target_avatar
        FROM reviewer_votes rv
        LEFT JOIN users u ON rv.target_type = 'user' AND rv.target_id = u.id
        WHERE rv.status = 'pending_admin'
        ORDER BY rv.created_at DESC
        '''
    )
    return jsonify({'ok': True, 'queue': pending})


@admin_bp.route('/api/admin/reviewer-queue/<int:vid>/confirm', methods=['POST'])
def confirm_reviewer_vote(vid):
    """管理员确认评鉴员一致封禁用户（设置时长并执行）。"""
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    data = request.get_json(silent=True) or {}
    duration_days = data.get('duration_days')  # None=永久
    is_public = 1 if data.get('is_public', True) else 0
    vote = query_one('SELECT * FROM reviewer_votes WHERE id = %s AND status = %s', [vid, 'pending_admin'])
    if not vote:
        return jsonify({'error': '投票记录不存在或已处理'}), 404
    if vote['target_type'] != 'user':
        return jsonify({'error': '此接口仅处理用户封禁'}), 400
    user = query_one('SELECT id, username FROM users WHERE id = %s', [vote['target_id']])
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    execute("UPDATE users SET status = 'banned' WHERE id = %s", [user['id']])
    expires_at = _compute_expiry(duration_days)
    execute(
        'INSERT INTO penalty_records (target_type, target_id, target_title, target_user_id, reason, action, '
        'duration_days, expires_at, is_public, admin_id) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        ('user', user['id'], user['username'], user['id'],
         vote['reason'] or '评鉴员一致投票封禁', 'ban',
         duration_days, expires_at, is_public, session.get('user_id'))
    )
    # 标记投票为已确认
    execute(
        'UPDATE reviewer_votes SET status = %s, confirmed_at = NOW() WHERE id = %s',
        ('confirmed', vid)
    )
    logger.info("管理员确认评鉴员封禁: vote_id=%s user_id=%s duration=%s admin_id=%s",
                vid, user['id'], duration_days, session.get('user_id'))
    return jsonify({'ok': True, 'message': '用户已封禁'})


# ==================== 公告管理 ====================
# 公告支持 info/success/warning/danger 四种类型，状态 active/draft/archived，
# 支持 is_pinned 置顶与 start_at/end_at 生效区间。首页仅展示 status='active'
# 且当前时间在 [start_at, end_at] 区间内的公告。
_VALID_ANN_TYPES = ('info', 'success', 'warning', 'danger')
_VALID_ANN_STATUS = ('active', 'draft', 'archived')


def _parse_ann_datetime(raw):
    """将 datetime-local 输入（如 '2026-07-21T14:30'）转换为 MySQL DATETIME 字符串。

    空字符串或 None 返回 None（表示未设置生效/失效时间）。
    """
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    # datetime-local 提交 'YYYY-MM-DDTHH:MM'，MySQL 接受 'YYYY-MM-DD HH:MM:SS'
    return raw.replace('T', ' ') if 'T' in raw else raw


@admin_bp.route('/admin/announcements')
@require_level('super_admin')
def admin_announcements():
    """公告管理页：列出全部公告（含创建人用户名）。"""
    announcements = query(
        """SELECT a.*, u.username AS creator_name
           FROM announcements a
           LEFT JOIN users u ON u.id = a.created_by
           ORDER BY a.created_at DESC"""
    ) or []
    return render_template(
        'admin_announcements.html',
        announcements=announcements,
        current_user=current_user(),
    )


@admin_bp.route('/admin/announcements/create', methods=['POST'])
@require_level('super_admin')
def admin_announcements_create():
    """创建新公告。"""
    admin = current_user()
    title = (request.form.get('title', '') or '').strip()
    ann_type = (request.form.get('type', 'info') or 'info').strip()
    content = (request.form.get('content', '') or '').strip()
    is_pinned = 1 if request.form.get('is_pinned') in ('1', 'on', 'true', 'True') else 0
    status = (request.form.get('status', 'active') or 'active').strip()
    start_at = _parse_ann_datetime(request.form.get('start_at'))
    end_at = _parse_ann_datetime(request.form.get('end_at'))

    if not title:
        return '标题不能为空', 400
    if ann_type not in _VALID_ANN_TYPES:
        ann_type = 'info'
    if status not in _VALID_ANN_STATUS:
        status = 'active'

    execute(
        'INSERT INTO announcements '
        '(title, type, content, is_pinned, status, start_at, end_at, created_by) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
        (title, ann_type, content, is_pinned, status, start_at, end_at, admin['id'])
    )
    logger.info("公告已创建: title=%s type=%s status=%s by=%s",
                title, ann_type, status, admin['id'])
    return redirect(url_for('admin.admin_announcements'))


@admin_bp.route('/admin/announcements/<int:aid>/update', methods=['POST'])
@require_level('super_admin')
def admin_announcements_update(aid):
    """更新公告。"""
    row = query_one('SELECT id FROM announcements WHERE id = %s', [aid])
    if not row:
        return '公告不存在', 404

    title = (request.form.get('title', '') or '').strip()
    ann_type = (request.form.get('type', 'info') or 'info').strip()
    content = (request.form.get('content', '') or '').strip()
    is_pinned = 1 if request.form.get('is_pinned') in ('1', 'on', 'true', 'True') else 0
    status = (request.form.get('status', 'active') or 'active').strip()
    start_at = _parse_ann_datetime(request.form.get('start_at'))
    end_at = _parse_ann_datetime(request.form.get('end_at'))

    if not title:
        return '标题不能为空', 400
    if ann_type not in _VALID_ANN_TYPES:
        ann_type = 'info'
    if status not in _VALID_ANN_STATUS:
        status = 'active'

    execute(
        'UPDATE announcements SET '
        'title = %s, type = %s, content = %s, is_pinned = %s, status = %s, '
        'start_at = %s, end_at = %s WHERE id = %s',
        (title, ann_type, content, is_pinned, status, start_at, end_at, aid)
    )
    logger.info("公告已更新: aid=%s title=%s by=%s", aid, title, current_user().get('id'))
    return redirect(url_for('admin.admin_announcements'))


@admin_bp.route('/admin/announcements/<int:aid>/delete', methods=['POST'])
@require_level('super_admin')
def admin_announcements_delete(aid):
    """删除公告。"""
    row = query_one('SELECT id FROM announcements WHERE id = %s', [aid])
    if not row:
        return '公告不存在', 404
    execute('DELETE FROM announcements WHERE id = %s', [aid])
    logger.info("公告已删除: aid=%s by=%s", aid, current_user().get('id'))
    return redirect(url_for('admin.admin_announcements'))
