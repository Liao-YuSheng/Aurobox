"""LINE webhook handling."""

from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
import hmac
import hashlib
import base64
from . import db
from .models import Package, DeliveryHistory, PackageStatus
import json
import logging

webhook_bp = Blueprint('webhooks', __name__)
logger = logging.getLogger(__name__)


def verify_line_signature(body, signature):
    """Verify LINE webhook signature."""
    channel_secret = current_app.config.get('LINE_CHANNEL_SECRET')
    if not channel_secret:
        return False
    
    hash_object = hmac.new(
        channel_secret.encode('utf-8'),
        body,
        hashlib.sha256
    )
    expected_signature = base64.b64encode(hash_object.digest()).decode('utf-8')
    return signature == expected_signature


@webhook_bp.route('/line', methods=['POST'])
def line_webhook():
    """
    Handle LINE webhook events.
    """
    # 驗證簽章
    signature = request.headers.get('X-Line-Signature')
    if not verify_line_signature(request.get_data(), signature):
        return jsonify({'error': 'Invalid signature'}), 403
    
    body = request.get_json()
    
    try:
        for event in body.get('events', []):
            event_type = event.get('type')
            
            if event_type == 'postback':
                handle_postback_event(event)
            elif event_type == 'message':
                handle_message_event(event)
        
        return jsonify({'status': 'success'}), 200
    
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({'error': str(e)}), 500


def handle_postback_event(event):
    """
    Handle postback events from LINE.
    用戶在 LINE 介面操作（選擇取貨、稍後再取等）。
    """
    user_id = event.get('source', {}).get('userId')
    postback_data = event.get('postback', {}).get('data', '')
    
    # 解析 postback data (format: action=value&package_id=xxx)
    params = {}
    for param in postback_data.split('&'):
        if '=' in param:
            key, value = param.split('=', 1)
            params[key] = value
    
    action = params.get('action')
    package_id = params.get('package_id')
    presented_token = params.get('pickup_qr_token')
    pickup_token_verified = None
    
    if not package_id or not action:
        return
    
    package = Package.query.get(package_id)
    if not package:
        return
    
    if action == 'pickup_now':
        package.status = PackageStatus.PICKUP_NOW
        logger.info(f"Package {package_id}: User selected pickup_now")
    
    elif action == 'later':
        package.status = PackageStatus.LATER
        logger.info(f"Package {package_id}: User selected later")
    
    elif action == 'cancel':
        package.status = PackageStatus.RETURNED_CANCELLED
        logger.info(f"Package {package_id}: User cancelled")

    elif action == 'pickup_complete':
        if package.pickup_qr_token and presented_token != package.pickup_qr_token:
            logger.warning(f"Package {package_id}: pickup_complete rejected due to invalid QR token")
            return

        package.status = PackageStatus.COMPLETED
        package.completed_at = datetime.utcnow()
        package.pickup_qr_token = None
        pickup_token_verified = True
        logger.info(f"Package {package_id}: User completed pickup")
    
    package.updated_at = datetime.utcnow()
    
    history = DeliveryHistory(
        package_id=package_id,
        action=f'line_postback_{action}',
        details={
            'user_id': user_id,
            'pickup_qr_token_verified': pickup_token_verified,
        }
    )
    
    db.session.add(history)
    db.session.commit()


def handle_message_event(event):
    """
    Handle message events.
    """
    user_id = event.get('source', {}).get('userId')
    message = event.get('message', {})
    message_type = message.get('type')
    
    if message_type == 'text':
        text = message.get('text', '')
        logger.info(f"Message from {user_id}: {text}")


def send_push_message(line_user_id, messages):
    """
    Send push message to LINE user.
    """
    access_token = current_app.config.get('LINE_CHANNEL_ACCESS_TOKEN')
    if not access_token:
        logger.error("LINE_CHANNEL_ACCESS_TOKEN not configured")
        return False
    
    import requests
    
    url = 'https://api.line.biz/v2/bot/message/push'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    
    payload = {
        'to': line_user_id,
        'messages': messages
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send push message: {str(e)}")
        return False


def send_delivery_notification(package):
    """Send LINE notification when package arrives."""
    if not package.line_user_id:
        return False
    
    # Flex Message 格式
    flex_message = {
        'type': 'flex',
        'altText': f'包裹已送達 {package.address}',
        'contents': {
            'type': 'bubble',
            'body': {
                'type': 'box',
                'layout': 'vertical',
                'contents': [
                    {
                        'type': 'text',
                        'text': '包裹已送達',
                        'weight': 'bold',
                        'size': 'lg'
                    },
                    {
                        'type': 'text',
                        'text': f'地址: {package.address}',
                        'size': 'sm',
                        'color': '#666666',
                        'margin': 'md'
                    },
                    {
                        'type': 'text',
                        'text': '請於 10 分鐘內至機器人處取貨',
                        'size': 'sm',
                        'color': '#FF6B6B',
                        'margin': 'md'
                    }
                ]
            },
            'footer': {
                'type': 'box',
                'layout': 'vertical',
                'spacing': 'sm',
                'contents': [
                    {
                        'type': 'button',
                        'style': 'link',
                        'height': 'sm',
                        'action': {
                            'type': 'postback',
                            'label': '已取貨',
                            'data': f'action=pickup_complete&package_id={package.id}'
                        }
                    },
                    {
                        'type': 'button',
                        'style': 'link',
                        'height': 'sm',
                        'action': {
                            'type': 'postback',
                            'label': '暫時無法取貨',
                            'data': f'action=cancel&package_id={package.id}'
                        }
                    }
                ]
            }
        }
    }
    
    return send_push_message(package.line_user_id, [flex_message])


def send_departed_notification(package):
    """Send notification when robot departs."""
    if not package.line_user_id:
        return False
    
    message = {
        'type': 'text',
        'text': f'機器人已開始配送您的包裹，將很快抵達 {package.address}'
    }
    
    return send_push_message(package.line_user_id, [message])


def send_returned_notification(package):
    """Send notification when package is returned."""
    if not package.line_user_id:
        return False
    
    if package.status == PackageStatus.RETURNED_TIMEOUT:
        text = f'您的包裹因為超過取件時間已退回，請洽管理室'
    else:
        text = f'您的包裹已退回管理室，請洽管理室'
    
    message = {
        'type': 'text',
        'text': text
    }
    
    return send_push_message(package.line_user_id, [message])
