# Aurobox 智連櫃社區 AMR 自動配送系統

住宅大樓的無人收送貨方案。管理員把包裹放進智能櫃，系統以 LINE 通知住戶；住戶用 LINE 呼叫搬運機器人（AMR）把包裹送到家門口、掃碼取貨，也能主動叫機器人來收退貨件。全程不需要另外安裝 App。

---

## 系統架構

系統由兩個獨立服務組成，透過 REST API 互相溝通：

```
LINE 使用者 ──webhook/LIFF──▶  LINE 後端服務（FastAPI）  ──REST API──▶  機器人端服務（Flask）  ──Pudu雲端API──▶  機器人硬體
                                      │
                                      ▼
                               管理員 Dashboard（LINE後端服務內建）
```

- **LINE 後端服務**：唯一的業務邏輯與狀態真相來源。處理 LINE 對話、包裹狀態機、管理員 Dashboard，決定「什麼時候該叫機器人做什麼事」。
- **機器人端服務**：收 LINE 後端的指令，透過 Pudu 雲端 API 操作機器人硬體（開關艙門、移動、充電、緊急召回），只維護艙門與機器人位置這兩項硬體狀態，不重複維護包裹的業務狀態。
- **機器人硬體**：Pudu 廠牌 AMR，機器人端服務透過 Pudu 官方雲端 API 中介溝通，不是直接控制硬體。

呼叫方向以「LINE 後端 → 機器人端」為主；機器人端也會主動打回 LINE 後端幾支 callback（抵達通知、返回管理室通知）。

---

## 專案結構

```
github.com/Liao-YuSheng/Aurobox
├── main 分支      LINE 後端服務 + 管理員 Dashboard
└── flashbot 分支  機器人端服務
```

**LINE 後端服務**（`main` 分支）：

```
line-backend/
├── requirements.txt
└── app/
    ├── config.py         讀取 .env 環境變數
    ├── db.py             資料庫連線設定
    ├── models.py         Package / LineBinding / PackageRecipient / TaskLog 等資料表
    ├── init_db.py        建立資料表用的腳本
    ├── line_verify.py     驗證 LIFF 傳來的 ID Token
    ├── line_messaging.py  封裝呼叫 LINE Messaging API（推播、Flex Message、datetimepicker）
    └── main.py            FastAPI 主程式、Webhook、所有 API 路由、管理員 Dashboard
```

**機器人端服務**（`flashbot` 分支）：

```
flashbot-robot/
└── app/
    ├── config.py       load_config() / require_config()，讀取 APP_KEY / APP_SECRET / SHOP_ID 等設定
    ├── models.py       Door / DoorStatus / RobotState 資料表（Flask-SQLAlchemy）
    ├── pudu_client.py  Pudu 官方雲端 API 的 SDK 封裝
    ├── robot.py        FlashbotController，包裝 pudu_client 提供的高階操作介面
    ├── services.py     update_robot_state / check_and_return_home_if_empty 等狀態管理邏輯
    ├── utils.py        build_custom_call_payload 等共用工具
    ├── tasks.py         背景執行緒任務（抵達輪詢、召回排隊等）
    ├── api.py           所有 /api/... 路由定義
    └── app.py           Flask app factory
```

---

## 核心概念

### 包裹狀態機（LINE 後端）

```
pending ──▶ pickup_now ──▶ delivering ──▶ arrived ──▶ completed
                                              │
                                              ├──▶ rejected_at_door（拒收 / 退貨取消）
                                              └──▶ returned_timeout（逾時未處理）
pending ──▶ voided（到貨當下直接不收）
```

- `pending`：包裹剛登記，已通知住戶，等待住戶回應
- `pickup_now`：住戶確認要收（或已預約時段），等待管理員放置包裹（分配艙門）
- `delivering`：已裝載、機器人正在前往這一站的路上（或排隊等前面的站處理完）
- `arrived`：機器人已抵達，住戶可以掃碼取貨 / 放入退貨物品
- `completed`：任務結束。送貨的 `completed` 代表艙門已釋放；退貨的 `completed` 代表物品剛被放入、艙門仍是滿的，要等管理員確認取出
- `rejected_at_door`：送貨被拒收，或退貨被住戶取消（兩者共用同一個狀態值，走同一套「機器人帶回→開門/關門→銷案」流程，以 `task_type` 分辨顯示文字）
- `returned_timeout`：機器人抵達後住戶超過時限沒有任何動作，系統自動觸發退回
- `voided`：住戶在到貨通知當下直接按「不收」，包裹從沒出過門

### `door_task_id`：以「一站」為分組單位

