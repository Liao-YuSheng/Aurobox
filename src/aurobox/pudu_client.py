import base64
import hashlib
import hmac
import json

from datetime import datetime, timezone
from email.utils import format_datetime

import requests


class PuduAuth:

    @staticmethod
    def utc_date():
        return format_datetime(datetime.now(timezone.utc), usegmt=True)

    @staticmethod
    def md5_base64(content: str) -> str:
        md5_hex = hashlib.md5(content.encode("utf-8")).hexdigest()
        return base64.b64encode(md5_hex.encode("utf-8")).decode("utf-8")

    @staticmethod
    def generate_signature(
        secret: str,
        method: str,
        path: str,
        x_date: str,
        content_md5: str = ""
    ) -> str:
        string_to_sign = (
            f"x-date: {x_date}\n"
            f"{method.upper()}\n"
            f"application/json\n"
            f"application/json\n"
            f"{content_md5}\n"
            f"{path}"
        )
        signature = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(signature).decode("utf-8")


class PuduApiClient:
    BASE_URL = "https://css-open-platform.pudutech.com"

    def __init__(self, app_key: str, app_secret: str, timeout: int = 30):
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout

    def _build_path(self, endpoint: str, params: dict | None = None) -> str:
        if not params:
            return endpoint
        sorted_params = sorted(params.items(), key=lambda item: item[0])
        query = "&".join(f"{key}={value}" for key, value in sorted_params)
        return f"{endpoint}?{query}"

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        x_date = PuduAuth.utc_date()
        content_md5 = ""
        if method.upper() == "POST" and body:
            content_md5 = PuduAuth.md5_base64(body)

        signature = PuduAuth.generate_signature(
            self.app_secret,
            method,
            path,
            x_date,
            content_md5,
        )

        authorization = (
            f'hmac id="{self.app_key}", '
            f'algorithm="hmac-sha1", '
            f'headers="x-date", '
            f'signature="{signature}"'
        )

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-date": x_date,
            "Authorization": authorization,
        }
        if content_md5:
            headers["Content-MD5"] = content_md5
        return headers

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        path = self._build_path(endpoint, params)
        headers = self._headers("GET", path)
        url = self.BASE_URL + endpoint

        response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        headers = self._headers("POST", endpoint, body)
        url = self.BASE_URL + endpoint

        response = requests.post(
            url,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_by_sn2(self, sn: str) -> dict:
        return self._get(
            "/pudu-entry/open-platform-service/v2/status/get_by_sn",
            {"sn": sn},
        )

    def get_position(self, sn: str) -> dict:
        return self._get(
            "/pudu-entry/open-platform-service/v1/robot/get_position",
            {"sn": sn},
        )

    def open_map(self, shop_id: str | int, map_name: str) -> dict:
        return self._get(
            "/pudu-entry/map-service/v1/open/map",
            {"shop_id": shop_id, "map_name": map_name},
        )

    def recharge(self, sn: str) -> dict:
        return self._get(
            "/pudu-entry/open-platform-service/v2/recharge",
            {"sn": sn},
        )

    def custom_call(
        self,
        sn: str,
        shop_id: str | int,
        map_name: str,
        point: str,
        point_type: str = "table",
        call_device_name: str = "PythonSDK",
        call_mode: str = "IMG",
        mode_data: dict | None = None,
        do_not_queue: bool = False,
        robot_group_ids: list | None = None,
        filter_category_ids: list | None = None,
        priority: int = 1,
    ) -> dict:
        payload = {
            "sn": sn,
            "shop_id": shop_id,
            "map_name": map_name,
            "point": point,
            "point_type": point_type,
            "call_device_name": call_device_name,
            "call_mode": call_mode,
            "mode_data": mode_data or {},
            "do_not_queue": do_not_queue,
            "robot_group_ids": robot_group_ids or [],
            "filter_category_ids": filter_category_ids or [],
            "priority": priority,
        }
        return self._post(
            "/pudu-entry/open-platform-service/v1/custom_call",
            payload,
        )

    def get_map_list(self, sn: str) -> dict:
        return self._get(
            "/pudu-entry/map-service/v1/open/list",
            {"sn": sn},
        )

    def get_door_state(self, sn: str) -> dict:
        return self._get(
            "/pudu-entry/open-platform-service/v1/door_state",
            {"sn": sn},
        )
