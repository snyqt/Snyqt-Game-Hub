# -*- coding: utf-8 -*-
"""
多权限系统模块（多行权限模型：每个用户可有多条权限记录，每条对应一个权限级别）
- get_permissions(user_id): 返回用户所有已批准权限的集合（始终包含 'user'）
- get_permission(user_id): 返回最高权限级别（向后兼容）
- has_permission(user_id, level): 检查是否拥有特定权限
- require_level(min_level): 装饰器，校验当前用户最高权限级别 >= min_level
- is_super_admin() / is_developer(): 辅助函数
- set_permission(user_id, level, granted_by): 插入一条已批准权限记录
- _submit_application(user_id, level, reason): 插入一条待审核申请记录
- /api/developer/apply / /api/permissions/apply: 申请权限
"""
import logging
from functools import wraps

from flask import Blueprint, session, jsonify, redirect, url_for, render_template, request

from config.config import DEVELOPER_REVIEW
from app.database import query, query_one, execute
from app.auth import current_user

logger = logging.getLogger(__name__)

permissions_bp = Blueprint('permissions', __name__)

# 权限级别排序：super_admin > developer > reviewer > user
_LEVEL_ORDER = {'user': 0, 'reviewer': 1, 'developer': 2, 'super_admin': 3}


def _level_value(level):
    """返回权限级别数值，未知级别视为 0。"""
    return _LEVEL_ORDER.get(level, 0)


def _parse_permissions(level_str):
    """解析逗号分隔的权限字符串为集合。"""
    if not level_str:
        return set()
    return {p.strip() for p in level_str.split(',') if p.strip()}


def _join_permissions(perms):
    """将权限集合合并为逗号分隔字符串。"""
    return ','.join(sorted(perms, key=lambda p: _level_value(p)))


# ==================== 辅助查询 ====================
def get_permissions(user_id):
    """
    查询用户所有已批准的权限级别（返回集合）。
    - 查询 permissions 表中 user_id 匹配且 status='approved' 的所有行
    - 收集所有 permission_level 值到集合
    - 始终包含 'user' 基础权限（每个用户默认拥有）
    """
    if not user_id:
        return {'user'}
    rows = query(
        'SELECT permission_level FROM permissions WHERE user_id = %s AND status = %s',
        (user_id, 'approved'),
    )
    perms = {'user'}
    for row in rows:
        level = (row.get('permission_level') or '').strip()
        if level:
            perms.add(level)
    return perms


def get_permission(user_id):
    """
    返回用户最高权限级别（向后兼容）。
    - 无记录返回 'user'
    - status != 'approved' 也视为 'user'
    """
    perms = get_permissions(user_id)
    return max(perms, key=lambda p: _level_value(p))


def has_permission(user_id, level):
    """检查用户是否拥有指定权限级别。"""
    return level in get_permissions(user_id)


def is_super_admin(user_id=None):
    """判断当前/指定用户是否为超级管理员。"""
    if user_id is None:
        user = current_user()
        if not user:
            return False
        user_id = user['id']
    return has_permission(user_id, 'super_admin')


def is_developer(user_id=None):
    """判断当前/指定用户是否为开发者。"""
    if user_id is None:
        user = current_user()
        if not user:
            return False
        user_id = user['id']
    return has_permission(user_id, 'developer')


def require_level(min_level):
    """
    装饰器：校验当前用户权限级别 >= min_level。
    - 未登录：重定向 /login
    - 权限不足：返回 403
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for('auth.login', next=request_full_path()))
            user_level = get_permission(user['id'])
            if _level_value(user_level) < _level_value(min_level):
                return '权限不足，需要 ' + min_level + ' 级别', 403
            return view_func(*args, **kwargs)
        return wrapper
    return decorator


def request_full_path():
    """获取当前请求完整路径（避免在模块顶部 import request 时的循环依赖风险）。"""
    from flask import request
    return request.full_path if request.query_string else request.path


# ==================== 工具函数（供 admin_bp 调用） ====================
def set_permission(user_id, level, granted_by=None):
    """
    授予用户一个权限级别：插入一条新的已批准权限记录。
    - 不修改用户已有的其他权限记录
    - 若该级别已有已批准记录，则跳过（避免重复）
    """
    valid_levels = {'user', 'developer', 'super_admin', 'reviewer'}
    if level not in valid_levels:
        raise ValueError(f"非法权限级别: {level}")

    # 避免重复插入相同的已批准权限
    existing = query_one(
        'SELECT id FROM permissions WHERE user_id = %s AND permission_level = %s AND status = %s',
        (user_id, level, 'approved'),
    )
    if existing:
        return existing['id']

    return execute(
        'INSERT INTO permissions (user_id, permission_level, status, granted_by) VALUES (%s, %s, %s, %s)',
        (user_id, level, 'approved', granted_by),
    )


# ==================== 路由 ====================
@permissions_bp.route('/api/developer/apply', methods=['POST'])
def apply_developer():
    """普通用户申请开发者权限：根据 DEVELOPER_REVIEW 决定 auto/manual。

    保留旧接口以向后兼容；新逻辑见 /api/permissions/apply。
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = user['id']
    if has_permission(user_id, 'developer') or has_permission(user_id, 'super_admin'):
        return jsonify({'success': False, 'message': '您已是开发者或更高权限'}), 400

    review_mode = DEVELOPER_REVIEW

    if review_mode == 'auto':
        set_permission(user_id, 'developer', granted_by=None)
        logger.info("开发者申请自动通过: user_id=%s", user_id)
        return jsonify({
            'success': True,
            'message': '开发者权限已开通',
            'status': 'approved',
        })
    else:
        _submit_application(user_id, 'developer', '')
        logger.info("开发者申请已提交，等待审核: user_id=%s", user_id)
        return jsonify({
            'success': True,
            'message': '申请已提交，等待管理员审核',
            'status': 'pending',
        })