一位收件人在同一個門牌、同一種任務類型（送貨或退貨）底下，可能同時用到不只一扇艙門（一次多件包裹）。這些包裹共用同一個 `door_task_id`，代表機器人這一趟要去的同一個實際地點、同一批門：一次 dispatch、一次抵達回報，就能讓整組包裹一起轉成 `arrived`；一次掃碼、一次「取貨完成」，就能一次關閉整組的門。

同一戶如果同時有送貨、退貨兩個任務，是兩個獨立的 `door_task_id`（`task_type` 不同）：不會共用同一扇艙門，也不會互相假設對方的狀態——機器人到了其中一個任務的地點，不代表另一個任務也一起處理好了，兩邊都要各自收到明確的 dispatch/arrived 事件才會前進。

### `task_type`：送貨 / 退貨

- `delivery`：機器人帶包裹來，住戶掃碼取件
- `return`：住戶請機器人來收件，機器人帶著空艙門抵達，住戶放入物品

兩種方向共用同一套 `door-tasks` API 端點，差異只在 LINE 文案、Dashboard 顯示文字，以及「艙門完成後應該是空的還是滿的」這件事的判斷邏輯不同。

### 艙門狀態（機器人端）

```
empty ──assign──▶ assigned ──開門裝載──▶ loading ──load──▶ full
                                                              │
                                          住戶掃碼 ┌───────────┴───────────┐
                                                   ▼                       ▼
                                              picking(取件中)        putting(放件中，退貨用)
                                                   │                       │
                                              complete後 → empty      complete後 → full（等管理員取出）
```

三門模式（`DOOR_MODE=3_DOORS`）艙門對應：邏輯 `H_01` → 實體 `H_01`+`H_02`（連動兩扇）、邏輯 `H_02` → 實體 `H_03`、邏輯 `H_03` → 實體 `H_04`。四門模式邏輯門與實體門一對一。

`Door` 資料列不可刪除（資料庫層事件監聽器直接擋掉刪除操作），是刻意的資料保護設計。

---

## LINE 使用者功能

- **綁定門牌**：輸入「門牌 姓名」（例如 `5F-1 王小明`），僅需一次
- **收件**：到貨通知可選「取貨」「預約取貨」或「不收」；機器人抵達後掃碼開門、取出包裹按「取貨完成」；抵達後也可以按「拒收」
- **預約取貨**：LINE 原生 datetimepicker 選時間，以半點為捨入基準（選 9:00–9:30 算「9–10」時段，9:31–9:59 算「10–11」時段），每個時段有上限戶數
- **退貨**：圖文選單「退貨」→ 選件數送出（LIFF 頁面）→ 機器人抵達後掃碼開門、放入物品後按「放貨完成」；抵達後也可以按「暫時不退貨」取消
- **我的包裹**：查詢自己名下所有還沒結束的包裹狀態
- **關門**：忘記按「完成」時的補救指令
- **通知設定**：同門牌多人時，可切換「限本人接收到貨通知」或「同門牌所有人都收到」
- **使用說明**：輸入「使用說明」隨時查看完整操作指引

---

## 管理員 Dashboard

HTTP Basic Auth 保護，四個頁面：

| 路徑 | 功能 |
|---|---|
| `/admin` | 主控台：建立包裹、機器人即時狀態與艙門狀態、包裹清單（放置/派送）、門牌查詢、艙門手動開關 |
| `/admin/reports` | 每日報表：包裹狀態統計、完整任務時間軸 |
| `/admin/exceptions` | 例外處理：拒收/逾時/退貨取消/不收 待處理清單，開關門確認、銷案、補發通知、重新派送 |
| `/admin/residents` | 住戶管理：LINE 綁定紀錄查詢與維護 |

主要管理操作：

- **建立包裹 / 放置 / 全部派送**：登記包裹 → 分配艙門 → 批次派送給機器人
- **緊急召回**：不管機器人目前在做什麼，強制中斷並要求返回管理室（機器人端會排隊等安全時機才真的執行，例如艙門正開著讓住戶取件時不會立即中斷）
- **叫機器人回充電**
- **艙門手動開/關**：機器人狀態欄的開關門鍵，是艙門開關的唯一入口
- **例外處理頁**：拒收/逾時/退貨取消的包裹，管理員確認開門取出、關門、銷案；也可以針對已結案但懷疑機器人異常的包裹重新派送或手動聯繫住戶

---

## LINE 後端排程任務（APScheduler）

| 排程 | 週期 | 用途 |
|---|---|---|
| `check_pickup_timeout` | 1 分鐘 | 送貨 `arrived` 超過時限自動觸發拒收流程 |
| `check_assign_timeout` | 1 分鐘 | 艙門分配了但管理員一直沒裝載，逾時釋放艙門 |
| `check_return_timeout` | 1 分鐘 | 退貨 `arrived` 超過時限自動觸發退回流程 |
| `poll_robot_returned` | 20 秒 | 輪詢機器人是否已回到管理室，更新對應包裹 |
| `check_stuck_dispatch` | 2 分鐘 | 安全網：有進行中的任務、但沒有其他事件會觸發下一步時，主動重試派送邏輯 |

