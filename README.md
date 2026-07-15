# LINE 後端 - 智連櫃社區 AMR 配送系統

對應整體系統三大部分（管理員Dashboard／LINE／送貨機器人）中的LINE部分，這支服務同時提供 Dashboard 網頁本身（`/admin`）。

## 目前完成進度

### 已完成

**住戶端（LINE）**
- 用戶綁定：住戶在LINE聊天室輸入「門牌 姓名」完成綁定
- 到貨通知：管理員登記包裹後，推播通知住戶，支援「限本人接收」設定（`solo_notify`，預設開啟）
- 用戶可用文字指令「開啟限本人通知」／「關閉限本人通知」自行切換
- 用戶回覆到貨通知：按「取貨」／「不收」（不收＝在管理員派工前就直接作廢，不會呼叫任何機器人動作）
- 機器人抵達：推播提醒＋「開啟相機掃碼」＋「拒收」按鈕
- 拒收（機器人已抵達後）：關門清任務畫面、觸發機器人送回管理室並自動開門，供管理員取出包裹
- 住戶取貨（兩階段）：LIFF掃碼驗證身分＋開門，再由住戶按「取貨完成」按鈕確認（QR驗證邏輯已完成，比對`scanned_content`與LINE `id_token`）
- 逾時自動退回：背景排程每分鐘檢查，超過8分鐘未完成取貨自動觸發（跟拒收共用同一套機器人動作：關門清畫面→送回開門）
- 我的包裹查詢：文字指令「我的包裹」，列出所有還沒結束（非completed/voided/rejected_at_door/returned_timeout）的包裹，附狀態說明，只有`pending`狀態才附「現在取」按鈕
- 用戶封鎖（Unfollow）處理：自動將對應綁定設為`inactive`，並記錄事件供追蹤

**管理員端（Dashboard, `/admin`）**
- 建立包裹並通知（防連點）
- 機器人即時狀態：位置、電量、各艙門狀況（自動輪詢，含機器人API原始資料結構的容錯解析）
- 包裹清單：狀態徽章、艙門、建立時間
- 門牌查詢框：輸入門牌查該門牌所有包裹歷史狀態（4種簡化分類：已完成／派送中／已退回／尚未派工），顯示收件人姓名（含是否已封鎖/停用標示）
- 紅色提示框：拒收／逾時／不收（作廢）需要管理員處理的包裹，統一列表＋各自的操作按鈕（「關門」／「確定」），避免同一筆包裹在畫面上出現重複按鈕
- 每日報表頁面（`/admin/reports`）：選日期查詢當天包裹狀態統計＋完整任務時間軸（`task_logs`表）

**系統層**
- 所有時間戳記統一存台灣當地時間（`now_taipei()`），不使用UTC
- 所有呼叫機器人API的地方統一透過`call_robot_api()`，支援重試、失敗時不誤判為成功
- 推播LINE訊息失敗（例如帳號已封鎖、token失效）不會讓API整個500，會記錄`notify_failed`並在Dashboard顯示
- `package_id`格式驗證：避免不合法的UUID直接打進資料庫查詢造成500
- 完整事件記錄（`task_logs`表）：取代原本只印在console、重啟就消失的log，可在每日報表回溯查詢

完整狀態機：
```
pending ─┬─→ voided（不收，從未派工）
         └─→ pickup_now → delivering → arrived ─┬─→ completed
                                                  ├─→ rejected_at_door（拒收）
                                                  └─→ returned_timeout（逾時未取，8分鐘）
```

### 待辦

- **多包裹批次派送**：目前「確認派送」是每筆包裹各自觸發（艙門數已擴充為4個，但派工仍是一對一）。若要支援「一次派送多筆、機器人自動依序跑完所有站再返回」，需要在完成/拒收/逾時各節點加上「檢查同一趟還有沒有下一站」的邏輯，這部分已有設計草案但尚未套用到目前這版程式碼
- **Dashboard即時通知（SSE）**：目前Dashboard靠前端輪詢（15～30秒），尚未改成後端主動推播
- **圖形化Rich Menu**：目前「我的包裹」、限本人通知切換都是純文字指令，尚未做成圖形選單
- **一戶多人的完整查詢/操作權限**：通知端已支援（依`solo_notify`決定通知範圍），但「我的包裹」查詢目前只認建立包裹時記錄的主要收件人（`Package.line_user_id`），同門牌其他人查詢不到

