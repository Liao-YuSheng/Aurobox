"""
LINE 後端 - 主程式入口
"""
from datetime import datetime
from typing import Optional
import requests

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import PlainTextResponse, HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, PostbackEvent, FollowEvent, UnfollowEvent, TextMessageContent

from app.config import settings
from app.db import get_db, SessionLocal
from app.models import LineBinding, Package, PackageRecipient
from app.line_verify import verify_liff_id_token
from app.line_messaging import (
    reply_welcome_with_binding_instructions,
    reply_text,
    push_arrival_notification,
    push_status_update,
    push_arrived_notification,
    push_pickup_complete_button,
    reply_later_packages,
)
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI(title="AMR 配送系統 - LINE 後端")

parser = WebhookParser(settings.LINE_CHANNEL_SECRET)


@app.get("/")
async def health_check():
    return {"status": "ok", "message": "LINE backend is running", "env": settings.APP_ENV}


@app.post("/webhook")
async def line_webhook(request: Request):
    signature = request.headers.get("X-Line-Signature")
    if signature is None:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body = await request.body()

    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=401, detail="Invalid signature")

    for event in events:
        if isinstance(event, FollowEvent):
            print(f"[follow] 新用戶加入好友, user_id={event.source.user_id}")
            reply_welcome_with_binding_instructions(event.reply_token)

        elif isinstance(event, UnfollowEvent):
            print(f"[unfollow] 用戶封鎖/退出, user_id={event.source.user_id}")
            db = SessionLocal()
            try:
                binding = db.query(LineBinding).filter(
                    LineBinding.line_user_id == event.source.user_id
                ).first()
                if binding:
                    binding.status = "inactive"
                    db.commit()
            finally:
                db.close()

        elif isinstance(event, PostbackEvent):
            print(f"[postback] user_id={event.source.user_id}, data={event.postback.data}")
            handle_postback(event.postback.data, event.reply_token, event.source.user_id)

        elif isinstance(event, MessageEvent):
            if isinstance(event.message, TextMessageContent):
                text = event.message.text.strip()
                if text == "我的包裹":
                    handle_my_packages_query(event.source.user_id, event.reply_token)
                elif text == "開啟限本人通知":
                    handle_solo_notify_toggle(event.source.user_id, event.reply_token, True)
                elif text == "關閉限本人通知":
                    handle_solo_notify_toggle(event.source.user_id, event.reply_token, False)
                else:
                    handle_text_binding(event.source.user_id, text, event.reply_token)
            else:
                print(f"[message] user_id={event.source.user_id} (非文字訊息)")

    return PlainTextResponse("OK")


def handle_solo_notify_toggle(line_user_id: str, reply_token: str, enable: bool):
    db = SessionLocal()
    try:
        binding = db.query(LineBinding).filter(LineBinding.line_user_id == line_user_id).first()
        if not binding:
            reply_text(reply_token, "請先完成綁定（輸入：門牌 姓名）")
            return
        binding.solo_notify = enable
        db.commit()
        msg = "已開啟：包裹到貨只通知您本人" if enable else "已關閉：包裹到貨通知同門牌所有人"
        reply_text(reply_token, msg)
    finally:
        db.close()


def handle_text_binding(line_user_id: str, text: str, reply_token: str):
    """解析「門牌 姓名」格式的文字訊息"""
    parts = text.split()
    if len(parts) == 2:
        unit, name = parts[0], parts[1]
        db = SessionLocal()
        try:
            existing = db.query(LineBinding).filter(LineBinding.line_user_id == line_user_id).first()
            if existing:
                existing.unit = unit
                existing.name = name
                existing.status = "active"
            else:
                existing = LineBinding(line_user_id=line_user_id, unit=unit, name=name)
                db.add(existing)
            db.commit()
            reply_text(reply_token, f"綁定成功！\n門牌：{unit}\n姓名：{name}")
        finally:
            db.close()
    else:
        reply_text(
            reply_token,
            "格式不正確，請輸入：門牌 姓名\n例如：5F-1 王小明",
        )


