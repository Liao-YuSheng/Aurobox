"""API routes for Robot Hardware & Door management."""

from flask import Blueprint, request, jsonify, current_app
from . import db
from .models import Door, DoorStatus
from .robot import FlashbotController
from .config import load_config

api_bp = Blueprint('api', __name__)

def get_controller():
    """Get FlashbotController instance."""
    return FlashbotController(load_config())

def check_and_return_home_if_empty():
    """檢查是否所有艙門都為 EMPTY，若是則命令機器人返回管理室"""
    sn = current_app.config.get('ROBOT_SN')
    
    # 尋找還有貨(不等於 EMPTY)的門
    non_empty_doors = Door.query.filter(
        Door.sn == sn, 
        Door.status != DoorStatus.EMPTY
    ).count()
    
    if non_empty_doors == 0:
        controller = get_controller()
        map_name = current_app.config.get('DEFAULT_MAP_NAME')
        # TODO: 請確保 '管理室' 是你普渡地圖中正確的點位名稱
        home_point = current_app.config.get('HOME_POINT_NAME', '管理室') 
        
        controller.custom_call2(
            sn=sn,
            map_name=map_name,
            point=home_point,
            point_type='table',
            call_device_name='dashboard'
        )
        return True
    return False

# ==========================================================
# 1. 管理員將包裹放入艙門 (Load)
# ==========================================================
@api_bp.route('/doors/<door_number>/load', methods=['POST'])
def load_package_to_door(door_number):
    """中央大腦通知：已將包裹放入指定艙門"""
    data = request.get_json()
    package_id = data.get('package_id')
    
    if not package_id:
        return jsonify({'error': 'package_id is required'}), 400
        
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(door_number=door_number, sn=sn).first()
    
    if not door:
        door = Door(sn=sn, door_number=door_number)
        db.session.add(door)
        
    door.package_id = package_id
    door.status = DoorStatus.FULL
    db.session.commit()
    
    return jsonify({'status': 'success', 'message': f'{door_number} is loaded with {package_id}'})

# ==========================================================
# 2. 住戶掃描 QR Code 成功 (Open Door & Close UI)
# ==========================================================
@api_bp.route('/packages/<package_id>/pickup-complete', methods=['POST'])
def package_pickup_complete(package_id):
    """中央大腦通知：QR 掃描成功。開門並關閉螢幕"""
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).first()
    
    if not door:
        return jsonify({'error': 'No door assigned to this package'}), 404

    controller = get_controller()
    
    try:
        # 1. 打開對應的艙門 (operation=True 為開門)
        controller.control_doors(sn=sn, door_number=door.door_number, operation=True)
        
        # 2. 關閉 QR Code 顯示畫面，讓機器人回復到預設表情狀態
        controller.custom_call(
            sn=sn, 
            shop_id=current_app.config.get('SHOP_ID'),
            call_device_name='dashboard',
            call_mode='NORMAL' 
        )
        
        return jsonify({
            'status': 'success', 
            'message': f'Door {door.door_number} opened, QR display closed.'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 3. 住戶在 LINE 點選「取貨完成」 (Close Door & Return)
# ==========================================================
@api_bp.route('/packages/<package_id>/complete', methods=['POST'])
def package_complete(package_id):
    """中央大腦通知：住戶已取貨完畢。關門並檢查是否返航"""
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).first()
    
    if not door:
        return jsonify({'error': 'No door assigned to this package'}), 404
        
    controller = get_controller()
    
    try:
        # 1. 關閉該艙門 (operation=False 為關門)
        controller.control_doors(sn=sn, door_number=door.door_number, operation=False)
        
        # 2. 釋放艙門資料庫狀態
        door.status = DoorStatus.EMPTY
        door.package_id = None
        db.session.commit()
        
        # 3. 檢查是否全部空了，如果是就回家
        is_returning = check_and_return_home_if_empty()
        
        return jsonify({
            'status': 'success', 
            'message': f'Door {door.door_number} closed and freed.',
            'returning_home': is_returning
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500