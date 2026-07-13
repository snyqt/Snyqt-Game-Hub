# -*- coding: utf-8 -*-
"""
SNYQT Game Hub 数据库访问层
使用 pymysql + DictCursor 提供简单连接函数与查询/执行封装；
启动期 check_tables() 检查 7 张表结构，缺失/错误则 DROP + CREATE。
"""
import logging
import pymysql
from pymysql.cursors import DictCursor

from config.config import (
    DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_CHARSET
)

logger = logging.getLogger(__name__)


# ==================== 表结构 DDL（须与现存表完全对齐） ====================
EXPECTED_TABLES = {
    'users': """
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    snyqt_user_id VARCHAR(64) NOT NULL UNIQUE,
    username VARCHAR(100) NOT NULL,
    avatar VARCHAR(255),
    points INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'permissions': """
CREATE TABLE permissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    permission_level VARCHAR(20) NOT NULL,
    granted_by INT,
    status VARCHAR(20) DEFAULT 'approved',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'games': """
CREATE TABLE games (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    cover_image VARCHAR(255),
    screenshots TEXT,
    developer_id INT NOT NULL,
    hosting_type VARCHAR(20),
    entry_file VARCHAR(255),
    python_main VARCHAR(255),
    python_command VARCHAR(500),
    python_port INT,
    python_pid INT,
    status VARCHAR(30) DEFAULT 'pending',
    is_banned TINYINT DEFAULT 0,
    download_count INT DEFAULT 0,
    avg_rating FLOAT DEFAULT 0,
    rating_count INT DEFAULT 0,
    tags VARCHAR(255),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'reviews': """
CREATE TABLE reviews (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    user_id INT NOT NULL,
    rating TINYINT NOT NULL,
    comment TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_review (game_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'game_library': """
CREATE TABLE game_library (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    game_id INT NOT NULL,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_lib (user_id, game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'python_review_queue': """
CREATE TABLE python_review_queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    reviewer_id INT,
    reason TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'points_log': """
CREATE TABLE points_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    points INT NOT NULL,
    action VARCHAR(50) NOT NULL,
    description VARCHAR(255),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
}


# ==================== 连接与查询 ====================
def get_db():
    """
    返回一个新的 MySQL 连接（DictCursor）。
    调用方需在用完后 close()，或在 with 语句中使用。
    """
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        charset=DB_CHARSET,
        cursorclass=DictCursor,
    )


def query(sql, args=()):
    """执行 SELECT，返回 dict 列表。"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def query_one(sql, args=()):
    """执行 SELECT，返回单条 dict 或 None。"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchone()
    finally:
        conn.close()


def execute(sql, args=()):
    """
    执行 INSERT/UPDATE/DELETE 并 commit。
    返回：INSERT 返回 lastrowid；UPDATE/DELETE 返回受影响行数。
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            conn.commit()
            # lastrowid 仅对 AUTO_INCREMENT INSERT 有意义；非 INSERT 时为 0
            if cur.lastrowid:
                return cur.lastrowid
            return cur.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ==================== 表结构检查 ====================
def _table_exists(conn, table_name):
    """检查指定表是否存在。"""
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE %s", (table_name,))
        return cur.fetchone() is not None


def check_tables():
    """
    启动期表结构检查：
    - 逐表检查 SHOW TABLES LIKE 'x'
    - 存在则日志输出 [OK] 表 'xxx' 结构正确
    - 不存在则日志输出 [FAIL] 表 'xxx': 表不存在
    - 全部 OK 输出"数据库表结构检查通过"
    - 否则输出"发现 N 个表结构不正确，开始重建"，逐表 DROP + CREATE
    """
    logger.info("=" * 60)
    logger.info("开始数据库表结构检查 (数据库: %s)", DB_NAME)
    logger.info("=" * 60)

    failed_tables = []

    conn = get_db()
    try:
        for table_name in EXPECTED_TABLES:
            if _table_exists(conn, table_name):
                logger.info("[OK] 表 '%s' 结构正确", table_name)
            else:
                logger.error("[FAIL] 表 '%s': 表不存在", table_name)
                failed_tables.append(table_name)
    finally:
        conn.close()

    if not failed_tables:
        logger.info("数据库表结构检查通过")
        logger.info("=" * 60)
        return

    # 存在不正确的表，开始重建
    logger.warning("发现 %d 个表结构不正确，开始重建", len(failed_tables))
    conn = get_db()
    try:
        for table_name in failed_tables:
            ddl = EXPECTED_TABLES[table_name]
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS `%s`" % table_name)
                cur.execute(ddl)
            conn.commit()
            logger.warning("已重建表: %s", table_name)
    finally:
        conn.close()
    logger.info("数据库表结构重建完成")
    logger.info("=" * 60)