@permissions_bp.route('/apply')
def apply_page():
    """权限申请页：展示当前权限与待审核申请。"""
    user = current_user()
    if not user:
        return redirect(url_for('auth.login', next=request.full_path if request.query_string else request.path))

    user_id = user['id']
    current_perms = get_permissions(user_id)

    # 查询当前用户的所有待审核申请记录
    pending_records = query(
        'SELECT id, permission_level, status, reason, created_at '
        'FROM permissions WHERE user_id = %s AND status = %s '
        'ORDER BY created_at DESC',
        (user_id, 'pending'),
    )

    return render_template(
        'apply.html',
        current_level=get_permission(user_id),
        current_perms=current_perms,
        pending_records=pending_records,
        developer_review=DEVELOPER_REVIEW,
    )


@permissions_bp.route('/api/permissions/apply', methods=['POST'])
def apply_permission():
    """通用权限申请接口。

    body/form: {level: developer|reviewer|super_admin, reason?: text}
    - developer：按 DEVELOPER_REVIEW（auto/manual）处理
    - reviewer/super_admin：一律 manual（pending），需管理员审核
    - 已拥有该权限则提示，但不阻止申请其他权限
    - 同一级别已有待审核申请时阻止重复申请
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    if request.is_json:
        body = request.get_json(silent=True) or {}
        level = body.get('level')
        reason = body.get('reason', '')
    else:
        level = request.form.get('level')
        reason = request.form.get('reason', '')
    level = (level or '').strip().lower()
    reason = (reason or '').strip()

    if level not in ('developer', 'reviewer', 'super_admin'):
        return jsonify({'success': False, 'message': '无效的申请级别'}), 400

    user_id = user['id']

    # 已拥有该权限则提示
    if has_permission(user_id, level):
        return jsonify({
            'success': False,
            'message': f'您已拥有 {level} 权限，无需重复申请',
        }), 400

    # 同一级别已有待审核申请则阻止
    pending_row = query_one(
        'SELECT COUNT(*) AS cnt FROM permissions WHERE user_id = %s AND permission_level = %s AND status = %s',
        (user_id, level, 'pending'),
    )
    if pending_row and pending_row['cnt'] > 0:
        return jsonify({'success': False, 'message': '您有待审核的申请'}), 400

    # developer：读取 DEVELOPER_REVIEW 决定 auto/manual
    # 其他：一律 manual
    if level == 'developer':
        auto = (DEVELOPER_REVIEW == 'auto')
    else:
        auto = False

    if auto:
        set_permission(user_id, 'developer', granted_by=None)
        logger.info("权限申请自动通过: user_id=%s level=developer", user_id)
        return jsonify({
            'success': True,
            'message': '开发者权限已开通',
            'status': 'approved',
            'level': 'developer',
        })

    # manual：写入 pending 等待审核
    _submit_application(user_id, level, reason)
    logger.info("权限申请已提交，等待审核: user_id=%s level=%s", user_id, level)
    return jsonify({
        'success': True,
        'message': '申请已提交，等待管理员审核',
        'status': 'pending',
        'level': level,
    })


def _submit_application(user_id, level, reason=''):
    """插入一条新的 pending 权限申请记录。

    - 不检查也不修改用户已有的权限记录
    - 用户的现有权限保持完全可用
    - 每次申请插入一行：(user_id, permission_level, status='pending', reason)
    """
    return execute(
        'INSERT INTO permissions (user_id, permission_level, status, reason) VALUES (%s, %s, %s, %s)',
        (user_id, level, 'pending', reason),
    )