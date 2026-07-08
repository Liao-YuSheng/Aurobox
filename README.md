# Aurobox 送貨機器人管理系統

Aurobox 是一套以普渡 Flashbot 為核心的送貨機器人管理系統，提供機器人狀態查詢、艙門控制、包裹配送流程、管理員 Dashboard、LINE webhook 與 CLI 工具。

## 功能總覽

- 機器人狀態查詢：`status`、`position`、`recharge`
- 地圖與呼叫控制：`map-list`、`open-map`、`call`
- 包裹生命週期管理：建立、放貨、出發、抵達、生成取貨 QR、掃碼完成、退回
- 艙門狀態管理：開啟、關閉、裝載、清空
- Dashboard 即時狀態：機器人、艙門、任務隊列、今日歷史紀錄
- 後台任務：輪詢機器人狀態、超時退回、艙門同步
- LINE webhook：接收外部事件、回寫包裹狀態、發送推播通知
- CLI 工具：快速查詢與發送控制指令

## 系統架構

```mermaid
flowchart TB
	Operator[管理員 / Dashboard] --> API[Flask API 伺服器]
	User[住戶 / LINE] --> Webhook[LINE Webhook]
	API --> Package[Package 狀態]
	API --> Door[Door 狀態]
	API --> Robot[RobotStatus]
	API --> Manager[ManagerService]
	API --> Tasks[TaskService]
	Webhook --> Package
	Manager --> Pudu[Pudu Flashbot API]
	Tasks --> Pudu
	Pudu --> RobotHW[Flashbot 機器人]
```

## 主要模組

- `src/aurobox/app.py`: Flask 應用工廠、資料表補欄、路由註冊
- `src/aurobox/api.py`: 包裹管理、Dashboard API、QR token 流程
- `src/aurobox/webhooks.py`: LINE webhook、postback 處理、推播通知
- `src/aurobox/manager.py`: 管理員操作流程
- `src/aurobox/tasks.py`: 背景輪詢與超時處理
- `src/aurobox/robot.py`: Flashbot 控制器包裝層與狀態整合
- `src/aurobox/pudu_client.py`: Pudu API 客戶端與簽章
- `src/aurobox/models.py`: SQLAlchemy 模型
- `src/aurobox/cli.py`: CLI 命令列入口

## 狀態整合策略

- `robot.py` 同時抓三個來源：
  - V1：`/v1/status/get_by_sn`
  - V2：`/v2/status/get_by_sn`
  - Task：`/v1/robot/task/state/get`
- 對外統一使用 `get_status_summary()` 的正規化結果，不直接依賴單一來源欄位。
- `state` 的判斷優先順序為：`move_state` → `is_charging` → `run_state` → 其餘視為 `Idle`。

## 安裝與啟動

### 1. 建立虛擬環境

```bash
python3 -m venv .venv
```

Windows：

```bash
.venv\Scripts\activate
```

Linux / macOS：

```bash
source .venv/bin/activate
```

### 2. 安裝套件

```bash
python -m pip install -e .
```

### 3. 建立環境變數

```bash
cp .env.example .env
```

請至少設定以下值：

```env
Pd_key=YOUR_PUDU_API_KEY
Pd_secret=YOUR_PUDU_API_SECRET
Aurotek_id=YOUR_SHOP_ID
FLASHBOT_SN=8FF055923050007
PUDU_BASE_URL=https://css-open-platform.pudutech.com
```

若要啟用 LINE 通知與 webhook 驗證，另外設定：

```env
LINE_CHANNEL_ACCESS_TOKEN=YOUR_LINE_CHANNEL_ACCESS_TOKEN
LINE_CHANNEL_SECRET=YOUR_LINE_CHANNEL_SECRET
```

### 4. 啟動服務

```bash
python run.py --debug
```

預設啟動於 `http://127.0.0.1:5000`

## API 端點

### 健康檢查與首頁

| 方法 | 端點 | 說明 |
|---|---|---|
| GET | `/` | 本機服務資訊首頁 |
| GET | `/healthz` | 健康檢查 |

### 包裹管理

| 方法 | 端點 | 說明 |
|---|---|---|
| POST | `/api/packages` | 建立新包裹 |
| GET | `/api/packages/<package_id>` | 取得包裹詳情 |
| POST | `/api/packages/<package_id>/response` | 住戶選擇 `pickup_now` / `later` |
| POST | `/api/packages/<package_id>/stored` | 管理員放貨並指定艙門 |
| POST | `/api/packages/<package_id>/departed` | 確認機器人出發 |
| POST | `/api/packages/<package_id>/arrived` | 記錄機器人抵達並生成取貨 QR token |
| POST | `/api/packages/<package_id>/pickup-complete` | 掃碼後完成取貨，驗證 QR token |
| POST | `/api/packages/<package_id>/complete` | 住戶確認完成 |
| POST | `/api/packages/<package_id>/cancel` | 取消或逾時退回 |
| POST | `/api/packages/<package_id>/returned` | 記錄機器人返回 |

### Dashboard

| 方法 | 端點 | 說明 |
|---|---|---|
| GET | `/api/dashboard/events` | 取得即時狀態、任務隊列、艙門與今日歷史紀錄 |

### Webhook

| 方法 | 端點 | 說明 |
|---|---|---|
| POST | `/webhooks/line` | LINE Messaging API webhook 入口，接收 postback / message 事件 |

### Dashboard 回傳重點

