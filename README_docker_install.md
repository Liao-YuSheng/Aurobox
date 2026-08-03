# 缺漏
- 本執行檔尚需一固定ngrok連結，供Line Webhook得以運作
  （取得方式見下方「附錄：如何取得固定 ngrok 網域」）

# 將在電腦內下載的應用程式
- Docker Desktop

# 需輸入的資訊:
1. 普渡 (Pudu) API Key: 
2. 普渡 (Pudu) API Secret: 
3. 店鋪或場域 ID (Aurotek_id): 
4. 機器人序號 (例如 8FF0559...): 
5. 預設地圖名稱:
6. 管理室點位名稱 (例如 office):
7. 充電樁點位名稱:
8. 硬體艙門模式 (填入 3_DOORS 或 4_DOORS):
9. LINE Channel Secret: 
10. LINE Access Token: 
11. LIFF ID:
12. LIFF ID RETURN:
13. LINE LOGIN CHANNEL ID:
14. ADMIN_USERNAME (預設為 aurotek):
15. ADMIN_PASSWORD (預設為 flashbot):
16. ngrok Auth Token:
17. ngrok 固定網域: (取得方式見附錄，免費帳號就有一個，不用付費)

# 手動操作方式
- 對start_aurobox.bat點擊兩下
- 輸入參數(見.input.example)
- Docker Desktop安裝成功後需要手動按鈕Accept服務條款 -> 進入登錄畫面，可按右上方的Skip -> 手動按下左下角開始鍵，確認顯示綠色的"Engine Running"
- 若系統無繼續下載其他檔案，請關閉當前終端機視窗後，重新點擊start_aurobox.bat

# LINE 官方帳號 URL 設定（第一次設定時需要做，之後只要網域沒變就不用再改）

因為第 17 項用的是固定 ngrok 網域，理論上下面這幾個 URL **只要設定一次**，之後每次重開機器重跑 `start_aurobox.bat`，只要 ngrok 接的是同一個固定網域，這裡都不用再改。

## 1. 設定 Webhook URL（讓 LINE 訊息/按鈕能傳到本機）
1. 登入 [LINE Developers Console](https://developers.line.biz/)
2. 選擇對應的 **Messaging API Channel**
3. 進入 **Messaging API** 分頁，找到 **Webhook settings**
4. Webhook URL 填：
   ```
   https://你的固定ngrok網域/webhook
   ```
5. 按右邊 **Verify** 測試連線（要先確定 Docker、ngrok 都已經啟動、顯示正常）
6. 確認 **Use webhook** 開關是打開的

## 2. 設定兩個 LIFF App 的 Endpoint URL
LIFF App 掛在 **LINE Login Channel** 底下，這個系統需要兩個不同的 LIFF App（一個不能兩用，Endpoint URL 是一對一綁定的）：

| LIFF App 用途 | Endpoint URL | 對應第几項參數 |
|---|---|---|
| 掃碼取貨 | `https://你的固定ngrok網域/liff/scan` | 對應第 11 項 LIFF ID |
| 退貨申請 | `https://你的固定ngrok網域/liff/return-request` | 對應第 12 項 LIFF ID RETURN |

設定步驟：
1. LINE Developers Console → 選 **LINE Login Channel** → **LIFF** 分頁
2. 點 **Add**，建立第一個 LIFF App，Endpoint URL 填上面表格裡「掃碼取貨」那一列的網址，Size 選 Full，建立後複製 LIFF ID，填進 `.input` 的第 11 項
3. 再點一次 **Add**，建立第二個 LIFF App，Endpoint URL 填「退貨申請」那一列的網址，複製 LIFF ID，填進第 12 項

## 3. 什麼時候需要重新設定
- 如果哪天固定 ngrok 網域整個換掉（例如換了 ngrok 帳號），上面三個 URL（Webhook + 兩個 LIFF Endpoint）都要跟著重新填一次
- 只要網域沒變，重開機、重跑 `start_aurobox.bat`，這裡完全不用動

# 關閉
## 關閉Docker Desktop
- 左下角點選 Quit Docker Desktop
- 在終端機執行:
```bash
wsl --shutdown
```
## 關閉ngrok
- 點選ngrok終端機畫面，按下ctrl+C

# 使用網址
- http://127.0.0.1:8000/admin (會自動開啟)
- http://127.0.0.1:5000/

---

# 附錄：如何取得固定 ngrok 網域

LINE 要求 Webhook 網址必須是固定不變的，但 ngrok 免費版預設每次重啟都會換一個新的隨機網址，一換掉，LINE 後台登記的舊網址就會失效，訊息/按鈕就收不到。解法是去 ngrok 帳號裡領一個「固定網域」：

1. 到 [ngrok.com](https://ngrok.com) 註冊帳號（免費即可）
2. 登入後到 Dashboard，找 **Universal Edge → Domains**（或類似名稱的選單）
3. 點 **+ New Domain**，會自動配一個永久不變的網址（長得像 `你的名字.ngrok-free.app`）
4. **免費帳號就有一個這樣的固定網域，不需要付費**——這是 ngrok 這幾年新推出的功能
5. 把這個網址填進第 17 項

領到之後，回到本文件「LINE 官方帳號 URL 設定」那一節，把 Webhook URL 跟兩個 LIFF Endpoint URL 都設定好，就完成整套設定了。
