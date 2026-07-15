# -*- coding: utf-8 -*-
"""评分/排行榜/积分/游戏库模块（蓝图 points_bp）。

依赖契约：
- from app.database import query, query_one, execute
- from app.auth import current_user
- from app.helpers import pack_game
"""
from flask import (
    Blueprint, render_template, request, jsonify, send_file, abort
)

from app.database import query, query_one, execute
from app.auth import current_user
from app.helpers import pack_game

points_bp = Blueprint('points', __name__)


def award_points(user_id, points, action, description):
    """发放积分：写 points_log 并累加 users.points。

    :param user_id: 用户 ID
    :param points: 积分变动值（可为负）
    :param action: 行为标识（如 review/purchase/downloaded）
    :param description: 描述文本
    """
    execute(
        'INSERT INTO points_log (user_id, points, action, description) '
        'VALUES (%s, %s, %s, %s)',
        (user_id, points, action, description)
    )
    execute(
        'UPDATE users SET points = points + %s WHERE id = %s',
        (points, user_id)
    )


@points_bp.route('/api/games/<int:gid>/review', methods=['POST'])
def review_game(gid):
    """提交评分与评论（需登录且已入库）。

    重算 games.avg_rating 与 rating_count；评分者 award_points +2。
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    # 校验游戏存在
    game = query_one('SELECT id, is_banned, developer_id FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404

    # 校验已入库
    lib = query_one(
        'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
        [user['id'], gid]
    )
    if not lib:
        return jsonify({'success': False, 'message': '请先获取游戏后再评分'}), 403

    # 解析评分
    rating = request.form.get('rating', type=int)
    if request.is_json:
        rating = (request.get_json() or {}).get('rating', rating)
    comment = request.form.get('comment', '').strip()
    if request.is_json:
        comment = (request.get_json() or {}).get('comment', comment)

    if not rating or rating < 1 or rating > 5:
        return jsonify({'success': False, 'message': '评分需在 1-5 之间'}), 400

    # 同一用户对同一游戏仅保留一条评分（覆盖更新）
    existing = query_one(
        'SELECT id FROM reviews WHERE game_id = %s AND user_id = %s',
        [gid, user['id']]
    )
    if existing:
        execute(
            'UPDATE reviews SET rating = %s, comment = %s WHERE id = %s',
            (rating, comment, existing['id'])
        )
    else:
        execute(
            'INSERT INTO reviews (game_id, user_id, rating, comment) '
            'VALUES (%s, %s, %s, %s)',
            (gid, user['id'], rating, comment)
        )

    # 重算平均分与评分数
    stats = query_one(
        'SELECT AVG(rating) AS avg_rating, COUNT(*) AS cnt '
        'FROM reviews WHERE game_id = %s',
        [gid]
    )
    avg_rating = float(stats['avg_rating']) if stats['avg_rating'] else 0.0
    rating_count = stats['cnt']
    execute(
        'UPDATE games SET avg_rating = %s, rating_count = %s WHERE id = %s',
        (round(avg_rating, 2), rating_count, gid)
    )

    # 评分者积分 +2
    award_points(user['id'], 2, 'review', f'评分游戏 #{gid}')

    return jsonify({
        'success': True,
        'avg_rating': round(avg_rating, 2),
        'rating_count': rating_count,
        'message': '评分成功'
    })


@points_bp.route('/api/games/<int:gid>/rate', methods=['POST'])
def rate_game(gid):
    """快速评分接口（仅记录评分，无需评论）。

    复用 review 逻辑：同用户同游戏覆盖更新，重算均分。
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    game = query_one('SELECT id, is_banned FROM games WHERE id = %s', [gid])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404

    # 校验已入库
    lib = query_one(
        'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
        [user['id'], gid]
    )
    if not lib:
        return jsonify({'success': False, 'message': '请先获取游戏后再评分'}), 403

    # 解析评分（支持 JSON 与 form）
    rating = request.form.get('rating', type=int)
    if request.is_json:
        rating = (request.get_json(silent=True) or {}).get('rating', rating)
    if not rating or rating < 1 or rating > 5:
        return jsonify({'success': False, 'message': '评分需在 1-5 之间'}), 400

    # 同一用户对同一游戏仅保留一条评分（覆盖更新，保留原评论）
    existing = query_one(
        'SELECT id, comment FROM reviews WHERE game_id = %s AND user_id = %s',
        [gid, user['id']]
    )
    if existing:
        execute(
            'UPDATE reviews SET rating = %s WHERE id = %s',
            (rating, existing['id'])
        )
    else:
        execute(
            'INSERT INTO reviews (game_id, user_id, rating) VALUES (%s, %s, %s)',
            (gid, user['id'], rating)
        )

    # 重算平均分与评分数
    stats = query_one(
        'SELECT AVG(rating) AS avg_rating, COUNT(*) AS cnt '
        'FROM reviews WHERE game_id = %s',
        [gid]
    )
    avg_rating = float(stats['avg_rating']) if stats['avg_rating'] else 0.0
    rating_count = stats['cnt']
    execute(
        'UPDATE games SET avg_rating = %s, rating_count = %s WHERE id = %s',
        (round(avg_rating, 2), rating_count, gid)
    )

    return jsonify({
        'success': True,
        'avg_rating': round(avg_rating, 2),
        'rating_count': rating_count,
        'message': '评分成功'
    })


