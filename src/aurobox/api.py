"""API routes for Robot Hardware & Door management."""

import threading
from flask import Blueprint, request, jsonify, current_app
from . import db
from .models import Door, DoorStatus, RobotState

from .utils import build_custom_call_payload
from .services import get_controller, set_robot_target_point, check_and_return_home_if_empty
from .tasks import _return_for_assign, _poll_notify_display_qr, _return_home_and_open_doors

api_bp = Blueprint('api', __name__)

# ==========================================================
# 0. 分派空艙門並為管理員開門 (Assign & Open) -- 多包裹維持單一艙門分派
# ==========================================================
@api_bp.route('/doors/assign', methods=['POST'])
def assign_door_for_package():
    """
    中央大腦通知：準備裝載包裹。
    【本機動作】：尋找空艙門 -> 將機器人叫回管理室(若不在) -> 打開該艙門。
    """
    
    data = request.get_json()
    package_id = data.get('id')
    """
    print("\n" + "="*40, flush=True)
    print(f"包裹 ID : {package_id}", flush=True)
    print("="*40 + "\n", flush=True)
    """
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
        set_robot_target_point(sn, home_point)

        # 3. 更新資料庫狀態為 ASSIGNED
        empty_door.package_id = package_id
        empty_door.status = DoorStatus.ASSIGNED
        db.session.commit()

        # 4. 啟動背景執行緒去等機器人抵達並開門
        if package_id:
            app = current_app._get_current_object()
            thread = threading.Thread(
                target=_return_for_assign,
                args=(app, controller, sn, empty_door.door_number, package_id),
                daemon=True,
            )
            thread.start()

        # 5. 立刻回傳 JSON，告訴中央大腦任務已經成功受理
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
# 1. 管理員將包裹放入艙門並確認 (Load) -- 多包裹直接關閉所有艙門
# ==========================================================
@api_bp.route('/doors/load', methods=['POST'])
def load_package_to_door():
    """
    中央大腦通知：管理員已將包裹全數放入艙門，準備出發。
    【本機動作】：找出所有狀態為 ASSIGNED 的艙門 -> 關閉艙門 -> 狀態改為 FULL。
    """
    sn = current_app.config.get('ROBOT_SN')
    
    # 1. 尋找本機所有狀態為 ASSIGNED 的艙門
    assigned_doors = Door.query.filter_by(sn=sn, status=DoorStatus.ASSIGNED).all()

    if not assigned_doors:
        return jsonify({'error': 'No doors in ASSIGNED state to load'}), 400

    controller = get_controller()
    loaded_doors_info = []

    try:
        # 2. 針對每一個 ASSIGNED 艙門進行關門與狀態更新
        for door in assigned_doors:
            # 呼叫普渡 API：關門 (operation=False)
            controller.control_doors(sn=sn, door_number=door.door_number, operation=False)

            # 更新資料庫狀態為 FULL
            door.status = DoorStatus.FULL
            
            # 記錄起來準備回傳給中控端
            loaded_doors_info.append({
                'door_number': door.door_number,
                'package_id': door.package_id
            })

        # 3. 統一 Commit 寫入資料庫
        db.session.commit()

        return jsonify({
            'status': 'success',
            'message': f'Successfully closed and loaded {len(assigned_doors)} doors.',
            'loaded_doors': loaded_doors_info
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    
# ==========================================================
# 2. 指揮機器人移動 (Dispatch)  -- 多包裹維持單一包裹配送
# ==========================================================
@api_bp.route('/robot/dispatch', methods=['POST'])
def robot_dispatch():
    """中央大腦下令：機器人出發前往指定點位 (例如住戶家門口)"""
    data = request.get_json()
    target_point = data.get('point') or data.get('unit')
    target_point_type = data.get('point_type', 'table')
    target_map_name = data.get('map_name')
    package_id = data.get('package_id')
    """
    print("\n" + "="*40, flush=True)
    print(f"住戶門牌  : {target_point}", flush=True)
    print(f"包裹 ID   : {package_id}", flush=True)
    print("="*40 + "\n", flush=True)
    """
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
            set_robot_target_point(sn, target_point)
            print(f"[系統] 成功取得 Task ID: {task}", flush=True)
            # 新增：把取得的 task_id 存進對應包裹的艙門資料庫中
            if package_id:
                door = Door.query.filter_by(package_id=package_id, sn=sn).first()
                if door:
                    door.task_id = task
                    db.session.commit()
        else:
            print(f"[系統] ⚠️ 未能取得 Task ID，回傳結果: {dispatch_res}", flush=True)

        # 若有 package_id，背景輪詢機器人狀態，抵達後通知中央大腦
        if package_id:
            app = current_app._get_current_object()
            callback_base_url = current_app.config.get('CENTRAL_API_BASE_URL', '')
            thread = threading.Thread(
                target=_poll_notify_display_qr,
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
# 3. 掃描 QR 碼完成 (Pickup Complete)
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

    # 新增：利用記錄在資料庫的 task_id 消除機器人螢幕的 QR Code
    if door.task_id:
        try:
            payload_complete = {"task_id": door.task_id}
            controller.client.custom_complete(payload_complete)
            print(f"[系統] 成功消除 QR Code 畫面 (Task: {door.task_id})", flush=True)
        except Exception as e:
            print(f"[系統] 消除 QR Code 畫面失敗: {e}", flush=True)

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
# 4. 住戶取貨完成 (Complete)
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
        print ("error: door not found")
        return jsonify({'error': 'Package not found in any door'}), 404
        
    controller = get_controller()
    
    try:
        # 1. 呼叫普渡 API：關門
        controller.control_doors(sn=sn, door_number=door.door_number, operation=False)
        
        # 2. 包裹被拿走了，物理上變為空位，釋放艙門
        door.status = DoorStatus.EMPTY
        door.package_id = None
        door.task_id = None
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
# 5. 住戶拒收 / 取消或逾時 (Cancel / Reject)
# ==========================================================
@api_bp.route('/packages/<package_id>/cancel', methods=['POST'])
def package_cancel(package_id):
    """
    中央大腦通知：住戶拒收、取消或逾時。
    【本機動作】：確保關門 -> 消除 QR Code 畫面 -> 保留包裹 (FULL)
    """
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).first()

    if not door:
        print(f"[系統] 找不到對應的包裹 {package_id}", flush=True)
        return jsonify({'error': 'Package not found in any door'}), 404
        
    controller = get_controller()

    try:
        # 1. 確保艙門是關上的
        print(f"[系統] 確保艙門 {door.door_number} 已關閉", flush=True)
        controller.control_doors(sn=sn, door_number=door.door_number, operation=False)
        
        # 2. 消除機器人螢幕的 QR Code 任務畫面
        if door.task_id:
            print(f"[系統] 準備消除機器人螢幕的 QR Code (Task ID: {door.task_id})", flush=True)
            payload_complete = {"task_id": door.task_id}
            controller.client.custom_complete(payload_complete)
            print(f"[系統] 成功消除 QR Code 畫面", flush=True)
            
            # 畫面消除後，這個 task_id 就失效了，可以清掉
            door.task_id = None 
        else:
            print(f"[系統] 沒有發現綁定的 Task ID，略過畫面消除", flush=True)

        # 3. 狀態保持 FULL，包裹依然在車上
        door.status = DoorStatus.FULL
        db.session.commit()
        print(f"[系統] 艙門 {door.door_number} 狀態維持 FULL，準備等待後續退回流程", flush=True)
        
        return jsonify({
            'status': 'success', 
            'message': f'Package {package_id} rejected. Door {door.door_number} closed and UI reset. Ready for next dispatch.',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 6. 管理室：退件返航與開門 (Return & Open)
# ========================================================== 
@api_bp.route('/packages/return', methods=['POST'])
def return_packages_to_home():
    """
    中央大腦通知：無其他包裹需配送，全部退回。
    【本機動作】：呼叫機器人回到管理室 -> 背景等待抵達 -> 將狀態為 FULL 的艙門打開。
    """
    sn = current_app.config.get('ROBOT_SN')
    controller = get_controller()

    # 找出所有裡面還有退件(FULL)的艙門
    full_doors = Door.query.filter_by(sn=sn, status=DoorStatus.FULL).all()
    full_door_numbers = [door.door_number for door in full_doors]

    print(f"[系統] 偵測到需要退回的艙門有: {full_door_numbers}", flush=True)

    try:
        # 1. 呼叫機器人前往管理室
        home_point = current_app.config.get('HOME_POINT_NAME')
        payload = build_custom_call_payload(sn=sn, point=home_point)
        print(f"[系統] 呼叫機器人前往 {home_point}...", flush=True)
        controller.custom_call2(payload=payload)
        set_robot_target_point(sn, home_point)
        
        # 2. 啟動背景執行緒去等機器人抵達並開門
        app = current_app._get_current_object()
        thread = threading.Thread(
            target=_return_home_and_open_doors,
            args=(app, controller, sn, full_door_numbers),
            daemon=True,
        )
        thread.start()
        print(f"[系統] 已啟動背景執行緒，等待抵達後自動開啟退件艙門", flush=True)

        return jsonify({
            'status': 'success', 
            'message': f'Robot returning to {home_point}. Doors {full_door_numbers} will open upon arrival.',
            'returning_home': True
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 7. 管理室：確認取出並關閉艙門 (Return Complete)
# ==========================================================
@api_bp.route('/doors/return-complete', methods=['POST'])
def complete_returned_doors():
    """
    中央大腦通知：管理員已將退回的包裹全數取出。
    【本機動作】：關閉所有原本是 FULL 的艙門，並將資料庫狀態清空為 EMPTY。
    """
    sn = current_app.config.get('ROBOT_SN')
    full_doors = Door.query.filter_by(sn=sn, status=DoorStatus.FULL).all()
    
    if not full_doors:
        print(f"[系統] 沒有發現任何 FULL 狀態的艙門需要關閉", flush=True)
        return jsonify({'status': 'success', 'message': 'No doors to close.', 'closed_doors': []})

    controller = get_controller()
    closed_doors = []

    try:
        for door in full_doors:
            # 1. 物理關門
            print(f"[系統] 正在關閉退件艙門 {door.door_number}...", flush=True)
            controller.control_doors(sn=sn, door_number=door.door_number, operation=False)
            
            # 2. 清空資料庫狀態，釋放資源
            door.status = DoorStatus.EMPTY
            door.package_id = None
            door.task_id = None
            closed_doors.append(door.door_number)
            print(f"[系統] 艙門 {door.door_number} 已關閉，狀態重置為 EMPTY", flush=True)
            
        db.session.commit()
        print(f"[系統] 所有退件艙門已清空，硬體資源完全釋放", flush=True)

        return jsonify({
            'status': 'success',
            'message': 'All returned packages removed. Doors closed and freed.',
            'closed_doors': closed_doors
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
# ==========================================================
# 8. Dashboard 監控 API (直接查實體機器人與艙門資料庫)
# ==========================================================
@api_bp.route('/dashboard/status', methods=['GET'])
def get_dashboard_status():
    """讓中央大腦隨時來查勤，回傳機器人即時物理狀態與本機艙門狀態"""
    sn = current_app.config.get('ROBOT_SN')
    controller = get_controller()
    
    try:
        # 1. 查詢 Pudu 硬體狀態
        live_status = controller.get_status_summary(sn)
        move_state = live_status.get('move_state') # 會是 MOVING, ARRIVE, 或空值
        # 2. 查詢我們自己記下來的最後點位
        robot_state = RobotState.query.filter_by(sn=sn).first()
        last_point = robot_state.last_point if robot_state else current_app.config.get('HOME_POINT_NAME')
        # 核心邏輯：組裝 Dashboard 要顯示的位置字串
        if move_state == "MOVING":
            live_status['current_location'] = "MOVING"
        else:
            # 如果是 IDLE 或 ARRIVE，就顯示我們記下來的最後點位
            live_status['current_location'] = last_point

        # 3. 查本機資料庫：目前三個艙門的使用狀況
        doors = Door.query.filter_by(sn=sn).all()
        door_states = [{
            'door_number': door.door_number,
            'status': door.status,
            'package_id': door.package_id
        } for door in doors]

        # print(live_status, door_states, flush=True)
        
        return jsonify({
            'status': 'success',
            'data': {
                'robot_status': live_status,
                'door_states': door_states
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500