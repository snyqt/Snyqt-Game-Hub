# -*- coding: utf-8 -*-
"""
SN YQT Game Hub 易支付集成模块
- /api/payment/create:  创建支付订单，重定向到易支付收银台
- /api/payment/notify:   异步通知回调（验签 + 标记已支付 + 发放权益）
- /api/payment/return:   同步返回页（展示支付结果）
"""
import hashlib
import time
import uuid
import logging
from urllib.parse import urlencode, quote_plus

from flask import Blueprint, request, jsonify, redirect, url_for, render_template

from config.config import EPAY_PID, EPAY_KEY, EPAY_API
from app.auth import current_user
from app.permissions import require_level
from app.database import query, query_one, execute
from app.points import award_points

logger = logging.getLogger(__name__)
payments_bp = Blueprint('payments', __name__)


def _build_sign(params: dict) -> str:
    """
    生成易支付签名：
    1. 过滤 sign/sign_type/空值
    2. 按 key ASCII 升序
    3. 拼接 key=value&...
    4. 末尾追加 KEY（不加 &）
    5. MD5 小写 hex
    """
    filtered = {k: v for k, v in params.items()
                if k not in ('sign', 'sign_type') and v is not None and v != ''}
    sorted_keys = sorted(filtered.keys())
    sign_str = '&'.join(f"{k}={filtered[k]}" for k in sorted_keys)
    sign_str += EPAY_KEY
    return hashlib.md5(sign_str.encode('utf-8')).hexdigest()


def _verify_sign(params: dict) -> bool:
    """验证易支付回调签名"""
    if 'sign' not in params or 'sign_type' not in params:
        return False
    expected = _build_sign(params)
    return expected == params['sign']


def _gen_out_trade_no() -> str:
    """生成唯一订单号：SYQT + 时间戳 + 8位 UUID 短码"""
    return f"SYQT{int(time.time())}{uuid.uuid4().hex[:8]}"


@payments_bp.route('/api/payment/create', methods=['POST'])
@require_level('user')
def payment_create():
    """
    创建支付订单。
    POST 参数：
      target_type: game/asset/topup
      target_id: 游戏/素材 ID（topup 时可省略）
      amount: 金额（topup 时必填；其他类型从 DB 读取价格）
      pay_type: alipay/wxpay/qqpay（默认 alipay）
    """
    cu = current_user()
    target_type = request.form.get('target_type', '').strip()
    target_id = request.form.get('target_id', type=int)
    pay_type = request.form.get('pay_type', 'alipay').strip()

    if target_type not in ('game', 'asset', 'topup'):
        return jsonify({"success": False, "message": "无效的支付类型"}), 400

    # 充值渠道已关闭：禁止创建 topup 订单
    if target_type == 'topup':
        return jsonify({"success": False, "message": "充值渠道已关闭，无法充值"}), 403

    amount = None
    name = ""

    if target_type == 'game':
        if not target_id:
            return jsonify({"success": False, "message": "缺少 target_id"}), 400
        g = query_one(
            "SELECT id, title, price FROM games WHERE id=%s AND status='active' AND is_banned=0",
            [target_id]
        )
        if not g:
            return jsonify({"success": False, "message": "游戏不存在或未上架"}), 404
        amount = float(g['price'])
        if amount <= 0:
            return jsonify({"success": False, "message": "该游戏免费，无需购买"}), 400
        name = f"游戏：{g['title']}"
    elif target_type == 'asset':
        if not target_id:
            return jsonify({"success": False, "message": "缺少 target_id"}), 400
        a = query_one("SELECT id, title, price FROM assets WHERE id=%s AND status='active'", [target_id])
        if not a:
            return jsonify({"success": False, "message": "素材不存在或已下架"}), 404
        amount = float(a['price'])
        if amount <= 0:
            return jsonify({"success": False, "message": "该素材免费，无需购买"}), 400
        name = f"素材：{a['title']}"
    else:  # topup - SB 钱包充值（渠道已关闭，此分支不会执行）
        amount = request.form.get('amount', type=float)
        if not amount or amount < 1:
            return jsonify({"success": False, "message": "充值金额无效"}), 400
        name = f"SB 钱包充值 {amount:.2f} SB"

    out_trade_no = _gen_out_trade_no()
    execute(
        """INSERT INTO payment_orders (out_trade_no, user_id, target_type, target_id, amount, status)
           VALUES (%s, %s, %s, %s, %s, 'pending')""",
        [out_trade_no, cu['id'], target_type, target_id, amount]
    )

    # 构造易支付跳转参数
    notify_url = request.host_url.rstrip('/') + url_for('payments.payment_notify')
    return_url = request.host_url.rstrip('/') + url_for('payments.payment_return')

    params = {
        'pid': EPAY_PID,
        'type': pay_type,
        'out_trade_no': out_trade_no,
        'notify_url': notify_url,
        'return_url': return_url,
        'name': name,
        'money': f"{amount:.2f}",
        'sign_type': 'MD5',
    }
    params['sign'] = _build_sign(params)

    # 重定向到易支付（GET 方式）
    query_str = urlencode(params, quote_via=quote_plus)
    return redirect(f"{EPAY_API}submit.php?{query_str}")


