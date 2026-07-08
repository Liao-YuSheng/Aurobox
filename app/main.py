"""
LINE 後端 - 主程式入口
"""
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import PlainTextResponse, HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, PostbackEvent, FollowEvent, UnfollowEvent

from app.config import settings
from app.db import get_db
from app.models import LineBinding, Package
from app.line_verify import verify_liff_id_token
from app.line_messaging import (
    reply_welcome_with_binding_instructions,
    reply_text,
    push_arrival_notification,
    push_status_update,
    push_arrived_notification,
)
from app.db import SessionLocal
from apscheduler.schedulers.background import BackgroundScheduler
from linebot.v3.webhooks import TextMessageContent

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
            handle_postback(event.postback.data, event.reply_token)

        elif isinstance(event, MessageEvent):
            if isinstance(event.message, TextMessageContent):
                handle_text_binding(event.source.user_id, event.message.text.strip(), event.reply_token)
            else:
                print(f"[message] user_id={event.source.user_id} (非文字訊息)")

    return PlainTextResponse("OK")

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
def handle_postback(data: str, reply_token: str):
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
            db.close()  # 因為complete邏輯自己會開一個新的db連線處理，這裡先關掉避免衝突
            import requests as _requests
            _requests.post(f"http://localhost:8000/packages/{package_id}/complete")
            return
        
        elif action == "CANCEL_PICKUP":
            if package.status == "arrived":
                package.status = "returned_cancelled"
                db.commit()
                reply_text(reply_token, "已為您安排退回，包裹將送回管理室")
            else:
                reply_text(reply_token, "這筆包裹目前無法取消")

    finally:
        db.close()


# ========== 階段3.1 用戶綁定 ==========

@app.get("/liff/bind", response_class=HTMLResponse)
async def liff_bind_page():
    """LIFF 綁定表單頁面"""
    html = LIFF_BIND_HTML.replace("__LIFF_ID__", settings.LIFF_ID)
    return HTMLResponse(content=html)


class BindingRequest(BaseModel):
    idToken: str
    unit: str
    name: str


@app.post("/bindings")
async def create_binding(payload: BindingRequest, db: Session = Depends(get_db)):
    claims = verify_liff_id_token(payload.idToken, settings.LINE_LOGIN_CHANNEL_ID)
    line_user_id = claims.get("sub")
    if not line_user_id:
        raise HTTPException(status_code=401, detail="無法取得LINE使用者身份")

    existing = db.query(LineBinding).filter(LineBinding.line_user_id == line_user_id).first()
    if existing:
        existing.unit = payload.unit
        existing.name = payload.name
        existing.status = "active"
    else:
        existing = LineBinding(
            line_user_id=line_user_id,
            unit=payload.unit,
            name=payload.name,
        )
        db.add(existing)
    db.commit()

    return {"status": "ok", "line_user_id": line_user_id}

# ========== 階段3.2 到貨通知 ==========

class CreatePackageRequest(BaseModel):
    unit: str


@app.post("/packages")
async def create_package(payload: CreatePackageRequest, db: Session = Depends(get_db)):
    binding = (
        db.query(LineBinding)
        .filter(LineBinding.unit == payload.unit, LineBinding.status == "active")
        .first()
    )
    if not binding:
        raise HTTPException(
            status_code=404,
            detail=f"找不到門牌 {payload.unit} 的綁定資料，請先確認住戶已完成綁定",
        )

    package = Package(unit=payload.unit, line_user_id=binding.line_user_id, status="pending")
    db.add(package)
    db.commit()
    db.refresh(package)

    push_arrival_notification(binding.line_user_id, str(package.id), payload.unit)

    return {"status": "ok", "package_id": str(package.id)}

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

    push_status_update(package.line_user_id, "機器人已出發，包裹正在配送中，請稍候")

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

    push_arrived_notification(package.line_user_id, str(package.id))

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
    push_pickup_complete_button(package.line_user_id, str(package.id))

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

    push_status_update(package.line_user_id, "取貨完成，感謝使用！")

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
            push_status_update(package.line_user_id, "⏰ 已逾時未取，包裹已退回管理室")
            print(f"[逾時自動退回] package_id={package.id}")
        db.commit()
    finally:
        db.close()

scheduler = BackgroundScheduler()
scheduler.add_job(check_pickup_timeout, "interval", minutes=1)
scheduler.start()

LIFF_BIND_HTML = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>住戶綁定</title>
  <script charset="utf-8" src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
  <style>
    body { font-family: sans-serif; padding: 20px; }
    input { display: block; width: 100%; padding: 10px; margin: 10px 0; font-size: 16px; box-sizing: border-box; }
    button { width: 100%; padding: 12px; font-size: 16px; background: #06C755; color: white; border: none; border-radius: 6px; }
    #message { margin-top: 10px; font-weight: bold; }
  </style>
</head>
<body>
  <h2>住戶綁定</h2>
  <p>請輸入您的門牌與姓名，完成綁定後才能收到包裹通知。</p>
  <input type="text" id="unit" placeholder="門牌（例如：5F-2）" />
  <input type="text" id="name" placeholder="姓名" />
  <button onclick="submitBinding()">確認綁定</button>
  <div id="message"></div>

  <script>
    const LIFF_ID = "__LIFF_ID__";

    async function initLiff() {
      await liff.init({ liffId: LIFF_ID });
      if (!liff.isLoggedIn()) {
        liff.login();
      }
    }

    async function submitBinding() {
      const unit = document.getElementById("unit").value.trim();
      const name = document.getElementById("name").value.trim();
      const messageEl = document.getElementById("message");

      if (!unit || !name) {
        messageEl.style.color = "red";
        messageEl.textContent = "請填寫完整資料";
        return;
      }

      try {
        const idToken = liff.getIDToken();
        const response = await fetch("/bindings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ idToken, unit, name }),
        });

        if (!response.ok) {
          const err = await response.json();
          throw new Error(err.detail || "綁定失敗");
        }

        messageEl.style.color = "green";
        messageEl.textContent = "綁定成功！可以關閉此頁面了。";
      } catch (e) {
        messageEl.style.color = "red";
        messageEl.textContent = "綁定失敗：" + e.message;
      }
    }

    initLiff();
  </script>
</body>
</html>
"""