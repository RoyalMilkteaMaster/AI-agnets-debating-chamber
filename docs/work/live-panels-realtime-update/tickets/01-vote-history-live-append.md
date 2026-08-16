# 01 — 票數變化即時追加

- Spec：`../spec.md`
- Blocked by：無

## 交付成果

辯論進行中，每筆改票／首次表態不重新整理即出現在「票數變化」面板：SSE frame 追加 `changes`（累積全量列表，重畫即冪等），`live.js` 依 frame 重畫該面板，列格式（`首次表態：X`／`A → B` 加「改票」旗標、`T+` 時刻、席位名、stance class）與伺服器渲染一致。換 run 重置後由新 run 的第一個 frame 回填；done frame 同樣攜帶 `changes`。

## 驗收條件

- 對進行中 run 往 `events.jsonl` 追加含改票的 seat_message 後，不重新整理，「票數變化」即時多出對應列，內容與同一 run 整頁重載的伺服器渲染一致。
- 換 run 後面板先顯示既有等待字樣「尚未投票。」，新 run 第一個 frame 送達即重畫。
- 回看已完成 run 的「票數變化」伺服器渲染與修正前逐字相同。
- WSL 下 `python3 -m unittest` 全綠，`node tests/js/live_harness.js` 通過（含本票新增的 frame 欄位與面板重畫案例）。

## 必要寫入範圍

- `hoya_market_agents/webapp/server.py`（`_room_payload`）、`hoya_market_agents/webapp/static/live.js`、`tests/js/live_harness.js`、`tests/test_webapp*.py`——與 02、03 共用，避免與其他票平行修改。
