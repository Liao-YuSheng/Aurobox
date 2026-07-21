"""API routes for Robot Hardware & Door management."""

import threading
import time
from flask import Blueprint, request, jsonify, current_app
from . import db
from .models import Door, DoorStatus, RobotState

from .utils import build_custom_call_payload
from .services import set_robot_target_point, check_and_return_home_if_empty
from .tasks import _return_for_assign, _poll_notify_display_qr

api_bp = Blueprint('api', __name__)

def _get_active_doors(app):
    """根據設定檔動態回傳目前應該啟用的門號清單"""
    mode = app.config.get('DOOR_MODE', '4_DOORS')
    if mode == '3_DOORS':
        return ("H_01", "H_03", "H_04")
    return ("H_01", "H_02", "H_03", "H_04")

# ==========================================================
# 0. 讓機器人返回充電站 (Recharge)
# ==========================================================
@api_bp.route('/robot/recharge', methods=['POST'])
def robot_recharge():
    """
    中央大腦通知：命令機器人返回充電站。
    【本機動作】：呼叫 Pudu API 的 recharge 指令，並更新資料庫中機器人的最後點位為充電站。
    """
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')
    charge_point = current_app.config.get('CHARGE_POINT_NAME')

    if not charge_point:
        print("[系統] 警告：未設定 CHARGE_POINT_NAME，機器人可能無法正確記錄充電站位置", flush=True)
        # 即使沒設定，還是可以嘗試呼叫 recharge，但建議要有
        charge_point = "閃閃充電" # 給個預設值防呆

    try:
        # 1. 檢查艙門是否全空 (加入防呆機制)
        non_empty_doors = Door.query.filter(
            Door.sn == sn, 
            Door.status != DoorStatus.EMPTY
        ).all()

        if non_empty_doors:
            # 抓出哪些艙門還有東西，方便回報給前端排查
            busy_door_numbers = [door.door_number for door in non_empty_doors]
            error_msg = f"Cannot recharge. Doors {busy_door_numbers} are not empty."
            print(f"[系統] 拒絕充電請求：{error_msg}", flush=True)
            
            # 回傳 409 Conflict 代表機器人當前狀態與請求發生衝突
            return jsonify({
                'status': 'error',
                'error': error_msg,
                'busy_doors': busy_door_numbers
            }), 409 

        # 2. 確定艙門全空，呼叫機器人回充電站的 API
        print(f"[系統] 艙門檢查完畢 (皆為空)。準備呼叫機器人 {sn} 返回充電站...", flush=True)
        response = controller.recharge(sn=sn)
        
        # 3. 將機器人的最後點位更新為充電站，讓 Dashboard 知道它去充電了
        set_robot_target_point(sn, charge_point)
        
        print(f"[系統] 返回充電站指令發送成功，回應: {response}", flush=True)
        
        return jsonify({
            'status': 'success',
            'message': f'Robot is returning to charge station ({charge_point}).',
            'response': response
        })
        
    except Exception as e:
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 1. 分派空艙門並為管理員開門 (Assign & Open)
# ==========================================================
@api_bp.route('/packages/<package_id>/assign', methods=['POST'])
def assign_door_for_package(package_id):
    """
    中央大腦通知：管理員準備裝載包裹。
    【本機動作】：尋找空艙門 -> 將機器人叫回管理室(若不在) -> 打開該艙門。
    """
    controller = current_app.pudu_controller
    home_point = current_app.home_point
    sn = current_app.config.get('ROBOT_SN')
    
    # --- 防禦機制 1：防止管理員在機器人送貨途中誤觸 ---
    full_doors = Door.query.filter_by(sn=sn, status=DoorStatus.FULL).order_by(Door.door_number).first()
    if full_doors:
        return jsonify({
            'error': 'Robot is currently out for delivery or fully loaded. Please wait until it returns.',
            'status': 'conflict'
        }), 409 
    
    # --- 防禦機制 1.5：防止同一個包裹被重複指派 (防連點/冪等性) ---
    existing_door = Door.query.filter_by(sn=sn, package_id=package_id).order_by(Door.door_number).first()
    if existing_door:
        print(f"[系統] 偵測到重複指派，包裹 {package_id} 已經在艙門 {existing_door.door_number}", flush=True)
        # 直接回傳 200 OK 與現有門號，安撫前端，不浪費新艙門
        return jsonify({
            'status': 'success', 
            'message': f'Package {package_id} is already assigned to door {existing_door.door_number}.',
            'door_number': existing_door.door_number
        }), 200
    
    # 尋找一個狀態為 EMPTY 且在「啟用清單內」的艙門
    active_doors = _get_active_doors(current_app)
    empty_door = Door.query.filter(
        Door.sn == sn, 
        Door.status == DoorStatus.EMPTY,
        Door.door_number.in_(active_doors)
    ).order_by(Door.door_number).with_for_update(skip_locked=True).first()
    
    if not empty_door:
        return jsonify({'error': 'No empty doors available'}), 400
        
    try:
        # --- 防禦機制 2：避免機器人原地轉圈 ---
        # 新增判斷：如果已經有其他門是 ASSIGNED，代表目前正在同一批次裝貨，絕對不要下導航指令
        already_assigning = Door.query.filter_by(sn=sn, status=DoorStatus.ASSIGNED).order_by(Door.door_number).first()
        
        if already_assigning:
            print(f"[系統] 機器人正在同一批次裝載中 (已指派艙門)，強制省略導航指令", flush=True)
        else:
            
            live_status = controller.get_status_summary(sn)
            
            is_already_home = (
                live_status.get('current_location') == home_point and 
                live_status.get('move_state') in ['IDLE', 'ARRIVE']
            )
            
            if not is_already_home:
                payload = build_custom_call_payload(sn=sn, point=home_point)
                controller.custom_call2(payload=payload)

        payload = build_custom_call_payload(sn=sn, point=home_point)
        result = controller.custom_call2(payload=payload)
        print(result)
        set_robot_target_point(sn, home_point)

        # 更新資料庫狀態為 ASSIGNED
        empty_door.package_id = package_id
        empty_door.status = DoorStatus.ASSIGNED
        db.session.commit()

        # 啟動背景執行緒去等機器人抵達並開門
        app = current_app._get_current_object()
        thread = threading.Thread(
            target=_return_for_assign,
            args=(app, controller, sn, empty_door.door_number), 
            daemon=True,
        )
        thread.start()

        return jsonify({
            'status': 'success', 
            'message': f'Home point: {home_point}. Door {empty_door.door_number} assigned and opened for {package_id}',
            'door_number': empty_door.door_number
        })
    
    except Exception as e:
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 1.5 管理員裝載逾時 (Assign Timeout)
# ==========================================================
@api_bp.route('/packages/<package_id>/assign-timeout', methods=['POST'])
def package_assign_timeout(package_id):
    """
    中央大腦通知：分派空艙門後，管理員超過 5 分鐘未放貨。
    【本機動作】：尋找對應的 ASSIGNED 艙門 -> 關門 -> 將狀態清空為 EMPTY。
    """
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')
    
    # 只尋找狀態為 ASSIGNED 且符合該 package_id 的艙門
    door = Door.query.filter_by(package_id=package_id, sn=sn, status=DoorStatus.ASSIGNED).order_by(Door.door_number).first()
    
    # 冪等性防護：如果找不到，代表管理員可能在最後一秒按下確認(已變 FULL)
    # 或者中控端重複呼叫了，我們回傳 200 安撫前端即可
    if not door:
        return jsonify({
            'status': 'success', 
            'message': 'No ASSIGNED door found for this package. It might be loaded already.',
        }), 200

    try:
        # 1. 呼叫普渡 API：關閉該艙門
        print(f"[系統] 裝載逾時，準備關閉艙門 {door.door_number}", flush=True)
        controller.control_doors(
            sn=sn, 
            control_states=[{"operation": False, "door_number": door.door_number}]
        )
        
        # 2. 清空資料庫狀態，釋放資源
        door.status = DoorStatus.EMPTY
        door.package_id = None
        db.session.commit()
        
        print(f"[系統] 艙門 {door.door_number} 已關閉並重置為 EMPTY", flush=True)
        
        return jsonify({
            'status': 'success', 
            'message': f'Assign timeout handled. Door {door.door_number} closed and freed.'
        })
        
    except Exception as e:
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 2. 管理員將包裹放入艙門並確認 (Load)
# ==========================================================
@api_bp.route('/doors/load', methods=['POST'])
def load_package_to_door():
    """
    中央大腦通知：管理員已將包裹全數放入艙門，準備出發。
    【本機動作】：找出所有狀態為 ASSIGNED 的艙門 -> 組裝批次指令 -> 一次關閉所有艙門 -> 狀態改為 FULL。
    """
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')
    
    # 尋找本機所有狀態為 ASSIGNED 的艙門
    assigned_doors = Door.query.filter_by(sn=sn, status=DoorStatus.ASSIGNED).order_by(Door.door_number).all()

    # 註解錯誤訊息，即使沒有 ASSIGNED 艙門，仍可以跳過關門的步驟
    # if not assigned_doors:
    #     return jsonify({'error': 'No doors in ASSIGNED state to load'}), 400

    loaded_doors_info = []
    control_states = []

    try:
        # 針對每一個 ASSIGNED 艙門進行資料狀態更新與 Payload 組裝
        for door in assigned_doors:
            # 加入批次控制陣列 (operation=False 代表關門)
            control_states.append({
                "operation": False,
                "door_number": door.door_number
            })

            # 更新資料庫狀態為 FULL
            door.status = DoorStatus.FULL
            
            loaded_doors_info.append({
                'door_number': door.door_number,
                'package_id': door.package_id
            })

        # 一次性呼叫硬體批次關門
        if control_states:
            controller.control_doors(sn=sn, control_states=control_states)

        db.session.commit()

        return jsonify({
            'status': 'success',
            'message': f'Successfully closed and loaded {len(assigned_doors)} doors in a single batch.',
            'loaded_doors': loaded_doors_info
        })
        
    except Exception as e:
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    
# ==========================================================
# 3. 指揮機器人移動 (Dispatch)  -- 多包裹維持單一包裹配送
# ==========================================================
@api_bp.route('/robot/dispatch', methods=['POST'])
def robot_dispatch():
    """中央大腦下令：機器人出發前往指定點位 (例如住戶家門口)"""
    data = request.get_json()
    target_point = data.get('unit') or data.get('point')
    package_id = data.get('id') or data.get('package_id')

    if not target_point:
        return jsonify({'error': 'point is required (or use unit for backward compatibility)'}), 400

    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')

    try:
        payload = build_custom_call_payload(
            sn=sn,
            point=target_point,
            call_mode = 'QR_CODE',
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
                door = Door.query.filter_by(package_id=package_id, sn=sn).order_by(Door.door_number).first()
                if door:
                    door.task_id = task
                    db.session.commit()
        else:
            print(f"[系統] ⚠️ 未能取得 Task ID，回傳結果: {dispatch_res}", flush=True)

        # 若有 package_id，背景輪詢機器人狀態，抵達後通知中央大腦
        if package_id:
            app = current_app._get_current_object()
            thread = threading.Thread(
                target=_poll_notify_display_qr,
                args=(app, controller, sn, package_id, task),
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
# 4. 掃描 QR 碼完成 (Pickup Complete)
# ==========================================================
@api_bp.route('/packages/<package_id>/pickup-complete', methods=['POST'])
def package_pickup_complete(package_id):
    """
    中央大腦通知：QR Token 已經在雲端驗證成功。
    【本機動作】：只負責打開對應的艙門。
    """
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).order_by(Door.door_number).first()
    
    if not door:
        return jsonify({'error': 'Package not found in any door'}), 404

    # 新增：利用記錄在資料庫的 task_id 消除機器人螢幕的 QR Code
    if door.task_id:
        try:
            payload_complete = {"task_id": door.task_id}
            controller.client.custom_complete(payload_complete)
            print(f"[系統] 成功消除 QR Code 畫面 (Task: {door.task_id})", flush=True)

            # 畫面消除後，這個 task_id 就失效了，可以清掉
            door.task_id = None
        except Exception as e:
            print(f"[系統] 消除 QR Code 畫面失敗: {e}", flush=True)

    try:
        # 呼叫普渡 API：開門
        controller.control_doors(sn=sn, control_states=[{"operation": True, "door_number": door.door_number}])
        
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
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).order_by(Door.door_number).first()
    
    # --- 冪等性防護：若包裹已結案或清空，直接回傳成功 ---
    if not door:
        print(f"[系統] 找不到包裹 {package_id}，可能已被取走或已結案", flush=True)
        return jsonify({
            'status': 'success', 
            'message': 'Package not found. It might have already been completed.',
            'returning_home': False
        }), 200

    try:
        # 1. 呼叫普渡 API：關門
        controller.control_doors(sn=sn, control_states=[{"operation": False, "door_number": door.door_number}])
        
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
# 6. 住戶拒收 / 取消或逾時 (Cancel / Reject)
# ==========================================================
@api_bp.route('/packages/<package_id>/cancel', methods=['POST'])
def package_cancel(package_id):
    """
    中央大腦通知：住戶拒收、取消或逾時。
    【本機動作】：確保關門 -> 消除 QR Code 畫面 -> 保留包裹 (FULL)
    """
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')
    door = Door.query.filter_by(package_id=package_id, sn=sn).order_by(Door.door_number).first()
    
    if not door:
        print(f"[系統] 找不到對應的包裹 {package_id}，可能已取消或結案", flush=True)
        return jsonify({
            'status': 'success', 
            'message': 'Package not found. It might have already been canceled.'
        }), 200

    try:
        # 1. 確保艙門是關上的
        print(f"[系統] 確保艙門 {door.door_number} 已關閉", flush=True)
        controller.control_doors(sn=sn, control_states=[{"operation": False, "door_number": door.door_number}])
        
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
# 7. 管理室：退件返航 (Return Home)
# ========================================================== 
@api_bp.route('/packages/return', methods=['POST'])
def return_packages_to_home():
    """
    中央大腦通知：無其他包裹需配送，全部退回。
    【本機動作】：呼叫機器人回到管理室 -> 背景等待抵達 -> 將狀態為 FULL 的艙門打開。
    """
    controller = current_app.pudu_controller
    home_point = current_app.home_point
    sn = current_app.config.get('ROBOT_SN')

    # 找出所有裡面還有退件(FULL)的艙門
    full_doors = Door.query.filter_by(sn=sn, status=DoorStatus.FULL).order_by(Door.door_number).all()
    full_door_numbers = [door.door_number for door in full_doors]

    if not full_door_numbers:
        return jsonify({'status': 'success', 'message': 'No returned packages.'})
    
    robot_state = RobotState.query.filter_by(sn=sn).first()
    if robot_state and robot_state.last_point == home_point:
        print(f"[系統] 機器人已經在前往 {home_point} 的路上，略過重複退件指令", flush=True)
        return jsonify({
            'status': 'success', 
            'message': f'Robot is already returning to {home_point}. Await manual open for doors {full_door_numbers}.',
            'returning_home': True
        }), 200
    
    print(f"[系統] 偵測到需要退回的艙門有: {full_door_numbers}", flush=True)

    try:
        set_robot_target_point(sn, home_point)
        time.sleep(10)
        payload = build_custom_call_payload(sn=sn, point=home_point)
        print(f"[系統] 呼叫機器人前往 {home_point}...", flush=True)
        controller.custom_call2(payload=payload)

        return jsonify({
            'status': 'success', 
            'message': f'Robot returning to {home_point}. Await manual open for doors {full_door_numbers}.',
            'returning_home': True
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 8. 管理室：退件返航後開門 (Open Returned Doors)
# ========================================================== 
@api_bp.route('/packages/return-open', methods=['POST'])
def open_returned_doors():
    """
    前端按鈕觸發：管理員準備取出退件。
    【本機動作】：批次打開所有狀態為 FULL 的艙門。
    """
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')

    # 找出所有裡面還有退件(FULL)的艙門
    full_doors = Door.query.filter_by(sn=sn, status=DoorStatus.FULL).order_by(Door.door_number).all()

    if not full_doors:
        return jsonify({'status': 'success', 'message': 'No full doors to open.', 'opened_doors': []})
    
    control_states = []
    opened_doors = []
    
    try:
        # 組裝批次開門指令
        for door in full_doors:
            control_states.append({
                "operation": True,
                "door_number": door.door_number
            })
            opened_doors.append(door.door_number)
            
        # 一次性呼叫硬體批次開門
        if control_states:
            print(f"[系統] 正在手動批次開啟退件艙門: {opened_doors}...", flush=True)
            controller.control_doors(sn=sn, control_states=control_states)

        return jsonify({
            'status': 'success', 
            'message': f'Returned doors: {opened_doors} opened successfully.',
            'returning_home': True
        })
    except Exception as e:
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 9. 管理室：確認取出並關閉艙門 (Return Complete)
# ==========================================================
@api_bp.route('/doors/return-complete', methods=['POST'])
def complete_returned_doors():
    """
    中央大腦通知：管理員已將退回的包裹全數取出。
    【本機動作】：組裝批次指令 -> 一次關閉所有原本是 FULL 的艙門 -> 將資料庫狀態清空為 EMPTY。
    """
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')

    full_doors = Door.query.filter_by(sn=sn, status=DoorStatus.FULL).order_by(Door.door_number).all()
    
    if not full_doors:
        print(f"[系統] 沒有發現任何 FULL 狀態的艙門需要關閉", flush=True)
        return jsonify({'status': 'success', 'message': 'No doors to close.', 'closed_doors': []})

    closed_doors = []
    control_states = []

    try:
        # 1. 準備批次關門指令與更新資料庫狀態
        for door in full_doors:
            # 加入批次控制陣列 (operation=False 代表關門)
            control_states.append({
                "operation": False,
                "door_number": door.door_number
            })
            
            # 清空資料庫狀態，釋放資源
            door.status = DoorStatus.EMPTY
            door.package_id = None
            door.task_id = None
            closed_doors.append(door.door_number)
            print(f"[系統] 艙門 {door.door_number} 狀態重置為 EMPTY", flush=True)
            
        # 2. 一次性呼叫硬體批次關門
        if control_states:
            print(f"[系統] 正在批次關閉退件艙門: {closed_doors}...", flush=True)
            controller.control_doors(sn=sn, control_states=control_states)
            
        db.session.commit()
        print(f"[系統] 所有退件艙門已清空，硬體資源完全釋放", flush=True)

        return jsonify({
            'status': 'success',
            'message': 'All returned packages removed. Doors closed and freed in a single batch.',
            'closed_doors': closed_doors
        })
    except Exception as e:
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 9.5 管理室：退件開門逾時強制關門 (Return Open Timeout)
# ==========================================================
@api_bp.route('/doors/return-timeout', methods=['POST'])
def return_doors_timeout():
    """
    中央大腦通知：退件艙門已開啟超過設定時間（例如 5 分鐘），但管理員遲遲未點擊完成。
    【本機動作】：保護硬體，強制關閉所有狀態為 FULL 的艙門，並重置狀態。
    """
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')

    # 找出現存所有 FULL（等待退件取出）的艙門
    full_doors = Door.query.filter_by(sn=sn, status=DoorStatus.FULL).order_by(Door.door_number).all()
    
    # 冪等性防護：如果找不到 FULL 的艙門，代表管理員可能壓線按了完成，或者中控重複呼叫
    if not full_doors:
        return jsonify({
            'status': 'success', 
            'message': 'No full doors found. Return process might be completed already.',
            'closed_doors': []
        }), 200

    closed_doors = []
    control_states = []

    try:
        # 準備批次強制關門指令
        for door in full_doors:
            control_states.append({
                "operation": False,
                "door_number": door.door_number
            })
            
            closed_doors.append(door.door_number)
            
        # 一次性呼叫硬體批次關門
        if control_states:
            print(f"[系統] 觸發退件逾時，強制批次關閉艙門: {closed_doors}...", flush=True)
            controller.control_doors(sn=sn, control_states=control_states)
            
        db.session.commit()
        print(f"[系統] 逾時退件艙門已強制關閉，硬體資源釋放", flush=True)

        return jsonify({
            'status': 'success',
            'message': f'Return timeout handled. Doors {closed_doors} force-closed and reset.',
            'closed_doors': closed_doors
        })
    except Exception as e:
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# ==========================================================
# 10. Dashboard 監控 API (直接查實體機器人與艙門資料庫)
# ==========================================================
@api_bp.route('/dashboard/status', methods=['GET'])
def get_dashboard_status():
    """讓中央大腦隨時來查勤，回傳機器人即時物理狀態與本機艙門狀態"""
    controller = current_app.pudu_controller
    sn = current_app.config.get('ROBOT_SN')
    
    try:
        # 1. 查詢 Pudu 硬體狀態
        live_status = controller.get_status_summary(sn)
        move_state = live_status.get('move_state') # 會是 MOVING, ARRIVE, 或空值
        # 2. 查詢我們自己記下來的最後點位
        robot_state = RobotState.query.filter_by(sn=sn).first()
        last_point = robot_state.last_point if robot_state else current_app.config.get('HOME_POINT_NAME')
        # 核心邏輯：組裝 Dashboard 要顯示的位置字串
        if move_state == "MOVING" or move_state == "APPROACHING":
            live_status['current_location'] = "MOVING"
        else:
            # 如果是 IDLE 或 ARRIVE，就顯示我們記下來的最後點位
            live_status['current_location'] = last_point

        # 3. 查本機資料庫：目前「啟用中」的艙門使用狀況
        active_doors = _get_active_doors(current_app)
        doors = Door.query.filter(
            Door.sn == sn,
            Door.door_number.in_(active_doors)
        ).order_by(Door.door_number).all()
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
