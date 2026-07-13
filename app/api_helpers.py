# -*- coding: utf-8 -*-
"""API 响应格式化工具，统一 JSON 格式 {success, data, message}。"""

from flask import jsonify, g


def api_response(success=True, data=None, message='', status_code=200):
    """统一 API 响应格式。

    :param success: 是否成功
    :param data: 响应数据（dict/list/None）
    :param message: 提示消息
    :param status_code: HTTP 状态码
    :return: Flask Response
    """
    resp = {'success': success, 'message': message}
    if data is not None:
        resp['data'] = data
    if hasattr(g, 'client_type') and g.client_type == 'desktop':
        resp['client'] = 'desktop'
    return jsonify(resp), status_code