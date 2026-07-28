# LINE 後端 - 智連櫃社區 AMR 配送系統

對應整體系統三大部分（管理員 Dashboard／LINE／送貨機器人）中的 **LINE 後端 + 管理員 Dashboard** 部分。
機器人模組（flashbot 分支）由另一位隊友負責，本模組只透過 HTTP 呼叫機器人 API，不碰機器人硬體邏輯。

---

## 完整狀態機

```
pending ──PICKUP_NOW(取貨)──▶ pickup_now ──放置包裹+全部派送──▶ delivering ──機器人抵達──▶ arrived
   │                                                                                          │
   └──REJECT(不收)──▶ voided                                              ┌───────────────────┼───────────────────┐
                                                                        取貨完成              拒收                逾時8分鐘未取
                                                                            │                   │                   │
                                                                       completed         rejected_at_door    returned_timeout
```

- `LINE 後端是狀態的唯一權威來源`，機器人模組只負責硬體動作（開門/關門/移動/回報位置），不自己維護一套平行的包裹狀態。
- 每個狀態轉換都在對應端點裡用 `if package.status != 預期狀態: raise` 的方式手動把關，資料庫本身沒有 CHECK constraint 或 enum 型別限制。
- 一戶多件（`package_count`，1~4件）用單一 `Package` 列代表整批任務，不是拆成多筆各自的 package。

---

## 目前完成進度

### 住戶端（LINE / LIFF）
- 用戶綁定：LINE 聊天室輸入「門牌 姓名」完成綁定；文字指令「開啟/關閉限本人通知」切換 `solo_notify`
- 到貨通知：管理員建立包裹後推播，支援一戶多人（`solo_notify` 決定通知本人還是全戶）、支援一次多件（`quantity`，1~4件）
- 住戶回覆：「取貨」／「不收」／預約取貨時段（整點）
- 機器人抵達通知：附開啟 LIFF 相機掃碼、「暫時無法取貨（拒收）」按鈕
- **取貨完成改為 LIFF 頁面內直接操作**：LIFF 掃碼驗證成功後，同一頁直接顯示「取貨完成」鍵，按下即呼叫 `/packages/{id}/complete` 關門，不再透過 LINE 推播按鈕（減少一次切換聊天室的步驟）
- 掃碼驗證（`pickup-complete`）：比對 QR 內容＝`package_id`，並用 `liff.getIDToken()` 驗證 LINE 身分（`sub` 需在 `package_recipients` 名單內），**開門前用列鎖重新確認狀態**，避免掃碼驗證的空檔期間被同戶其他成員的拒收動作搶過去
- 「我的包裹」文字查詢：列出目前所有還沒結束的包裹任務（不限稍後再取）
- 逾時自動退回：背景排程每分鐘檢查，超過 8 分鐘自動觸發（8 分鐘而非 10 分鐘，因為機器人閒置超過 10 分鐘會死機）

### 管理員 Dashboard（`GET /admin`）
- 建立包裹：門牌／收件人／件數（1~4件）下拉選單，建立成功會顯示「已通知 X 位，Y 位失敗」，件數選單每次建立成功後自動跳回預設 1 件
- 包裹清單：分頁、依建立時間區間篩選、依門牌查詢
- **多選刪除**：「選取」鍵開啟勾選模式（整列可點擊選取、圓角放大 checkbox），可批次刪除包裹紀錄；只允許刪除「艙門非使用中」的包裹（`pickup_now` 已放置／`delivering`／`arrived`，或退回中艙門還沒關過的，一律擋下），避免刪掉資料庫紀錄後機器人那邊的艙門狀態變成查無來源
- 放置包裹（開艙門）／全部派送（批次派送＋單一目的地依序派送下一站）
- 機器人狀態：即時位置/電量/艙門狀態、開啟艙門／關閉艙門（機器人回到管理室後，管理員取件用）
- **叫回機器人**：緊急中斷機器人目前任務，連帶把所有還在進行中的包裹任務（`pickup_now`已指派艙門／`delivering`／`arrived`）重置為「待派送」、清空艙門欄位，讓管理員可以重新走一次「放置包裹→派送」
- 機器人回充電站
- 手動聯繫住戶：已完成但懷疑艙門裡還留有包裹時，補發提醒
- 住戶綁定管理（`/admin/residents`）：查詢/刪除/修改綁定
- 每日報表（`/admin/reports`）：當日包裹狀態統計＋完整 `task_logs` 時間軸

### 退回/作廢包裹處理頁（`GET /admin/exceptions`）
- 列出所有拒收／逾時／不收（`voided`／`rejected_at_door`／`returned_timeout`）且還沒銷案的包裹
- 通知住戶：稍後再取提醒（只能通知一次）
- 重新派貨：建立一筆全新包裹、沿用原門牌與收件人，重新走一次到貨通知流程（僅限主畫面已完成確認/關門的）
- 手動銷案／**多選批次銷案**（跟主頁面同一套選取 UI：選取鍵在門牌查詢左邊、整列可點擊、動作完成自動退出選取模式）
- 手動強制解決（`force-resolve`）：機器人硬體被人工直接處理過後，跳過系統流程直接標記已解決

---

## 已知的併發/競態限制（尚未修）

系統裡大部分「讀狀態→改狀態」的地方都有處理併發，但還有幾個已知縫隙，記錄下來提醒自己之後有空要補：

