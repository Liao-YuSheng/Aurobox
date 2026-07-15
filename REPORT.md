# Aurobox 全目錄盤點報告

完成日期: 2026-07-15  
版本: 0.2.1  
狀態: 已完成文件同步與 API 整合測試補齊

## 1. 本次工作範圍

本次針對 AUROBOX 根目錄進行更新盤點，涵蓋：

- src/aurobox 核心程式
- tests 測試
- scripts 維運工具
- run.py、pyproject.toml
- 既有文件（README.md、REPORT.md、docs 目錄）

目的：將 0.2.1 文件內容與實際程式行為對齊，並反映已完成修正項目。

## 2. 程式現況總結

### 2.1 核心定位

專案目前是 Flashbot 硬體控制 API，聚焦於：

- Flask API 對外接口
- Pudu Open Platform 呼叫與簽章
- 艙門狀態最小管理（Door）
- 機器人點位記錄（RobotState）
- 背景輪詢（抵達判斷 / 通知中央系統）

### 2.2 主要模組

- app.py: App factory、DB 初始化、預設四個艙門重置
- api.py: 對外流程 API（assign/load/dispatch/pickup-complete/complete/cancel/return/status）
- robot.py: FlashbotController 與多來源狀態整合
- pudu_client.py: HMAC 簽章、GET/POST 包裝、指令紀錄
- models.py: Door、RobotState 與資料約束
- services.py: Controller 取得、目標點記錄、全空返航
- tasks.py: 背景輪詢抵達、通知中央系統、顯示 QR 內容
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

SQLite 使用 instance/aurobox.db。

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

### 5.2 本次執行結果

在目前工作環境執行 pytest：

- 結果：8 passed
- 耗時：約 0.40s
- 備註：原先 SQLAlchemy datetime.utcnow() deprecation warning 已修正。

## 6. 差異與修正狀態

- 舊的路由參數不一致問題：已解決
- 舊文件與實作不同步風險：已透過 README/REPORT 更新對齊

## 7. 本次文件更新成果

- 已更新 README.md：
  - 測試章節加入 API 整合測試資訊
  - 已知問題改為目前無阻擋性問題
  - 版本資訊更新為 0.2.1
- 已更新 REPORT.md（本文件）：
  - 以 2026-07-15 現況為基準
  - 記錄新增 API 整合測試與實測結果

## 8. 建議下一步

1. 以 `python -m pip install -e ".[dev]"` 作為新環境標準安裝流程。
2. 視需求擴充整合測試到 return / return-complete / dashboard/status。
3. 評估加入 CI 自動執行 pytest，確保後續改動不回歸。

