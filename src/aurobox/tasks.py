"""Background threading tasks."""
import time
import requests as http_requests

def _return_for_assign(
    app,
    controller,
    sn: str,
    door_number: int,
    package_id: str,
    timeout_seconds: int = 3000,
    poll_interval: int = 5,
):
    """背景執行緒：輪詢機器人直到抵達管理室，然後才把分配的艙門打開。"""
    with app.app_context():
        print(f"[系統] 開始輪詢機器人是否抵達管理室 (準備開啟艙門 {door_number})", flush=True)
        
        arrived = controller.wait_until_arrived(
            sn=sn,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
        
        if not arrived:
            print(f"[系統] 包裹 {package_id} 輪詢超時，機器人未能在預期時間內抵達管理室", flush=True)
            return

        try:
            print(f"[系統] 機器人已抵達管理室，準備開啟艙門 {door_number}", flush=True)
            controller.control_doors(sn=sn, door_number=door_number, operation=True)
        except Exception as e:
            print(f"[系統] 開門失敗: {e}", flush=True)

def _poll_notify_display_qr(
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
        time.sleep(3)
        
        arrived = controller.wait_until_arrived(
            sn=sn,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
        if not arrived:
            print(f"[系統] 包裹 {package_id} 輪詢超時，未收到抵達確認", flush=True)
            return
        
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
        
def _return_home_and_open_doors(
    app,
    controller,
    sn: str,
    door_numbers: list,
    timeout_seconds: int = 3000,
    poll_interval: int = 5,
):
    """背景執行緒：輪詢機器人直到抵達管理室，然後打開有退件的艙門。"""
    with app.app_context():
        time.sleep(3)
        
        print(f"[系統] 開始輪詢機器人是否抵達管理室 (準備開啟退件艙門: {door_numbers})", flush=True)
        
        arrived = controller.wait_until_arrived(
            sn=sn,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
        
        if not arrived:
            print(f"[系統] 輪詢超時，機器人未能在預期時間內抵達管理室", flush=True)
            return

        # 確定抵達後，才呼叫硬體開門
        if door_numbers:
            print(f"[系統] 機器人已抵達管理室，準備開啟退件艙門: {door_numbers}", flush=True)
            for door_number in door_numbers:
                try:
                    controller.control_doors(sn=sn, door_number=door_number, operation=True)
                except Exception as e:
                    print(f"[系統] ⚠️ 開門失敗 (艙門 {door_number}): {e}", flush=True)
        else:
            print(f"[系統] 機器人已抵達管理室，空車無退件需開啟。", flush=True)