---

## 兩端 API 契約

LINE 後端透過 `ROBOT_API_BASE_URL` + `/api/...` 呼叫機器人端服務：

| 端點 | 用途 |
|---|---|
| `POST /api/door-tasks/{id}/assign` | 指派艙門 |
| `POST /api/door-tasks/{id}/assign-timeout` | 指派了但沒裝載，逾時釋放艙門 |
| `POST /api/doors/load` | 批次關閉已裝載的艙門 |
| `POST /api/robot/dispatch` | 派機器人前往某一站（內建防重複派工保護） |
| `POST /api/door-tasks/{id}/pickup-complete` | 住戶掃碼驗證通過，開門 |
| `POST /api/door-tasks/{id}/complete` | 取貨完成／放貨完成，關門並釋放（或轉為待取回） |
| `POST /api/door-tasks/{id}/cancel` | 拒收／暫時不退貨，關門+收任務畫面 |
| `POST /api/door-tasks/return` | 帶著拒收/退貨包裹返回管理室 |
| `POST /api/doors/return-open` | 返回管理室後開門供管理員檢查 |
| `POST /api/doors/return-complete` | 管理員確認取出，關門釋放艙門 |
| `POST /api/doors/return-timeout` | 開著檢查太久沒關，強制關門 |
| `GET /api/dashboard/status` | Dashboard 顯示機器人即時狀態、艙門狀態 |
| `POST /api/robot/recall` | 緊急召回 |
| `POST /api/robot/recharge` | 回充電站 |

LINE 後端呼叫機器人 API 一律走 `call_robot_api()` 統一封裝，具備逾時、重試與失敗記錄機制。機器人端 `get_status_summary()` 組出的 JSON 結構（`state`／`move_state`／`battery_level`／`current_location`／`sources`）與 Dashboard 的解析邏輯完全對應。

---

## 環境設置

### LINE 後端服務

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

`.env`：

```env
LINE_CHANNEL_SECRET=
LINE_CHANNEL_ACCESS_TOKEN=
LIFF_ID=
LIFF_ID_RETURN=
LINE_LOGIN_CHANNEL_ID=
DATABASE_URL=postgresql+psycopg://postgres:密碼@localhost:5432/aurobox_line
ROBOT_API_BASE_URL=          # 機器人端服務位址，例如 http://127.0.0.1:5000
ROBOT_HOME_POINT_NAME=       # 需與機器人端 HOME_POINT_NAME 完全一致
ADMIN_USERNAME=
ADMIN_PASSWORD=
APP_ENV=development
```

```bash
python -m app.init_db
uvicorn app.main:app --reload --port 8000
```

開發階段用 ngrok 對外連線，Webhook URL 設為 `https://網址/webhook`，兩個 LIFF App 的 Endpoint URL 分別設為 `/liff/scan`（取貨掃碼）與 `/liff/return-request`（退貨申請）。啟動後開 `http://localhost:8000/docs` 可互動測試所有 API。

### 機器人端服務

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt   # Flask、Flask-SQLAlchemy 為必要套件
```

設定值透過 `load_config()` 讀入，關鍵項目：

```
APP_KEY / APP_SECRET      Pudu 官方核發的 API 憑證
SHOP_ID                   Pudu 雲端註冊的店鋪 ID
DEFAULT_SN                機器人序號
DEFAULT_MAP_NAME          機器人所在地圖名稱
HOME_POINT_NAME           管理室/回充位置名稱（需與 LINE 後端 ROBOT_HOME_POINT_NAME 一致）
CHARGE_POINT_NAME         充電站位置名稱
DOOR_MODE                 3_DOORS 或 4_DOORS
DATABASE_URL              選填，不設定則自動使用 SQLite（instance/aurobox.db）
CENTRAL_API_BASE_URL
```

服務啟動時 `db.create_all()` 自動建表，不需要另外的 init 腳本。啟動時預設會把所有艙門重置為 EMPTY、清空機器人當前任務、並對硬體發送關閉所有艙門的指令——正式營運時應避免在配送進行中重啟這支服務。

---

## 已知限制

- LINE 後端 `handle_postback` 的「不收」（`REJECT`）分支在多收件人情境下，通知同門牌其他人的段落引用了一個未定義變數（`triggered_name`），會被外層 `except` 吞掉、不影響主流程，但那則「已取消收件」的通知不會真的送出
- 機器人端服務本質是透過 Pudu 雲端 API 中介，需要官方核發的憑證與已註冊的機器人/地圖/店鋪資訊；沒有這組憑證時能否以本地模擬模式運作尚待確認
