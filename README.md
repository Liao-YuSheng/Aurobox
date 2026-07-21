# Aurobox Flashbot Hardware API

Aurobox 是一個聚焦在 Pudu Flashbot 的硬體控制層，提供對外 HTTP API 讓中央系統可進行派車、艙門控制與狀態查詢。

目前版本重點是「最小本地狀態 + 外部中控整合」，不包含完整住戶互動流程、前端 Dashboard 或 LINE webhook。
本次重大更新導入 PostgreSQL 與行級悲觀鎖（Row-level Lock），確保在多執行緒與高併發環境下不會發生艙門超賣（Overbooking）的競態條件。

## 目前定位

- 控制 Flashbot 移動、地圖呼叫與艙門開關。
- 對外提供可整合的 Flask API。
- 本地僅保存最小必要資料：`doors` 與 `robot_state`。
- 整合 Pudu 多來源狀態並輸出統一摘要。

## 系統架構

```mermaid
flowchart LR
  Central[中央系統] --> API[Flask API]
  API --> DB[(PostgreSQL: doors, robot_state)]
  API --> CTRL[FlashbotController]
  CTRL --> PUDU[Pudu Open Platform]
  PUDU --> ROBOT[Flashbot]
```

## 專案結構

```text
src/aurobox/
├── api.py           # 對外路由
├── app.py           # Flask app factory
├── cli.py           # CLI 指令
├── config.py        # .env 設定載入
├── models.py        # Door / RobotState
├── pudu_client.py   # Pudu API client + 簽章
├── robot.py         # FlashbotController
├── services.py      # DB / 返航邏輯
├── tasks.py         # 背景輪詢與通知
└── utils.py         # payload 組裝工具

scripts/
├── check_db.py
└── read_maps_and_position.py

tests/
├── load_test.py
├── test_api_integration.py
└── test_pudu_client.py

run.py
pyproject.toml
REPORT.md
```

## 環境需求

- Python 3.10+
- requests >= 2.31.0
- python-dotenv >= 1.0.0
- flask >= 3.0.0
- flask-sqlalchemy >= 3.0.0
- python-dateutil >= 2.8.0
- cryptography >= 41.0.0

## 快速啟動

1. 建立虛擬環境

```bash
python3 -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1

python -m pip install -e .
# 若要安裝開發與測試工具： python -m pip install -e ".[dev]"
```

2. 資料庫建置 (PostgreSQL)
本專案強烈依賴 PostgreSQL 來處理高併發的硬體控制請求。請依照以下任一方式建置本地端資料庫：

選項 A：使用 Docker 快速啟動（推薦）
```bash
docker run --name aurobox-postgres \
  -e POSTGRES_USER=myuser \
  -e POSTGRES_PASSWORD=mypassword \
  -e POSTGRES_DB=aurobox_db \
  -p 5432:5432 \
  -d postgres:15
```
選項 B：Linux 原生安裝 (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql

# 進入控制台建立帳號與資料庫
sudo -u postgres psql

# 執行以下 SQL (請在 psql 提示字元下執行)：
CREATE USER myuser WITH PASSWORD 'mypassword';
CREATE DATABASE aurobox_db;
GRANT ALL PRIVILEGES ON DATABASE aurobox_db TO myuser;
ALTER DATABASE aurobox_db OWNER TO myuser;
\c aurobox_db
GRANT ALL ON SCHEMA public TO myuser;
\q
```

3. 建立 `.env`

```env
# Pudu API 憑證與機器人設定
Pd_key=YOUR_PUDU_API_KEY
Pd_secret=YOUR_PUDU_API_SECRET
Aurotek_id=YOUR_SHOP_ID
FLASHBOT_SN=8FF055923050007
DEFAULT_MAP_NAME=YOUR_MAP_NAME
HOME_POINT_NAME=閃閃充電

# 資料庫連線 (對應步驟 2 的設定)
DATABASE_URL=postgresql://myuser:mypassword@localhost:5432/aurobox_db

# 中控系統連線
CENTRAL_API_BASE_URL=[https://your-central-api.example.com](https://your-central-api.example.com)
```

4. 啟動服務

```bash
python3 -u run.py --debug 2>&1 | tee -a instance/aurobox.log
```
如需外網穿透測試，可使用 `ngrok http 5000`

預設監聽：`http://0.0.0.0:5000`

## API 一覽（依目前程式）

基礎：

- `GET /`
- `GET /healthz`

硬體流程：

- `POST /api/robot/recharge`​ #增
  - 行為：​呼叫機器人回充電站。
- `POST /api/packages/<package_id>/assign`​ #改
  - 行為：找空艙門 (具備行級防超賣鎖)、呼叫機器人回管理室、背景輪詢抵達後開門。
- `POST /api/packages/<package_id>/assign-timeout`​ #增
  - 行為：空艙門打開後並沒有關門，視為未放貨，自動關門(仍為`empty`)。
- `POST /api/doors/load`​
  - 行為：關門，狀態從 `assigned` 轉 `full`。​
- `POST /api/robot/dispatch`​
  - 行為：派遣機器人到指定點位，背景輪詢並通知中央系統抵達、顯示QR Code。​
- `POST /api/packages/<package_id>/pickup-complete`​
  - 行為：關閉QR Code畫面並開啟對應艙門。​
- `POST /api/packages/<package_id>/complete`​
  - 行為：關門並清空艙門；若所有艙門皆空，觸發返航。​
- `POST /api/packages/<package_id>/cancel`​
  - 行為：關門、關閉任務畫面，包裹仍保留在艙門內（維持 `full`）。​
- `POST /api/packages/return`​
  - 行為：退回包裹回到管理室。​
- `POST /api/packages/return-open`​
  - 行為：回到管理室後釋放艙門。​
- `POST /api/doors/return-complete`​
  - 行為：拿出被退回的包裹後關閉艙門。​
- `POST /api/doors/return-timeout`​ #增
  - 行為：退貨開門後並沒有關門，視為還沒拿貨，自動關門(維持 `full`)。
- `GET /api/dashboard/status` #還原
  - 行為：回傳機器人即時狀態摘要與本地艙門狀態。
  
## CLI

```bash
aurobox --sn 8FF055923050007 status
aurobox --sn 8FF055923050007 position
aurobox --sn 8FF055923050007 recharge
aurobox --sn 8FF055923050007 map-list
aurobox --sn 8FF055923050007 door-state
aurobox --shop-id YOUR_SHOP_ID open-map --map-name map1
aurobox --sn 8FF055923050007 --shop-id YOUR_SHOP_ID call --map-name map1 --point 閃閃充電
```

## 狀態整合策略

`FlashbotController.get_status_summary()` 會合併：

- `/pudu-entry/open-platform-service/v1/status/get_by_sn`
- `/pudu-entry/open-platform-service/v2/status/get_by_sn`
- `/pudu-entry/open-platform-service/v1/robot/task/state/get`

並輸出統一欄位如 `state`、`move_state`、`run_state`、`task_state`、`battery_level`、`current_location`。

## 測試

```bash
pytest -q
```

目前測試涵蓋：

- 設定載入與初始化（`tests/test_pudu_client.py`）
- API 流程整合（`tests/test_api_integration.py`）
  - `assign -> load`
  - `dispatch`（含 task_id 寫入）
  - `complete`
  - `cancel`

## 已知問題

- 目前無阻擋性已知問題。

## 版本資訊

- 套件版本：`0.3.0`
- 本次盤點報告：`REPORT.md`
