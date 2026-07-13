# -*- coding: utf-8 -*-
"""
三级权限系统模块（super_admin > developer > user）
- get_permission(user_id): 查 permissions 表，无记录或非 approved 视为 'user'
- require_level(min_level): 装饰器，校验当前用户权限级别
- is_super_admin() / is_developer(): 辅助函数
- set_permission(user_id, level, granted_by): 工具函数，供 admin_bp 调用
- /api/developer/apply (POST, 需登录): 读 DEVELOPER_REVIEW，auto->approved / manual->pending
"""
import logging
from functools import wraps

from flask import Blueprint, session, jsonify, redirect, url_for, render_template, request

from config.config import DEVELOPER_REVIEW
from app.database import query_one, execute
from app.auth import current_user

logger = logging.getLogger(__name__)

permissions_bp = Blueprint('permissions', __name__)

# 权限级别排序：super_admin > developer > user
_LEVEL_ORDER = {'user': 0, 'developer': 1, 'super_admin': 2}


def _level_value(level):
    """返回权限级别数值，未知级别视为 0。"""
    return _LEVEL_ORDER.get(level, 0)


# ==================== 辅助查询 ====================
def get_permission(user_id):
    """
    查询用户权限级别。
    - 无记录返回 'user'
    - status != 'approved' 也视为 'user'
    """
    if not user_id:
        return 'user'
    row = query_one(
        'SELECT permission_level, status FROM permissions WHERE user_id = %s',
        (user_id,),
    )
    if not row:
        return 'user'
    if row.get('status') != 'approved':
        return 'user'
    return row.get('permission_level', 'user')


def is_super_admin(user_id=None):
    """判断当前/指定用户是否为超级管理员。"""
    if user_id is None:
        user = current_user()
        if not user:
            return False
        user_id = user['id']
    return get_permission(user_id) == 'super_admin'


def is_developer(user_id=None):
    """判断当前/指定用户是否为开发者（含 super_admin，因其权限更高）。"""
    if user_id is None:
        user = current_user()
        if not user:
            return False
        user_id = user['id']
    level = get_permission(user_id)
    return _level_value(level) >= _level_value('developer')


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
    设置/更新用户权限级别。
    - 若 permissions 表无该用户记录，INSERT
    - 否则 UPDATE permission_level + granted_by + status=approved + updated_at
    返回 lastrowid 或受影响行数。
    """
    valid_levels = {'user', 'developer', 'super_admin'}
    if level not in valid_levels:
        raise ValueError(f"非法权限级别: {level}")

    existing = query_one('SELECT id FROM permissions WHERE user_id = %s', (user_id,))
    if existing:
        return execute(
            'UPDATE permissions SET permission_level=%s, granted_by=%s, status=%s WHERE user_id=%s',
            (level, granted_by, 'approved', user_id),
        )
    return execute(
        'INSERT INTO permissions (user_id, permission_level, granted_by, status) VALUES (%s, %s, %s, %s)',
        (user_id, level, granted_by, 'approved'),
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
    current_level = get_permission(user_id)
    if current_level == 'developer' or current_level == 'super_admin':
        return jsonify({'success': False, 'message': '您已是开发者或更高权限'}), 400

    # 读取配置决定审核规则（auto=直接通过 / manual=需管理员审核）
    review_mode = DEVELOPER_REVIEW

    if review_mode == 'auto':
        # 直接通过
        set_permission(user_id, 'developer', granted_by=None)
        logger.info("开发者申请自动通过: user_id=%s", user_id)
        return jsonify({
            'success': True,
            'message': '开发者权限已开通',
            'status': 'approved',
        })
    else:
        # manual：写入 pending，等待管理员审核
        _submit_application(user_id, 'developer')
        logger.info("开发者申请已提交，等待审核: user_id=%s", user_id)
        return jsonify({
            'success': True,
            'message': '申请已提交，等待管理员审核',
            'status': 'pending',
        })


@permissions_bp.route('/apply')
def apply_page():
    """权限申请页：展示当前权限与可申请的级别（开发者 / 超级管理员）。"""
    user = current_user()
    if not user:
        return redirect(url_for('auth.login', next=request.full_path if request.query_string else request.path))

    user_id = user['id']
    current_level = get_permission(user_id)

    # 查询当前申请记录（含状态）
    application = query_one(
        'SELECT permission_level, status, updated_at FROM permissions WHERE user_id = %s',
        (user_id,),
    )

    return render_template(
        'apply.html',
        current_level=current_level,
        application=application,
        developer_review=DEVELOPER_REVIEW,
    )


@permissions_bp.route('/api/permissions/apply', methods=['POST'])
def apply_permission():
    """通用权限申请接口。

    body/form: {level: developer|super_admin}
    - developer：按 DEVELOPER_REVIEW（auto/manual）处理
    - super_admin：一律 manual（pending），需现有超级管理员审核
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    if request.is_json:
        level = (request.get_json(silent=True) or {}).get('level')
    else:
        level = request.form.get('level')
    level = (level or '').strip().lower()

    if level not in ('developer', 'super_admin'):
        return jsonify({'success': False, 'message': '无效的申请级别'}), 400

    user_id = user['id']
    current_level = get_permission(user_id)

    # 不允许申请同级或更低权限
    if _level_value(level) <= _level_value(current_level):
        return jsonify({
            'success': False,
            'message': f'您当前已是 {current_level}，无需申请该权限',
        }), 400

    # 已有 pending 申请则提示
    existing = query_one(
        'SELECT status FROM permissions WHERE user_id = %s',
        (user_id,),
    )
    if existing and existing.get('status') == 'pending':
        return jsonify({'success': False, 'message': '您已有一个待审核的申请，请等待管理员处理'}), 400

    # developer：读取 DEVELOPER_REVIEW 决定 auto/manual
    # super_admin：一律 manual
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
    _submit_application(user_id, level)
    logger.info("权限申请已提交，等待审核: user_id=%s level=%s", user_id, level)
    return jsonify({
        'success': True,
        'message': '申请已提交，等待管理员审核',
        'status': 'pending',
        'level': level,
    })


def _submit_application(user_id, level):
    """写入/更新一条 pending 权限申请。"""
    existing = query_one('SELECT id FROM permissions WHERE user_id = %s', (user_id,))
    if existing:
        execute(
            'UPDATE permissions SET permission_level=%s, status=%s, granted_by=NULL WHERE user_id=%s',
            (level, 'pending', user_id),
        )
    else:
        execute(
            'INSERT INTO permissions (user_id, permission_level, status) VALUES (%s, %s, %s)',
            (user_id, level, 'pending'),
        )
