# -*- coding: utf-8 -*-
"""
SNYQT Game Hub 数据库访问层
使用 pymysql + DictCursor 提供简单连接函数与查询/执行封装；
启动期 check_tables() 检查 17 张表结构，缺失/错误则 DROP + CREATE。
"""
import logging
import uuid
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
    status VARCHAR(20) DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login DATETIME,
    bio VARCHAR(2000) NOT NULL DEFAULT '',
    custom_profile_html LONGTEXT NULL DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'permissions': """
CREATE TABLE permissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    permission_level VARCHAR(20) NOT NULL,
    status ENUM('pending','approved','rejected') DEFAULT 'approved',
    reason TEXT,
    granted_by INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    category VARCHAR(50) DEFAULT '',
    access_mode VARCHAR(20) DEFAULT 'public',
    source_open TINYINT DEFAULT 1,
    external_url VARCHAR(500) DEFAULT '',
    price DECIMAL(10,2) DEFAULT 0.00,
    platform_share DECIMAL(5,2) DEFAULT 30.00,
    game_uid VARCHAR(20) UNIQUE,
    version VARCHAR(20) DEFAULT '1.0.0',
    play_count INT DEFAULT 0
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
    'game_versions': """
CREATE TABLE IF NOT EXISTS game_versions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    version VARCHAR(20) NOT NULL,
    changelog TEXT,
    zip_path VARCHAR(500),
    entry_file VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_game (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'file_hashes': """
CREATE TABLE IF NOT EXISTS file_hashes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    hash VARCHAR(64) UNIQUE NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'community_posts': """
CREATE TABLE community_posts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    title VARCHAR(200) NOT NULL,
    content TEXT,
    post_type VARCHAR(20) DEFAULT 'discussion',
    game_tag VARCHAR(50) DEFAULT '',
    is_starred TINYINT DEFAULT 0,
    is_pinned TINYINT DEFAULT 0,
    likes INT DEFAULT 0,
    comment_count INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'community_comments': """
CREATE TABLE community_comments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    post_id INT NOT NULL,
    user_id INT NOT NULL,
    content TEXT,
    is_developer_reply TINYINT DEFAULT 0,
    is_best_answer TINYINT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'community_likes': """
CREATE TABLE community_likes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    post_id INT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_like (user_id, post_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'game_co_devs': """
CREATE TABLE game_co_devs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    user_id INT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    invited_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    accepted_at DATETIME,
    UNIQUE KEY uk_codev (game_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'invite_codes': """
CREATE TABLE invite_codes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    code VARCHAR(32) NOT NULL UNIQUE,
    created_by INT NOT NULL,
    is_used TINYINT DEFAULT 0,
    used_by INT,
    used_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    price DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    purchased_by INT,
    purchased_at DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'wallets': """
CREATE TABLE wallets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    balance DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    total_recharged DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    total_spent DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'wallet_transactions': """
CREATE TABLE wallet_transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    tx_type VARCHAR(20) NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    balance_after DECIMAL(10,2) NOT NULL,
    related_type VARCHAR(20),
    related_id INT,
    invite_code VARCHAR(32),
    game_id INT,
    remark VARCHAR(255) DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_user (user_id),
    KEY idx_type (tx_type),
    KEY idx_game (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'game_earnings': """
CREATE TABLE game_earnings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    developer_id INT NOT NULL,
    game_id INT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    order_id INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_dev (developer_id),
    KEY idx_game (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'game_pricing': """
CREATE TABLE game_pricing (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL UNIQUE,
    price DECIMAL(10,2) DEFAULT 0.00,
    platform_share DECIMAL(5,2) DEFAULT 30.00,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'config_review_queue': """
CREATE TABLE config_review_queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    game_id INT NOT NULL,
    field_name VARCHAR(50) NOT NULL,
    old_value VARCHAR(255) NOT NULL DEFAULT '',
    new_value VARCHAR(255) NOT NULL DEFAULT '',
    status VARCHAR(20) DEFAULT 'pending',
    reviewer_id INT,
    reason VARCHAR(500) DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'tags': """
CREATE TABLE tags (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    is_verified TINYINT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'game_tags': """
CREATE TABLE game_tags (
    game_id INT NOT NULL,
    tag_id INT NOT NULL,
    PRIMARY KEY (game_id, tag_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'penalty_records': """
CREATE TABLE IF NOT EXISTS penalty_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    target_type ENUM('game','post','user') NOT NULL,
    target_id INT NOT NULL,
    target_title VARCHAR(255) DEFAULT '',
    target_user_id INT DEFAULT NULL,
    reason TEXT,
    action VARCHAR(20) DEFAULT 'ban',
    duration_days INT DEFAULT NULL,
    expires_at DATETIME DEFAULT NULL,
    is_public TINYINT DEFAULT 1,
    admin_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_target (target_type, target_id),
    INDEX idx_public (is_public)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'reports': """
CREATE TABLE IF NOT EXISTS reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    reporter_id INT NOT NULL,
    target_type ENUM('post') NOT NULL DEFAULT 'post',
    target_id INT NOT NULL,
    reason TEXT NOT NULL,
    status ENUM('pending','valid','invalid') DEFAULT 'pending',
    admin_id INT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_reporter_target (reporter_id, target_type, target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'post_tags': """
CREATE TABLE IF NOT EXISTS post_tags (
    id INT AUTO_INCREMENT PRIMARY KEY,
    post_id INT NOT NULL,
    tag_name VARCHAR(50) NOT NULL,
    INDEX idx_post (post_id),
    INDEX idx_tag (tag_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'reviewer_votes': """
CREATE TABLE IF NOT EXISTS reviewer_votes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    target_type ENUM('game','post','user') NOT NULL,
    target_id INT NOT NULL,
    reviewer_id INT NOT NULL,
    reason TEXT,
    status ENUM('voting','auto_banned','pending_admin','confirmed','rejected') DEFAULT 'voting',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confirmed_at DATETIME DEFAULT NULL,
    UNIQUE KEY uk_vote (target_type, target_id, reviewer_id),
    INDEX idx_target_status (target_type, target_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'assets': """
CREATE TABLE IF NOT EXISTS assets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    tagline VARCHAR(255) DEFAULT '',
    description TEXT,
    author_id INT NOT NULL,
    category VARCHAR(50) DEFAULT 'other',
    tags VARCHAR(500) DEFAULT '',
    cover_image VARCHAR(500) DEFAULT '',
    asset_file VARCHAR(500) DEFAULT '',
    asset_size BIGINT DEFAULT 0,
    preview_images TEXT,
    price DECIMAL(10,2) DEFAULT 0.00,
    version VARCHAR(20) DEFAULT '1.0.0',
    license_type VARCHAR(50) DEFAULT 'cc-by',
    license_detail TEXT,
    status VARCHAR(20) DEFAULT 'active',
    download_count INT DEFAULT 0,
    asset_uid VARCHAR(20) UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_author (author_id),
    INDEX idx_status (status),
    INDEX idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'asset_library': """
CREATE TABLE IF NOT EXISTS asset_library (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    asset_id INT NOT NULL,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_asset_lib (user_id, asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'announcements': """
CREATE TABLE IF NOT EXISTS announcements (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    type VARCHAR(20) DEFAULT 'info',
    content TEXT,
    is_pinned TINYINT DEFAULT 0,
    status VARCHAR(20) DEFAULT 'active',
    start_at DATETIME NULL,
    end_at DATETIME NULL,
    created_by INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_pinned (is_pinned)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'user_follows': """
CREATE TABLE IF NOT EXISTS user_follows (
    id INT AUTO_INCREMENT PRIMARY KEY,
    follower_id INT NOT NULL,
    followed_id INT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_follow (follower_id, followed_id),
    INDEX idx_followed (followed_id),
    INDEX idx_follower (follower_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'payment_orders': """
CREATE TABLE IF NOT EXISTS payment_orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    out_trade_no VARCHAR(64) NOT NULL UNIQUE,
    user_id INT NOT NULL,
    target_type VARCHAR(20) NOT NULL,
    target_id INT,
    amount DECIMAL(10,2) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    trade_no VARCHAR(64),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    paid_at DATETIME NULL,
    INDEX idx_user (user_id),
    INDEX idx_status (status),
    INDEX idx_target (target_type, target_id)
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


# query_all 作为 query 的别名，语义更清晰（与 query_one 对应）
query_all = query


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


def _column_exists(conn, table_name, column_name):
    """检查指定表的指定列是否存在。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (DB_NAME, table_name, column_name)
        )
        return cur.fetchone()['cnt'] > 0


# games 表 V2 新增列定义（列名 → DDL 片段）
_GAMES_NEW_COLUMNS = {
    'category': "ALTER TABLE games ADD COLUMN category VARCHAR(50) DEFAULT '' AFTER tags",
    'access_mode': "ALTER TABLE games ADD COLUMN access_mode VARCHAR(20) DEFAULT 'public' AFTER category",
    'source_open': "ALTER TABLE games ADD COLUMN source_open TINYINT DEFAULT 1 AFTER access_mode",
    'external_url': "ALTER TABLE games ADD COLUMN external_url VARCHAR(500) DEFAULT '' AFTER source_open",
    'price': "ALTER TABLE games ADD COLUMN price DECIMAL(10,2) DEFAULT 0.00 AFTER external_url",
    'platform_share': "ALTER TABLE games ADD COLUMN platform_share DECIMAL(5,2) DEFAULT 30.00 AFTER price",
    'game_uid': "ALTER TABLE games ADD COLUMN game_uid VARCHAR(20) UNIQUE",
    'version': "ALTER TABLE games ADD COLUMN version VARCHAR(20) DEFAULT '1.0.0'",
    'play_count': "ALTER TABLE games ADD COLUMN play_count INT DEFAULT 0",
}

# game_versions 表新增列定义（列名 → DDL 片段）
_GAME_VERSIONS_NEW_COLUMNS = {
    'zip_path': "ALTER TABLE game_versions ADD COLUMN zip_path VARCHAR(500)",
    'entry_file': "ALTER TABLE game_versions ADD COLUMN entry_file VARCHAR(255)",
}


def _migrate_schema(conn):
    """执行所有表/列的增量迁移（幂等）。"""
    # games 表新增列
    if _table_exists(conn, 'games'):
        for col_name, ddl in _GAMES_NEW_COLUMNS.items():
            if _column_exists(conn, 'games', col_name):
                logger.debug("[OK] games.%s 列已存在", col_name)
            else:
                with conn.cursor() as cur:
                    cur.execute(ddl)
                conn.commit()
                logger.warning("[MIGRATE] 已添加 games.%s 列", col_name)

        # backfill：为已存在的游戏补全 game_uid（旧数据可能为 NULL）
        if _column_exists(conn, 'games', 'game_uid'):
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM games WHERE game_uid IS NULL OR game_uid = ''")
                null_rows = cur.fetchall()
                for r in null_rows:
                    new_uid = uuid.uuid4().hex[:8].upper()
                    cur.execute("UPDATE games SET game_uid = %s WHERE id = %s", (new_uid, r['id']))
                if null_rows:
                    conn.commit()
                    logger.warning("[MIGRATE] 已为 %d 款游戏补全 game_uid", len(null_rows))

    # users 表：修正旧域名头像（account.snyqt.top → snyqt-account.iepose.cn）
    if _table_exists(conn, 'users') and _column_exists(conn, 'users', 'avatar'):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET avatar = REPLACE(avatar, 'account.snyqt.top', 'snyqt-account.iepose.cn') "
                "WHERE avatar LIKE '%account.snyqt.top%'"
            )
            affected = cur.rowcount
            if affected:
                conn.commit()
                logger.warning("[MIGRATE] 已修正 %d 条用户头像的旧域名", affected)

    # users 表：添加 status 列（用户封禁支持）
    if _table_exists(conn, 'users') and not _column_exists(conn, 'users', 'status'):
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE users ADD COLUMN status VARCHAR(20) DEFAULT 'active' AFTER points")
        conn.commit()
        logger.warning("[MIGRATE] 已添加 users.status 列")
    # permissions 表：迁移至多行权限模型（每个用户可有多条权限记录）
    if _table_exists(conn, 'permissions'):
        # 1. 删除 pending_level 列（如存在）
        if _column_exists(conn, 'permissions', 'pending_level'):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE permissions DROP COLUMN pending_level")
            conn.commit()
            logger.warning("[MIGRATE] permissions.pending_level 列已删除")

        # 2. 删除 uk_user 唯一索引（如存在），允许每个用户多条权限记录
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'permissions' AND INDEX_NAME = 'uk_user'",
                (DB_NAME,)
            )
            idx_row = cur.fetchone()
            if idx_row and idx_row['cnt'] > 0:
                cur.execute("ALTER TABLE permissions DROP INDEX uk_user")
                conn.commit()
                logger.warning("[MIGRATE] permissions.uk_user 唯一索引已删除")

        # 3. 添加 granted_by 列（如缺失）
        if not _column_exists(conn, 'permissions', 'granted_by'):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE permissions ADD COLUMN granted_by INT DEFAULT NULL")
            conn.commit()
            logger.warning("[MIGRATE] permissions.granted_by 列已添加")

        # 4. 添加 reason 列（如缺失）
        if not _column_exists(conn, 'permissions', 'reason'):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE permissions ADD COLUMN reason TEXT")
            conn.commit()
            logger.warning("[MIGRATE] permissions.reason 列已添加")

        # 5. 拆分逗号分隔的权限值为多行（须在收窄列宽之前执行）
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, permission_level, status, reason, granted_by "
                "FROM permissions WHERE permission_level LIKE '%%,%%'"
            )
            multi_rows = cur.fetchall()
            for r in multi_rows:
                levels = [p.strip() for p in str(r.get('permission_level', '')).split(',') if p.strip()]
                if not levels:
                    continue
                # 删除原行，插入拆分后的多行（每行一个权限级别）
                cur.execute("DELETE FROM permissions WHERE id = %s", (r['id'],))
                for lv in levels:
                    cur.execute(
                        "INSERT INTO permissions (user_id, permission_level, status, reason, granted_by) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (r['user_id'], lv, r.get('status') or 'approved',
                         r.get('reason'), r.get('granted_by'))
                    )
            if multi_rows:
                conn.commit()
                logger.warning("[MIGRATE] 已拆分 %d 条逗号分隔权限记录为多行", len(multi_rows))

        # 6. 将 permission_level 从 VARCHAR(100) 收窄为 VARCHAR(20)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT CHARACTER_MAXIMUM_LENGTH AS maxlen "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'permissions' AND COLUMN_NAME = 'permission_level'",
                (DB_NAME,)
            )
            col_row = cur.fetchone()
            if col_row and col_row['maxlen'] and col_row['maxlen'] > 20:
                cur.execute("ALTER TABLE permissions MODIFY COLUMN permission_level VARCHAR(20) NOT NULL")
                conn.commit()
                logger.warning("[MIGRATE] permissions.permission_level 已收窄至 VARCHAR(20)")

    # community_posts 表：添加 status 列（用于封禁状态）
    if _table_exists(conn, 'community_posts'):
        if not _column_exists(conn, 'community_posts', 'status'):
            with conn.cursor() as cur:
                cur.execute(
                    "ALTER TABLE community_posts ADD COLUMN status VARCHAR(20) DEFAULT 'active'"
                )
            conn.commit()
            logger.warning("[MIGRATE] community_posts.status 列已添加")

    # 自动创建 penalty_records 表（如缺失）
    if not _table_exists(conn, 'penalty_records'):
        with conn.cursor() as cur:
            cur.execute(EXPECTED_TABLES['penalty_records'])
        conn.commit()
        logger.warning("[MIGRATE] 已创建表 penalty_records")

    # 自动创建 reports 表（如缺失）
    if not _table_exists(conn, 'reports'):
        with conn.cursor() as cur:
            cur.execute(EXPECTED_TABLES['reports'])
        conn.commit()
        logger.warning("[MIGRATE] 已创建表 reports")

    # 自动创建 game_versions 表（如缺失）
    if not _table_exists(conn, 'game_versions'):
        with conn.cursor() as cur:
            cur.execute(EXPECTED_TABLES['game_versions'])
        conn.commit()
        logger.warning("[MIGRATE] 已创建表 game_versions")
    else:
        # game_versions 表已存在：补充新增列（zip_path、entry_file）
        for col_name, ddl in _GAME_VERSIONS_NEW_COLUMNS.items():
            if _column_exists(conn, 'game_versions', col_name):
                logger.debug("[OK] game_versions.%s 列已存在", col_name)
            else:
                with conn.cursor() as cur:
                    cur.execute(ddl)
                conn.commit()
                logger.warning("[MIGRATE] 已添加 game_versions.%s 列", col_name)

    # 自动创建 file_hashes 表（如缺失）
    if not _table_exists(conn, 'file_hashes'):
        with conn.cursor() as cur:
            cur.execute(EXPECTED_TABLES['file_hashes'])
        conn.commit()
        logger.warning("[MIGRATE] 已创建表 file_hashes")

    # 自动创建 post_tags 表（如缺失）- 社区帖子标签关联表
    if not _table_exists(conn, 'post_tags'):
        with conn.cursor() as cur:
            cur.execute(EXPECTED_TABLES['post_tags'])
        conn.commit()
        logger.warning("[MIGRATE] 已创建表 post_tags")

    # tags 表：添加 is_verified 列（管理员认证标签）
    if _table_exists(conn, 'tags') and not _column_exists(conn, 'tags', 'is_verified'):
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE tags ADD COLUMN is_verified TINYINT DEFAULT 0 AFTER name")
        conn.commit()
        logger.warning("[MIGRATE] tags.is_verified 列已添加")

    # penalty_records 表：扩展处罚类型与时长/公开字段
    if _table_exists(conn, 'penalty_records'):
        # 1. target_type ENUM 扩展为 ('game','post','user')
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'penalty_records' AND COLUMN_NAME = 'target_type'",
                (DB_NAME,)
            )
            col_row = cur.fetchone()
            if col_row and "'user'" not in str(col_row.get('COLUMN_TYPE', '')):
                cur.execute("ALTER TABLE penalty_records MODIFY COLUMN target_type ENUM('game','post','user') NOT NULL")
                conn.commit()
                logger.warning("[MIGRATE] penalty_records.target_type 已扩展为 ENUM('game','post','user')")

        # 2. 新增 target_user_id 列
        if not _column_exists(conn, 'penalty_records', 'target_user_id'):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE penalty_records ADD COLUMN target_user_id INT DEFAULT NULL AFTER target_title")
            conn.commit()
            logger.warning("[MIGRATE] penalty_records.target_user_id 列已添加")

        # 3. 新增 duration_days 列（NULL=永久封禁）
        if not _column_exists(conn, 'penalty_records', 'duration_days'):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE penalty_records ADD COLUMN duration_days INT DEFAULT NULL AFTER action")
            conn.commit()
            logger.warning("[MIGRATE] penalty_records.duration_days 列已添加")

        # 4. 新增 expires_at 列（封禁到期时间）
        if not _column_exists(conn, 'penalty_records', 'expires_at'):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE penalty_records ADD COLUMN expires_at DATETIME DEFAULT NULL AFTER duration_days")
            conn.commit()
            logger.warning("[MIGRATE] penalty_records.expires_at 列已添加")

        # 5. 新增 is_public 列（是否公开处罚记录）
        if not _column_exists(conn, 'penalty_records', 'is_public'):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE penalty_records ADD COLUMN is_public TINYINT DEFAULT 1 AFTER expires_at")
            conn.commit()
            logger.warning("[MIGRATE] penalty_records.is_public 列已添加")

    # 自动创建 reviewer_votes 表（如缺失）- 评鉴员投票记录
    if not _table_exists(conn, 'reviewer_votes'):
        with conn.cursor() as cur:
            cur.execute(EXPECTED_TABLES['reviewer_votes'])
        conn.commit()
        logger.warning("[MIGRATE] 已创建表 reviewer_votes")


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
        # 已有表结构正确，执行列级迁移（如 games 表新增字段）
        conn2 = get_db()
        try:
            _migrate_schema(conn2)
        finally:
            conn2.close()
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
        # 重建后也执行列级迁移（幂等，已有则跳过）
        _migrate_schema(conn)
    finally:
        conn.close()
    logger.info("数据库表结构重建完成")
    logger.info("=" * 60)
