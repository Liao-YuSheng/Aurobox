#!/usr/bin/env python
"""Run the Aurobox Flashbot application."""

import sys
import argparse
from pathlib import Path

# 把 src 目錄加入系統路徑，這樣程式才能認得 aurobox 這個套件
src_path = Path(__file__).parent / 'src'
sys.path.insert(0, str(src_path))

# ⚠️ 注意：如果你把剛剛的 app_ge.py 檔名保留了，請將這行改為 from aurobox.app_ge import create_app
# 但強烈建議你直接把它重新命名回 app.py，保持專案整潔！
from aurobox.app import create_app
from aurobox.config import load_config, require_config

def main():
    parser = argparse.ArgumentParser(description='Run Aurobox Flashbot Hardware Server')
    
    # 【最關鍵的修改】將預設 host 從 127.0.0.1 改為 0.0.0.0
    # 這樣 Ngrok、Cloudflare 或同一台 WiFi 下的手機與雲端大腦，才能順利連進來！
    parser.add_argument('--host', default='0.0.0.0', help='Server host')
    parser.add_argument('--port', type=int, default=5000, help='Server port')
    # 開發期間，建議啟動時加上 --debug 參數
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    args = parser.parse_args()
    
    # 讀取並驗證 .env 環境變數 (檢查是否有漏填 APP_KEY 等資訊)
    config = load_config()
    require_config(config)
    
    # 建立 Flask 應用程式小腦
    app = create_app(config)
    
    print(f"===================================================")
    print(f"Flashbot 硬體控制伺服器啟動中...")
    print(f"監聽位址: http://{args.host}:{args.port}")
    print(f"===================================================")
    
    # 正式啟動伺服器
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug
    )

if __name__ == '__main__':
    main()