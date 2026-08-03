# 缺漏
- 本執行檔尚需一固定ngrok連結，供Line Webhook得以運作

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
17. ngrok 固定網域:

# 手動操作方式
- 對start_aurobox.bat點擊兩下
- 輸入參數(見.input.example)
- Docker Desktop安裝成功後需要手動按鈕Accept服務條款 -> 進入登錄畫面，可按右上方的Skip -> 手動按下左下角開始鍵，確認顯示綠色的"Engine Running"
- 若系統無繼續下載其他檔案，請關閉當前終端機視窗後，重新點擊start_aurobox.bat

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



