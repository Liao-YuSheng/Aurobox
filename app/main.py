"""
LINE 後端 - 主程式入口
"""
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import PlainTextResponse, HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, PostbackEvent, FollowEvent, UnfollowEvent

from app.config import settings
from app.db import get_db
from app.models import LineBinding
from app.line_verify import verify_liff_id_token
from app.line_messaging import reply_welcome_with_binding_link

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
            reply_welcome_with_binding_link(event.reply_token)

        elif isinstance(event, UnfollowEvent):
            print(f"[unfollow] 用戶封鎖/退出, user_id={event.source.user_id}")

        elif isinstance(event, PostbackEvent):
            print(f"[postback] user_id={event.source.user_id}, data={event.postback.data}")

        elif isinstance(event, MessageEvent):
            print(f"[message] user_id={event.source.user_id}")

    return PlainTextResponse("OK")


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