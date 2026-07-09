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
        home_point = current_app.config.get('HOME_POINT_NAME', '喵喵待機') 
        
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
# 0. 分派空艙門並為管理員開門 (Assign & Open)
# ==========================================================
@api_bp.route('/doors/assign', methods=['POST'])
def assign_door_for_package():
    """
    中央大腦通知：準備裝載包裹。
    【本機動作】：尋找空艙門 -> 將機器人叫回管理室(若不在) -> 打開該艙門。
    """
    data = request.get_json()
    package_id = data.get('package_id')
    
    if not package_id:
        return jsonify({'error': 'package_id is required'}), 400
        
    sn = current_app.config.get('ROBOT_SN')
    controller = get_controller()
    
    # 1. 尋找一個狀態為 EMPTY 的艙門
    empty_door = Door.query.filter_by(sn=sn, status=DoorStatus.EMPTY).first()
    
    if not empty_door:
        return jsonify({'error': 'No empty doors available'}), 400
        
    try:
        # 2. 呼叫機器人前往管理室 (可選：如果確定它已經在管理室可省略，或由另一個 dispatch API 處理)
        map_name = current_app.config.get('DEFAULT_MAP_NAME')
        home_point = current_app.config.get('HOME_POINT_NAME', '管理室') 
        controller.custom_call2(
            sn=sn, map_name=map_name, point=home_point, 
            point_type='table', call_device_name='dashboard'
        )

        # 3. 呼叫普渡 API：打開分配到的艙門
        controller.control_doors(sn=sn, door_number=empty_door.door_number, operation=True)
        
        # 4. 更新資料庫狀態為 ASSIGNED
        # 注意：需確保你的 models.py 裡面的 DoorStatus 已經有 ASSIGNED 這個列舉
        empty_door.package_id = package_id
        empty_door.status = DoorStatus.ASSIGNED
        db.session.commit()
        
        return jsonify({
            'status': 'success', 
            'message': f'Door {empty_door.door_number} assigned and opened for {package_id}',
            'door_number': empty_door.door_number
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 1. 管理員將包裹放入艙門並確認 (Load)
# ==========================================================
@api_bp.route('/doors/<door_number>/load', methods=['POST'])
def load_package_to_door(door_number):
    """
    中央大腦通知：管理員已將包裹放入指定艙門。
    【本機動作】：關閉艙門 -> 狀態改為 FULL。
    """
    data = request.get_json()
    package_id = data.get('package_id') # 再次核對用，也可省略只用 door_number
    
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(door_number=door_number, sn=sn).first()
    
    # 確保該門有被指派
    if not door or door.status != DoorStatus.ASSIGNED:
        return jsonify({'error': 'Door is not in ASSIGNED state'}), 400

    controller = get_controller()
    
    try:
        # 1. 呼叫普渡 API：關門
        controller.control_doors(sn=sn, door_number=door_number, operation=False)
        
        # 2. 更新資料庫狀態為 FULL
        door.status = DoorStatus.FULL
        # 如果前面 assign 沒寫入 package_id，也可以在這裡寫入
        if package_id:
            door.package_id = package_id 
            
        db.session.commit()
        
        return jsonify({
            'status': 'success', 
            'message': f'Door {door_number} closed and marked as FULL with {door.package_id}'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
# ==========================================================
# 2. 指揮機器人移動 (Dispatch)
# ==========================================================
@api_bp.route('/robot/dispatch', methods=['POST'])
def robot_dispatch():
    """中央大腦下令：機器人出發前往指定點位 (例如住戶家門口)"""
    data = request.get_json()
    target_point = data.get('point')  # 例如傳入 "5F-1"
    
    if not target_point:
        return jsonify({'error': 'point is required'}), 400
        
    controller = get_controller()
    sn = current_app.config.get('ROBOT_SN')
    map_name = current_app.config.get('DEFAULT_MAP_NAME')
    
    try:
        # 呼叫普渡 API 讓機器人導航到該住址
        controller.custom_call2(
            sn=sn,
            map_name=map_name,
            point=target_point,
            point_type='table',
            call_device_name='dashboard'
        )
        return jsonify({
            'status': 'success', 
            'message': f'Robot is moving to {target_point}'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
# ==========================================================
# 2.5 抵達並顯示 QR Code (Arrived & Show QR)
# ==========================================================
@api_bp.route('/packages/<package_id>/show-qr', methods=['POST'])
def show_qr(package_id):
    """中央大腦通知：機器人已抵達，請在螢幕上顯示 QR Code"""
    data = request.get_json()
    qr_content = data.get('qr_content') # 中央大腦傳來的 token 或 包含 package_id 的 LIFF 網址
    
    if not qr_content:
        return jsonify({'error': 'qr_content is required'}), 400
        
    sn = current_app.config.get('ROBOT_SN')
    controller = get_controller()
    
    try:
        # 呼叫普渡 API 顯示 QR Code 畫面
        controller.custom_call(
            sn=sn,
            shop_id=current_app.config.get('SHOP_ID'),
            call_device_name='dashboard',
            call_mode='QR_CODE',
            mode_data={
                'qrcode': qr_content,
                'text': '請掃描取件',
            },
            priority=1
        )
        return jsonify({'status': 'success', 'message': 'QR code is now displayed on robot.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
# ==========================================================
# 3. 取消或逾時 (Cancel / Timeout)
# ==========================================================
@api_bp.route('/packages/<package_id>/cancel', methods=['POST'])
def package_cancel(package_id):
    """
    中央大腦通知：包裹已被取消或逾時。
    【本機動作】：強制中斷當前任務(關門、關閉QR畫面)，但保留包裹(DoorStatus.FULL)
    """
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).first()
    
    if not door:
        return jsonify({'error': 'Package not found in any door'}), 404
        
    controller = get_controller()
    
    try:
        # 1. 物理防呆：確保該艙門是關上的 (避免取消瞬間門還開著)
        controller.control_doors(sn=sn, door_number=door.door_number, operation=False)
        
        # 2. 強制中斷螢幕畫面：取消 QR Code 顯示，恢復預設狀態
        # (傳入空的或NORMAL的 call_mode 來覆蓋掉前一個 QR_CODE 任務)
        controller.custom_call(
            sn=sn, 
            shop_id=current_app.config.get('SHOP_ID'),
            call_device_name='dashboard',
            call_mode='CALL' 
        )
        
        # 3. 保持艙門狀態為 FULL (因為貨物還在裡面)
        
        return jsonify({
            'status': 'success', 
            'message': f'Task cancelled. Door {door.door_number} closed and UI reset. Package is still inside.'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 4. 掃描 QR 碼完成 (Pickup Complete)
# ==========================================================
@api_bp.route('/packages/<package_id>/pickup-complete', methods=['POST'])
def package_pickup_complete(package_id):
    """
    中央大腦通知：QR Token 已經在雲端驗證成功。
    【本機動作】：只負責打開對應的艙門。
    """
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).first()
    
    if not door:
        return jsonify({'error': 'Package not found in any door'}), 404

    controller = get_controller()
    
    try:
        # 呼叫普渡 API：開門
        controller.control_doors(sn=sn, door_number=door.door_number, operation=True)
        
        return jsonify({
            'status': 'success', 
            'message': f'Door {door.door_number} opened successfully.'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 5. 住戶取貨完成 (Complete)
# ==========================================================
@api_bp.route('/packages/<package_id>/complete', methods=['POST'])
def package_complete(package_id):
    """
    中央大腦通知：住戶已在 LINE 上確認取走包裹。
    【本機動作】：關門 -> 標記為空位 -> 檢查是否要自動返航。
    """
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).first()
    
    if not door:
        return jsonify({'error': 'Package not found in any door'}), 404
        
    controller = get_controller()
    
    try:
        # 1. 呼叫普渡 API：關門
        controller.control_doors(sn=sn, door_number=door.door_number, operation=False)
        
        # 2. 包裹被拿走了，物理上變為空位，釋放艙門
        door.status = DoorStatus.EMPTY
        door.package_id = None
        db.session.commit()
        
        # 3. 檢查所有艙門是否全空，若是則呼叫普渡前往「管理室」
        is_returning = check_and_return_home_if_empty()
        
        return jsonify({
            'status': 'success', 
            'message': f'Door {door.door_number} closed and freed.',
            'returning_home': is_returning
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 6. 機器人退回並清空 (Returned)
# ==========================================================
@api_bp.route('/packages/<package_id>/returned', methods=['POST'])
def package_returned(package_id):
    """
    中央大腦通知：因取消或逾時而退回的包裹，管理員已經在管理室實體取出了。
    【本機動作】：退回的包裹被拿走後，艙門物理上才真正清空，此時釋放艙門。
    """
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).first()
    
    if not door:
        return jsonify({'error': 'Package not found in any door'}), 404
    
    # 物理狀態現在才是真正的 EMPTY
    door.status = DoorStatus.EMPTY
    door.package_id = None
    db.session.commit()
    
    return jsonify({
        'status': 'success',
        'message': f'Returned package removed by admin. Door {door.door_number} is now freed.'
    })

# ==========================================================
# 7. Dashboard 監控 API (直接查實體機器人與艙門資料庫)
# ==========================================================
@api_bp.route('/dashboard/status', methods=['GET'])
def get_dashboard_status():
    """讓中央大腦隨時來查勤，回傳機器人即時物理狀態與本機艙門狀態"""
    sn = current_app.config.get('ROBOT_SN')
    controller = get_controller()
    
    try:
        # 1. 直接問普渡硬體：目前電量、動作狀態 (不碰資料庫)
        live_status = controller.get_status_summary(sn)
        
        # 2. 查本機資料庫：目前四個艙門的使用狀況
        doors = Door.query.filter_by(sn=sn).all()
        door_states = [{
            'door_number': door.door_number,
            'status': door.status,
            'package_id': door.package_id
        } for door in doors]
        
        return jsonify({
            'status': 'success',
            'data': {
                'robot_status': live_status,
                'door_states': door_states
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500