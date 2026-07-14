"""
LINE 後端 - 主程式入口
"""
from datetime import datetime, timedelta
from typing import Optional
import uuid
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
from app.models import LineBinding, Package, PackageRecipient, TaskLog, now_taipei
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
            .filter(
                Package.line_user_id == line_user_id,
                Package.status.notin_(["completed", "returned_cancelled", "returned_timeout", "voided"]),
            )
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


def parse_package_uuid(package_id: str):
    """
    packages.id 欄位是UUID型別，如果package_id不是合法UUID格式，
    直接拿去查DB會讓PostgreSQL在型別轉換時噴錯（invalid input syntax for type uuid），
    這個錯誤沒被攔截的話會變成500而不是乾淨的404。
    這裡先在Python端驗證格式，不合法就回傳None，不要讓這種輸入碰到DB。
    """
    try:
        return uuid.UUID(package_id)
    except (ValueError, AttributeError, TypeError):
        return None


def get_package_or_404(db: Session, package_id: str) -> Package:
    """FastAPI路由共用：查不到、或package_id格式本身就不合法，統一回傳乾淨的404"""
    parsed = parse_package_uuid(package_id)
    if parsed is None:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")
    package = db.query(Package).filter(Package.id == parsed).first()
    if not package:
        raise HTTPException(status_code=404, detail="找不到這筆包裹")
    return package


def log_event(db: Session, event_type: str, detail: str = None, package_id=None, level: str = "info"):
    """
    記錄任務事件，給每日報表用。
    這裡刻意再開一個獨立的DB session來寫log，不共用呼叫端傳進來的db session，
    這樣就算呼叫端那個session之後rollback（例如外層流程failed），log紀錄本身還是保得住，
    不會因為主要交易失敗就連事件紀錄也一起消失。
    """
    print(f"[{level}][{event_type}] package_id={package_id} {detail or ''}")
    log_db = SessionLocal()
    try:
        entry = TaskLog(
            package_id=package_id,
            event_type=event_type,
            level=level,
            detail=detail,
        )
        log_db.add(entry)
        log_db.commit()
    except Exception as e:
        print(f"[錯誤] 寫入task_log失敗: {e}")
    finally:
        log_db.close()


def call_robot_api(method: str, path: str, json: dict = None, timeout: int = 5, retries: int = 0):
    """
    統一呼叫機器人 API。
    回傳 (ok, response, error_message_or_None)。
    - 成功：response是200的requests.Response
    - 失敗但有收到回應（例如404/500）：response是那個非200的requests.Response，呼叫端可以自己檢查
      status_code / text 判斷要怎麼處理（例如判斷是不是「已經完成」這種可以視為成功的特定錯誤）
    - 失敗且完全連不上（timeout/連線被拒）：response是None
    retries=1 代表失敗後再試一次（間隔0秒，機器人API本身若是暫時性錯誤通常立即重試就有機會成功）。
    呼叫端要自己決定：ok=False時是要中止流程並回錯誤，還是繼續往下走但要大聲記錄。
    """
    url = f"{settings.ROBOT_API_BASE_URL}{path}"
    last_error = None
    last_resp = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, json=json, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_error = f"連線問題: {e}"
            continue
        last_resp = resp
        if resp.status_code == 200:
            return True, resp, None
        last_error = f"HTTP {resp.status_code}: {resp.text}"
    return False, last_resp, last_error

