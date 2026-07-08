from .config import require_config, load_config
from .pudu_client import PuduApiClient
import time


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

    def get_status_v1(self, sn: str | None = None) -> dict:
        return self.client.get_by_sn1(sn or self.default_sn)

    def get_task_state(self, sn: str | None = None) -> dict:
        return self.client.get_task_state(sn or self.default_sn)

    def get_status_sources(self, sn: str | None = None) -> dict:
        """Fetch V1/V2/task-state sources with best-effort fallbacks."""
        sn = sn or self.default_sn

        sources = {"v1": {}, "v2": {}, "task": {}}

        try:
            sources["v1"] = self.get_status_v1(sn)
        except Exception:
            sources["v1"] = {}

        try:
            sources["v2"] = self.get_status(sn)
        except Exception:
            sources["v2"] = {}

        try:
            sources["task"] = self.get_task_state(sn)
        except Exception:
            sources["task"] = {}

        return sources

    def get_status_summary(self, sn: str | None = None) -> dict:
        """Build a stable robot status summary from V1/V2/task-state responses."""
        sources = self.get_status_sources(sn)
        data_v1 = sources.get("v1", {}).get("data", {}) or {}
        data_v2 = sources.get("v2", {}).get("data", {}) or {}
        data_task = sources.get("task", {}).get("data", {}) or {}

        move_state = data_v2.get("move_state") or data_v1.get("move_state") or ""
        run_state = data_v2.get("run_state") or ""
        task_state = data_task.get("state") or ""
        is_charging = data_v2.get("is_charging")
        if is_charging is None:
            is_charging = data_v1.get("is_charging")
        charge_stage = data_v2.get("charge_stage") or data_v1.get("charge_stage") or ""
        battery_level = data_v2.get("battery_level")
        if battery_level is None:
            battery_level = data_v1.get("battery_level", 0)

        current_location = (
            data_v2.get("current_location")
            or data_v1.get("current_location")
            or data_v1.get("position_name")
            or ""
        )

        if move_state == "MOVING":
            state = "Moving"
        elif is_charging == 1:
            state = "Charging"
        elif run_state == "ERROR":
            state = "Error"
        elif move_state == "ARRIVE":
            state = "Arrive"
        elif run_state == "BUSY":
            state = "Busy"
        else:
            state = "Idle"

        return {
            "state": state,
            "move_state": move_state,
            "run_state": run_state,
            "task_state": task_state,
            "is_charging": is_charging,
            "charge_stage": charge_stage,
            "battery_level": battery_level,
            "current_location": current_location,
            "sources": sources,
        }

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
        call_mode: str = "CALL",
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
    
    def control_doors(self, sn: str | None, door_number: str, operation: bool) -> dict:
        return self.client.control_doors(sn or self.default_sn, door_number, operation)

    def wait_until_arrived(self, sn: str | None = None, timeout_seconds: int = 300, poll_interval: int = 3) -> bool:
        """
        輪詢監控機制：每隔 poll_interval 秒詢問一次，直到機器人抵達定點 (ARRIVE)。
        """
        sn = sn or self.default_sn
        start_time = time.time()
        
        print(f"[系統] 開始監控機器人 {sn} ...")
        
        while time.time() - start_time < timeout_seconds:
            response = self.get_status_summary(sn)
            
            move_state = response.get("move_state", "")

            # 你可以把這行註解掉，這只是開發時用來觀察狀態變化的
            print(f"[{time.strftime('%H:%M:%S')}] 當前移動狀態: {move_state}")

            if move_state == "ARRIVE":
                print("[系統] 🎯 機器人已成功抵達定點！")
                return True
            
            # 暫停 poll_interval 秒後再問一次 (避免塞爆 Pudu 伺服器)
            time.sleep(poll_interval)
            
        print("[系統] ⚠️ 輪詢超時，機器人可能卡在路上了！")
        return False
