# -*- coding: utf-8 -*-
"""共享工具模块：Python 进程管理、端口分配、文件打包。

依赖契约：
- from app.database import execute, query
- from config.config import UPLOAD_FOLDER, PORT_RANGE_START, PORT_RANGE_END
"""
import os
import io
import logging
import socket
import zipfile
import shlex
import subprocess

from app.database import execute, query
from config.config import UPLOAD_FOLDER, PORT_RANGE_START, PORT_RANGE_END

_log = logging.getLogger('helpers')


def get_public_ipv6():
    """获取本机公网 IPv6 地址。

    通过向 Google DNS (2001:4860:4860::8888) 发起连接并读取 getsockname()
    来获取本机对外 IPv6 地址。若 IPv6 不可用，返回 None。

    :return: 公网 IPv6 地址字符串，或 None
    """
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.connect(('2001:4860:4860::8888', 80))
        addr = sock.getsockname()[0]
        sock.close()
        # 过滤掉链路本地地址（fe80::）和回环地址（::1）
        if addr.startswith('fe80:') or addr == '::1':
            return None
        return addr
    except (OSError, socket.error):
        return None


def game_dir(game_id):
    """获取游戏文件目录的绝对路径。

    :param game_id: 游戏 ID
    :return: uploads/games/<game_id> 的绝对路径
    """
    # UPLOAD_FOLDER 已为 'uploads/games'，无需再拼接 'games'
    path = os.path.join(UPLOAD_FOLDER, str(game_id))
    return os.path.abspath(path)


def is_process_alive(pid):
    """判断进程是否存活（Windows 实现）。

    :param pid: 进程 ID
    :return: True 表示进程仍在运行
    """
    if not pid:
        return False
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False

    if os.name == 'nt':
        # Windows: 使用 ctypes 调用 OpenProcess + GetExitCodeProcess
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_int)
        if not handle:
            return False
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        if not ok:
            return False
        return exit_code.value == STILL_ACTIVE
    else:
        # POSIX: 使用 os.kill(pid, 0) 探测
        try:
            os.kill(pid_int, 0)
            return True
        except OSError:
            return False


def _port_in_use(port):
    """检测端口是否被占用（同时尝试 IPv4 与 IPv6 绑定，兼容双栈子进程）。"""
    for family, addr in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                # IPv6 only flag 关闭 → 双栈监听
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            try:
                s.bind((addr, port))
                return False
            except OSError:
                continue
    return True


def _port_responds(port):
    """检测端口是否已有进程在监听（优先尝试 IPv4，回退 IPv6）。"""
    for family, addr in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        try:
            with socket.create_connection((addr, port), timeout=1):
                return True
        except OSError:
            continue
    return False


def allocate_port():
    """在 PORT_RANGE_START..END 范围内寻找空闲端口。

    会跳过已分配给其它游戏的端口，并通过 socket 绑定测试确认空闲。

    :return: 可用端口 int
    :raises RuntimeError: 无可用端口时抛出
    """
    # 查询已被占用的端口（数据库中已记录的 python_port）
    used_rows = query(
        'SELECT python_port FROM games WHERE python_port IS NOT NULL', []
    )
    used_ports = {row['python_port'] for row in used_rows}

    for port in range(PORT_RANGE_START, PORT_RANGE_END + 1):
        if port in used_ports:
            continue
        if not _port_in_use(port):
            return port
    raise RuntimeError(
        f'端口范围 {PORT_RANGE_START}-{PORT_RANGE_END} 内无可用端口'
    )


def start_python(game):
    """启动游戏 Python 子进程。

    在游戏上传目录下用 subprocess.Popen 执行 game['python_command']，
    cwd 设为游戏目录，记录 pid 到 games.python_pid。

    :param game: 游戏记录 dict，需含 id、python_command
    :return: 进程 pid
    """
    game_id = game['id']
    cwd = game_dir(game_id)
    command = game.get('python_command') or ''
    if not command:
        raise ValueError('游戏未配置 python_command')

    # 解析命令字符串为参数列表
    args = shlex.split(command)

    # Windows 下使用 CREATE_NEW_PROCESS_GROUP 创建独立进程组
    creationflags = 0
    if os.name == 'nt':
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        args,
        cwd=cwd,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 记录 pid 到数据库
    execute(
        'UPDATE games SET python_pid=%s WHERE id=%s',
        (proc.pid, game_id)
    )
    return proc.pid


def stop_python(game):
    """终止游戏 Python 子进程。

    按 python_pid 终止进程树（Windows 用 taskkill /T），并清空 python_pid。

    :param game: 游戏记录 dict，需含 id、python_pid
    """
    pid = game.get('python_pid')
    game_id = game['id']

    if pid:
        if os.name == 'nt':
            # Windows: taskkill 终止整个进程树
            subprocess.run(
                ['taskkill', '/PID', str(pid), '/F', '/T'],
                capture_output=True
            )
        else:
            import signal
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass

    # 清空 python_pid
    execute(
        'UPDATE games SET python_pid=NULL WHERE id=%s',
        (game_id,)
    )


def ensure_running(game):
    """确保 Python 游戏进程正在运行；若未运行或端口无响应则启动。

    :param game: 游戏记录 dict
    :return: 启动后的 pid（若已运行则返回原 pid）
    """
    game_id = game['id']
    pid = game.get('python_pid')
    port = game.get('python_port')

    # 若 pid 存活且端口在监听，视为已运行
    if pid and is_process_alive(pid) and port and _port_responds(port):
        return pid

    # 进程存活但端口无响应：先终止旧进程
    if pid and is_process_alive(pid):
        stop_python(game)

    # 若未分配端口，分配一个
    if not port:
        port = allocate_port()
        execute(
            'UPDATE games SET python_port=%s WHERE id=%s',
            (port, game_id)
        )
        game['python_port'] = port

    # 启动新进程
    new_pid = start_python(game)
    game['python_pid'] = new_pid
    return new_pid


def pack_game(game_id):
    """将 uploads/games/<game_id>/ 目录打包为 zip，返回 BytesIO（内存）。

    :param game_id: 游戏 ID
    :return: io.BytesIO 对象，指针已定位到 0
    """
    buffer = io.BytesIO()
    base = game_dir(game_id)

    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, base)
                    zf.write(full_path, arcname)

    buffer.seek(0)
    return buffer