def handle_my_packages_query(line_user_id: str, reply_token: str):
    db = SessionLocal()
    try:
        packages = (
            db.query(Package)
            .filter(Package.line_user_id == line_user_id, Package.status == "later")
            .all()
        )
        if not packages:
            reply_text(reply_token, "目前沒有待取包裹")
        else:
            reply_later_packages(reply_token, packages)
    finally:
        db.close()

def get_recipients(db: Session, package_id: str) -> list:
    """查詢這筆包裹當初通知過的所有LINE User ID"""
    rows = db.query(PackageRecipient).filter(PackageRecipient.package_id == package_id).all()
    return [row.line_user_id for row in rows]

def try_assign_door(package_id: str, db: Session) -> bool:
    """嘗試跟機器人要一個空艙門，成功會把door_id存進package並回傳True"""
    try:
        resp = requests.post(
            f"{settings.ROBOT_API_BASE_URL}/api/doors/assign",
            json={"id": package_id},
            timeout=5,
        )
    except requests.exceptions.RequestException as e:
        print(f"[錯誤] 呼叫機器人分配艙門失敗（連線問題）: {e}")
        return False

    if resp.status_code != 200:
        print(f"[分配艙門] package_id={package_id} 暫時無法分配：{resp.status_code} {resp.text}")
        return False

    door_number = resp.json().get("door_number")
    package = db.query(Package).filter(Package.id == package_id).first()
    package.door_id = door_number
    db.commit()

    for line_user_id in get_recipients(db, package_id):
        push_status_update(line_user_id, f"已為您準備包裹，管理員正在安排放置艙門 {door_number}")

    return True

def handle_postback(data: str, reply_token: str, triggered_by: str):
    """解析postback的data參數，格式類似 action=PICKUP_NOW&package_id=xxx"""
    params = dict(item.split("=") for item in data.split("&"))
    action = params.get("action")
    package_id = params.get("package_id")

    if not action or not package_id:
        return

    db = SessionLocal()
    try:
        package = db.query(Package).filter(Package.id == package_id).first()
        if not package:
            reply_text(reply_token, "找不到這筆包裹資料，請聯繫管理員")
            return

        if action == "PICKUP_NOW":
            package.status = "pickup_now"
            db.commit()

            try:
                resp = requests.post(
                    f"{settings.ROBOT_API_BASE_URL}/api/doors/assign",
                    json={"id": package_id},
                    timeout=5,
                )
                if resp.status_code == 200:
                    package.door_id = resp.json().get("door_number")
                    db.commit()
                else:
                    print(f"[錯誤] 分配艙門失敗: {resp.status_code} {resp.text}")
            except requests.exceptions.RequestException as e:
                print(f"[錯誤] 呼叫機器人分配艙門失敗: {e}")

            reply_text(reply_token, "已收到您的取貨請求，管理員正在為您準備包裹！")

        elif action == "LATER":
            package.status = "later"
            db.commit()
            reply_text(reply_token, "好的，您可以之後透過選單「我的包裹」隨時回來取貨")

        elif action == "PICKUP_DONE":
            if package.status != "arrived":
                db.close()
                reply_text(reply_token, "這筆包裹已經處理過了")
                return
            db.close()
            result = complete_pickup(package_id)
            if not result["ok"]:
                reply_text(reply_token, f"取貨確認失敗：{result['detail']}")
            return

        elif action == "CANCEL_PICKUP":
            if package.status == "arrived":
                package.status = "returned_cancelled"
                db.commit()
                reply_text(reply_token, "已為您安排退回，包裹將送回管理室")

                triggered_binding = db.query(LineBinding).filter(
                    LineBinding.line_user_id == triggered_by
                ).first()
                triggered_name = triggered_binding.name if triggered_binding else "同門牌住戶"

                for line_user_id in get_recipients(db, package_id):
                    if line_user_id != triggered_by:
                        push_status_update(
                            line_user_id,
                            f"{triggered_name} 已將包裹安排退回，包裹將送回管理室",
                        )
            else:
                reply_text(reply_token, "這筆包裹目前無法取消")
    finally:
        db.close()


