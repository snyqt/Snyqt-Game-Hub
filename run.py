# -*- coding: utf-8 -*-
"""
SNYQT Game Hub 启动入口
- 配置 logging 输出到 game_hub.log 与控制台
- 创建 Flask app 并启动
- 监听 0.0.0.0:5000，debug=True
"""
import logging
import os
import sys

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def setup_logging():
    """配置 logging：同时输出到 game_hub.log 文件与控制台。"""
    log_path = os.path.join(BASE_DIR, 'game_hub.log')

    log_format = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    formatter = logging.Formatter(log_format, date_format)

    # 文件 handler
    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # 配置 root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    # 清除已有 handler，避免重复输出
    root_logger.handlers = []
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


# 配置日志（在导入 app 前完成，确保 app 内的日志能被捕获）
setup_logging()

from app import create_app
app = create_app()


if __name__ == '__main__':
    # 启动开发服务器
    # use_reloader=False：避免上传文件时 reloader 检测到目录变化重启进程，
    # 导致正在处理的大文件上传连接被中断（前端表现为进度条卡住 + 网络错误）
    # threaded=True：开启多线程，避免大文件上传长时间阻塞单线程导致
    # 反代（Nginx/Cloudflare）HTTP/2 流控超时、连接被重置（ERR_HTTP2_PROTOCOL_ERROR）
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False, threaded=True)