def try_assign_door(package_id: str, db: Session) -> bool:
    """嘗試跟機器人要一個空艙門，成功會把door_id存進package並回傳True"""
    ok, resp, error = call_robot_api("POST", "/api/doors/assign", json={"id": package_id})
    if not ok:
        log_event(db, "door_assign_failed", detail=error, package_id=package_id, level="error")
        return False

    door_number = resp.json().get("door_number")
    package = db.query(Package).filter(Package.id == package_id).first()
    package.door_id = door_number
    db.commit()
    log_event(db, "door_assigned", detail=f"door_number={door_number}", package_id=package_id)

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
        parsed = parse_package_uuid(package_id)
        if parsed is None:
            reply_text(reply_token, "找不到這筆包裹資料，請聯繫管理員")
            return
        package = db.query(Package).filter(Package.id == parsed).first()
        if not package:
            reply_text(reply_token, "找不到這筆包裹資料，請聯繫管理員")
            return

        if action == "PICKUP_NOW":
            package.status = "pickup_now"
            db.commit()

            assigned = try_assign_door(package_id, db)
            if assigned:
                reply_text(reply_token, "已收到您的取貨請求，管理員正在為您準備包裹！")
            else:
                # TODO(待辦-1): process_door_queue 還沒排程，這裡失敗後目前沒有自動重試機制
                reply_text(
                    reply_token,
                    "已收到您的取貨請求，但目前置物櫃暫時無法安排，我們會盡快處理，請稍候",
                )

        elif action == "REJECT":
            if package.status == "pending":
                package.status = "voided"
                db.commit()
                log_event(db, "rejected", detail="住戶到貨通知直接按不收，包裹作廢", package_id=package.id)
                reply_text(reply_token, "已為您取消這次收件，包裹不會派送，將維持在管理室")

                triggered_binding = db.query(LineBinding).filter(
                    LineBinding.line_user_id == triggered_by
                ).first()
                triggered_name = triggered_binding.name if triggered_binding else "同門牌住戶"

                for line_user_id in get_recipients(db, package_id):
                    if line_user_id != triggered_by:
                        push_status_update(
                            line_user_id,
                            f"{triggered_name} 已取消這次收件，包裹不會派送",
                        )
            else:
                reply_text(reply_token, "這筆包裹目前無法取消收件（可能已經開始派送）")

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

        elif action == "REJECT_AT_DOOR":
            if package.status == "arrived":
                package.status = "rejected_at_door"
                db.commit()
                log_event(db, "rejected_at_door", detail="住戶在機器人抵達後按拒收", package_id=package.id)
                reply_text(reply_token, "已為您取消取貨，包裹將由機器人送回管理室，請聯繫管理員協助處理")

                triggered_binding = db.query(LineBinding).filter(
                    LineBinding.line_user_id == triggered_by
                ).first()
                triggered_name = triggered_binding.name if triggered_binding else "同門牌住戶"

                for line_user_id in get_recipients(db, package_id):
                    if line_user_id != triggered_by:
                        push_status_update(
                            line_user_id,
                            f"{triggered_name} 已拒收，包裹將由機器人送回管理室",
                        )

                # 機器人動作1：關門 + 關閉任務畫面（包裹此時還在艙門內，機器人還沒開始移動）
                ok, resp, error = call_robot_api(
                    "POST", f"/api/packages/{package_id}/cancel", retries=1
                )
                if not ok:
                    log_event(db, "cancel_task_failed", detail=error, package_id=package.id, level="error")

                import time
                time.sleep(10)

                # 機器人動作2：觸發機器人真正把包裹送回管理室，抵達後機器人會自動釋放（開啟）艙門
                ok, resp, error = call_robot_api(
                    "POST", "/api/packages/return", json={"package_id": package_id}, retries=1
                )
                if not ok:
                    log_event(db, "return_failed", detail=error, package_id=package.id, level="error")
                else:
                    log_event(db, "returned_and_opened", package_id=package.id)
            else:
                reply_text(reply_token, "這筆包裹目前無法拒收")
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

    log_event(
        db, "created",
        detail=f"unit={payload.unit} notified_count={len(targets)}",
        package_id=package.id,
    )

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
            "door_closed_at": p.door_closed_at.isoformat() if p.door_closed_at else None,
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


