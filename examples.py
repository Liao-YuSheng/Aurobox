#!/usr/bin/env python
"""Example usage of Aurobox delivery system."""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / 'src'
sys.path.insert(0, str(src_path))

from aurobox.app import create_app
from aurobox.manager import ManagerService
from aurobox.models import db
from aurobox.config import load_config, require_config


def example_delivery_workflow():
    """
    Example of complete delivery workflow.
    """
    # 初始化應用
    config = load_config()
    require_config(config)
    app = create_app(config)
    
    with app.app_context():
        manager_service = ManagerService()
        
        # 步驟 1: 管理員登記包裹
        print("=== 步驟 1: 管理員登記包裹 ===")
        package = manager_service.register_package(
            phone_number='0912345678',
            address='3樓 101號',
            line_user_id='Uabcdef1234567890'
        )
        print(f"✓ 包裹已登記: {package.id}")
        print(f"  地址: {package.address}")
        print(f"  狀態: {package.status}")
        
        # 步驟 2: 分配艙門
        print("\n=== 步驟 2: 分配艙門 ===")
        door_number = manager_service.allocate_door(package.id)
        print(f"✓ 艙門已分配: {door_number}")
        
        # 步驟 3: 呼叫機器人到管理室
        print("\n=== 步驟 3: 呼叫機器人到管理室 ===")
        try:
            result = manager_service.call_robot_to_management()
            print(f"✓ 機器人已呼叫")
            print(f"  結果: {result.get('message')}")
        except Exception as e:
            print(f"✗ 呼叫失敗: {e}")
            print("  (請確保 PUDU API 認證信息正確)")
        
        # 步驟 4: 打開艙門進行裝載
        print("\n=== 步驟 4: 打開艙門進行裝載 ===")
        print(f"ℹ 準備打開 {door_number} 艙門...")
        # try:
        #     result = manager_service.confirm_door_open(
        #         sn=config.get('DEFAULT_SN'),
        #         door_number=door_number
        #     )
        #     print(f"✓ 艙門已打開")
        # except Exception as e:
        #     print(f"✗ 打開失敗: {e}")
        
        # 步驟 5: 管理員確認出發
        print("\n=== 步驟 5: 管理員確認出發 ===")
        print("ℹ 包裹已裝載，準備出發...")
        try:
            result = manager_service.confirm_package_loaded(package.id)
            print(f"✓ 機器人已出發")
            print(f"  目的地: {package.address}")
            print(f"  狀態: {package.status}")
        except Exception as e:
            print(f"✗ 出發失敗: {e}")
        
        # 步驟 6: 模擬機器人抵達
        print("\n=== 步驟 6: 機器人抵達 ===")
        package.arrived_at = db.func.now()
        from aurobox.models import PackageStatus
        package.status = PackageStatus.ARRIVED
        db.session.commit()
        print(f"✓ 機器人已抵達: {package.address}")
        
        # 步驟 7: 用戶掃碼取貨
        print("\n=== 步驟 7: 用戶掃碼取貨 ===")
        print("ℹ 用戶掃描 QR Code...")
        package.status = PackageStatus.COMPLETED
        from datetime import datetime
        package.completed_at = datetime.utcnow()
        db.session.commit()
        print(f"✓ 包裹已取出")
        
        # 步驟 8: 取得任務隊列
        print("\n=== 步驟 8: 當前任務隊列 ===")
        queue = manager_service.get_task_queue()
        print(f"待處理訂單: {len(queue['pending_orders'])}")
        print(f"進行中訂單: {len(queue['delivering_orders'])}")
        print(f"稍後處理訂單: {len(queue['later_orders'])}")
        
        print("\n✓ 配送流程示例完成!")


def example_line_integration():
    """
    Example of LINE integration.
    """
    print("=== LINE 集成示例 ===\n")
    
    config = load_config()
    app = create_app(config)
    
    with app.app_context():
        # 模擬用戶綁定
        print("1. 用戶追蹤官方 LINE 帳號")
        print("   -> 觸發 follow 事件")
        print("   -> LineUser 記錄自動創建\n")
        
        print("2. 用戶在 LIFF 中輸入資料")
        print("   POST /api/bindings")
        print("""
{
  "line_user_id": "U123456789",
  "phone_number": "0912345678",
  "address": "3樓 101號",
  "name": "王小明"
}
        """)
        print("   -> 用戶綁定完成\n")
        
        print("3. 包裹送達時 -> LINE 推播通知\n")
        
        print("4. 用戶點擊按鈕:")
        print("   - 「立即取貨」-> 設置狀態為 PICKUP_NOW")
        print("   - 「稍後再取」-> 設置狀態為 LATER")
        print("   - 「無法取貨」-> 機器人自動退回\n")
        
        print("5. 用戶掃描 QR Code 取貨")
        print("   -> 艙門自動打開")
        print("   -> 用戶確認取貨完成\n")


def example_dashboard():
    """
    Example of dashboard event monitoring.
    """
    print("=== Dashboard 即時監控示例 ===\n")
    
    config = load_config()
    app = create_app(config)
    
    with app.app_context():
        print("curl http://127.0.0.1:5000/api/dashboard/events\n")
        
        print("""
返回數據示例:
{
  "robot_status": {
    "state": "Delivering",
    "battery_level": 85.5,
    "current_location": "3樓 101號",
    "move_state": "ARRIVE",
    "updated_at": "2024-07-08T12:30:45.123456"
  },
  "task_queue": {
    "pending_orders": 2,
    "delivering_orders": 1,
    "later_orders": 3,
    "history_count": 15
  },
  "door_states": [
    {
      "door_number": "H_01",
      "status": "closed",
      "loading_status": "loaded",
      "address": "3樓 101號",
      "package_id": "abc-123"
    },
    ...
  ],
  "pending_orders": [...],
  "delivering_orders": [...]
}
        """)


if __name__ == '__main__':
    print("╔═══════════════════════════════════════════════════════╗")
    print("║         Aurobox 送貨機器人管理系統 - 使用示例           ║")
    print("╚═══════════════════════════════════════════════════════╝\n")
    
    example_delivery_workflow()
    print("\n" + "="*60 + "\n")
    
    example_line_integration()
    print("\n" + "="*60 + "\n")
    
    example_dashboard()
    
    print("\n" + "="*60)
    print("詳細文檔請參考: README.md")
    print("="*60)
