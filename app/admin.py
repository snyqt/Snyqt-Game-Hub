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
    Blueprint, render_template, request, jsonify
)

from app.database import query, query_one, execute
from app.permissions import require_level, set_permission
from app.auth import current_user
from app.helpers import allocate_port

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
    # 用户与权限列表
    users_perms = query(
        'SELECT u.id, u.username, u.avatar, u.points, '
        'p.permission_level, p.status '
        'FROM users u LEFT JOIN permissions p ON u.id = p.user_id '
        'ORDER BY u.id'
    )

    # Python 审核队列（全部记录）
    review_queue = query(
        'SELECT q.*, g.title, g.developer_id, g.python_main, g.python_command '
        'FROM python_review_queue q JOIN games g ON q.game_id = g.id '
        'ORDER BY q.created_at DESC'
    )

    # 权限申请队列（pending 的权限申请，附带用户名/头像）
    applications = query(
        'SELECT p.user_id, p.permission_level, p.status, p.updated_at, '
        'u.username, u.avatar, u.points '
        'FROM permissions p JOIN users u ON p.user_id = u.id '
        "WHERE p.status = 'pending' "
        'ORDER BY p.updated_at DESC'
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

    if level not in ('user', 'developer', 'super_admin'):
        return jsonify({'success': False, 'message': '无效权限级别'}), 400

    set_permission(user_id, level, granted_by=admin['id'])
    return jsonify({'success': True, 'message': '权限已更新'})


@admin_bp.route('/api/admin/applications/<int:user_id>', methods=['POST'])
@require_level('super_admin')
def review_application(user_id):
    """审核权限申请。body: {action: approve|reject}。

    - approve：将申请级别置为 approved（granted_by 记录当前管理员）
    - reject：将状态置为 rejected，permission_level 回退为 user
    """
    admin = current_user()
    action = _get_json_or_form('action')

    row = query_one(
        'SELECT permission_level, status FROM permissions WHERE user_id = %s',
        [user_id]
    )
    if not row:
        return jsonify({'success': False, 'message': '申请记录不存在'}), 404

    if action == 'approve':
        level = row.get('permission_level', 'developer')
        execute(
            'UPDATE permissions SET status = %s, granted_by = %s WHERE user_id = %s',
            ('approved', admin['id'], user_id)
        )
        logger.info("权限申请已批准: user_id=%s level=%s by=%s", user_id, level, admin['id'])
        return jsonify({'success': True, 'message': f'已批准 {level} 申请'})

    elif action == 'reject':
        execute(
            'UPDATE permissions SET status = %s, permission_level = %s WHERE user_id = %s',
            ('rejected', 'user', user_id)
        )
        logger.info("权限申请已拒绝: user_id=%s", user_id)
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