# ========== 階段3.2 到貨通知 ==========

class CreatePackageRequest(BaseModel):
    unit: str
    recipient_name: Optional[str] = None

class ConfirmDispatchRequest(BaseModel):
    door_id: Optional[str] = None

class PickupVerifyRequest(BaseModel):
    scanned_content: Optional[str] = None
    id_token: Optional[str] = None


@app.post("/packages")
async def create_package(payload: CreatePackageRequest, db: Session = Depends(get_db)):
    bindings = (
        db.query(LineBinding)
        .filter(LineBinding.unit == payload.unit, LineBinding.status == "active")
        .all()
    )
    if not bindings:
        raise HTTPException(
            status_code=404,
            detail=f"找不到門牌 {payload.unit} 的綁定資料，請先確認住戶已完成綁定",
        )

    targets = bindings
    if payload.recipient_name:
        matched = [b for b in bindings if b.name == payload.recipient_name]
        if matched and matched[0].solo_notify:
            targets = matched

    package = Package(unit=payload.unit, line_user_id=targets[0].line_user_id, status="pending")
    db.add(package)
    db.commit()
    db.refresh(package)

    for binding in targets:
        db.add(PackageRecipient(package_id=package.id, line_user_id=binding.line_user_id, unit=payload.unit))
    db.commit()

    for binding in targets:
        push_arrival_notification(binding.line_user_id, str(package.id), payload.unit)

    return {"status": "ok", "package_id": str(package.id), "notified_count": len(targets)}

# ========== 管理員後台 API ==========

