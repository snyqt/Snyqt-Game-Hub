# -*- coding: utf-8 -*-
"""SB 钱包与交易模块（蓝图 wallet_bp）。

业务规则：
- SB 为平台虚拟币，仅通过注册赠送、收益及转让等方式流通，不开放充值
- 新用户注册赠送 100 SB
- 游戏价格 > 0 时，用户支付 SB 购买邀请码；开发者获得 99% SB 收益
- 游戏价格 = 0 时，仅开发者可在控制面板生成邀请码
- 平台抽成固定 1%，不可修改
- 邀请码购买后仅发放到交易记录，用户需手动到游戏库激活
- 用户可将 SB 转让给其他用户
"""
import logging

from flask import Blueprint, request, jsonify, render_template, abort

from app.database import query, query_one, execute
from app.permissions import require_level
from app.auth import current_user

wallet_bp = Blueprint('wallet', __name__)
logger = logging.getLogger(__name__)

# 平台抽成 1%，开发者得 99%
PLATFORM_FEE_RATE = 0.01
DEV_EARN_RATE = 0.99


def get_wallet(user_id):
    """获取用户钱包（不存在则自动创建）。"""
    w = query_one('SELECT * FROM wallets WHERE user_id = %s', [user_id])
    if not w:
        execute('INSERT INTO wallets (user_id) VALUES (%s)', [user_id])
        w = query_one('SELECT * FROM wallets WHERE user_id = %s', [user_id])
    return w


def _record_tx(user_id, tx_type, amount, balance_after,
               related_type=None, related_id=None,
               invite_code=None, game_id=None, remark=''):
    """记录一笔钱包交易流水。"""
    execute(
        'INSERT INTO wallet_transactions '
        '(user_id, tx_type, amount, balance_after, related_type, related_id, '
        ' invite_code, game_id, remark) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
        [user_id, tx_type, amount, balance_after,
         related_type, related_id, invite_code, game_id, remark]
    )


# ==================== 钱包页面 ====================

@wallet_bp.route('/wallet')
@require_level('user')
def wallet_page():
    """我的钱包页面（SB 余额 + 交易记录）。"""
    user = current_user()
    wallet = get_wallet(user['id'])
    transactions = query(
        'SELECT * FROM wallet_transactions WHERE user_id = %s ORDER BY created_at DESC LIMIT 100',
        [user['id']]
    )
    return render_template('wallet.html', wallet=wallet, transactions=transactions)


@wallet_bp.route('/earnings')
@require_level('developer')
def earnings_page():
    """开发者收益管理面板。"""
    user = current_user()
    # 总收益
    total = query_one(
        'SELECT COALESCE(SUM(amount), 0) AS total FROM game_earnings WHERE developer_id = %s',
        [user['id']]
    )
    # 按游戏分组的收益
    by_game = query(
        '''SELECT g.id, g.title, g.game_uid, g.cover_image,
                  COALESCE(SUM(e.amount), 0) AS earnings,
                  COUNT(e.id) AS sales_count
           FROM games g
           LEFT JOIN game_earnings e ON g.id = e.game_id
           WHERE g.developer_id = %s
           GROUP BY g.id, g.title, g.game_uid, g.cover_image
           ORDER BY earnings DESC, g.created_at DESC''',
        [user['id']]
    )
    # 最近 30 天收益明细
    recent = query(
        '''SELECT e.*, g.title AS game_title, g.game_uid
           FROM game_earnings e
           JOIN games g ON e.game_id = g.id
           WHERE e.developer_id = %s
           ORDER BY e.created_at DESC LIMIT 50''',
        [user['id']]
    )
    return render_template(
        'earnings.html',
        total_earnings=total['total'] if total else 0,
        by_game=by_game,
        recent=recent,
    )


# ==================== 钱包查询 API ====================

@wallet_bp.route('/api/wallet')
@require_level('user')
def api_wallet():
    """获取当前用户钱包信息。"""
    user = current_user()
    w = get_wallet(user['id'])
    return jsonify({
        'success': True,
        'balance': float(w['balance']),
        'total_recharged': float(w['total_recharged']),
        'total_spent': float(w['total_spent']),
    })


@wallet_bp.route('/api/wallet/transactions')
@require_level('user')
def api_wallet_transactions():
    """获取当前用户交易记录。"""
    user = current_user()
    txs = query(
        'SELECT * FROM wallet_transactions WHERE user_id = %s ORDER BY created_at DESC LIMIT 100',
        [user['id']]
    )
    # 序列化 decimal/datetime
    for t in txs:
        t['amount'] = float(t['amount'])
        t['balance_after'] = float(t['balance_after'])
        if t.get('created_at'):
            t['created_at'] = t['created_at'].strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({'success': True, 'transactions': txs})


