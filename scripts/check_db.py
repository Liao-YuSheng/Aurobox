#!/usr/bin/env python
import sys
from pathlib import Path

# 將 src 加入系統路徑，這樣 Python 才能找到 aurobox 這個套件
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))

from aurobox.app import create_app
from aurobox.config import load_config
from aurobox.models import db, Door, DoorStatus

def main():
    # 初始化 Flask 環境
    config = load_config()
    app = create_app(config)
    
    with app.app_context():
        print("="*40)
        print("🔍 正在查詢資料庫中的艙門狀態...")
        # 加上 order_by 讓輸出結果照 H_01, H_02, H_03 排序
        doors = Door.query.order_by(Door.door_number).all()
        
        if not doors:
            print("⚠️ 警告：資料庫裡面完全沒有艙門資料！請確認 app.py 是否有正常啟動並補齊艙門。")
        else:
            print(f"📊 目前共有 {len(doors)} 個艙門：")
            for door in doors:
                print(f" - 門號: {door.door_number} | 狀態: {door.status} | 綁定包裹: {door.package_id}")
                
        print("="*40)

if __name__ == '__main__':
    main()