@payments_bp.route('/api/payment/notify', methods=['GET', 'POST'])
def payment_notify():
    """异步通知回调"""
    params = dict(request.form) if request.method == 'POST' else dict(request.args)
    logger.info("易支付异步通知：%s", params)

    if not _verify_sign(params):
        logger.warning("易支付验签失败")
        return "fail", 200

    trade_status = params.get('trade_status', '')
    if trade_status != 'TRADE_SUCCESS':
        return "success", 200  # 非成功状态也返回 success 避免重发

    out_trade_no = params.get('out_trade_no', '')
    trade_no = params.get('trade_no', '')

    order = query_one("SELECT * FROM payment_orders WHERE out_trade_no=%s FOR UPDATE", [out_trade_no])
    if not order:
        logger.warning("订单不存在：%s", out_trade_no)
        return "fail", 200

    if order['status'] == 'paid':
        return "success", 200  # 已处理，幂等返回

    # 标记订单为已支付
    execute(
        "UPDATE payment_orders SET status='paid', trade_no=%s, paid_at=NOW() WHERE id=%s",
        [trade_no, order['id']]
    )

    # 发放权益
    try:
        _grant_rights(order)
    except Exception as e:
        logger.error("权益发放失败 order=%s: %s", out_trade_no, e, exc_info=True)
        # 不回滚订单状态，后续可手动补发
    return "success", 200


@payments_bp.route('/api/payment/return', methods=['GET'])
def payment_return():
    """同步返回页"""
    params = dict(request.args)
    out_trade_no = params.get('out_trade_no', '')
    order = query_one("SELECT * FROM payment_orders WHERE out_trade_no=%s", [out_trade_no])

    success = False
    message = "支付未完成或验签失败"

    if order and _verify_sign(params) and params.get('trade_status') == 'TRADE_SUCCESS':
        if order['status'] == 'paid':
            success = True
            message = "支付成功！"
        else:
            message = "支付正在处理中，请稍后刷新查看"
    elif order:
        message = "支付未完成或已取消"

    return render_template('payment_result.html',
                           success=success, message=message,
                           order=order, current_user=current_user())


def _grant_rights(order: dict):
    """根据订单类型发放权益"""
    target_type = order['target_type']
    target_id = order['target_id']
    user_id = order['user_id']

    if target_type == 'game':
        # 入库（INSERT IGNORE 保证幂等，避免异步通知重发时重复插入）
        execute(
            "INSERT IGNORE INTO game_library (user_id, game_id) VALUES (%s, %s)",
            [user_id, target_id]
        )
        logger.info("游戏入库：user=%s game=%s", user_id, target_id)

    elif target_type == 'asset':
        # 入库（同上，幂等）
        execute(
            "INSERT IGNORE INTO asset_library (user_id, asset_id) VALUES (%s, %s)",
            [user_id, target_id]
        )
        logger.info("素材入库：user=%s asset=%s", user_id, target_id)

    elif target_type == 'topup':
        # SB 钱包充值（充值渠道已关闭，仅处理历史订单回调）
        amount = float(order['amount'])
        from app.wallet import get_wallet, _record_tx
        w = get_wallet(user_id)
        new_balance = round(float(w['balance']) + amount, 2)
        execute(
            'UPDATE wallets SET balance = %s, total_recharged = total_recharged + %s WHERE user_id = %s',
            [new_balance, amount, user_id]
        )
        _record_tx(
            user_id=user_id,
            tx_type='recharge',
            amount=amount,
            balance_after=new_balance,
            related_type='topup',
            related_id=order['id'],
            remark=f"充值 {amount:.2f} SB"
        )
        logger.info("SB 充值：user=%s amount=%s SB", user_id, amount)