# ==================== 购买邀请码 API ====================

@wallet_bp.route('/api/wallet/buy-invite', methods=['POST'])
@require_level('user')
def buy_invite_code():
    """用 SB 余额购买游戏邀请码。

    POST JSON: { game_id: int }
    流程：
    1. 校验游戏存在、价格 > 0
    2. 校验用户余额充足
    3. 扣减 SB、生成专属邀请码、记录交易流水
    4. 记录开发者收益（99%）
    5. 返回邀请码（用户需手动到游戏库激活）
    """
    user = current_user()
    data = request.get_json(silent=True) or {}
    game_id = data.get('game_id')
    if not game_id:
        return jsonify({'success': False, 'message': '缺少游戏 ID'}), 400

    game = query_one(
        "SELECT id, title, price, developer_id FROM games WHERE id = %s "
        "AND is_banned = 0 AND status = 'active'",
        [game_id]
    )
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在或已下架'}), 404

    price = float(game['price'])
    if price <= 0:
        return jsonify({'success': False, 'message': '该游戏免费，无需购买邀请码'}), 400

    wallet = get_wallet(user['id'])
    balance = float(wallet['balance'])
    if balance < price:
        return jsonify({
            'success': False,
            'message': f'SB 余额不足（当前 {balance:.2f} SB，需 {price:.2f} SB）',
            'need_recharge': True,
            'shortfall': round(price - balance, 2)
        }), 400

    # 扣减余额
    new_balance = round(balance - price, 2)
    execute(
        'UPDATE wallets SET balance = %s, total_spent = total_spent + %s WHERE user_id = %s',
        [new_balance, price, user['id']]
    )

    # 生成专属邀请码（8 位大写）
    import uuid as _uuid
    code = _uuid.uuid4().hex[:8].upper()
    dev_earn = round(price * DEV_EARN_RATE, 2)

    # 写入邀请码（已标记 purchased_by，但 is_used=0 待用户手动激活）
    execute(
        'INSERT INTO invite_codes (game_id, code, created_by, price, purchased_by, purchased_at) '
        'VALUES (%s, %s, %s, %s, %s, NOW())',
        [game_id, code, game['developer_id'], price, user['id']]
    )

    # 记录交易流水（负数表示支出）
    _record_tx(
        user_id=user['id'],
        tx_type='spend',
        amount=-price,
        balance_after=new_balance,
        related_type='game',
        related_id=game_id,
        invite_code=code,
        game_id=game_id,
        remark=f"购买「{game.get('title', '')}」邀请码"
    )

    # 记录开发者收益
    execute(
        'INSERT INTO game_earnings (developer_id, game_id, amount) VALUES (%s, %s, %s)',
        [game['developer_id'], game_id, dev_earn]
    )

    logger.info(
        "邀请码购买: user=%s game=%s price=%s code=%s dev_earn=%s",
        user['id'], game_id, price, code, dev_earn
    )

    return jsonify({
        'success': True,
        'message': f'购买成功！邀请码：{code}（请到游戏库激活）',
        'invite_code': code,
        'game_id': game_id,
        'balance': new_balance
    })


# ==================== SB 转让 API ====================

@wallet_bp.route('/api/wallet/transfer', methods=['POST'])
@require_level('user')
def transfer_sb():
    """将 SB 转让给其他用户。

    POST JSON: { username: str, amount: float }
    流程：
    1. 校验接收方用户存在且非自己
    2. 校验转让金额 > 0 且不超过自身余额
    3. 扣减自身余额、增加接收方余额
    4. 双方各记录一笔交易流水（transfer_out / transfer_in）
    """
    user = current_user()
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    amount = float(data.get('amount', 0) or 0)

    if not username:
        return jsonify({'success': False, 'message': '请输入接收方用户名'}), 400
    if amount <= 0:
        return jsonify({'success': False, 'message': '转让数量必须大于 0'}), 400

    # 查找接收方
    recipient = query_one('SELECT id, username FROM users WHERE username = %s', [username])
    if not recipient:
        return jsonify({'success': False, 'message': f'用户「{username}」不存在'}), 404
    if recipient['id'] == user['id']:
        return jsonify({'success': False, 'message': '不能向自己转让'}), 400

    # 校验余额
    sender_wallet = get_wallet(user['id'])
    balance = float(sender_wallet['balance'])
    if balance < amount:
        return jsonify({
            'success': False,
            'message': f'SB 余额不足（当前 {balance:.2f} SB，需 {amount:.2f} SB）'
        }), 400

    # 扣减发送方余额
    sender_new_balance = round(balance - amount, 2)
    execute(
        'UPDATE wallets SET balance = %s, total_spent = total_spent + %s WHERE user_id = %s',
        [sender_new_balance, amount, user['id']]
    )

    # 增加接收方余额
    recipient_wallet = get_wallet(recipient['id'])
    recipient_new_balance = round(float(recipient_wallet['balance']) + amount, 2)
    execute(
        'UPDATE wallets SET balance = %s, total_recharged = total_recharged + %s WHERE user_id = %s',
        [recipient_new_balance, amount, recipient['id']]
    )

    # 记录发送方转出流水（负数）
    _record_tx(
        user_id=user['id'],
        tx_type='transfer_out',
        amount=-amount,
        balance_after=sender_new_balance,
        related_type='user',
        related_id=recipient['id'],
        remark=f"转让给「{recipient.get('username', '')}」"
    )

    # 记录接收方转入流水（正数）
    sender_name = user.get('username', '用户')
    _record_tx(
        user_id=recipient['id'],
        tx_type='transfer_in',
        amount=amount,
        balance_after=recipient_new_balance,
        related_type='user',
        related_id=user['id'],
        remark=f"收到「{sender_name}」转让"
    )

    logger.info(
        "SB 转让: from=%s to=%s(%s) amount=%s",
        user['id'], recipient['id'], recipient['username'], amount
    )

    recipient_name = recipient['username']
    return jsonify({
        'success': True,
        'message': f'已成功向「{recipient_name}」转让 {amount:.2f} SB',
        'balance': sender_new_balance
    })


