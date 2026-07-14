"""
封裝呼叫LINE Messaging API的邏輯
"""
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
    FlexMessage, FlexContainer, PushMessageRequest,
)
from app.config import settings

configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)


def push_arrival_notification(line_user_id: str, package_id: str, unit: str):
    """推播到貨通知，附「取貨」「不收」兩個按鈕"""
    contents = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "有新包裹送達", "weight": "bold", "size": "lg"}
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"門牌：{unit}", "wrap": True},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#029C4D",
                    "action": {
                        "type": "postback",
                        "label": "取貨",
                        "data": f"action=PICKUP_NOW&package_id={package_id}",
                    },
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "action": {
                        "type": "postback",
                        "label": "不收",
                        "data": f"action=REJECT&package_id={package_id}",
                    },
                },
            ],
        },
    }

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=line_user_id,
                messages=[FlexMessage(alt_text="有新包裹送達", contents=FlexContainer.from_dict(contents))],
            )
        )


def reply_text(reply_token: str, text: str):
    """回覆一則純文字訊息"""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )


def reply_welcome_with_binding_instructions(reply_token: str):
    """新用戶加好友時，改用純文字說明綁定方式"""
    text = (
        "歡迎加入！請在此聊天室輸入以下資料完成綁定：\n"
        "門牌 姓名\n"
        "例如：5F-1 王小明"
    )
    reply_text(reply_token, text)


def push_status_update(line_user_id: str, text: str):
    """推播單純的狀態更新文字訊息"""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=line_user_id,
                messages=[TextMessage(text=text)],
            )
        )


def push_arrived_notification(line_user_id: str, package_id: str):
    """機器人抵達時，推播提醒+開啟掃碼+拒收按鈕"""
    contents = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "機器人已抵達", "weight": "bold", "size": "lg"}
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "請於 10 分鐘內完成取貨", "wrap": True},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#029C4D",
                    "action": {
                        "type": "uri",
                        "label": "開啟相機掃碼",
                        "uri": f"https://liff.line.me/{settings.LIFF_ID}?package_id={package_id}",
                    },
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "action": {
                        "type": "postback",
                        "label": "拒收",
                        "data": f"action=REJECT_AT_DOOR&package_id={package_id}",
                    },
                },
            ],
        },
    }

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=line_user_id,
                messages=[FlexMessage(alt_text="機器人已抵達", contents=FlexContainer.from_dict(contents))],
            )
        )



def push_pickup_complete_button(line_user_id: str, package_id: str):
    """掃碼驗證通過、艙門已開啟後，推播讓用戶確認取貨完成的按鈕"""
    contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "艙門已開啟，請取出您的包裹", "wrap": True},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#029C4D",
                    "action": {
                        "type": "postback",
                        "label": "取貨完成",
                        "data": f"action=PICKUP_DONE&package_id={package_id}",
                    },
                },
            ],
        },
    }

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=line_user_id,
                messages=[FlexMessage(alt_text="請確認取貨完成", contents=FlexContainer.from_dict(contents))],
            )
        )


PACKAGE_STATUS_LABEL_ZH = {
    "pending": "待處理，可直接按下方按鈕取貨",
    "pickup_now": "已請求取貨，管理員準備中",
    "delivering": "機器人配送中",
    "arrived": "機器人已抵達，請至LINE通知點選掃碼取貨",
}


def reply_later_packages(reply_token: str, packages: list):
    """
    回覆使用者目前所有還沒結束的包裹清單（函式名稱是舊的，沿用沒改，
    但內容不再只限"later"狀態，包含pending/pickup_now/delivering/arrived）。
    只有還是pending（完全還沒回應到貨通知）的包裹才會附上「現在取」按鈕，
    其他狀態已經在流程中，不應該讓使用者從這裡重新觸發一次PICKUP_NOW。
    """
    bubbles = []
    for package in packages:
        status_text = PACKAGE_STATUS_LABEL_ZH.get(package.status, package.status)
        footer_contents = []
        if package.status == "pending":
            footer_contents.append({
                "type": "button",
                "style": "primary",
                "color": "#06C755",
                "action": {
                    "type": "postback",
                    "label": "現在取",
                    "data": f"action=PICKUP_NOW&package_id={package.id}",
                },
            })

        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": f"📦 門牌：{package.unit}", "wrap": True},
                    {"type": "text", "text": f"登記時間：{package.created_at.strftime('%m/%d %H:%M')}", "size": "sm", "color": "#888888"},
                    {"type": "text", "text": status_text, "size": "sm", "color": "#029C4D", "wrap": True, "margin": "md"},
                ],
            },
        }
        if footer_contents:
            bubble["footer"] = {
                "type": "box",
                "layout": "vertical",
                "contents": footer_contents,
            }
        bubbles.append(bubble)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[FlexMessage(
                    alt_text="您的待取包裹清單",
                    contents=FlexContainer.from_dict({"type": "carousel", "contents": bubbles}),
                )],
            )
        )