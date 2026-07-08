<<<<<<< HEAD
# LINE 後端 - 起始專案（階段0）

對應《LINE模組_實作步驟.md》階段0～階段2的骨架。

## 快速開始

### 1. 建立虛擬環境
```bash
python -m venv venv

# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

### 2. 安裝套件
```bash
pip install -r requirements.txt
```

### 3. 設定環境變數
把 `.env.example` 複製一份改名為 `.env`，填入你在階段0拿到的三組資料：

```bash
cp .env.example .env
```

編輯 `.env`，貼上：
- `LINE_CHANNEL_SECRET`（Messaging API Channel > Basic settings）
- `LINE_CHANNEL_ACCESS_TOKEN`（Messaging API Channel > Messaging API 分頁 > Issue）
- `LIFF_ID`（LINE Login Channel > LIFF 分頁）

`DATABASE_URL` 現在可以先不管，階段1才會用到。

### 4. 啟動伺服器
```bash
uvicorn app.main:app --reload --port 8000
```

打開瀏覽器 http://localhost:8000ㅤ應該看到：
```json
{"status": "ok", "message": "LINE backend is running", "env": "development"}
```

看到這個就代表程式本身沒問題。

### 5. 用 ngrok 讓 LINE 連得到你的本機

LINE 的Webhook要求必須是公開的HTTPS網址，本機開發階段用 ngrok 產生一個暫時的公開網址：

```bash
# 如果還沒安裝 ngrok，先到 https://ngrok.com/download 安裝
ngrok http 8000
```

執行後會顯示一行類似：
```
Forwarding  https://xxxx-xx-xx-xxx-xx.ngrok-free.app -> http://localhost:8000
```

複製這個 `https://xxxx....ngrok-free.app` 網址。

### 6. 設定 Webhook URL
1. 回到 LINE Developers Console，進你的 Messaging API Channel
2. 「Messaging API」分頁 → 找到「Webhook settings」
3. Webhook URL 填入：`https://xxxx....ngrok-free.app/webhook`（**注意結尾要加 /webhook**）
4. 點擊「Verify」，應該顯示 **Success**
5. 確認「Use webhook」開關是打開的

### 7. 實際測試
用手機 LINE 掃描你的官方帳號 QR Code、加入好友。這時候：
- 終端機（跑 uvicorn 那個視窗）應該印出 `[follow] 新用戶加入好友, user_id=xxxx`
- 這樣就代表 Webhook 整條路都通了：LINE → ngrok → 你的FastAPI → 簽章驗證通過 → 印出log

## 驗收checklist（對應階段0）

- [ ] `uvicorn app.main:app --reload` 能正常啟動，沒有錯誤
- [ ] 瀏覽器打開 localhost:8000 看到健康檢查訊息
- [ ] ngrok 產生公開網址
- [ ] LINE Developers 的 Webhook Verify 顯示 Success
- [ ] 手機實際加好友，終端機印出 `[follow]` log

全部打勾，階段0跟階段2的骨架就算完成，可以往階段1（資料庫）走。

## 專案結構
```
line-backend/
├── requirements.txt
├── .env.example       # 複製成 .env 並填入機密資訊
├── .env                # 不要commit進版控！
└── app/
    ├── __init__.py
    ├── config.py       # 讀取環境變數
    └── main.py         # FastAPI主程式 + Webhook端點
```

之後階段3開始，會陸續在 `app/` 底下新增 `routes/`、`services/`、`db/` 等資料夾，對應實作步驟文件裡的模組。