@app.get("/admin/reports/daily")
async def admin_daily_report(date: str, db: Session = Depends(get_db)):
    """
    每日報表：某一天的包裹狀態統計 + 任務時間軸。
    date格式：YYYY-MM-DD（依台灣當地日期，因為DB裡的時間本來就是存台灣時間，不用另外轉時區）。
    """
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date格式錯誤，需要YYYY-MM-DD")

    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # 包裹狀態統計：當天「有更新」的包裹（建立、狀態轉換都會更新updated_at）
    packages_today = (
        db.query(Package)
        .filter(Package.updated_at >= day_start, Package.updated_at < day_end)
        .order_by(Package.updated_at)
        .all()
    )

    status_summary = {}
    for p in packages_today:
        status_summary[p.status] = status_summary.get(p.status, 0) + 1

    # 任務時間軸
    logs_today = (
        db.query(TaskLog)
        .filter(TaskLog.created_at >= day_start, TaskLog.created_at < day_end)
        .order_by(TaskLog.created_at)
        .all()
    )

    return {
        "date": date,
        "package_status_summary": status_summary,
        "package_count": len(packages_today),
        "packages": [
            {
                "id": str(p.id),
                "unit": p.unit,
                "status": p.status,
                "door_id": p.door_id,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in packages_today
        ],
        "task_logs": [
            {
                "id": str(log.id),
                "package_id": str(log.package_id) if log.package_id else None,
                "event_type": log.event_type,
                "level": log.level,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs_today
        ],
        "log_count": len(logs_today),
    }

# ========== 階段3.4 管理員確認出發（放貨+派工合併） ==========

@app.post("/packages/{package_id}/stored")
async def confirm_dispatch(package_id: str, payload: ConfirmDispatchRequest = None, db: Session = Depends(get_db)):
    package = get_package_or_404(db, package_id)

    if package.status != "pickup_now":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是可以派工的狀態",
        )

    ok, resp, error = call_robot_api("POST", "/api/doors/load", json={"id": package_id}, retries=1)
    if not ok:
        log_event(db, "dispatch_failed", detail=f"裝載艙門失敗: {error}", package_id=package.id, level="error")
        raise HTTPException(status_code=502, detail="機器人裝載艙門失敗，請確認艙門與連線狀態後再試")

    package.status = "delivering"
    db.commit()

    ok, resp, error = call_robot_api(
        "POST", "/api/robot/dispatch",
        json={"unit": package.unit, "package_id": package_id},
        retries=1,
    )
    if not ok:
        # 艙門已裝載成功，物理上包裹已經在機器人上了，狀態不往回改；
        # 但派工這步確實沒成功，機器人不會真的出發，需要人工介入補派工
        log_event(
            db, "dispatch_failed",
            detail=f"艙門裝載成功但呼叫機器人出發失敗: {error}",
            package_id=package.id, level="error",
        )
        raise HTTPException(
            status_code=502,
            detail="裝載艙門成功，但呼叫機器人出發失敗，包裹目前卡在裝載完成、尚未出發的狀態，請聯繫管理員手動派工",
        )

    log_event(db, "dispatched", detail=f"unit={package.unit}", package_id=package.id)

    for line_user_id in get_recipients(db, package_id):
        push_status_update(line_user_id, "機器人已出發，包裹正在配送中，請稍候")

    return {"status": "ok", "package_id": str(package.id), "new_status": package.status}

# ========== 階段3.5 機器人抵達（暫時用手動呼叫模擬） ==========

@app.post("/packages/{package_id}/arrived")
async def robot_arrived(package_id: str, db: Session = Depends(get_db)):
    package = get_package_or_404(db, package_id)

    if package.status != "delivering":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是配送中的狀態",
        )

    package.status = "arrived"
    package.arrived_at = now_taipei()
    db.commit()

    # 之前這裡有呼叫機器人 /show-qr 顯示QR Code，但確認過機器人是自己內部處理顯示QR的，
    # 這支路徑在機器人服務上根本不存在（打了一定收到404），拿掉不必要的呼叫
    log_event(db, "arrived", package_id=package.id)

    for line_user_id in get_recipients(db, package_id):
        push_arrived_notification(line_user_id, str(package.id))

    return {"status": "ok", "package_id": str(package.id), "new_status": package.status}


