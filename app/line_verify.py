"""
驗證 LIFF 傳來的 ID Token，確認是真的LINE使用者身份
"""
import requests

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


def verify_liff_id_token(id_token: str, channel_id: str) -> dict:
    """
    向LINE官方驗證ID Token，成功會回傳裡面包含的資訊(claims)，
    其中 claims['sub'] 就是這個用戶的 LINE User ID。
    驗證失敗會丟出例外。
    """
    resp = requests.post(
        LINE_VERIFY_URL,
        data={"id_token": id_token, "client_id": channel_id},
        timeout=5,
    )
    if resp.status_code != 200:
        raise ValueError(f"ID Token 驗證失敗: {resp.text}")
    return resp.json()