# LINE 後端 - 智連櫃社區 AMR 配送系統

對應整體系統三大部分（管理員Dashboard／LINE／送貨機器人）中的LINE部分。

## 目前完成進度

### 已完成
- 用戶綁定：住戶在LINE聊天室輸入「門牌 姓名」完成綁定
- 到貨通知：管理員登記包裹後，推播通知住戶，支援「限本人接收」設定
- 用戶回覆：住戶按「取貨」／「稍後再取」
- 管理員確認出發：放貨與派工合併為單一動作
- 機器人抵達（模擬）：記錄抵達時間，推播提醒＋暫時無法取貨按鈕
- 住戶取貨（兩階段）：掃碼開門與按下取貨完成分開處理
- 逾時自動退回：背景排程每分鐘檢查，超過10分鐘自動觸發
- 機器人返回管理室：僅記錄log，不通知住戶
- 我的包裹查詢：文字指令查詢「稍後再取」清單

完整狀態機：
pending → pickup_now → delivering → arrived → completed
↘ returned_cancelled
↘ returned_timeout

### 待辦（需要其他模組配合才能繼續）
- QR Code驗證邏輯（`pickup-complete`目前為TODO，等機器人team提供QR Code格式規格）
- Dashboard即時通知（SSE）
- 圖形化Rich Menu（目前用文字指令代替）
- 一戶多人的完整查詢/操作權限（通知端已支援，查詢端仍有限制）

## 專案結構
line-backend/
├── requirements.txt
└── app/
├── init.py
├── config.py         # 讀取環境變數
├── db.py             # 資料庫連線設定
├── models.py         # Package、LineBinding 資料表定義
├── init_db.py        # 建立資料表用的腳本
├── line_verify.py    # （目前未使用，保留供未來QR Code驗證參考）
├── line_messaging.py # 封裝呼叫LINE Messaging API的邏輯
└── main.py           # FastAPI主程式、Webhook端點、所有API路由

## 快速開始

### 1. 建立虛擬環境並安裝套件
```bash
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### 2. 設定環境變數

建立`.env`檔案（不要commit進版控），需要以下變數：
LINE_CHANNEL_SECRET=你的Channel Secret
LINE_CHANNEL_ACCESS_TOKEN=你的Channel Access Token
LIFF_ID=你的LIFF ID
LINE_LOGIN_CHANNEL_ID=你的Login Channel ID
DATABASE_URL=postgresql+psycopg://postgres:你的密碼@localhost:5432/aurobox_line
APP_ENV=development

### 3. 建立資料表
```bash
python -m app.init_db
```

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

## API 測試
啟動伺服器後，開啟 `http://localhost:8000/docs` 可以互動測試所有API端點。

## 需要跟其他模組（送貨機器人／管理員Dashboard）確認的事項
1. `/packages/{id}/stored` 確認出發後，如何觸發機器人真正派工（直接呼叫API，或機器人team自行輪詢資料庫）
2. `departed`／`arrived`／`returned` 三支API，由機器人模組在偵測到對應狀態變化時呼叫
3. QR Code格式、產生時機、由誰產生
4. Dashboard串接這些API的方式是否需要調整