@app.get("/admin/packages")
async def admin_list_packages(db: Session = Depends(get_db)):
    """給後台頁面用的包裹清單，包含系統指派的艙門"""
    packages = db.query(Package).order_by(Package.created_at.desc()).all()
    return [
        {
            "id": str(p.id),
            "unit": p.unit,
            "status": p.status,
            "door_id": p.door_id,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in packages
    ]


@app.get("/admin/bindings")
async def admin_list_bindings(db: Session = Depends(get_db)):
    """給建立包裹表單用的下拉選單資料，只列出還有效的綁定"""
    bindings = db.query(LineBinding).filter(LineBinding.status == "active").all()
    return [
        {"unit": b.unit, "name": b.name, "line_user_id": b.line_user_id, "solo_notify": b.solo_notify}
        for b in bindings
    ]


@app.get("/admin/robot-status")
async def admin_robot_status():
    """轉發呼叫機器人的即時狀態（位置、電量、三個艙門狀況）"""
    try:
        resp = requests.get(f"{settings.ROBOT_API_BASE_URL}/api/dashboard/status", timeout=5)
        if resp.status_code != 200:
            return {"status": "error", "detail": f"機器人回應異常: {resp.status_code}"}
        return resp.json()
    except requests.exceptions.RequestException as e:
        return {"status": "error", "detail": f"無法連線到機器人: {e}"}

# ========== 階段3.4 管理員確認出發（放貨+派工合併） ==========

@app.post("/packages/{package_id}/stored")
async def confirm_dispatch(package_id: str, payload: ConfirmDispatchRequest = None, db: Session = Depends(get_db)):
    package = db.query(Package).filter(Package.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")

    if package.status != "pickup_now":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是可以派工的狀態",
        )

    try:
        requests.post(
            f"{settings.ROBOT_API_BASE_URL}/api/doors/load",
            json={"id": package_id},
            timeout=5,
        )
    except requests.exceptions.RequestException as e:
        print(f"[錯誤] 呼叫機器人裝載艙門失敗: {e}")


    package.status = "delivering"
    db.commit()

    try:
        requests.post(
            f"{settings.ROBOT_API_BASE_URL}/api/robot/dispatch",
            json={"unit": package.unit, "package_id": package_id},
            timeout=5,
        )
    except requests.exceptions.RequestException as e:
        print(f"[錯誤] 呼叫機器人派工失敗: {e}")

    for line_user_id in get_recipients(db, package_id):
        push_status_update(line_user_id, "機器人已出發，包裹正在配送中，請稍候")

    return {"status": "ok", "package_id": str(package.id), "new_status": package.status}

# ========== 階段3.5 機器人抵達（暫時用手動呼叫模擬） ==========

@app.post("/packages/{package_id}/arrived")
async def robot_arrived(package_id: str, db: Session = Depends(get_db)):
    package = db.query(Package).filter(Package.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")

    if package.status != "delivering":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是配送中的狀態",
        )

    package.status = "arrived"
    package.arrived_at = datetime.utcnow()
    db.commit()

    try:
        requests.post(
            f"{settings.ROBOT_API_BASE_URL}/api/packages/{package_id}/show-qr",
            json={"id": package_id},
            timeout=5,
        )
    except requests.exceptions.RequestException as e:
        print(f"[錯誤] 呼叫機器人顯示QR Code失敗: {e}")

    for line_user_id in get_recipients(db, package_id):
        push_arrived_notification(line_user_id, str(package.id))

    return {"status": "ok", "package_id": str(package.id), "new_status": package.status}


@app.post("/packages/{package_id}/pickup-complete")
async def pickup_verify(package_id: str, payload: PickupVerifyRequest = None, db: Session = Depends(get_db)):
    package = db.query(Package).filter(Package.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")

    if package.status != "arrived":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是等待取貨的狀態",
        )

    scanned = payload.scanned_content if payload else None
    if not scanned or scanned != package_id:
        raise HTTPException(status_code=403, detail="取貨碼驗證失敗")

    id_token = payload.id_token if payload else None
    if not id_token:
        raise HTTPException(status_code=403, detail="缺少身分驗證資訊，請重新從LINE進入")

    try:
        claims = verify_liff_id_token(id_token, settings.LINE_LOGIN_CHANNEL_ID)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=f"身分驗證失敗：{e}")

    scanning_user_id = claims.get("sub")
    if scanning_user_id not in get_recipients(db, package_id):
        raise HTTPException(status_code=403, detail="您不是這筆包裹的收件人，無法取貨")

    try:
        resp = requests.post(
            f"{settings.ROBOT_API_BASE_URL}/api/packages/{package_id}/pickup-complete",
            timeout=5,
        )
    except requests.exceptions.RequestException as e:
        print(f"[錯誤] 呼叫機器人開門失敗（連線問題）: {e}")
        raise HTTPException(status_code=502, detail="無法連線到機器人，請稍後再試或聯繫管理員")

    if resp.status_code != 200:
        print(f"[錯誤] 機器人開門失敗: {resp.status_code} {resp.text}")
        raise HTTPException(status_code=502, detail="機器人開門失敗，請聯繫管理員協助取件")

    for line_user_id in get_recipients(db, package_id):
        push_pickup_complete_button(line_user_id, str(package.id))

    return {"status": "ok", "message": "驗證通過，艙門已開啟"}

def complete_pickup(package_id: str) -> dict:
    """
    真正的業務邏輯：把包裹標記完成、通知機器人關門返航、推播通知。
    不依賴FastAPI路由，可以被 /docs 的API呼叫，也可以被 handle_postback 直接呼叫。
    回傳一個dict，裡面標明成功與否，呼叫的地方自己決定怎麼處理。
    """
    db = SessionLocal()
    try:
        package = db.query(Package).filter(Package.id == package_id).first()
        if not package:
            return {"ok": False, "detail": "找不到這筆包裹"}

        if package.status != "arrived":
            return {"ok": False, "detail": f"這筆包裹目前狀態是 {package.status}，不是可以完成取貨的狀態"}

        package.status = "completed"
        db.commit()

        try:
            requests.post(
                f"{settings.ROBOT_API_BASE_URL}/api/packages/{package_id}/complete",
                timeout=5,
            )
        except requests.exceptions.RequestException as e:
            print(f"[錯誤] 呼叫機器人關門返航失敗: {e}")

        for line_user_id in get_recipients(db, package_id):
            push_status_update(line_user_id, "取貨完成，感謝使用！")

        return {"ok": True, "package_id": package_id, "new_status": "completed"}
    finally:
        db.close()


