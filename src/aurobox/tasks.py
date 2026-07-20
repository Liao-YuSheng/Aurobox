"""Background threading tasks."""
import time
from flask import current_app
import requests as http_requests
import threading
import queue
from .models import Door, RobotState

timeout_seconds = 300
# 宣告全域 Lock，防止多個分派任務同時執行開門動作
_assign_lock = threading.Lock()
_assign_queue = queue.Queue()

def _return_for_assign(
    app,
    controller,
    sn: str,
    door_number: str,
    timeout_seconds: int = timeout_seconds,
    poll_interval: int = 5,
):
    """背景執行緒：統籌處理開門請求，確保每個門只被呼叫一次開門。"""
    
    # 1. 第一時間把這次要開的門塞進全域佇列
    _assign_queue.put(door_number)
    
    # 2. 確保同一時間只有一個執行緒在等待並消耗佇列
    if not _assign_lock.acquire(blocking=False):
        print(f"[系統] 已有統籌執行緒在運作，新任務艙門 {door_number} 已加入佇列等待。", flush=True)
        return

    try:
        with app.app_context():
            time.sleep(10)
            print(f"[系統] 開始輪詢機器人是否抵達管理室", flush=True)
            
            arrived = controller.wait_until_arrived(
                sn=sn,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
            
            if not arrived:
                print(f"[系統] 輪詢超時，機器人未能在預期時間內抵達", flush=True)
                # 超時的話，清空沒處理完的佇列避免後續發生異常開門
                while not _assign_queue.empty():
                    _assign_queue.get()
                return

            print(f"[系統] 機器人已抵達，準備依序開啟佇列中的艙門...", flush=True)
            
            # 3. 只要佇列裡面還有任務，就拿出來開門 (開完就丟掉，不會重複)
            while True:
                if _assign_queue.empty():
                    break  # 確定全空才準備跳出
                    
                door_to_open = _assign_queue.get()
                try:
                    controller.control_doors(sn=sn, control_states=[{"operation": True, "door_number": door_to_open}])
                    print(f"[系統] 成功開啟艙門 {door_to_open}", flush=True)
                    time.sleep(1) 
                except Exception as e:
                    print(f"[系統] ⚠️ 開門失敗 (艙門 {door_to_open}): {e}", flush=True)
                    
    finally:
        # 確保即使發生異常，也一定會釋放 Lock
        _assign_lock.release()

def _poll_notify_display_qr(
    app,
    controller,
    sn: str,
    package_id: str,
    task: str = None,
    timeout_seconds: int = timeout_seconds,
    poll_interval: int = 5,
) -> None:
    """背景執行緒：輪詢機器人狀態直到抵達，再通知中央大腦。"""
    with app.app_context():
        time.sleep(3)
        
        arrived = controller.wait_until_arrived(
            sn=sn,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
        if not arrived:
            print(f"[系統] 包裹 {package_id} 輪詢超時，未收到抵達確認", flush=True)
            return
        
        callback_base_url = current_app.config.get('CENTRAL_API_BASE_URL', '')
        base = callback_base_url.rstrip('/')
        url = f"{base}/packages/{package_id}/arrived"
        try:
            resp = http_requests.post(url, timeout=10)
            if resp.ok:
                print(f"[系統] 抵達通知成功 ({resp.status_code})  →  {url}", flush=True)
            else:
                print(f"[系統] 抵達通知回應異常 ({resp.status_code})  →  {url}\n回應內容: {resp.text[:300]}", flush=True)
        except Exception as e:
            print(f"[系統] 抵達通知失敗: {e}  →  {url}", flush=True)

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

def _push_dashboard_status_loop(app, poll_interval: int = 3):
    """背景執行緒：輪詢監控目標狀態，當發生變化時立刻推播給中央大腦。"""
    with app.app_context():
        controller = app.pudu_controller
        sn = app.config.get('ROBOT_SN')
        callback_base_url = current_app.config.get('CENTRAL_API_BASE_URL', '')
        base = callback_base_url.rstrip('/')
            
        push_url = f"{base}/admin/robot-status"
        print(f"[系統] 啟動狀態變更監控執行緒 (間隔 {poll_interval}s)，將推播至 {push_url}", flush=True)

        # 宣告暫存變數，用來記憶上一次成功推播的狀態
        previous_target_state = None

        while True:
            try:
                # 1. 查詢 Pudu 硬體狀態
                live_status = controller.get_status_summary(sn)
                move_state = live_status.get('move_state')
                
                # 2. 查詢本機資料庫的最後點位
                robot_state = RobotState.query.filter_by(sn=sn).first()
                last_point = robot_state.last_point if robot_state else app.config.get('HOME_POINT_NAME')
                
                # 核心邏輯：組裝位置字串
                if move_state == "MOVING" or move_state == "APPROACHING":
                    live_status['current_location'] = "MOVING"
                else:
                    live_status['current_location'] = last_point

                # 3. 查本機資料庫：目前四個艙門的使用狀況
                doors = Door.query.filter_by(sn=sn).all()
                door_states = [{
                    'door_number': door.door_number,
                    'status': door.status,
                    'package_id': door.package_id
                } for door in doors]
                
                # 4. 提取要監控的「目標資訊」進行比對
                current_target_state = {
                    # 將 4 個門的狀態轉為 Tuple 列表，方便比對
                    "doors": [(d.door_number, d.status) for d in doors], 
                    "location": live_status.get('current_location'),
                    "battery": live_status.get('battery_level'),
                    "move_state": move_state
                }

                # 如果目前的狀態與上一次紀錄的狀態「不同」，才進行推播
                if current_target_state != previous_target_state:
                    
                    # 組裝要推播的完整 Payload
                    payload = {
                        'status': 'success',
                        'data': {
                            'robot_status': live_status,
                            'door_states': door_states
                        }
                    }
                    
                    # print(payload, flush=True)
                    # 發送 POST 請求主動推播
                    resp = http_requests.post(push_url, json=payload, timeout=10)
                    
                    if resp.ok:
                        print(f"[系統] 偵測到目標資訊變更，已即時推播狀態！", flush=True)
                        # 成功推播後，將目前的狀態記憶下來，作為下一次比對的基準
                        previous_target_state = current_target_state
                    else:
                        print(f"[系統] Dashboard 推播回應異常 ({resp.status_code})", flush=True)
                        poll_interval = 300
                        # 注意：若推播失敗，不更新 previous_target_state，讓系統下一秒繼續嘗試推播
                        
            except Exception as e:
                print(f"[系統] Dashboard 狀態推播發生異常: {e}", flush=True)
            
            # 暫停 poll_interval 秒後再檢查 (設定為 3 秒以確保即時性)
            time.sleep(poll_interval)
