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
            response = self.get_status(sn)
            
            # 確保 API 回傳成功才解析狀態
            if response.get("message") == "SUCCESS":
                data = response.get("data", {})
                move_state = data.get("move_state")
                
                # 你可以把這行註解掉，這只是開發時用來觀察狀態變化的
                print(f"[{time.strftime('%H:%M:%S')}] 當前移動狀態: {move_state}")
                
                if move_state == "ARRIVE":
                    print("[系統] 🎯 機器人已成功抵達定點！")
                    return True
            
            # 暫停 poll_interval 秒後再問一次 (避免塞爆 Pudu 伺服器)
            time.sleep(poll_interval)
            
        print("[系統] ⚠️ 輪詢超時，機器人可能卡在路上了！")
        return False
