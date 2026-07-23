# -*- coding: utf-8 -*-
"""
SNYQT Game Hub Flask 应用工厂
- 创建 app，配置 SECRET_KEY、session、templates/static 路径
- 启动期调用 check_tables() 检查表结构（打分隔线日志）
- 注册 context_processor 注入 current_user()
- 注册全部蓝图（turnstile/auth/permissions/games/view/points/admin）
- 注册 Turnstile 全局中间件
"""
import logging
import os
import time
import uuid

from flask import Flask, session, request, g

from config.config import SECRET_KEY
from app.database import check_tables
from app.turnstile import register_turnstile_middleware
# current_user 来自 auth 模块，供 context_processor 注入
from app.auth import current_user

logger = logging.getLogger(__name__)


def create_app():
    """Flask 应用工厂。"""
    # 项目根目录：app/__init__.py 的上两级
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(base_dir, 'templates')
    static_dir = os.path.join(base_dir, 'static')

    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
    )

    # ---------- 配置 ----------
    app.config['SECRET_KEY'] = SECRET_KEY
    # session 永久化（默认 31 天）；Turnstile 验证状态会写入 session
    app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 60 * 24 * 7  # 7 天
    app.config['SESSION_PERMANENT'] = True
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 上传上限 500MB

    # ---------- Session Cookie 配置 ----------
    # OAuth 回调是从 snyqt-account.iepose.cn 跨站重定向回 /callback，
    # 必须正确设置 SameSite / Secure，否则浏览器会丢弃 cookie，
    # 导致回调时 session 丢失、state 校验失败。
    app.config['SESSION_COOKIE_SECURE'] = True          # 仅 HTTPS 传输
    app.config['SESSION_COOKIE_HTTPONLY'] = True         # 禁止 JS 访问
    app.config['SESSION_COOKIE_SAMESITE'] = 'None'       # 允许跨站回调携带
    # cookie 名称固定，便于反代场景下识别
    app.config['SESSION_COOKIE_NAME'] = 'snyqt_session'
    # 若本地用 http 调试，环境变量可关闭 Secure 与 SameSite=None
    if os.getenv('SESSION_COOKIE_SECURE', 'true').lower() == 'false':
        app.config['SESSION_COOKIE_SECURE'] = False
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # ---------- 启动期表结构检查 ----------
    logger.info("#" * 60)
    logger.info("# SNYQT Game Hub 启动中")
    logger.info("#" * 60)
    try:
        check_tables()
    except Exception as e:
        logger.exception("数据库表结构检查失败: %s", e)

    # ---------- context processor：注入 current_user ----------
    @app.context_processor
    def inject_user():
        return {'current_user': current_user()}

    # ---------- 注册蓝图 ----------
    # 局部导入，避免在模块加载阶段触发循环依赖
    from app.turnstile import turnstile_bp
    from app.auth import auth_bp
    from app.permissions import permissions_bp
    from app.games import games_bp
    from app.view import view_bp
    from app.points import points_bp
    from app.admin import admin_bp
    from app.community import community_bp
    from app.payments import payments_bp
    from app.wallet import wallet_bp

    app.register_blueprint(turnstile_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(permissions_bp)
    app.register_blueprint(games_bp)
    app.register_blueprint(view_bp)
    app.register_blueprint(points_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(community_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(wallet_bp)

    # ---------- 请求生命周期日志中间件 ----------
    @app.before_request
    def log_request_start():
        """请求开始：记录方法、路径、Content-Length、请求 ID。"""
        g.req_id = uuid.uuid4().hex[:8]
        g.req_start_time = time.time()
        g.client_type = request.headers.get('X-Client', 'web')
        g.api_version = request.headers.get('X-API-Version', '1')
        content_length = request.headers.get('Content-Length', '0')
        content_type = request.headers.get('Content-Type', '')
        # 上传请求重点记录
        if request.path.startswith('/api/games/upload') or int(content_length or 0) > 1_000_000:
            logger.info(
                "[REQ %s] 开始 %s %s | Content-Length: %s bytes (%.1f MB) | Content-Type: %s",
                g.req_id, request.method, request.path,
                content_length, int(content_length or 0) / 1024 / 1024, content_type
            )
        else:
            logger.debug("[REQ %s] %s %s", g.req_id, request.method, request.path)

    @app.after_request
    def log_request_end(response):
        """请求结束：记录状态码、耗时、响应大小。"""
        req_id = getattr(g, 'req_id', '--------')
        start = getattr(g, 'req_start_time', None)
        elapsed = f"{(time.time() - start) * 1000:.0f}ms" if start else '-'
        content_length = request.headers.get('Content-Length', '0')
        # 上传请求或大响应重点记录
        is_upload = request.path.startswith('/api/games/upload')
        is_large = int(content_length or 0) > 1_000_000 or int(response.headers.get('Content-Length', 0) or 0) > 1_000_000
        if is_upload or is_large:
            logger.info(
                "[REQ %s] 结束 %s %s -> %d | 耗时: %s | 响应: %s bytes",
                req_id, request.method, request.path,
                response.status_code, elapsed,
                response.headers.get('Content-Length', '?')
            )
        # PC 客户端 API 适配：统一响应头
        response.headers['X-API-Version'] = '1'
        response.headers['X-Client-Supported'] = 'web,desktop'
        response.headers['Access-Control-Expose-Headers'] = 'X-API-Version, X-Client-Supported'
        return response

    @app.teardown_request
    def log_request_teardown(exc):
        """请求销毁：记录未捕获的异常。"""
        if exc:
            req_id = getattr(g, 'req_id', '--------')
            logger.exception("[REQ %s] 异常: %s", req_id, exc)

    # ---------- 注册 Turnstile 中间件 ----------
    from config.config import TURNSTILE_ENABLED
    if TURNSTILE_ENABLED:
        register_turnstile_middleware(app)
        logger.info("Turnstile 人机验证已启用")
    else:
        logger.info("Turnstile 人机验证已关闭（TURNSTILE_ENABLED=false）")

    logger.info("#" * 60)
    logger.info("# SNYQT Game Hub 启动完成，已注册全部蓝图与中间件")
    logger.info("#" * 60)

    return app