| 位置 | 有沒有鎖 | 風險 |
|---|---|---|
| `handle_postback`（LINE 所有 postback）| ✅ `with_for_update(nowait=True)` | — |
| `complete_pickup`（取貨完成，postback 與 LIFF 共用） | ✅ | — |
| `pickup_verify`（LIFF 掃碼開門） | ✅（本次新增） | 開門前重新鎖定確認狀態，關掉「掃碼驗證中，同戶另一位成員拒收」的競態窗口 |
| `check_pickup_timeout`／`check_assign_timeout`／`check_return_timeout`／`check_auto_close_case`（4支背景排程） | ❌ | **最需要優先修**：排程整批查詢＋逐筆寫回，沒有重新確認目前狀態，理論上可能把剛好在同一時刻被使用者動作改掉的狀態覆蓋回去（例如住戶剛好在逾時判定的臨界點完成取貨，卻被排程蓋回退回狀態） |
| `place_package`／`admin_dispatch_batch` | ❌ | 雙分頁或連續手滑點擊，理論上可能讓同一筆包裹被重複指派艙門、重複派送 |
| `admin_robot_recall` | ❌ | 如果剛好跟放置包裹/全部派送同時執行，可能重置到不該重置的包裹 |

---

## 專案結構

```
line-backend/
├── requirements.txt
├── README.md
└── app/
    ├── __init__.py
    ├── config.py          # 讀取環境變數（含 ROBOT_API_BASE_URL、ROBOT_HOME_POINT_NAME）
    ├── db.py               # 資料庫連線設定
    ├── models.py           # Package／LineBinding／PackageRecipient／TaskLog 資料表定義
    ├── init_db.py          # 建立資料表用的腳本
    ├── line_verify.py      # 驗證 LIFF 傳來的 ID Token（掃碼取貨身分驗證，實際使用中）
    ├── line_messaging.py   # 封裝呼叫 LINE Messaging API 的邏輯
    ├── main.py             # FastAPI 主程式：Webhook、住戶端 API、管理員 Dashboard（含內嵌 HTML/JS）、背景排程
    └── tests/
        └── test_config.py
```

Dashboard、退回/作廢處理頁、每日報表頁、住戶綁定管理頁、LIFF 掃碼頁，都是直接在 `main.py` 裡用 Python 字串組出完整 HTML/JS 回傳（沒有另外的前端專案/模板檔案）。

**⚠️ 這幾個大段 HTML 常數用的是一般三重引號字串（不是 raw string）**：如果要在裡面的 JS 加 `\n`、`\t` 這類跳脫序列給瀏覽器解析，切記要在 Python 原始碼裡寫成雙反斜線（`\\n`），不然 Python 自己會先把它解析成真正的換行字元，送到瀏覽器手上時 JS 語法就會壞掉（曾經真實發生過，整個 Dashboard 白屏卡在「載入中」）。

---

## 快速開始

### 1. 建立虛擬環境並安裝套件
```bash
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### 2. 設定環境變數
建立 `.env` 檔案（不要 commit 進版控）：
```
LINE_CHANNEL_SECRET=你的Channel Secret
LINE_CHANNEL_ACCESS_TOKEN=你的Channel Access Token
LIFF_ID=你的LIFF ID
LINE_LOGIN_CHANNEL_ID=你的Login Channel ID
DATABASE_URL=postgresql+psycopg://postgres:你的密碼@localhost:5432/aurobox_line
ROBOT_API_BASE_URL=機器人模組對外的API網址
ROBOT_HOME_POINT_NAME=機器人回到管理室時，dashboard/status回報的地圖點位名稱
APP_ENV=development
```

### 3. 建立資料表
```bash
python -m app.init_db
```
> 如果 `models.py` 欄位有改動（新增欄位等），需要先 `DROP TABLE` 對應的表再重新執行，`init_db.py` 只會建立不存在的表，不會自動 migrate 既有欄位。

### 4. 啟動伺服器
```bash
uvicorn app.main:app --reload --port 8000
```
或用 `start-server.bat`（Windows，已內建虛擬環境啟用+切換目錄）。

### 5. 開發階段對外連線
```bash
ngrok http 8000
```
或 `start-ngrok.bat`。把產生的網址設定到 LINE Developers 的 Webhook URL（記得加 `/webhook`），LIFF 網址也要對應更新。

---

## 常用網址一覽

| 路徑 | 用途 |
|---|---|
| `/docs` | FastAPI Swagger UI，互動測試所有 API 端點 |
| `/admin` | 管理員 Dashboard 主畫面 |
| `/admin/exceptions` | 退回/作廢包裹處理頁 |
| `/admin/reports` | 每日報表 |
| `/admin/residents` | 住戶綁定管理 |
| `/liff/scan` | LIFF 掃碼取貨頁（需帶 `?package_id=`） |

---

## 測試

`mock_robot.py`（獨立 Flask app）模擬 flashbot 的機器人 API，含記憶體版艙門狀態，可以不接真實硬體就測完整流程。

---

## 需要跟機器人模組確認/同步的事項

- `check_pickup_timeout` 等 4 支背景排程目前沒有列鎖保護，理論上跟即時的使用者/管理員動作有極小機率互相覆蓋，之後有空要補 `with_for_update(skip_locked=True)`
- `place_package`／`admin_dispatch_batch` 目前也還沒加鎖，雙管理員/雙分頁同時操作理論上可能重複下指令給機器人
- 一戶多件（`package_count > 1`）目前 `try_assign_door` 的 body 會帶 `quantity`，但機器人端是否已經支援回傳 `door_numbers`（陣列）而不是單一 `door_number`，需要跟機器人 team 對規格，沒對齊前 `quantity > 1` 實際上還是只會拿到一個門號
