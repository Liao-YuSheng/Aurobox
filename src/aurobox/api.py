"""API routes for Robot Hardware & Door management."""

import threading

import requests as http_requests
from flask import Blueprint, request, jsonify, current_app
from . import db
from .models import Door, DoorStatus
from .robot import FlashbotController
from .config import load_config

api_bp = Blueprint('api', __name__)


def get_controller():
    """Get FlashbotController instance."""
    return FlashbotController(load_config())


def build_custom_call_payload(
    sn: str,
    *,
    point: str | None = None,
    map_name: str | None = None,
    point_type: str = 'table',
    call_device_name: str = 'dashboard',
    call_mode: str = 'CALL',
    task_id: str | None = None,
    mode_data: dict | None = None,
    do_not_queue: bool = False,
    robot_group_ids: list | None = None,
    filter_category_ids: list | None = None,
    priority: int = 1,
) -> dict:
    """Build a standard custom_call2 payload with app defaults."""
    payload = {
        'sn': sn,
        'shop_id': current_app.config.get('SHOP_ID'),
        'call_device_name': call_device_name,
        'call_mode': call_mode,
        "task_id": task_id,
        'mode_data': mode_data or {},
        'do_not_queue': do_not_queue,
        'robot_group_ids': robot_group_ids or [],
        'filter_category_ids': filter_category_ids or [],
        'priority': priority,
    }

    resolved_map_name = map_name if map_name is not None else current_app.config.get('DEFAULT_MAP_NAME')
    if resolved_map_name:
        payload['map_name'] = resolved_map_name

    if point is not None:
        payload['point'] = point
        payload['point_type'] = point_type

    return payload

