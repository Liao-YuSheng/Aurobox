# 普渡三支唯讀狀態 API 實測 — FlashBotMax（2026-07-07）

一趟真機測試，量出 `task/state/get`、`status/get_by_sn`（V1 / V2）三支唯讀 API 在
「移動 → 抵達 → 結束 → 回充」四階段各回什麼、準不準，並釐清多處與官方文件不符之處。

- **機器**：FlashBotMax，SN `8FF055923050007`（`product_code=FlashBotMax`）
- **地圖 / 點位**：`1#1#內湖展間v20`；目標點 `喵喵待機`（type `dining_outlet`）；充電樁點位 `閃閃充電`
- **環境**：`PUDU_BASE_URL = https://css-open-platform.pudutech.com/pudu-entry`（正式、直連普渡）
- **金鑰**：與 age-out 測試同一組（`PUDU_API_APP_KEY/SECRET`，HMAC 簽章）
- **測試腳本**：`flashbot_state_recharge_test.py`（每 3s 同刻併打三支，內容變化就 dump 原始 JSON）

---

## 一、被測 API 一覽

| 用途 | Method + Path（相對 base_url） | 備註 |
| --- | --- | --- |
| 任務狀態 | `GET /open-platform-service/v1/robot/task/state/get?sn=` | 官方註明「新版機不支援」→ **實測可用** |
| 機器狀態 V1 | `GET /open-platform-service/v1/status/get_by_sn?sn=` | 官方註明「已廢棄，改用 V2」→ **實測仍在且資訊最全** |
| 機器狀態 V2 | `GET /open-platform-service/v2/status/get_by_sn?sn=` | 現行版，三態合一 `run_state` |
| 派工顯示 QR | `POST /open-platform-service/v1/custom_call`（`call_mode=QR_CODE`） | 回 `state=CALL_SUCCESS` + `task_id` |
| 結束任務 | `POST /open-platform-service/v1/custom_call/complete`（`task_id`） | 回 `SUCCESS`、`data=null` |
| 一鍵回充 V2 | `GET /open-platform-service/v2/recharge?sn=` | 回 `desc=success` + `task_id`；**滿電也接受** |

---

## 二、四階段實測狀態轉移

同一時刻三支併排。`task state`（Await/Ongoing/Arrive/Complete/Fail/Cancel）與
`move_state`（IDLE/MOVING/ARRIVE/…）是**兩套不同詞彙**，不可混用。

### 派工 → 抵達 → 結束（custom_call）

| 階段 | task/state | V1·V2 `move_state` | V2 `run_state` | V1 `work_status` |
| --- | --- | --- | --- | --- |
| 派工前（在充電樁） | 上一任務殘留 | `IDLE` | `IDLE` | -1 |
| 移動中 | `Ongoing` | `MOVING` | `BUSY` | 1 |
| 到位（顯示 QR） | `Arrive` | `ARRIVE` | `BUSY` | 1 |
| complete 後 | `Complete`（慢幾秒） | `IDLE` | `IDLE` | -1 |

- 近距離導航約 **34 秒**到位；三支**同刻**一致轉 `ARRIVE`/`Arrive`。
- 到位顯示 QR 期間 `run_state` 維持 `BUSY`（未回 IDLE）。

### 回充 → 到樁（recharge，run 2）

| t（自派工起） | task/state | `move_state` | V2 `run_state` | `is_charging` | `charge_stage` |
| --- | --- | --- | --- | --- | --- |
| 68~76s | `閃閃充電:Charge:Ongoing` | `MOVING` | `BUSY` | -1 | IDLE |
| 80~84s | 同上 | `IDLE`（短暫） | `IDLE` | -1 | IDLE |
| 88~100s | 同上 | `MOVING` | **`IDLE`** | -1 | IDLE |
| 104s（到樁） | `閃閃充電:Charge:Complete` | `ARRIVE` | `BUSY` | **1** | `CHARGE_FULL_USE_PILE` |

- recharge 會建一個 **`Charge` 型任務**（名稱=充電樁點位 `閃閃充電`），`task/state` 追得到。
- 回程 `move_state` 非平滑：`MOVING → 短暫 IDLE → MOVING → ARRIVE`（疑路徑重規劃/避障）。

---

## 三、三支 API 判定

