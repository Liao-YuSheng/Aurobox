from .config import require_config, load_config
from .pudu_client import PuduApiClient


class FlashbotController:
    def __init__(self, config: dict | None = None):
        config = require_config(config or load_config())
        self.shop_id = config.get("SHOP_ID")
        self.default_sn = config.get("DEFAULT_SN")
        self.client = PuduApiClient(
            app_key=config["APP_KEY"],
            app_secret=config["APP_SECRET"],
        )

    def get_status(self, sn: str | None = None) -> dict:
        return self.client.get_by_sn2(sn or self.default_sn)

    def get_position(self, sn: str | None = None) -> dict:
        return self.client.get_position(sn or self.default_sn)

    def recharge(self, sn: str | None = None) -> dict:
        return self.client.recharge(sn or self.default_sn)

    def get_map_list(self, sn: str | None = None) -> dict:
        return self.client.get_map_list(sn or self.default_sn)

    def get_door_state(self, sn: str | None = None) -> dict:
        return self.client.get_door_state(sn or self.default_sn)

    def open_map(self, shop_id: str | None, map_name: str) -> dict:
        shop_id = shop_id or self.shop_id
        if not shop_id:
            raise ValueError("shop_id is required to open a map")
        return self.client.open_map(shop_id=shop_id, map_name=map_name)

    def custom_call(
        self,
        sn: str | None,
        shop_id: str | None,
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
        return self.client.custom_call(
            sn or self.default_sn,
            shop_id or self.shop_id,
            map_name,
            point,
            point_type=point_type,
            call_device_name=call_device_name,
            call_mode=call_mode,
            mode_data=mode_data,
            do_not_queue=do_not_queue,
            robot_group_ids=robot_group_ids,
            filter_category_ids=filter_category_ids,
            priority=priority,
        )
