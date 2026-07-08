"""
LINE 後端 - 主程式入口
"""
from datetime import datetime
from typing import Optional

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

app = FastAPI(title="智連櫃社區 AMR 配送系統 - LINE 後端")

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
        msg = "已開啟：包裹到貨只會通知您本人" if enable else "已關閉：包裹到貨會通知同門牌所有人"
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
            reply_text(reply_token, "目前沒有待取的包裹")
        else:
            reply_later_packages(reply_token, packages)
    finally:
        db.close()

def get_recipients(db: Session, package_id: str) -> list:
    """查詢這筆包裹當初通知過的所有LINE User ID"""
    rows = db.query(PackageRecipient).filter(PackageRecipient.package_id == package_id).all()
    return [row.line_user_id for row in rows]

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
            reply_text(reply_token, "已收到您的取貨請求，管理員正在為您準備包裹！")

        elif action == "LATER":
            package.status = "later"
            db.commit()
            reply_text(reply_token, "好的，您可以之後透過選單「我的包裹」隨時回來取貨")

        elif action == "PICKUP_DONE":
            db.close()
            import requests as _requests
            _requests.post(f"http://localhost:8000/packages/{package_id}/complete")
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
        db.add(PackageRecipient(package_id=package.id, line_user_id=binding.line_user_id))
    db.commit()

    for binding in targets:
        push_arrival_notification(binding.line_user_id, str(package.id), payload.unit)

    return {"status": "ok", "package_id": str(package.id), "notified_count": len(targets)}

# ========== 階段3.4 管理員確認出發（放貨+派工合併） ==========

@app.post("/packages/{package_id}/stored")
async def confirm_dispatch(package_id: str, db: Session = Depends(get_db)):
    package = db.query(Package).filter(Package.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")

    if package.status != "pickup_now":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是可以派工的狀態",
        )

    package.status = "delivering"
    db.commit()

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

    for line_user_id in get_recipients(db, package_id):
        push_arrived_notification(line_user_id, str(package.id))

    return {"status": "ok", "package_id": str(package.id), "new_status": package.status}


# ========== 階段3.6 住戶取貨（兩階段） ==========

@app.post("/packages/{package_id}/pickup-complete")
async def pickup_verify(package_id: str, db: Session = Depends(get_db)):
    """
    掃碼驗證通過、開門這一步。
    目前簡化：先不做真正的QR Code比對，等機器人模組完成後再補上驗證邏輯（見階段3.6.1）。
    """
    package = db.query(Package).filter(Package.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")

    if package.status != "arrived":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是等待取貨的狀態",
        )

    # TODO(階段3.6.1): 這裡之後要驗證掃到的QR Code內容是否合法、有沒有過期
    for line_user_id in get_recipients(db, package_id):
        push_pickup_complete_button(line_user_id, str(package.id))

    return {"status": "ok", "message": "驗證通過，艙門已開啟"}


@app.post("/packages/{package_id}/complete")
async def pickup_complete(package_id: str, db: Session = Depends(get_db)):
    """用戶按下取貨完成，機器人關門返航"""
    package = db.query(Package).filter(Package.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")

    if package.status != "arrived":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是可以完成取貨的狀態",
        )

    package.status = "completed"
    db.commit()

    for line_user_id in get_recipients(db, package_id):
        push_status_update(line_user_id, "取貨完成，感謝使用！")

    return {"status": "ok", "package_id": str(package.id), "new_status": package.status}


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
                push_status_update(line_user_id, "⏰ 已逾時未取，包裹已退回管理室")
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

        const response = await fetch(`/packages/${packageId}/pickup-complete`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scanned_content: scannedContent }),
        });

        if (!response.ok) {
          const err = await response.json();
          throw new Error(err.detail || "驗證失敗");
        }

        messageEl.style.color = "green";
        messageEl.textContent = "驗證成功！艙門已開啟，請取出您的包裹，可以關閉此頁面了。";
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

class PickupVerifyRequest(BaseModel):
    scanned_content: Optional[str] = None


@app.post("/packages/{package_id}/pickup-complete")
async def pickup_verify(package_id: str, payload: PickupVerifyRequest = None, db: Session = Depends(get_db)):
    """
    掃碼驗證通過、開門這一步。
    目前簡化：先不做真正的QR Code比對，等機器人模組完成後再補上驗證邏輯（見階段3.6.1）。
    """
    package = db.query(Package).filter(Package.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")

    if package.status != "arrived":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是等待取貨的狀態",
        )

    # TODO(階段3.6.1): 這裡之後要驗證 payload.scanned_content 是否合法、有沒有過期
    print(f"[掃碼內容] package_id={package_id}, scanned_content={payload.scanned_content if payload else None}")

    for line_user_id in get_recipients(db, package_id):
        push_pickup_complete_button(line_user_id, str(package.id))

    return {"status": "ok", "message": "驗證通過，艙門已開啟"}