@app.post("/packages/{package_id}/pickup-complete")
async def pickup_verify(package_id: str, payload: PickupVerifyRequest = None, db: Session = Depends(get_db)):
    package = get_package_or_404(db, package_id)

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

    ok, resp, error = call_robot_api(
        "POST", f"/api/packages/{package_id}/pickup-complete", retries=1
    )
    if not ok:
        log_event(db, "pickup_open_failed", detail=error, package_id=package.id, level="error")
        raise HTTPException(status_code=502, detail="機器人開門失敗，請聯繫管理員協助取件")

    log_event(db, "pickup_opened", detail=f"scanned_by={scanning_user_id}", package_id=package.id)

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
        parsed = parse_package_uuid(package_id)
        if parsed is None:
            return {"ok": False, "detail": "找不到這筆包裹"}

        package = db.query(Package).filter(Package.id == parsed).first()
        if not package:
            return {"ok": False, "detail": "找不到這筆包裹"}

        if package.status != "arrived":
            return {"ok": False, "detail": f"這筆包裹目前狀態是 {package.status}，不是可以完成取貨的狀態"}

        package.status = "completed"
        db.commit()

        ok, resp, error = call_robot_api(
            "POST", f"/api/packages/{package_id}/complete", retries=1
        )
        # 「Package not found in any door」代表機器人自己已經完成關門返航、艙門已經清空了，
        # 我們這次呼叫只是晚了一步、多餘的，不是真的失敗——不要把這種情況記成error，
        # 不然每次都會誤報，log裡全是雜訊，真正需要人工介入的失敗反而會被淹沒
        already_returned = (
            resp is not None
            and resp.status_code == 404
            and "not found in any door" in (resp.text or "").lower()
        )

        if ok or already_returned:
            log_event(
                db, "completed",
                detail=None if ok else "機器人已自行關門返航（呼叫時艙門已清空，視為已完成）",
                package_id=package.id,
            )
        else:
            # 使用者已經拿到包裹了（門在pickup_verify那步就開過），這件事不能反悔；
            # 但機器人關門返航確實失敗，可能卡在原地沒有回管理室，需要人工去確認機器人實際狀態
            log_event(
                db, "complete_failed",
                detail=f"機器人關門返航失敗，請確認是否卡在原地: {error}",
                package_id=package.id, level="error",
            )

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
        timeout_threshold = now_taipei() - timedelta(minutes=10)
        overdue_packages = (
            db.query(Package)
            .filter(Package.status == "arrived", Package.arrived_at <= timeout_threshold)
            .all()
        )
        for package in overdue_packages:
            package.status = "returned_timeout"
            for line_user_id in get_recipients(db, str(package.id)):
                push_status_update(line_user_id, "逾時未取，包裹將退回管理室")
            log_event(db, "returned_timeout", package_id=package.id)
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
    package = get_package_or_404(db, package_id)

    if package.status not in ("returned_cancelled", "returned_timeout"):
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是退回中的狀態",
        )

    log_event(db, "returned", detail=f"status={package.status}", package_id=package.id)
    # TODO(階段3.9): 之後透過SSE通知Dashboard，目前先只記錄log

    return {"status": "ok", "package_id": str(package.id)}


# ========== 拒收流程：管理員取出包裹後按關門 ==========

