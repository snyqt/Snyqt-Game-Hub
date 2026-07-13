# -*- coding: utf-8 -*-
"""
SNYQT Game Hub uWSGI 入口文件

uWSGI 通过 `module = wsgi:app` 加载本文件，获取 Flask app 实例。
本地开发仍使用 `python run.py`（带 debug + reloader 关闭）。
"""
from app import create_app

app = create_app()


if __name__ == '__main__':
    # 允许直接 python wsgi.py 运行（仅供调试）
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False, threaded=True)