@app.post("/packages/{package_id}/complete")
async def pickup_complete(package_id: str):
    """API路由，給/docs測試或未來Dashboard呼叫用，內部直接轉呼叫上面的邏輯"""
    result = complete_pickup(package_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["detail"])
    return {"status": "ok", "package_id": result["package_id"], "new_status": result["new_status"]}

def process_door_queue():
    """檢查 pickup_now 但尚未分配到艙門的包裹（=排隊中），嘗試重新分配"""
    db = SessionLocal()
    try:
        waiting_packages = (
            db.query(Package)
            .filter(Package.status == "pickup_now", Package.door_id.is_(None))
            .order_by(Package.updated_at)
            .all()
        )
        for package in waiting_packages:
            if try_assign_door(str(package.id), db):
                print(f"[排隊處理] package_id={package.id} 已分配到艙門")
    finally:
        db.close()

# ========== 階段3.7 逾時自動退回 ==========

def check_pickup_timeout():
    """檢查arrived狀態超過10分鐘還沒完成取貨的包裹，自動觸發退回"""
    from datetime import timedelta

    db = SessionLocal()
    try:
        timeout_threshold = datetime.utcnow() - timedelta(minutes=10)
        overdue_packages = (
            db.query(Package)
            .filter(Package.status == "arrived", Package.arrived_at <= timeout_threshold)
            .all()
        )
        for package in overdue_packages:
            package.status = "returned_timeout"
            for line_user_id in get_recipients(db, str(package.id)):
                push_status_update(line_user_id, "逾時未取，包裹將退回管理室")
            print(f"[逾時自動退回] package_id={package.id}")
        db.commit()
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(check_pickup_timeout, "interval", minutes=1)
scheduler.start()


# ========== 階段3.7 機器人真正返回管理室 ==========

@app.post("/packages/{package_id}/returned")
async def robot_returned(package_id: str, db: Session = Depends(get_db)):
    """
    機器人實際回到管理室時，由送貨機器人模組呼叫。
    不通知住戶（退回當下已經通知過了），只留紀錄給管理員後台知道。
    """
    package = db.query(Package).filter(Package.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")

    if package.status not in ("returned_cancelled", "returned_timeout"):
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是退回中的狀態",
        )

    print(f"[機器人已返回管理室] package_id={package.id}, status={package.status}")
    # TODO(階段3.9): 之後透過SSE通知Dashboard，目前先只記錄log

    return {"status": "ok", "package_id": str(package.id)}

# ========== QR Code 掃描 LIFF ==========

@app.get("/liff/scan", response_class=HTMLResponse)
async def liff_scan_page():
    html = LIFF_SCAN_HTML.replace("__LIFF_ID__", settings.LIFF_ID)
    return HTMLResponse(content=html)


LIFF_SCAN_HTML = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>掃描取貨</title>
  <script charset="utf-8" src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
  <style>
    body { font-family: sans-serif; padding: 20px; text-align: center; }
    button { width: 100%; padding: 14px; font-size: 18px; background: #06C755; color: white; border: none; border-radius: 6px; margin-top: 20px; }
    #message { margin-top: 16px; font-weight: bold; }
  </style>
</head>
<body>
  <h2>掃描機器人上的 QR Code</h2>
  <p>請對準機器人螢幕上顯示的 QR Code 進行掃描</p>
  <button onclick="startScan()">開啟相機掃描</button>
  <div id="message"></div>

  <script>
    const LIFF_ID = "__LIFF_ID__";
    let packageId = null;

    function getPackageIdFromUrl() {
      const params = new URLSearchParams(window.location.search);
      return params.get("package_id");
    }

    async function initLiff() {
      await liff.init({ liffId: LIFF_ID });
      if (!liff.isLoggedIn()) {
        liff.login();
        return;
      }
      packageId = getPackageIdFromUrl();
      if (!packageId) {
        document.getElementById("message").textContent = "缺少包裹資訊，請從LINE通知重新進入";
      }
    }

    async function startScan() {
        const messageEl = document.getElementById("message");
        try {
            const result = await liff.scanCodeV2();
            const scannedContent = result.value;
            const idToken = liff.getIDToken();

            const response = await fetch(`/packages/${packageId}/pickup-complete`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scanned_content: scannedContent, id_token: idToken }),
            });

            if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "驗證失敗");
            }

            messageEl.style.color = "green";
            messageEl.textContent = "驗證成功！艙門已開啟，請取出您的包裹。";
        } catch (e) {
            messageEl.style.color = "red";
            messageEl.textContent = "掃描失敗：" + e.message;
        }
        }

    initLiff();
  </script>
