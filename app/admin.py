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
    Blueprint, render_template, request, jsonify, abort, session
)

from app.database import query, query_one, execute, query_all
from app.permissions import require_level, set_permission, is_super_admin
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
        'SELECT u.id, u.username, u.avatar, u.points, '
        'GROUP_CONCAT(DISTINCT p.permission_level SEPARATOR ",") AS permission_level, '
        'GROUP_CONCAT(DISTINCT p.status SEPARATOR ",") AS status '
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
    """封禁游戏。"""
    execute('UPDATE games SET is_banned = 1 WHERE id = %s', [gid])
    return jsonify({'success': True, 'message': '游戏已封禁'})


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
    """处理举报：action=valid 标记属实并封禁目标，action=invalid 标记不属实并删除。"""
    if not is_super_admin(session.get('user_id')):
        return jsonify({'error': '权限不足'}), 403
    action = (request.get_json(silent=True) or {}).get('action')
    if action == 'valid':
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
            # 创建处罚记录
            post = query_one('SELECT title FROM community_posts WHERE id=%s', (report['target_id'],))
            target_title = post['title'] if post else ''
            execute(
                'INSERT INTO penalty_records (target_type, target_id, target_title, reason, action, admin_id) '
                'VALUES (%s,%s,%s,%s,%s,%s)',
                ('post', report['target_id'], target_title, report['reason'], 'ban', session.get('user_id'))
            )
            logger.info("举报处理-属实: report_id=%s target_id=%s admin_id=%s",
                        rid, report['target_id'], session.get('user_id'))
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
    else:
        return jsonify({'error': '未知目标类型'}), 400

    # 删除对应的处罚记录
    execute('DELETE FROM penalty_records WHERE target_type=%s AND target_id=%s',
            (target_type, target_id))
    logger.info("解除封禁: type=%s id=%s admin_id=%s", target_type, target_id, session.get('user_id'))
    return jsonify({'ok': True})