`GET /api/dashboard/events` 會回傳：

- `robot_status`: `state`、`battery_level`、`current_location`、`move_state`、`run_state`、`task_state`、`is_charging`、`charge_stage`
- `task_queue`: 待處理、進行中、稍後處理、歷史紀錄數量
- `door_states`: 每個艙門的狀態與對應包裹
- `pending_orders`、`delivering_orders`: 目前進行中的訂單清單

備註：當上游 Pudu API 授權失敗時，`/api/dashboard/events` 會回傳對應上游錯誤狀態碼（例如 401），不再一律回傳 500。

## QR Code 取貨流程

- `POST /api/packages/<package_id>/arrived` 會替該包裹建立 `pickup_qr_token`
- 系統會嘗試讓機器人以 `call_mode=QR_CODE` 顯示取貨 QR
- `POST /api/packages/<package_id>/pickup-complete` 會驗證 token，驗證成功後才完成取貨
- 目前沒有額外的 QR 圖片產生器；前端若需要顯示 QR，可直接把 `pickup_qr_token` 轉成 QR 圖片

## custom_call 與呼叫模式

- `custom_call` 預設 `call_mode` 為 `CALL`
- `CALL`：非自訂呼叫，呼叫到達點位就結束任務
- `IMG`：圖片模式，保留給需要圖片展示的情境
- `QR_CODE`：取貨 QR 顯示模式，用於機器人到達後顯示掃碼資訊
- `VIDEO`：視頻模式
- `CALL_CONFIRM`：呼叫抵達確認模式

對於非 P-ONE 機器，`call_mode` 若不是 `CALL`，通常需要走舊開放平台鏈路；因此系統只在「到達後顯示取貨 QR」這一步明確使用 `QR_CODE`。

## LINE webhook

### 支援行為

- 驗證 `X-Line-Signature`
- 處理 `postback` 事件，更新包裹狀態（`pickup_now` / `later` / `cancel`）
- 處理 `message` 事件，記錄文字訊息日誌
- 提供推播通知函式：出發、送達、退回

### 目前路徑

- `src/aurobox/webhooks.py`：Webhook 實作
- `src/aurobox/app.py`：Blueprint 掛載在 `/webhooks`

## CLI 指令

```bash
aurobox status --sn 8FF055923050007
aurobox position --sn 8FF055923050007
aurobox recharge --sn 8FF055923050007
aurobox map-list --sn 8FF055923050007
aurobox door-state --sn 8FF055923050007
aurobox open-map --map-name map1 --shop-id YOUR_SHOP_ID
aurobox call --map-name map1 --point management --sn 8FF055923050007
```

## 資料模型

- `Package`: 包裹配送記錄，含狀態、艙門、時間戳記、取貨 QR token
- `Door`: 艙門狀態，含開關與裝載狀態
- `RobotStatus`: 機器人即時狀態
- `DeliveryHistory`: 操作歷史紀錄

## 包裹流程

```mermaid
sequenceDiagram
	participant M as 管理員
	participant API as Aurobox API
	participant R as Flashbot
	participant L as LINE

	M->>API: POST /api/packages
	M->>API: POST /api/packages/{id}/stored
	M->>API: POST /api/packages/{id}/departed
	API->>R: custom_call / control_doors
	R->>API: POST /api/packages/{id}/arrived
	API->>R: custom_call(call_mode=QR_CODE)
	API->>API: 生成 pickup_qr_token
	L->>API: POST /webhooks/line (postback/message)
	M->>API: POST /api/packages/{id}/pickup-complete
	API->>API: 驗證 pickup_qr_token
	M->>API: POST /api/packages/{id}/complete
	API->>R: robot 返回管理室
```

## 背景任務

- `poll_robot_status(sn)`: 週期性輪詢機器人狀態並更新資料庫
- `check_pickup_timeout(timeout_minutes=10)`: 檢查超時未取貨訂單
- `sync_door_states(sn)`: 同步艙門狀態
- `handle_robot_returning(sn)`: 機器人返回後的清理流程

## 故障排查

### 機器人狀態查詢失敗

- 檢查 `Pd_key` 與 `Pd_secret`
- 確認 `FLASHBOT_SN` 正確
- 確認網路可以連到 Pudu API

### Dashboard 沒有資料

- 確認資料庫已建立：`instance/aurobox.db`
- 確認 Flask 應用已正常啟動
- 檢查 `ROBOT_SN` 是否和實際機器人一致

### QR 掃碼取貨失敗

- 確認 `arrived` 端點是否已回傳 `pickup_qr_token`
- 確認 `pickup-complete` 有帶相同 token
- 確認機器人是否支援 `call_mode=QR_CODE`

### 包裹流程異常

- 檢查包裹狀態是否與艙門狀態一致
- 使用 `force_reset_all_doors()` 做防呆檢查
- 查看 `delivery_history` 是否有記錄

## 專案結構

```text
src/aurobox/
├── __init__.py
├── app.py
├── api.py
├── cli.py
├── config.py
├── manager.py
├── models.py
├── pudu_client.py
├── robot.py
├── tasks.py
└── webhooks.py
```

其他重要檔案：

- `run.py`: Flask 啟動入口
- `examples.py`: 使用範例
- `REPORT.md`: 實作完成報告

## 技術棧

- Flask 3.0+
- SQLAlchemy + SQLite
- requests
- python-dotenv
- cryptography
- Pudu Flashbot API

## 授權

MIT License
