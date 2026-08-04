# Aurobox Docker 安裝與設定說明

> 本文件整理 Docker、ngrok 與 LINE 連動所需的步驟，請依序完成。

## 1. 快速開始摘要

若你想先快速知道整體流程，建議依照以下順序操作：

1. 取得固定 ngrok 網域
2. 完成 LINE Webhook 與 LIFF Endpoint 設定
3. 準備好所有必要參數，並填入 `.input.example` 檔案
4. 啟動 `start_aurobox.bat`，開始下載 Docker Desktop 與 ngrok
5. 確認管理後台可正常開啟

---

## 2. 前置需求

### 2.1 需要下載的應用程式
- Docker Desktop

### 2.2 啟動前請先確認
- 請先確認 `.input.example`
- 確認 Docker Desktop 已安裝完成，並可正常啟動
- 確認 ngrok 帳號與固定網域已取得 (請見下方「附錄：如何取得固定 ngrok 網域」)
- 第一次啟動時，可能需要手動接受 Docker 的服務條款

## 3. 需要填入的資訊

請準備以下參數：

1. 普渡 (Pudu) API Key
2. 普渡 (Pudu) API Secret
3. 店鋪或場域 ID (Aurotek_id)
4. 機器人序號（例如 8FF0559...）
5. 預設地圖名稱
6. 管理室點位名稱（例如 office）
7. 充電樁點位名稱
8. 艙門模式（填入 3_DOORS 或 4_DOORS）
9. 艙門編排（4 門模式可免填）
10. LINE Channel Secret
11. LINE Access Token
12. LIFF ID
13. LIFF ID RETURN
14. LINE LOGIN CHANNEL ID
15. ADMIN_USERNAME（預設為 aurotek）
16. ADMIN_PASSWORD（預設為 flashbot）
17. ngrok Auth Token
18. ngrok 固定網域（免費帳號即可，取得方式見附錄）

## 4. 手動操作流程

1. 雙擊啟動檔案 `start_aurobox.bat`
2. 輸入參數（請參考 `.input.example`）
3. Docker Desktop 安裝完成後，手動點擊 Accept 接受服務條款
4. 進入登入畫面時，可點選右上角 Skip
5. 手動點選左下角 Start，確認顯示綠色的 "Engine Running"
6. 若系統沒有繼續下載其他檔案，請關閉目前終端機視窗後，再重新點擊 `start_aurobox.bat`

> 建議第一次啟動時不要立即關閉視窗，讓程式完整下載與初始化。

## 5. LINE 官方帳號 URL 設定

因為第 18 項使用的是固定 ngrok 網域，下面這幾個 URL 通常只需要設定一次。只要 ngrok 維持接到同一個固定網域，之後每次重開機器或重跑 start_aurobox.bat，都不需要再修改。

### 5.1 設定 Webhook URL

讓 LINE 訊息與按鈕能正確傳到本機：

1. 登入 [LINE Developers Console](https://developers.line.biz/)
2. 選擇對應的 Messaging API Channel
3. 進入 Messaging API 分頁，找到 Webhook settings
4. 將 Webhook URL 設為：

   ```text
   https://你的固定ngrok網域/webhook
   ```

5. 點擊右側 Verify 測試連線（請先確認 Docker 與 ngrok 都已正常啟動）
6. 確認 Use webhook 開關已開啟

### 5.2 設定兩個 LIFF App 的 Endpoint URL

LIFF App 掛在 LINE Login Channel 底下，系統需要兩個不同的 LIFF App，因為 Endpoint URL 是一對一綁定的。

| LIFF App 用途 | Endpoint URL | 對應參數 |
|---|---|---|
| 掃碼取貨 | `https://你的固定ngrok網域/liff/scan` | 第 12 項 LIFF ID |
| 退貨申請 | `https://你的固定ngrok網域/liff/return-request` | 第 13 項 LIFF ID RETURN |

設定步驟：

1. 進入 LINE Developers Console → 選擇 LINE Login Channel → LIFF 分頁
2. 點選 Add，建立第一個 LIFF App
   - Endpoint URL 填入「掃碼取貨」那一列的網址
   - Size 選 Full
   - 建立後複製 LIFF ID，填入第 12 項
3. 再次點選 Add，建立第二個 LIFF App
   - Endpoint URL 填入「退貨申請」那一列的網址
   - 複製 LIFF ID，填入第 13 項

### 5.3 何時需要重新設定

- 如果固定 ngrok 網域整個變更（例如換了 ngrok 帳號），Webhook 與兩個 LIFF Endpoint 都需要重新設定一次。
- 只要網域沒有變更，重開機或重跑 start_aurobox.bat 時，這裡通常不用再動。

## 6. 常見問題與排錯

### 6.1 啟動後沒有繼續下載其他檔案
- 關閉目前終端機視窗後，再重新點擊 `start_aurobox.bat`
- 確認 Docker Desktop 已正常啟動

### 6.2 LINE 訊息無法收到
- 先確認 Webhook URL 是否填寫正確
- 確認 ngrok 的固定網域沒有變更
- 確認 Docker 與 ngrok 都已正常啟動

### 6.3 LIFF App 無法正常開啟
- 確認 Endpoint URL 是否與兩個 LIFF App 分別對應
- 確認 LIFF ID 與 LIFF ID RETURN 是否填入正確

## 7. 關閉服務

### 7.1 關閉 Docker Desktop
- 左下角點選 Quit Docker Desktop
- 在終端機執行：

```bash
wsl --shutdown
```

### 7.2 關閉 ngrok
- 點選 ngrok 終端機畫面，按下 Ctrl + C

## 8. 使用網址

- 管理後台： http://127.0.0.1:8000/admin

## 附錄：如何取得固定 ngrok 網域

LINE 會要求 Webhook 網址必須是固定不變的，但 ngrok 免費版預設每次重啟都會換新的隨機網址。一旦網址變更，LINE 後台原本登記的舊網址就會失效，訊息與按鈕就收不到。

解法是到 ngrok 帳號中領取一個「固定網域」：

1. 到 [ngrok.com](https://ngrok.com) 註冊帳號（免費即可）
2. 登入後進入 Dashboard，找尋 Universal Edge → Domains（或類似選單）
3. 點選 + New Domain，系統會自動配一個永久不變的網址，例如 `你的名字.ngrok-free.app`
4. 免費帳號也有這項功能，不需要付費
5. 將這個網址填入第 17 項

取得後，回到本文件的「LINE 官方帳號 URL 設定」章節，把 Webhook URL 與兩個 LIFF Endpoint URL 一起設定完成即可。
