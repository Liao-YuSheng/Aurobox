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
    """推播到貨通知，附「取貨」「稍後再取」兩個按鈕"""
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
                        "label": "稍後再取",
                        "data": f"action=LATER&package_id={package_id}",
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
    """機器人抵達時，推播提醒+開啟掃碼+暫時無法取貨按鈕"""
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
                        "label": "暫時無法取貨",
                        "data": f"action=CANCEL_PICKUP&package_id={package_id}",
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


def reply_later_packages(reply_token: str, packages: list):
    """回覆「稍後再取」包裹清單，每張卡片可直接按「現在取」"""
    bubbles = []
    for package in packages:
        bubbles.append({
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": f"📦 門牌：{package.unit}", "wrap": True},
                    {"type": "text", "text": f"登記時間：{package.created_at.strftime('%m/%d %H:%M')}", "size": "sm", "color": "#888888"},
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#06C755",
                        "action": {
                            "type": "postback",
                            "label": "現在取",
                            "data": f"action=PICKUP_NOW&package_id={package.id}",
                        },
                    }
                ],
            },
        })

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