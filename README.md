# LINE 後端 - 智連櫃社區 AMR 配送系統

對應整體系統三大部分（管理員Dashboard／LINE／送貨機器人）中的LINE部分。

## 目前完成進度（更新於 2026/7/20）

### 已完成

**住戶端（LINE）**
- 用戶綁定：LINE聊天室輸入「門牌 姓名」完成綁定
- 到貨通知：管理員建立包裹後推播，支援「限本人接收」（`solo_notify`）
- 住戶回覆：「取貨」／「不收」
- 抵達通知：推播提醒＋開啟相機掃碼按鈕＋「拒收」按鈕
- QR掃碼驗證：LIFF掃碼內容比對＋LINE ID Token身份驗證＋收件人清單比對，三層驗證都通過才開門
- 取貨完成兩階段：掃碼開門與按下「取貨完成」分開處理，已解決連續按兩次觸發的race condition
- 逾時自動退回：背景排程每分鐘檢查，`arrived`狀態超過8分鐘自動觸發退回
- 我的包裹查詢：文字指令「我的包裹」，排除已終止狀態的包裹
- 開啟／關閉限本人通知：文字指令切換

**管理員後台（四頁）**
- `/admin` Dashboard：建立包裹（門牌+收件人下拉選單）、機器人即時狀態（15/30秒自動輪詢）、包裹清單（每50筆分頁＋門牌查詢，查詢結果灰底顯示）、放置包裹＋全部批次派送、拒收/逾時/不收待處理提示框
- `/admin/reports` 每日報表：包裹狀態統計、任務時間軸（依包裹分組，一頁顯示一筆包裹的完整紀錄）
- `/admin/exceptions` 退回/作廢包裹處理：門牌查詢、「重新派貨」（沿用原門牌與收件人綁定建立全新包裹）、「銷案」（手動結案，只影響本頁顯示）、已重新派送且新包裹已完成的紀錄自動從頁面消失
- `/admin/residents` 住戶綁定管理：查看所有門牌所有綁定（含已停用）、刪除誤綁或惡意綁定的LINE帳號

**機器人整合**
- 完整狀態機：`pending → pickup_now → delivering → arrived → completed`，另有 `rejected_at_door` / `returned_timeout` / `voided` 三種例外分支
- 目前艙位為四艙位（`H_01`～`H_04`，機器人端硬性限制，同時最多4個艙門在用），支援單次最多5筆包裹需求：多包裹批次派送機制（`place_package`／`admin_dispatch_batch`／`advance_trip_or_return`）先派送前4筆（裝滿4個艙門），全部送達、機器人返航、艙門清空後，管理員再對第5筆重複「放置包裹」＋派送，等於分兩趟循環完成——這個流程完全依賴機器人回報的艙門空缺狀態動態判斷，程式碼裡沒有寫死門數上限，未來艙位數量調整不需要改code
- 多包裹批次派送：一次關閉所有已裝載艙門，依序派往每一站（`advance_trip_or_return`），整趟結束後自動判斷要不要帶回未完成的包裹
- 拒收/逾時退回統一走「機器人關門帶回管理室＋管理員取出後按關門」流程

```
狀態機：
pending → pickup_now → delivering → arrived → completed
                                   ↘ rejected_at_door ─┐
                       ↘ voided                        ├→ 例外處理頁
       逾時8分鐘 → returned_timeout ────────────────────┘
```

### 待辦／已知風險

**尚未處理的邊界情況**（依風險排序）
- `delivering` 狀態沒有逾時偵測機制，機器人卡在半路不會被自動發現
- 批次派送時「艙門已關但派送失敗」，包裹會卡在看不出異常的狀態，且不會出現在例外處理頁面
- 背景排程逾時檢查跟使用者正在取貨的操作之間沒有互斥，理論上可能同時發生衝突
- 若未來改用多worker部署，背景排程（APScheduler）會在每個worker各自重複執行
- 同一門牌多筆包裹共用同一個艙門的情境，目前完全沒有群組化處理
- 一戶多收件人各自操作時沒有互斥（例如一人在家點拒收、同時另一人正在機器人前取貨）
- Webhook事件處理迴圈沒有try/except，單一事件解析失敗會讓同批次其餘事件無法被處理
- `verify_liff_id_token` 沒有捕捉網路層例外（目前只處理LINE官方API回傳非200的情況）

**功能性待辦**
- 住戶綁定沒有身份驗證，任何人輸入任意「門牌 姓名」都能綁定成功——規劃中的解法是白名單機制（管理員預先登記每個門牌對應的住戶姓名，綁定時比對，不符合就拒絕），需要先取得完整住戶名冊才能上線，目前尚未實作
- 圖形化Rich Menu，已設計、PNG已生成，尚未部署，目前用文字指令代替
- `/admin/*` 所有路由目前沒有任何身份驗證機制

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
    ├── line_verify.py    # 驗證LIFF傳來的LINE ID Token，確認掃碼者的真實身份
    ├── line_messaging.py # 封裝呼叫LINE Messaging API的邏輯
    └── main.py           # FastAPI主程式、Webhook端點、所有API路由與管理後台四個頁面
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
ROBOT_API_BASE_URL=機器人模組的API網址
DATABASE_URL=postgresql+psycopg://postgres:你的密碼@localhost:5432/aurobox_line
APP_ENV=development
```

### 3. 建立資料表
```bash
python -m app.init_db
```

如果是在既有資料庫上加新欄位（例如這次新增的 `redispatched_at`／`case_closed_at` 等），不要重跑 `init_db`（會漏掉ALTER TABLE），改用個別的migration腳本手動加欄位。

### 4. 啟動伺服器
```bash
uvicorn app.main:app --reload --port 8000
```

### 5. 開發階段對外連線
使用 ngrok 讓LINE能連到本機：
```bash
ngrok http 8000
```
將產生的網址設定到 LINE Developers 的 Webhook URL（記得加`/webhook`）。

## 管理後台頁面

啟動伺服器後可以直接訪問：
- `http://localhost:8000/admin` — Dashboard主頁
- `http://localhost:8000/admin/reports` — 每日報表
- `http://localhost:8000/admin/exceptions` — 退回/作廢包裹處理
- `http://localhost:8000/admin/residents` — 住戶綁定管理

## API 測試
啟動伺服器後，開啟 `http://localhost:8000/docs` 可以互動測試所有API端點。

## 需要跟機器人模組確認的事項
1. 機器人回到管理室後的開門時機（見上方「需要機器人team配合」）
2. Pudu API連線狀態與艙門即時狀態，目前是LINE後端主動呼叫 `/api/dashboard/status` 轉發給Dashboard顯示