## 專案結構
```
line-backend/
├── requirements.txt
└── app/
    ├── __init__.py
    ├── config.py         # 讀取環境變數
    ├── db.py             # 資料庫連線設定
    ├── models.py         # Package、LineBinding、PackageRecipient、TaskLog 資料表定義
    ├── init_db.py        # 建立資料表用的腳本
    ├── line_verify.py    # LIFF id_token 驗證（QR取貨流程用）
    ├── line_messaging.py # 封裝呼叫LINE Messaging API的邏輯
    └── main.py           # FastAPI主程式、Webhook端點、Dashboard頁面、所有API路由
```

## 快速開始

### 1. 建立虛擬環境並安裝套件
```bash
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### 2. 設定環境變數

建立`.env`檔案（不要commit進版控），需要以下變數：
```
LINE_CHANNEL_SECRET=你的Channel Secret
LINE_CHANNEL_ACCESS_TOKEN=你的Channel Access Token
LIFF_ID=你的LIFF ID
LINE_LOGIN_CHANNEL_ID=你的Login Channel ID
ROBOT_API_BASE_URL=機器人服務的base URL
DATABASE_URL=postgresql+psycopg://postgres:你的密碼@localhost:5432/aurobox_line
APP_ENV=development
```

### 3. 建立資料表
```bash
python -m app.init_db
```
**注意**：目前部分欄位（例如`task_logs`表、`packages.door_closed_at`、`packages.acknowledged_at`）是後續開發過程中陸續加上去的，正式環境的資料庫如果是舊的，記得手動補`ALTER TABLE`，`init_db`只會處理全新建表的情況，不會自動幫既有資料表補欄位。

### 4. 啟動伺服器
```bash
uvicorn app.main:app --reload --port 8000
```

### 5. 開發階段對外連線
使用 ngrok 讓LINE能連到本機：
```bash
ngrok http 8000
```
ngrok免費版每次重啟網址都會換，記得同時更新兩個地方：
- LINE Developers Console 的 **Webhook URL**（Messaging API分頁，記得加`/webhook`）
- 對應Channel的 **LIFF Endpoint URL**（LIFF分頁，記得加`/liff/scan`，只填網域忘記加路徑是常見錯誤）

## API 測試
啟動伺服器後，開啟 `http://localhost:8000/docs` 可以互動測試所有API端點。

## 已確認的機器人API（`ROBOT_API_BASE_URL`）

| 用途 | 方法/路徑 | 說明 |
|---|---|---|
| 分配艙門 | `POST /api/doors/assign` | 回傳`door_number` |
| 裝載艙門 | `POST /api/doors/load` | 派送前確認包裹已放入艙門 |
| 派工 | `POST /api/robot/dispatch` | 單一目的地，帶`unit`/`package_id` |
| 取貨開門 | `POST /api/packages/{id}/pickup-complete` | 掃碼驗證通過後開門 |
| 取貨完成 | `POST /api/packages/{id}/complete` | 關門並釋放艙門；若所有艙門皆空，機器人自動觸發返航 |
| 拒收/逾時關門 | `POST /api/packages/{id}/cancel` | 關門、關閉任務畫面，包裹仍保留在艙門內（維持full） |
| 退回 | `POST /api/packages/return` | 退回包裹後釋放艙門，機器人送回管理室並開門 |
| 管理員取件關門 | `POST /api/doors/return-complete` | 拿出被退回的包裹後關閉艙門 |
| 即時狀態 | `GET /api/dashboard/status` | 位置、電量、艙門狀況（Dashboard輪詢用，注意實際資料在`robot_status.sources.v1/v2.data`底下，外層欄位不可靠） |

## 需要跟其他模組（送貨機器人／管理員Dashboard）確認的事項
1. 多包裹批次派送時，`/api/robot/dispatch`是否支援一次帶多個目的地，或是否需要我們自己依序呼叫單一目的地
2. `/api/packages/return`同時有多筆包裹要退回時，一次呼叫是否會把所有還在艙門裡的都一起帶回來（目前假設是，尚待大量實測驗證）
3. `robot_arrived`（`/packages/{id}/arrived`）目前由機器人模組在抵達時呼叫，實際串接是否已完全穩定
4. Dashboard即時通知若要做成SSE，需要跟機器人模組確認事件推送的時機與方式