</body>
</html>
"""

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_page():
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML)


ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>FlashBot Dashboard</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif;
    background: #f5f5f5; margin: 0; padding: 20px; color: #222; }
  h1 { color: #E2231A; font-size: 22px; margin-bottom: 20px; }
  h2 { font-size: 16px; margin: 0 0 12px 0; color: #333; }
  .card { background: white; border-radius: 8px; padding: 16px; margin-bottom: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
  select, button { padding: 8px 12px; font-size: 14px; border-radius: 6px;
    border: 1px solid #ccc; margin-right: 8px; margin-bottom: 8px; }
  button { background: #E2231A; color: white; border: none; cursor: pointer; }
  button:hover { background: #c41c14; }
  button.secondary { background: white; color: #E2231A; border: 1px solid #E2231A; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; }
  th { color: #888; font-weight: normal; }
  .status-badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #eee; }
  .status-pickup_now { background: #fff3cd; color: #856404; }
  .status-delivering { background: #cce5ff; color: #004085; }
  .status-arrived { background: #d4edda; color: #155724; }
  .status-completed { background: #e2e3e5; color: #383d41; }
  #createMsg { margin-top: 8px; font-size: 14px; }
  .robot-info { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 16px; }
  .robot-info div { font-size: 14px; }
  .robot-info b { display: block; color: #888; font-size: 12px; font-weight: normal; }
  .doors { display: flex; gap: 12px; }
  .door-box { flex: 1; padding: 12px; border-radius: 6px; text-align: center; font-size: 13px; }
  .door-EMPTY { background: #e9ecef; color: #666; }
  .door-ASSIGNED { background: #fff3cd; color: #856404; }
  .door-FULL { background: #f8d7da; color: #721c24; }
</style>
</head>
<body>

<h1> FlashBot Dashboard</h1>

<div class="card">
  <h2>建立包裹</h2>
  <select id="unitSelect"><option value="">請選擇門牌</option></select>
  <select id="nameSelect"><option value="">請先選擇門牌</option></select>
  <button onclick="createPackage()">建立包裹並通知</button>
  <div id="createMsg"></div>
</div>

<div class="card">
  <h2>機器人狀態 <button class="secondary" onclick="loadRobotStatus()">重新整理</button></h2>
  <div id="robotInfo" class="robot-info">載入中...</div>
  <div id="doorInfo" class="doors"></div>
</div>

<div class="card">
  <h2>包裹清單 <button class="secondary" onclick="loadPackages()">重新整理</button></h2>
  <table>
    <thead><tr><th>門牌</th><th>狀態</th><th>艙門</th><th>建立時間</th><th>操作</th></tr></thead>
    <tbody id="packageTableBody"><tr><td colspan="5">載入中...</td></tr></tbody>
  </table>
</div>

<script>
let bindingsData = [];

async function loadBindings() {
  const resp = await fetch('/admin/bindings');
  bindingsData = await resp.json();
  const units = [...new Set(bindingsData.map(b => b.unit))];
  const unitSelect = document.getElementById('unitSelect');
  unitSelect.innerHTML = '<option value="">請選擇門牌</option>' +
    units.map(u => `<option value="${u}">${u}</option>`).join('');
}

function updateNameOptions() {
  const unit = document.getElementById('unitSelect').value;
  const nameSelect = document.getElementById('nameSelect');
  const names = bindingsData.filter(b => b.unit === unit);
  nameSelect.innerHTML = '<option value="">請選擇收件人</option>' +
    names.map(b => `<option value="${b.name}">${b.name}</option>`).join('');
}

document.getElementById('unitSelect').addEventListener('change', updateNameOptions);

async function createPackage() {
  const unit = document.getElementById('unitSelect').value;
  const recipient_name = document.getElementById('nameSelect').value;
  const msgEl = document.getElementById('createMsg');
  if (!unit) { msgEl.style.color = 'red'; msgEl.textContent = '請先選擇門牌'; return; }
  if (!recipient_name) { msgEl.style.color = 'red'; msgEl.textContent = '請選擇收件人'; return; }

  try {
    const resp = await fetch('/packages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ unit, recipient_name: recipient_name || null }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || '建立失敗');
    msgEl.style.color = 'green';
    msgEl.textContent = `建立成功，已通知 ${data.notified_count} 位住戶`;
    loadPackages();
  } catch (e) {
    msgEl.style.color = 'red';
    msgEl.textContent = '錯誤：' + e.message;
  }
}

const STATUS_LABEL = {
  pending: '待處理', later: '稍後再取', pickup_now: '待派送',
  delivering: '配送中', arrived: '已抵達', completed: '已完成',
  returned_cancelled: '已退回（取消）', returned_timeout: '已退回（逾時）',
};

async function loadPackages() {
  const resp = await fetch('/admin/packages');
  const packages = await resp.json();
  const tbody = document.getElementById('packageTableBody');
  if (packages.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5">目前沒有包裹</td></tr>';
    return;
  }
  tbody.innerHTML = packages.map(p => {
    const label = STATUS_LABEL[p.status] || p.status;
    const createdAt = p.created_at ? p.created_at.replace('T', ' ').slice(0, 16) : '-';
    const door = p.door_id || '尚未分配';
    const action = p.status === 'pickup_now'
      ? `<button onclick="dispatchPackage('${p.id}')">確認派送</button>` : '-';
    return `<tr>
      <td>${p.unit}</td>
      <td><span class="status-badge status-${p.status}">${label}</span></td>
      <td>${door}</td><td>${createdAt}</td><td>${action}</td>
    </tr>`;
  }).join('');
}

async function dispatchPackage(packageId) {
  try {
    const resp = await fetch(`/packages/${packageId}/stored`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || '派送失敗');
    loadPackages();
  } catch (e) {
    alert('派送失敗：' + e.message);
  }
}

async function loadRobotStatus() {
  const infoEl = document.getElementById('robotInfo');
  const doorEl = document.getElementById('doorInfo');
  infoEl.textContent = '載入中...';
  try {
    const resp = await fetch('/admin/robot-status');
    const data = await resp.json();
    if (data.status === 'error') {
      infoEl.innerHTML = `<span style="color:red">${data.detail}</span>`;
      doorEl.innerHTML = '';
      return;
    }
    const robot = data.data.robot_status;
    const doors = data.data.door_states;
    infoEl.innerHTML = `
      <div><b>狀態</b>${robot.state}</div>
      <div><b>目前位置</b>${robot.current_location || '未知'}</div>
      <div><b>電量</b>${robot.battery_level}%</div>`;
    doorEl.innerHTML = doors.map(d => `
      <div class="door-box door-${d.status}">
        <div>${d.door_number}</div><div>${d.status}</div>
        ${d.package_id ? `<div style="font-size:11px">${d.package_id.slice(0,8)}...</div>` : ''}
      </div>`).join('');
  } catch (e) {
    infoEl.innerHTML = `<span style="color:red">無法載入：${e.message}</span>`;
  }
}

loadBindings();
loadPackages();
loadRobotStatus();
setInterval(loadPackages, 15000);
setInterval(loadRobotStatus, 15000);
</script>
</body>
</html>
"""