@app.post("/packages/{package_id}/close-door")
async def close_door_after_reject(package_id: str, db: Session = Depends(get_db)):
    """
    拒收流程專用：機器人送回管理室、艙門已經開啟讓管理員取出包裹之後，
    管理員在Dashboard按「關門」，通知機器人把艙門關起來。
    """
    package = get_package_or_404(db, package_id)

    if package.status != "rejected_at_door":
        raise HTTPException(
            status_code=400,
            detail=f"這筆包裹目前狀態是 {package.status}，不是拒收待關門的狀態",
        )

    if package.door_closed_at is not None:
        raise HTTPException(status_code=400, detail="這筆包裹的艙門已經關過了")

    ok, resp, error = call_robot_api(
        "POST", "/api/doors/return-complete", json={"package_id": package_id, "door_id": package.door_id}, retries=1
    )
    if not ok:
        log_event(db, "close_door_failed", detail=error, package_id=package.id, level="error")
        raise HTTPException(status_code=502, detail="呼叫機器人關門失敗，請確認機器人狀態後再試")

    package.door_closed_at = now_taipei()
    db.commit()
    log_event(db, "door_closed", package_id=package.id)

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
  <button id="scanBtn" onclick="startScan()">開啟相機掃描</button>
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
        const btn = document.getElementById("scanBtn");
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
            // 掃描成功、門已經開了，不需要再掃第二次，按鈕改成完成狀態並鎖住
            btn.textContent = "掃描完成";
            btn.disabled = true;
            btn.style.background = "#999";
        } catch (e) {
            messageEl.style.color = "red";
            messageEl.textContent = "掃描失敗：" + e.message;
            // 失敗要讓使用者能重新掃，按鈕維持原本可點的「開啟相機掃描」
            btn.textContent = "開啟相機掃描";
            btn.disabled = false;
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
  button.secondary:hover { background: #e9e9e9; }
  button:disabled { opacity: 0.6; cursor: default; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; }
  th { color: #888; font-weight: normal; }
  .status-badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #eee; }
  .status-pickup_now { background: #fff3cd; color: #856404; }
  .status-delivering { background: #cce5ff; color: #004085; }
  .status-arrived { background: #d4edda; color: #155724; }
  .status-completed { background: #e2e3e5; color: #383d41; }
  .status-voided { background: #f8d7da; color: #721c24; }
  .status-rejected_at_door { background: #dc3545; color: white; font-weight: bold; }
  .reject-alert { background: #dc3545; color: white; border-radius: 8px; padding: 14px 16px;
    margin-bottom: 20px; font-size: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.2); }
  .reject-alert b { display: block; font-size: 15px; margin-bottom: 6px; }
  .reject-alert ul { margin: 0; padding-left: 20px; }
  #createMsg { margin-top: 8px; font-size: 14px; }
  .robot-info { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 16px; }
  .robot-info div { font-size: 14px; text-align: center; }
  .robot-info b { display: block; color: #888; font-size: 12px; font-weight: normal; }
  .robot-status-header { display: flex; align-items: center; flex-wrap: wrap; gap: 20px; margin-bottom: 16px; }
  .robot-status-header h2 { margin: 0; white-space: nowrap; }
  .robot-status-header .robot-info { flex: 1; margin-bottom: 0; justify-content: space-evenly; }
  .robot-status-header button { margin-left: auto; margin-right: 0; margin-bottom: 0; flex-shrink: 0; }
  .card-header { display: flex; align-items: center; flex-wrap: wrap; gap: 20px; margin-bottom: 12px; }
  .card-header h2 { margin: 0; white-space: nowrap; }
  .card-header button { margin-left: auto; margin-right: 0; margin-bottom: 0; flex-shrink: 0; }
  .create-package-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  .create-package-selects { display: flex; flex: 1; align-items: center;
    padding: 0 16px; flex-wrap: nowrap; gap: 16px; min-width: 0; }
  .create-package-selects select { flex: 1 1 0; min-width: 0; width: auto; }
  .create-package-row button { flex-shrink: 0; margin-left: 0; margin-right: 0; }
  .doors { display: flex; gap: 12px; border-top: 0.5px solid #eee; padding-top: 16px; }
  .door-box { flex: 1; padding: 12px; border-radius: 6px; text-align: center; font-size: 13px; }
  .door-EMPTY { background: #e9ecef; color: #666; }
  .door-ASSIGNED { background: #fff3cd; color: #856404; }
  .door-FULL { background: #f8d7da; color: #721c24; }
</style>
</head>
<body>

<h1> FlashBot Dashboard
  <a href="/admin/reports" style="font-size:14px;font-weight:normal;color:#E2231A;margin-left:16px;">查看每日報表 →</a>
</h1>

<div class="card">
  <h2>建立包裹</h2>
  <div class="create-package-row">
    <div class="create-package-selects">
      <select id="unitSelect"><option value="">請選擇門牌</option></select>
      <select id="nameSelect"><option value="">請先選擇門牌</option></select>
    </div>
    <button id="createBtn" onclick="createPackage()">建立包裹並通知</button>
  </div>
  <div id="createMsg"></div>
</div>

<div class="card">
  <div class="robot-status-header">
    <h2>機器人狀態</h2>
    <div id="robotInfo" class="robot-info">載入中...</div>
    <button class="secondary" onclick="withButtonFeedback(this, loadRobotStatus)">重新整理</button>
  </div>
  <div id="doorInfo" class="doors"></div>
</div>

<div id="rejectAlert" class="reject-alert" style="display:none;"></div>

<div class="card">
  <div class="card-header">
    <h2>包裹清單</h2>
    <button class="secondary" onclick="withButtonFeedback(this, loadPackages)">重新整理</button>
  </div>
  <table>
    <thead><tr><th>門牌</th><th>狀態</th><th>艙門</th><th>建立時間</th><th>操作</th></tr></thead>
    <tbody id="packageTableBody"><tr><td colspan="5">載入中...</td></tr></tbody>
  </table>
</div>

<script>
let bindingsData = [];
let packagesById = {};

async function withButtonFeedback(button, fn) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = '更新中...';
  try {
    await fn();
  } finally {
    button.textContent = originalText;
    button.disabled = false;
  }
}

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
  const btn = document.getElementById('createBtn');
  if (!unit) { msgEl.style.color = 'red'; msgEl.textContent = '請先選擇門牌'; return; }
  if (!recipient_name) { msgEl.style.color = 'red'; msgEl.textContent = '請選擇收件人'; return; }

  btn.disabled = true;
  btn.textContent = '建立中...';
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
  } finally {
    btn.disabled = false;
    btn.textContent = '建立包裹並通知';
  }
}

const STATUS_LABEL = {
  pending: '待處理', pickup_now: '待派送',
  delivering: '配送中', arrived: '已抵達', completed: '已完成',
  returned_cancelled: '已退回（取消）', returned_timeout: '已退回（逾時）',
  voided: '不收（作廢）', rejected_at_door: '拒收（作廢）',
};

async function loadPackages() {
  const resp = await fetch('/admin/packages');
  const packages = await resp.json();
  packagesById = Object.fromEntries(packages.map(p => [p.id, p]));

  renderRejectAlert(packages);

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
      ? `<button onclick="dispatchPackage('${p.id}')">確認派送</button>`
      : (p.status === 'rejected_at_door' && !p.door_closed_at)
        ? `<button onclick="closeDoor('${p.id}')">關門</button>`
        : '-';
    return `<tr>
      <td>${p.unit}</td>
      <td><span class="status-badge status-${p.status}">${label}</span></td>
      <td>${door}</td><td>${createdAt}</td><td>${action}</td>
    </tr>`;
  }).join('');
}

function renderRejectAlert(packages) {
  const alertEl = document.getElementById('rejectAlert');
  // 住戶已拒收、機器人已送回，但管理員還沒按「關門」確認取出包裹的
  const pending = packages.filter(p => p.status === 'rejected_at_door' && !p.door_closed_at);

  if (pending.length === 0) {
    alertEl.style.display = 'none';
    alertEl.innerHTML = '';
    return;
  }

  alertEl.style.display = 'block';
  alertEl.innerHTML = `
    <b>⚠️ 有 ${pending.length} 筆包裹被拒收，機器人已送回管理室，請盡快取出包裹並按「關門」</b>
    <ul>
      ${pending.map(p => `<li>門牌：${p.unit}（艙門：${p.door_id || '未知'}）</li>`).join('')}
    </ul>
  `;
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

async function closeDoor(packageId) {
  try {
    const resp = await fetch(`/packages/${packageId}/close-door`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || '關門失敗');
    loadPackages();
  } catch (e) {
    alert('關門失敗：' + e.message);
  }
}

async function loadRobotStatus() {
  const infoEl = document.getElementById('robotInfo');
  const doorEl = document.getElementById('doorInfo');
  try {
    const resp = await fetch('/admin/robot-status');
    const data = await resp.json();
    if (data.status === 'error') {
      infoEl.innerHTML = `<span style="color:red">${data.detail}</span>`;
      doorEl.innerHTML = '';
      return;
    }
    const payload = data.data;
    const robot = payload.robot_status;
    const doors = payload.door_states;

    // 機器人API目前把真正即時的數值塞在 robot_status.sources.v1/v2.data 裡，
    // 外層的 battery_level / current_location 是機器人那邊還沒同步更新的舊欄位，
    // state則是掛在data這層，不在robot_status裡面。
    // 這裡照實際結構讀，並保留舊路徑當fallback，以後機器人那邊修好了也不用再改。
    const src = (robot.sources && (robot.sources.v1 || robot.sources.v2))
      ? (robot.sources.v1 || robot.sources.v2).data
      : null;

    const state = payload.state || robot.state || robot.move_state || '未知';
    const battery = src?.battery ?? robot.battery ?? robot.battery_level ?? null;
    const mapName = src?.map_name ? src.map_name.replace(/^\\d+#\\d+#/, '') : null;
    const location = robot.current_location
      || mapName
      || (src?.position ? `(${src.position.x.toFixed(1)}, ${src.position.y.toFixed(1)})` : null);

    infoEl.innerHTML = `
      <div><b>狀態</b>${state}</div>
      <div><b>目前位置</b>${location || '未知'}</div>
      <div><b>電量</b>${battery !== null ? battery + '%' : '未知'}</div>`;
    doorEl.innerHTML = doors.map(d => {
      const pkg = d.package_id ? packagesById[d.package_id] : null;
      // 正常情況顯示門牌；如果packagesById還沒抓到對應資料（例如剛載入頁面時兩個API還沒都回來），
      // 退回顯示package_id前8碼，之後下一次自動更新就會補正確
      const label = pkg ? pkg.unit : (d.package_id ? d.package_id.slice(0, 8) + '...' : '');
      return `
      <div class="door-box door-${d.status}">
        <div>${d.door_number}</div><div>${d.status}</div>
        ${label ? `<div style="font-size:11px">${label}</div>` : ''}
      </div>`;
    }).join('');
  } catch (e) {
    infoEl.innerHTML = `<span style="color:red">無法載入：${e.message}</span>`;
  }
}

loadBindings();
loadPackages();
loadRobotStatus();
setInterval(loadPackages, 15000);
setInterval(loadRobotStatus, 30000);
</script>
</body>
</html>
"""


@app.get("/admin/reports", response_class=HTMLResponse)
async def admin_reports_page():
    return HTMLResponse(content=ADMIN_REPORTS_HTML)


ADMIN_REPORTS_HTML = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>FlashBot 每日報表</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif;
    background: #f5f5f5; margin: 0; padding: 20px; color: #222; }
  h1 { color: #E2231A; font-size: 22px; margin-bottom: 20px; }
  h2 { font-size: 16px; margin: 0 0 12px 0; color: #333; }
  .card { background: white; border-radius: 8px; padding: 16px; margin-bottom: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
  input, button { padding: 8px 12px; font-size: 14px; border-radius: 6px;
    border: 1px solid #ccc; margin-right: 8px; }
  button { background: #E2231A; color: white; border: none; cursor: pointer; }
  button:hover { background: #c41c14; }
  button:disabled { opacity: 0.6; cursor: default; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; }
  th { color: #888; font-weight: normal; }
  .summary-grid { display: flex; gap: 16px; flex-wrap: wrap; }
  .summary-box { background: #f9f9f9; border-radius: 8px; padding: 12px 20px; text-align: center; min-width: 90px; }
  .summary-box b { display: block; font-size: 22px; color: #E2231A; }
  .summary-box span { font-size: 12px; color: #888; }
  .level-error { color: #c41c14; font-weight: bold; }
  .level-warning { color: #b58105; }
  .level-info { color: #333; }
  .empty-hint { color: #999; font-size: 14px; padding: 12px 0; }
</style>
</head>
<body>

<h1>FlashBot 每日報表
  <a href="/admin" style="font-size:14px;font-weight:normal;color:#E2231A;margin-left:16px;">← 回 Dashboard</a>
</h1>

<div class="card">
  <h2>選擇日期</h2>
  <input type="date" id="reportDate" />
  <button id="queryBtn" onclick="queryReport()">查詢</button>
</div>

<div class="card">
  <h2>包裹狀態統計</h2>
  <div id="summaryGrid" class="summary-grid"><div class="empty-hint">請選擇日期後查詢</div></div>
</div>

<div class="card">
  <h2>任務時間軸</h2>
  <table>
    <thead><tr><th>時間</th><th>等級</th><th>事件</th><th>包裹ID</th><th>內容</th></tr></thead>
    <tbody id="logTableBody"><tr><td colspan="5" class="empty-hint">請選擇日期後查詢</td></tr></tbody>
  </table>
</div>

<script>
// 預設帶入今天日期，方便直接查詢
const today = new Date();
const yyyy = today.getFullYear();
const mm = String(today.getMonth() + 1).padStart(2, '0');
const dd = String(today.getDate()).padStart(2, '0');
document.getElementById('reportDate').value = `${yyyy}-${mm}-${dd}`;

async function queryReport() {
  const btn = document.getElementById('queryBtn');
  const date = document.getElementById('reportDate').value;
  if (!date) { alert('請先選擇日期'); return; }

  btn.disabled = true;
  btn.textContent = '查詢中...';
  try {
    const resp = await fetch(`/admin/reports/daily?date=${date}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || '查詢失敗');

    renderSummary(data.package_status_summary, data.package_count);
    renderLogs(data.task_logs);
  } catch (e) {
    alert('查詢失敗：' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '查詢';
  }
}

function renderSummary(summary, total) {
  const el = document.getElementById('summaryGrid');
  const keys = Object.keys(summary || {});
  if (keys.length === 0) {
    el.innerHTML = '<div class="empty-hint">這天沒有包裹狀態異動紀錄</div>';
    return;
  }
  el.innerHTML = `
    <div class="summary-box"><b>${total}</b><span>當日異動總數</span></div>
    ${keys.map(k => `<div class="summary-box"><b>${summary[k]}</b><span>${k}</span></div>`).join('')}
  `;
}

function renderLogs(logs) {
  const el = document.getElementById('logTableBody');
  if (!logs || logs.length === 0) {
    el.innerHTML = '<tr><td colspan="5" class="empty-hint">這天沒有任務紀錄</td></tr>';
    return;
  }
  el.innerHTML = logs.map(log => `
    <tr>
      <td>${log.created_at ? log.created_at.replace('T', ' ').slice(0, 19) : '-'}</td>
      <td class="level-${log.level}">${log.level}</td>
      <td>${log.event_type}</td>
      <td>${log.package_id ? log.package_id.slice(0, 8) + '...' : '-'}</td>
      <td>${log.detail || ''}</td>
    </tr>
  `).join('');
}

queryReport();
</script>
</body>
</html>
"""