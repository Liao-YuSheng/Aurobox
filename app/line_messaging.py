"""
封裝呼叫LINE Messaging API的邏輯
"""
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
)
from app.config import settings

configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)


def reply_welcome_with_binding_link(reply_token: str):
    """新用戶加好友時，回覆一則含綁定連結的訊息"""
    liff_url = f"https://liff.line.me/{settings.LIFF_ID}"
    text = f"歡迎加入！請點擊以下連結完成綁定，讓我們知道您的門牌與姓名：\n{liff_url}"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )