# Aurobox 全目錄盤點報告

完成日期: 2026-07-17  
版本: 0.3.0  
狀態: 已完成 PostgreSQL 遷移與高併發防超賣架構升級

## 1. 本次工作範圍

本次針對 AUROBOX 根目錄進行更新盤點，涵蓋：

- src/aurobox 核心程式
- tests 測試
- scripts 維運工具
- run.py、pyproject.toml
- 既有文件（README.md、REPORT.md、docs 目錄）

目的：將文件內容與實際程式行為對齊，並反映由 SQLite 遷移至 PostgreSQL 以及解決高併發競態條件（Race Condition）的重大修正項目。

## 2. 程式現況總結

### 2.1 核心定位

專案目前是 Flashbot 硬體控制 API，聚焦於：

- Flask API 對外接口
- Pudu Open Platform 呼叫與簽章
- 艙門狀態最小管理（Door）
- 機器人點位記錄（RobotState）
- 背景輪詢（抵達判斷 / 通知中央系統）

### 2.2 主要模組

- app.py: App factory、DB 初始化、**單例模式綁定控制器**與預設四個艙門重置
- api.py: 對外流程 API，**包含防超賣 (Overbooking) 的行級鎖邏輯**
- robot.py: FlashbotController 與多來源狀態整合
- pudu_client.py: HMAC 簽章、GET/POST 包裝、指令紀錄，**並統整為批次陣列控制 (`control_doors`)**
- models.py: Door、RobotState 與資料約束
- services.py: Controller 取得、目標點記錄、全空返航 (新增防原地轉圈邏輯)
- tasks.py: 背景輪詢抵達、通知中央系統、顯示 QR 內容，**完善全域佇列與雙重檢查鎖**
- cli.py: CLI 子命令封裝

## 3. API 現況（依實際路由）

基礎：

- GET /
- GET /healthz

硬體流程：

- POST /api/doors/assign
- POST /api/doors/load
- POST /api/robot/dispatch
- POST /api/packages/<package_id>/pickup-complete
- POST /api/packages/<package_id>/complete
- POST /api/packages/<package_id>/cancel
- POST /api/packages/return
- POST /api/doors/return-complete
- GET /api/dashboard/status

## 4. 資料層現況

**已正式遷移至 PostgreSQL**，取代原有的 SQLite (instance/aurobox.db)，以支援高併發與行級鎖 (Row-level Lock)。

資料表：

- doors: sn, door_number, status, package_id, task_id, updated_at
- robot_state: sn, last_point, updated_at

約束：

- 僅允許艙門 H_01/H_02/H_03/H_04
- status 僅允許 empty/assigned/full

## 5. 測試與可執行狀態

### 5.1 測試覆蓋

目前測試包含：

- tests/test_pudu_client.py
  - 設定載入
  - 必填環境變數驗證
  - Client / Controller 初始化
- tests/test_api_integration.py（本次新增）
  - assign -> load 流程
  - dispatch（含 task_id 寫入）
  - complete（釋放艙門）
  - cancel（保留 FULL 並清 task_id）

### 5.2 壓力測試 (Load Test)

- 已撰寫並執行 20 執行緒瞬間併發腳本 (`load_test.py`)。
- 測試結果：成功
  瞬間發起 20 個請求
  成功分配 (200): 4 (請求01~04)
  擋車或無空門 (400/409): 16 (請求05~20，皆為400)

## 6. 差異與修正狀態

- **架構安全性**：已移除全域變數，改用 Flask `current_app` 綁定單例控制器。
- **硬體保護機制**：已修正批次裝貨時被導航指令強制關門的韌體衝突。
- **資料庫鎖死**：透過 PostgreSQL 取代 SQLite，解決多執行緒背景任務與 API 請求交錯造成的崩潰。

## 7. 本次文件更新成果

- 已更新 README.md：
  - 加入 PostgreSQL Docker 與 Linux 本機建置教學。
  - 更新環境需求 (新增 `psycopg2-binary`)。
  - 版本資訊更新為 0.3.0。
- 已更新 REPORT.md（本文件）：
  - 記錄資料庫遷移與高併發架構升級實測結果。

## 8. 建議下一步

1. 準備生產環境的部署管線 (Deployment Pipeline)，建議導入 **Gunicorn** 搭配 **Nginx** 作為反向代理。
2. 考慮將整個 Flask 應用程式 Docker 化，以統一開發與正式上線的環境。
3. 視需求擴充整合測試到 return / return-complete / dashboard/status。
4. 評估加入 CI 自動執行 pytest，確保後續改動不回歸。

