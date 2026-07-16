# -*- coding: utf-8 -*-
"""
SNYQT Game Hub 启动入口
- 配置 logging 输出到 game_hub.log 与控制台
- 创建 Flask app 并启动
# - 监听 IPv4 (0.0.0.0) + IPv6 (::) 双栈，端口 80
"""
import logging
import os
import socket
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


def _run_dual_stack(app, host_v4, host_v6, port, debug=False, use_reloader=False, threaded=True):
    """在双栈（IPv4 + IPv6）下启动 Flask 开发服务器。

    Python 在不同平台上对 ``host='::'`` 的行为不一致：
    - Windows / macOS 默认 IPV6_V6ONLY=1，只接受 IPv6 客户端
    - Linux 默认 IPV6_V6ONLY=0，会同时接受 IPv4

    为保证跨平台双栈可用，此处手动建立两个 socket，分别绑定 IPv4 和 IPv6，
    通过 werkzeug.serving.make_server 接管为 WSGI server（threaded=True 时多线程）。
    """
    from werkzeug.serving import make_server

    def _make_listen_socket(family, bind_host):
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind((bind_host, port))
        sock.listen(128)
        return sock

    import threading

    servers = []

    # IPv4 server（make_server 通过占位 host/port 创建，fd 为真实绑定）
    s4 = None
    try:
        s4 = _make_listen_socket(socket.AF_INET, host_v4)
        srv_v4 = make_server(
            host='0.0.0.0', port=port, app=app, threaded=threaded, fd=s4.fileno()
        )
        logging.getLogger('run').info("[IPv4] 监听 %s:%d", host_v4, port)
        servers.append(srv_v4)
    except OSError as e:
        logging.getLogger('run').warning("IPv4 监听失败: %s", e)
        if s4:
            s4.close()

    # IPv6 server
    s6 = None
    try:
        s6 = _make_listen_socket(socket.AF_INET6, host_v6)
        srv_v6 = make_server(
            host='::', port=port, app=app, threaded=threaded, fd=s6.fileno()
        )
        logging.getLogger('run').info("[IPv6] 监听 %s:%d", host_v6, port)
        servers.append(srv_v6)
    except OSError as e:
        logging.getLogger('run').warning("IPv6 监听失败: %s", e)
        if s6:
            s6.close()

    if not servers:
        raise OSError("IPv4 与 IPv6 监听全部失败，请检查端口 %d 是否被占用" % port)

    # 每个 server 在独立线程 serve_forever
    threads = []
    for srv in servers:
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        threads.append(t)

    # 主线程等待停止事件
    _stop_event = threading.Event()
    try:
        # 注册 SIGINT 处理器（仅 Unix 主线程有效；Windows 用 KeyboardInterrupt）
        import signal
        try:
            signal.signal(signal.SIGINT, lambda *_: _stop_event.set())
            signal.signal(signal.SIGTERM, lambda *_: _stop_event.set())
        except (ValueError, OSError):
            # 非主线程环境（线程化启动时），退回到键盘轮询
            pass

        while not _stop_event.is_set():
            _stop_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for srv in servers:
            try:
                srv.shutdown()
            except Exception:
                pass


if __name__ == '__main__':
    # 启动开发服务器
    # use_reloader=False：避免上传文件时 reloader 检测到目录变化重启进程，
    # 导致正在处理的大文件上传连接被中断（前端表现为进度条卡住 + 网络错误）
    # threaded=True：开启多线程，避免大文件上传长时间阻塞单线程导致
    # 反代（Nginx/Cloudflare）HTTP/2 流控超时、连接被重置（ERR_HTTP2_PROTOCOL_ERROR）
    #
    # 双栈监听：IPv4 (0.0.0.0) + IPv6 (::)，让 IPv4 与 IPv6 客户端都能访问
    _run_dual_stack(
        app,
        host_v4='0.0.0.0',
        host_v6='::',
        port=80,
        debug=True,
        use_reloader=False,
        threaded=True,
    )