@points_bp.route('/api/games/<int:gid>/purchase', methods=['POST'])
def purchase_game(gid):
    """购买/获取游戏（需登录）。

    直接加入 game_library（未实装支付），award_points +5。
    download_count 不变。
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    game = query_one(
        'SELECT id, is_banned, developer_id, access_mode FROM games WHERE id = %s',
        [gid]
    )
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404
    if game['is_banned'] and game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404

    # 访问控制：
    # - private 模式：仅开发者可获取
    # - invite 模式：必须先通过邀请码兑换（/api/invite/redeem）方可获取
    if game.get('access_mode') == 'private' and game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '此游戏为私密模式，仅开发者可获取'}), 403
    if game.get('access_mode') == 'invite':
        return jsonify({
            'success': False,
            'message': '此游戏需要邀请码才能获取，请在游戏详情页输入邀请码兑换'
        }), 403

    # 已入库则直接返回
    existing = query_one(
        'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
        [user['id'], gid]
    )
    if existing:
        return jsonify({'success': True, 'message': '游戏已在库中', 'already': True})

    execute(
        'INSERT INTO game_library (user_id, game_id) VALUES (%s, %s)',
        (user['id'], gid)
    )
    # 购买积分 +5
    award_points(user['id'], 5, 'purchase', f'获取游戏 #{gid}')

    return jsonify({'success': True, 'message': '已加入游戏库'})


@points_bp.route('/api/games/<int:gid>/download')
def download_game(gid):
    """下载游戏（需登录且已入库）。

    打包项目文件供下载，download_count +1；开发者 award_points +1。
    """
    user = current_user()
    if not user:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    # 校验已入库
    lib = query_one(
        'SELECT id FROM game_library WHERE user_id = %s AND game_id = %s',
        [user['id'], gid]
    )
    if not lib:
        return jsonify({'success': False, 'message': '请先获取游戏后再下载'}), 403

    game = query_one('SELECT * FROM games WHERE id = %s', [gid])
    if not game:
        abort(404)
    # 封禁游戏不允许下载
    if game.get('is_banned'):
        return jsonify({'success': False, 'message': '游戏已封禁，无法下载'}), 403

    # 不开放源代码的游戏不允许下载
    if not game.get('source_open', 1):
        return jsonify({'success': False, 'message': '该游戏不开放源代码下载'}), 403

    # 打包并下载
    buffer = pack_game(gid)

    # download_count +1
    execute(
        'UPDATE games SET download_count = download_count + 1 WHERE id = %s',
        [gid]
    )

    # 开发者获得积分 +1
    if game.get('developer_id'):
        award_points(
            game['developer_id'], 1, 'downloaded',
            f'游戏 #{gid} 被下载'
        )

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'{game.get("title", "game")}_{gid}.zip',
        mimetype='application/zip'
    )


@points_bp.route('/library')
def library():
    """个人游戏库页面（需登录）。"""
    user = current_user()
    if not user:
        return render_template('view_denied.html', message='请先登录'), 403

    games = query(
        'SELECT g.*, gl.added_at '
        'FROM games g JOIN game_library gl ON g.id = gl.game_id '
        'WHERE gl.user_id = %s ORDER BY gl.added_at DESC',
        [user['id']]
    )
    return render_template('library.html', games=games)


@points_bp.route('/leaderboard')
def leaderboard():
    """排行榜：游戏排行（按 avg_rating / download_count）+ 用户积分排行。"""
    # 游戏排行
    games = query(
        'SELECT * FROM games WHERE is_banned = 0 '
        'ORDER BY avg_rating DESC, download_count DESC LIMIT 50'
    )
    # 用户积分排行
    users = query(
        'SELECT id, snyqt_user_id, username, avatar, points '
        'FROM users ORDER BY points DESC LIMIT 50'
    )
    return render_template('leaderboard.html', games=games, users=users)