# ==================== 开发者生成邀请码（价格=0 游戏） ====================

@wallet_bp.route('/api/dev/gen-invite', methods=['POST'])
@require_level('developer')
def dev_gen_invite():
    """开发者为价格=0 的游戏生成邀请码。

    POST JSON: { game_id: int, count: int (可选，默认 1，最多 50) }
    """
    user = current_user()
    data = request.get_json(silent=True) or {}
    game_id = data.get('game_id')
    count = min(int(data.get('count', 1) or 1), 50)

    if not game_id:
        return jsonify({'success': False, 'message': '缺少游戏 ID'}), 400

    game = query_one(
        'SELECT id, title, price, developer_id FROM games WHERE id = %s',
        [game_id]
    )
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404
    if game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '无权操作他人游戏'}), 403
    if float(game['price']) > 0:
        return jsonify({'success': False, 'message': '付费游戏由系统自动发放邀请码'}), 400

    import uuid as _uuid
    codes = []
    for _ in range(count):
        code = _uuid.uuid4().hex[:8].upper()
        execute(
            'INSERT INTO invite_codes (game_id, code, created_by, price) VALUES (%s, %s, %s, 0)',
            [game_id, code, user['id']]
        )
        codes.append(code)

    logger.info("开发者生成邀请码: dev=%s game=%s count=%s", user['id'], game_id, len(codes))
    return jsonify({
        'success': True,
        'message': f'已生成 {len(codes)} 个邀请码',
        'codes': codes
    })


@wallet_bp.route('/api/dev/invites/<int:game_id>')
@require_level('developer')
def dev_list_invites(game_id):
    """开发者查看某游戏的所有邀请码。"""
    user = current_user()
    game = query_one('SELECT id, developer_id FROM games WHERE id = %s', [game_id])
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404
    if game['developer_id'] != user['id']:
        return jsonify({'success': False, 'message': '无权操作他人游戏'}), 403

    invites = query(
        '''SELECT id, code, price, is_used, purchased_by, used_by,
                  created_at, purchased_at, used_at
           FROM invite_codes WHERE game_id = %s ORDER BY created_at DESC''',
        [game_id]
    )
    for inv in invites:
        inv['price'] = float(inv['price'])
        for k in ('created_at', 'purchased_at', 'used_at'):
            if inv.get(k):
                inv[k] = inv[k].strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({'success': True, 'invites': invites})


# ==================== 收益查询 API ====================

@wallet_bp.route('/api/dev/earnings')
@require_level('developer')
def api_dev_earnings():
    """开发者收益统计 API。"""
    user = current_user()
    total = query_one(
        'SELECT COALESCE(SUM(amount), 0) AS total FROM game_earnings WHERE developer_id = %s',
        [user['id']]
    )
    by_game = query(
        '''SELECT g.id, g.title, g.game_uid,
                  COALESCE(SUM(e.amount), 0) AS earnings,
                  COUNT(e.id) AS sales_count
           FROM games g
           LEFT JOIN game_earnings e ON g.id = e.game_id
           WHERE g.developer_id = %s
           GROUP BY g.id, g.title, g.game_uid
           ORDER BY earnings DESC''',
        [user['id']]
    )
    for g in by_game:
        g['earnings'] = float(g['earnings'])
    return jsonify({
        'success': True,
        'total_earnings': float(total['total']) if total else 0,
        'by_game': by_game
    })
