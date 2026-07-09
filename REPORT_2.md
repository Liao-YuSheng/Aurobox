# Aurobox 進度報告（二）

完成日期: 2026-07-09  
版本: 0.2.0  
狀態: 已完成架構收斂，聚焦硬體控制 API

## 1. 本次調整目標

本次重點是將專案從「完整配送平台」收斂為「可被外部系統呼叫的機器人硬體控制層」，以降低維護範圍並明確責任邊界。

### 1.1 目前責任

- 控制機器人移動與艙門開關
- 提供對外 HTTP API
- 回傳機器人即時狀態
- 維護最小本地資料（doors）

### 1.2 外移責任

- 多數資料庫與配送業務狀態
- Dashboard 前端呈現
- LINE webhook/推播與住戶互動流程

## 2. 目前保留模組（程式碼現況）

- src/aurobox/app.py: Flask app factory、DB 初始化、路由註冊
- src/aurobox/api.py: 對外控制 API 與本地 door 狀態更新
- src/aurobox/robot.py: FlashbotController、狀態整合
- src/aurobox/pudu_client.py: Pudu API client、簽章與指令紀錄
- src/aurobox/models.py: Door / DoorStatus（最小資料模型）
- src/aurobox/config.py: 環境設定載入與必要欄位檢查
- src/aurobox/cli.py: CLI 指令入口
- run.py: 啟動入口

## 3. 對外 API 能力（0.2.0）

### 3.1 基礎服務

- GET /
- GET /healthz

### 3.2 硬體控制流程

- POST /api/doors/<door_number>/load
- POST /api/robot/dispatch
- POST /api/packages/<package_id>/show-qr
- POST /api/packages/<package_id>/cancel
- POST /api/packages/<package_id>/pickup-complete
- POST /api/packages/<package_id>/complete
- POST /api/packages/<package_id>/returned
- GET /api/dashboard/status

## 4. 資料與狀態策略

### 4.1 本地資料

- 僅維護 doors table
- 追蹤欄位: sn、door_number、status、package_id、updated_at

### 4.2 機器人狀態整合

- 來源:
  - v1/status/get_by_sn
  - v2/status/get_by_sn
  - v1/robot/task/state/get
- 由 get_status_summary() 產生統一輸出

## 5. 文件與版本同步成果

### 5.1 文件

- README.md 已改為「現況精簡版」，移除不符現況的 Dashboard/Webhook/完整包裹模型描述
- 新增 REPORT_2.md（本文件）

### 5.2 版本

- pyproject.toml: 0.1.0 -> 0.2.0
- src/aurobox/__init__.py: __version__ 0.1.0 -> 0.2.0

## 6. 已知差異與待確認事項

以下為整理過程中觀察到、建議後續確認的項目：

- README 舊版提到的 manager.py、tasks.py、webhooks.py 已不在目前核心模組中
- examples.py 仍包含舊流程示例（ManagerService、Package 等），與現況架構不一致
- 目前測試偏向初始化與設定檢查，尚未涵蓋主要 API 路由行為

## 7. 結論

0.2.0 版本已完成「Aurobox 作為硬體控制層」的定位收斂。

專案目前可作為外部中控系統的機器人控制 API，並保有最小本地狀態管理。後續建議優先補齊 API 行為測試與文件/範例的一致性校正。