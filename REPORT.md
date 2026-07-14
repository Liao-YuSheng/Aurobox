# Aurobox 全目錄盤點報告

完成日期: 2026-07-14  
版本: 0.2.0  
狀態: 已完成全專案檢視與文件同步

## 1. 本次工作範圍

本次針對 AUROBOX 根目錄進行盤點，涵蓋：

- `src/aurobox` 核心程式
- `tests` 測試
- `scripts` 維運工具
- `run.py`、`pyproject.toml`、`examples.py`
- 現有文件（`README.md`、`REPORT.md`、`REPORT_2.md`、`docs` 目錄）

目的：將文件描述與實際程式行為對齊，並標註已知風險。

## 2. 程式現況總結

### 2.1 核心定位

專案目前是「Flashbot 硬體控制 API」，不再是完整配送平台。

保留能力：

- Flask API 對外接口
- Pudu Open Platform 簽章與呼叫
- 艙門狀態最小管理（Door）
- 機器人點位記錄（RobotState）
- 背景輪詢（抵達判斷 / 通知中央系統）

已不存在於核心程式：

- LINE webhook 路由
- Package / DeliveryHistory 等完整業務模型
- 舊版 manager service 流程

### 2.2 主要模組

- `app.py`: App factory、DB 初始化、預設三個艙門重置
- `api.py`: 對外流程 API（assign/load/dispatch/complete/cancel/return/status）
- `robot.py`: `FlashbotController` 與 V1/V2/task-state 狀態整合
- `pudu_client.py`: HMAC 簽章、GET/POST 包裝、指令紀錄 log
- `models.py`: `Door`、`RobotState` 與約束
- `services.py`: Controller 取得、目標點記錄、全空返航
- `tasks.py`: 背景輪詢抵達、通知中央系統、顯示 QR 內容
- `cli.py`: CLI 子命令封裝

## 3. API 現況（依實際路由）

基礎：

- `GET /`
- `GET /healthz`

硬體流程：

- `POST /api/doors/assign`
- `POST /api/doors/load`
- `POST /api/robot/dispatch`
- `POST /api/packages/<package_id>/pickup-complete`
- `POST /api/packages/<package_id>/complete`
- `POST /api/packages/<package_id>/cancel`
- `POST /api/packages/return`
- `POST /api/doors/return-complete`
- `GET /api/dashboard/status`

## 4. 資料層現況

SQLite 使用 `instance/aurobox.db`。

資料表：

- `doors`: `sn`, `door_number`, `status`, `package_id`, `task_id`, `updated_at`
- `robot_state`: `sn`, `last_point`, `updated_at`

約束：

- 僅允許艙門 `H_01/H_02/H_03`
- `status` 僅允許 `empty/assigned/full`

## 5. 測試與可執行狀態

### 5.1 測試覆蓋

目前測試檔 `tests/test_pudu_client.py` 主要涵蓋：

- 設定載入
- 必填環境變數驗證
- Client / Controller 初始化

尚未涵蓋：

- API 路由行為
- DB 狀態轉換
- 背景輪詢執行緒

### 5.2 本次執行結果

在目前工作環境中無法直接執行測試：

- `pytest` 指令不存在
- `python -m pytest` 回報 `No module named pytest`

## 6. 舊檔與現況差異

- `examples.py` 仍引用舊架構（如 `aurobox.manager`、`PackageStatus`），與 0.2.0 現況不一致。

## 7. 已知問題（需優先處理）

### 7.1 路由參數不一致 (正在解決)

- `api.py` 中 `POST /api/packages/return` 對應函式為 `package_return(package_id)`。
- 路由未帶 `package_id`，實際呼叫時會造成參數錯誤風險。

### 7.2 文件與實作不同步風險

- 若仍沿用舊版 API 文件（例如 `show-qr`、`returned` 路由），整合端會打錯端點。

## 8. 本次文件更新成果

- 已更新 `README.md`：
  - API 清單改為實際路由
  - 補齊當前專案結構與環境變數
  - 新增已知問題區塊
- 已重建 `REPORT.md`（本文件）：
  - 以 2026-07-14 全目錄盤點結果為基準

## 9. 建議下一步

1. 修正 `POST /api/packages/return` 之路由與函式參數一致性。
2. 新增 API 整合測試（至少覆蓋 assign/load/dispatch/complete/cancel）。
3. 汰換或重寫 `examples.py`，避免誤導新開發者。

