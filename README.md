# Aurobox 0.4.0 多包裹配送硬體控制層

Aurobox 是 Pudu Flashbot 的本地硬體控制服務，對中央系統提供 HTTP API，負責機器人導航、艙門批次控制、配送任務狀態同步與異常保護。

本版文件為 0.4.0 完整更新，主軸是「多包裹配送流程」，涵蓋從管理室批次裝貨、住戶端取件、退件返航到緊急召回的全流程。

## 0.4.0 核心能力

- 多包裹分配：同一包裹可一次要求 quantity，多門同時分配。
- 高併發防超賣：使用 PostgreSQL 搭配 with_for_update(skip_locked=True) 避免重複分配空門。
- 批次艙門控制：支援一次開關多門。
- 單機任務保護：配送中禁止重複派工，避免運送狀態衝突。
- 背景輪詢與回呼：抵達後通知中央系統，並顯示 QR 取件畫面。
- 完整退件流程：return、return-open、return-complete、return-timeout。
- 緊急召回：取消當前任務後強制返航，並保護門狀態。
- 3 門/4 門兼容：DOOR_MODE=3_DOORS 時，邏輯門 H_01 會同步對應實體 H_01 + H_02。

## 系統邊界

本專案只負責硬體控制層，不包含：

- LINE Webhook 與住戶身分綁定
- 完整訂單生命週期資料模型
- 管理員前端 Dashboard 畫面

## 架構總覽

```mermaid
flowchart LR
  Central[中央系統] --> API[Flask API]
  API --> DB[(PostgreSQL)]
  API --> Controller[FlashbotController]
  Controller --> Pudu[Pudu Open Platform]
  API --> Tasks[Background Threads]
  Tasks --> Central
```

## 專案結構

```text
src/aurobox/
├── api.py           # 硬體流程 API（多包裹核心）
├── app.py           # Flask app factory + 啟動初始化
├── config.py        # .env 設定讀取
├── models.py        # Door / RobotState
├── services.py      # 狀態寫入與空門返航
├── tasks.py         # 背景輪詢、抵達通知、開門排程
├── robot.py         # FlashbotController
├── pudu_client.py   # Pudu API client + HMAC 簽章
├── utils.py         # custom_call payload 組裝
└── cli.py           # CLI 工具

scripts/
├── check_db.py
└── read_maps_and_position.py

tests/
├── test_pudu_client.py
├── test_api_integration.py
└── load_test.py
```

## 門與任務狀態模型

Door.status 支援：

- empty：空門
- assigned：已分配，等待管理員放貨
- full：已放貨，待配送或退件中
- picking：住戶正在取件

RobotState：

- last_point：最後記錄點位
- current_task_id：目前任務 ID

## 多包裹配送流程

### 1) 分配空門

呼叫 POST /api/packages/{package_id}/assign，可帶 quantity。

- 若目前已有 full 門，回 409（機器人視為配送中）。
- 以資料庫鎖挑選 needed_count 個 empty 門。
- 若已有 assigned 任務，省略重複導航；否則先叫機器人回管理室。
- 背景執行緒等待機器人到位後，依佇列逐門開門。

### 2) 管理員裝貨

呼叫 POST /api/doors/load。

- 將所有 assigned 門批次關門。
- 狀態轉為 full。

### 3) 派送到住戶點位

呼叫 POST /api/robot/dispatch。

- 用 QR_CODE 模式派送。
- 寫入 current_task_id。
- 背景輪詢到站後回呼中央系統 packages/{id}/arrived。
- 同步切換機器人畫面顯示 QR Code。

### 4) 住戶掃碼與取件

- POST /api/packages/{package_id}/pickup-complete：清除任務畫面並開門，門狀態轉 picking。
- POST /api/packages/{package_id}/complete：關門後清空門；若全部為 empty，觸發返航。

### 5) 取消與退件

- POST /api/packages/{package_id}/cancel：關門，包裹保留為 full。
- POST /api/packages/return：要求機器人回管理室。
- POST /api/packages/return-open：管理室檢查時批次開啟啟用門。
- POST /api/doors/return-complete：檢查完批次關門並清空。
- POST /api/doors/return-timeout：退件開門逾時，強制批次關門。

### 6) 緊急召回

POST /api/robot/recall：

- 取消目前 task_id
- 等待硬體重置
- 重新導航回管理室
- 將 assigned/picking 一律保護成 full

## API 一覽

### 基礎

- GET /
- GET /healthz

### 多包裹配送與艙門控制

- POST /api/packages/{package_id}/assign
- POST /api/packages/{package_id}/assign-timeout
- POST /api/doors/load
- POST /api/robot/dispatch
- POST /api/packages/{package_id}/pickup-complete
- POST /api/packages/{package_id}/complete
- POST /api/packages/{package_id}/cancel
- POST /api/packages/return
- POST /api/packages/return-open
- POST /api/doors/return-complete
- POST /api/doors/return-timeout
- POST /api/robot/recharge
- POST /api/robot/recall
- GET /api/dashboard/status

## 環境需求

- Python 3.10+
- PostgreSQL 14+
- requests
- flask
- flask-sqlalchemy
- python-dotenv
- cryptography
- psycopg2-binary

## 快速啟動

1. 建立虛擬環境並安裝

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

2. 啟動 PostgreSQL（Docker 範例）

```bash
docker run --name aurobox-postgres \
  -e POSTGRES_USER=myuser \
  -e POSTGRES_PASSWORD=mypassword \
  -e POSTGRES_DB=aurobox_db \
  -p 5432:5432 \
  -d postgres:15
```

3. 建立 .env（可由 .env.example 複製）

```env
Pd_key=YOUR_PUDU_API_KEY
Pd_secret=YOUR_PUDU_API_SECRET
Aurotek_id=YOUR_SHOP_ID
FLASHBOT_SN=8FF055923050007
DEFAULT_MAP_NAME=YOUR_MAP_NAME
HOME_POINT_NAME=office
CHARGE_POINT_NAME=閃閃充電
DOOR_MODE=4_DOORS
DATABASE_URL=postgresql://myuser:mypassword@localhost:5432/aurobox_db
CENTRAL_API_BASE_URL=https://your-central-api.example.com
```

4. 啟動服務

```bash
python3 -u run.py --debug
```

預設服務位置：

- http://0.0.0.0:5000

## CLI 範例

```bash
aurobox --sn 8FF055923050007 status
aurobox --sn 8FF055923050007 position
aurobox --sn 8FF055923050007 map-list
aurobox --sn 8FF055923050007 recharge
aurobox --sn 8FF055923050007 --shop-id YOUR_SHOP_ID open-map --map-name map1
aurobox --sn 8FF055923050007 --shop-id YOUR_SHOP_ID call --map-name map1 --point 閃閃充電
```

## 觀測與維運

- 指令紀錄：instance/robot_commands.log
- DB 盤點腳本：scripts/check_db.py
- Pudu 快速讀取：scripts/read_maps_and_position.py

## 測試現況

- 自動化測試：pytest 結果為 4 passed, 1 skipped。
- tests/test_api_integration.py：已對齊多包裹路由與現行資料模型。
- tests/load_test.py：已升級為 4 個情境（quantity > 1、concurrent assign、return-timeout、recall）。

## 版本對齊

- 文件版次：0.4.0（多包裹配送完整更新）
- 程式套件版號：0.4.0
- 盤點報告：REPORT.md