def _poll_and_notify_arrived(
    app,
    controller,
    sn: str,
    package_id: str,
    callback_base_url: str,
    task: str = None,
    timeout_seconds: int = 3000,
    poll_interval: int = 5,
) -> None:
    """背景執行緒：輪詢機器人狀態直到抵達，再通知中央大腦。"""
    with app.app_context():
        arrived = controller.wait_until_arrived(
            sn=sn,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
        if not arrived:
            print(f"[系統] 包裹 {package_id} 輪詢超時，未收到抵達確認", flush=True)
            return
        # ==========================================================
        # 💡 新增：抵達定點後，立刻利用 task_id 呼叫 custom_content 顯示 QR Code
        # ⚠️ custom_content 需要嵌套 payload 結構，且不包含 shop_id
        # ==========================================================
        if task:
            print(f"[系統] 抵達定點，準備顯示 QR Code (Task ID: {task})", flush=True)
            payload_qr = {
                "sn": sn,
                "payload": {
                    "call_mode": "QR_CODE",
                    "task_id": task,
                    "mode_data": {
                        "qrcode": package_id,
                        "text": "請掃描 QR Code 取件"
                    }
                }
            }
            try:
                res = controller.custom_content(payload=payload_qr)
                if res and res.get('message') == 'SUCCESS':
                    print("[系統] QR Code 畫面切換成功", flush=True)
                else:
                    print(f"[系統] QR Code 顯示異常: {res}", flush=True)
            except Exception as e:
                print(f"[系統] QR Code 顯示失敗: {e}", flush=True)

        if not callback_base_url:
            print("[系統] CENTRAL_API_BASE_URL 未設定，略過抵達通知", flush=True)
            return

        base = callback_base_url.rstrip('/')
        url = f"{base}/packages/{package_id}/arrived"
        try:
            resp = http_requests.post(url, timeout=10)
            if resp.ok:
                print(f"[系統] 抵達通知成功 ({resp.status_code})  →  {url}", flush=True)
            else:
                print(
                    f"[系統] 抵達通知回應異常 ({resp.status_code})  →  {url}\n"
                    f"       回應內容: {resp.text[:300]}",
                    flush=True,
                )
        except Exception as e:
            print(f"[系統] ⚠️ 抵達通知失敗: {e}  →  {url}", flush=True)


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
        home_point = current_app.config.get('HOME_POINT_NAME')
        payload = build_custom_call_payload(sn=sn, point=home_point)
        controller.custom_call2(payload=payload)
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
    package_id = data.get('id')

    print("\n" + "="*40, flush=True)
    print(f"包裹 ID : {package_id}", flush=True)
    print("="*40 + "\n", flush=True)
    
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
        home_point = current_app.config.get('HOME_POINT_NAME')
        payload = build_custom_call_payload(sn=sn, point=home_point)
        controller.custom_call2(payload=payload)

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
        # 加入這兩行，它會在終端機印出紅色的詳細報錯，告訴你是第幾行出錯
        import traceback
        traceback.print_exc()
        
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 1. 管理員將包裹放入艙門並確認 (Load)
# ==========================================================
@api_bp.route('/doors/load', methods=['POST'])
def load_package_to_door():
    """
    中央大腦通知：管理員已將包裹放入指定艙門。
    【本機動作】：依 package_id 找到對應艙門 -> 關閉艙門 -> 狀態改為 FULL。
    """
    data = request.get_json()
    package_id = data.get('id')

    print("\n" + "="*40, flush=True)
    print(f"包裹 ID : {package_id}", flush=True)
    print("="*40 + "\n", flush=True)

    if not package_id:
        return jsonify({'error': 'package_id is required'}), 400

    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).first()

    if not door:
        return jsonify({'error': f'No door found for package_id {package_id}'}), 404

    # 確保該門有被指派
    if door.status != DoorStatus.ASSIGNED:
        return jsonify({'error': f'Door {door.door_number} is not in ASSIGNED state'}), 400

    controller = get_controller()

    try:
        # 1. 呼叫普渡 API：關門
        controller.control_doors(sn=sn, door_number=door.door_number, operation=False)

        # 2. 更新資料庫狀態為 FULL
        door.status = DoorStatus.FULL
        db.session.commit()

        return jsonify({
            'status': 'success',
            'message': f'Door {door.door_number} closed and marked as FULL with {door.package_id}',
            'door_number': door.door_number,
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
    target_point = data.get('point') or data.get('unit')
    target_point_type = data.get('point_type', 'table')
    target_map_name = data.get('map_name')
    package_id = data.get('package_id')

    print("\n" + "="*40, flush=True)
    print(f"住戶門牌  : {target_point}", flush=True)
    print(f"包裹 ID   : {package_id}", flush=True)
    print("="*40 + "\n", flush=True)

    if not target_point:
        return jsonify({'error': 'point is required (or use unit for backward compatibility)'}), 400

    controller = get_controller()
    sn = current_app.config.get('ROBOT_SN')

    try:
        # 送貨點由外部 point 傳入，unit 僅保留向下相容
        payload = build_custom_call_payload(
            sn=sn,
            point=target_point,
            call_mode = 'QR_CODE',
            map_name=target_map_name,
            point_type=target_point_type,
        )

        # 1. 抓取回傳的 response
        dispatch_res = controller.custom_call2(payload=payload)
        
        # 2. 從 response 中解析出 task_id (根據先前的 Log，它藏在 data 裡面)
        task = None
        if dispatch_res and dispatch_res.get('message') == 'SUCCESS':
            task = dispatch_res.get('data', {}).get('task_id')
            print(f"[系統] 成功取得 Task ID: {task}", flush=True)
        else:
            print(f"[系統] ⚠️ 未能取得 Task ID，回傳結果: {dispatch_res}", flush=True)

        # 若有 package_id，背景輪詢機器人狀態，抵達後通知中央大腦
        if package_id:
            app = current_app._get_current_object()
            callback_base_url = current_app.config.get('CENTRAL_API_BASE_URL', '')
            thread = threading.Thread(
                target=_poll_and_notify_arrived,
                args=(app, controller, sn, package_id, callback_base_url, task),
                daemon=True,
            )
            thread.start()

        return jsonify({
            'status': 'success',
            'message': f'Robot is moving to {target_point}',
            'task_id': task,
            'polling': package_id is not None,
        })
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
        payload = build_custom_call_payload(sn=sn)
        controller.custom_call2(payload=payload)
        
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