| API | 有反應 | 正確反映實況 | 一句話 |
| --- | --- | --- | --- |
| `task/state/get` | ✅ 全程 | ⚠️ 準但**延遲＋開頭殘留** | 看「任務語意」可以，即時判斷會慢半拍 |
| `status/get_by_sn` **V1** | ✅ 全程 | ✅ **最準最完整**（唯一帶座標） | 首選 |
| `status/get_by_sn` **V2** | ✅ 全程 | ⚠️ move/charge 準，**`run_state` 會誤導** | 別用 `run_state` 判動作 |

### 實用結論（哪個欄位判哪件事）
- **要動態 / 座標** → 用 **V1**：唯一帶 `position(x,y,yaw)`，另有 `map_name`、`is_online`、`work_status`、`schedule_status`。
- **判「在不在動」** → 一律看 **`move_state`**（V1/V2 皆準）；**不要**看 `run_state`／`work_status`。
- **判「充電/回充完成」** → 看 **`is_charging`（1/-1）**；**不要**比 `charge_stage` 字串。
- **task/state/get** → 適合看任務語意與回充任務進度；不適合即時動作判斷。

---

## 四、重點發現（多處打臉文件 / 修正先前認知）

1. **`task/state/get` 在 FlashBotMax 完全可用** — 文件寫「新版本機器不支援」，實測
   `Ongoing → Arrive → Complete`、回充 `Charge` 任務都正確回。先前「age-out 時全盲」的印象
   應是**特定 age-out 情境**，健康窗口這支是好的。

2. **`move_state` FlashBotMax 有回** — 文件寫「僅支援 T300」「只有 P-ONE 上報」，實測 FlashBotMax
   V1、V2 都正常回 `MOVING/ARRIVE/IDLE`。

3. **V1（號稱廢棄）反而比 V2 資訊多** — V1 獨有 `position` 座標、`map_name`、`is_online`；
   V2 獨有 `run_state`、`product_code`、`charge_type`。要軌跡/定位就得留 V1。

4. **`run_state` / `work_status` ≠ 是否在動** — 回充途中出現 `run_state=IDLE` 但 `move_state=MOVING`。
   `run_state` 量的是「可否派工 / 是否執行工作任務」，回充屬系統任務且可被打斷 → 報 IDLE。
   定義上沒錯，但當「有沒有在動」用會判錯。

5. **`charge_stage` 實際值多於文件** — 實測見 `CHARGE_FULL_USE_PILE`、`STOP_CHARGE_USE_PILE`，
   文件僅列 `CHARGE_FULL`/`CHARGING`。滿電回樁時是 `CHARGE_FULL_USE_PILE`（非 `CHARGING`），
   故判充電務必用 `is_charging`。

6. **狀態機收斂速度**：`move_state`/`run_state`（status）比 `task/state` 快；complete 後
   status 幾乎即時回 IDLE，task/state 慢幾秒才轉 Complete。

---

## 五、踩坑：recharge 404 = base_url 前綴重複

第一趟 recharge 回 **HTTP 404**，根因：

- `PUDU_BASE_URL` **本身已含 `/pudu-entry`** → `https://css-open-platform.pudutech.com/pudu-entry`
- 腳本路徑又寫 `/pudu-entry/open-platform-service/v2/recharge`
- 實際請求變成 `.../pudu-entry/pudu-entry/open-platform-service/v2/recharge` → **重複前綴 → 404**

**正解**：所有路徑（含 recharge）都**不要**自帶 `/pudu-entry`，靠 base_url 補；
recharge 路徑用 `/open-platform-service/v2/recharge`。修正後回 `desc=success` + `task_id`，
且**電量 100% 也接受回充**（不會因滿電被拒）。

> 對照：backend 另一支 client 的 `recharge` 帶 `/pudu-entry`，是因為那個 client 的 base_url
> **不含** `/pudu-entry`（`css-open-platform.pudutech.com`）。前綴要不要帶，取決於該 client 的 base_url。

---

## 六、備忘

- 測試兩趟時間：run 1 = 09:45（recharge 404）、run 2 = 09:56（全通）。
- 每 3s 併打三支 ≈ 1 req/s，對雲端 API 負擔可忽略；未見限流。
- 腳本安全設計：偵測不到抵達 `send+8min` 強制 complete（避 ~600s age-out 卡死）；
  回充監看 10min 封頂；`--post-arrive` 硬上限 179s。
