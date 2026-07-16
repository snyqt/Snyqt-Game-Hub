# -*- coding: utf-8 -*-
"""
SNYQT Game Hub uWSGI 入口文件

uWSGI 通过 `module = wsgi:app` 加载本文件，获取 Flask app 实例。
生产环境推荐使用 uWSGI（已配置 IPv4 + IPv6 双 socket 监听，详见 uwsgi.ini）。
本地开发请使用 `python run.py`（带双栈监听 + debug + reloader 关闭）。
"""
from app import create_app

app = create_app()


if __name__ == '__main__':
    # 兜底：直接 python wsgi.py 运行时使用双栈监听（仅供调试）
    # 优先复用 run.py 的双栈实现，保证 Windows/macOS/Linux 行为一致
    try:
        from run import _run_dual_stack
        _run_dual_stack(
            app,
            host_v4='0.0.0.0',
            host_v6='::',
            port=80,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except ImportError:
        # run.py 不在 PYTHONPATH 时退化到 Flask 原生（Linux 自动双栈）
        app.run(host='::', port=80, debug=False, use_reloader=False, threaded=True)
