# 03 — 可驗證證據封存後即時顯示

- Spec：`../spec.md`
- Blocked by：無

## 交付成果

辯論進行中，證據快照封存（`evidence.jsonl` 出現且含可解析證據卡，沿用既有顯示閘門）後，「可驗證證據」面板不重新整理即顯示證據卡：

- SSE frame 追加 `evidence`（封存證據卡全量）：每條 stream 首次可見時傳一次（per-stream 已送旗標），重連開新 stream 自然重送；`_follow` 偵測證據首次可見即發 frame。
- `live.js` 依 frame 繪製證據卡（evidence_id／seat／statement／excerpt／來源等級與來源），結構與伺服器 `_evidence_card` 渲染同形。
- 來源連結安全判準（`is_safe_source_url`）仍由伺服器唯一權威把關：frame 只帶已通過判準的可點連結（或判定結果），client 不建立第二套 URL 判準；未通過者維持純文字。

## 驗收條件

- 對進行中 run 寫入含卡片的 `evidence.jsonl` 後，不重新整理，「可驗證證據」即顯示證據卡，內容與同一 run 整頁重載的伺服器渲染一致；封存後內容不再改變。
- 含不安全 `source_url` 的卡片在即時繪製下不產生可點連結，與伺服器渲染行為一致。
- 換 run 後面板先顯示既有等待字樣「證據將在證據快照封存後顯示。」，新 run 封存後即回填。
- 回看已完成 run 的「可驗證證據」伺服器渲染與修正前逐字相同。
- WSL 下 `python3 -m unittest` 全綠，`node tests/js/live_harness.js` 通過（含證據首次可見推送與面板繪製案例）。

## 必要寫入範圍

- `hoya_market_agents/webapp/server.py`（`_room_payload`、`_follow`）、`hoya_market_agents/webapp/static/live.js`、`tests/js/live_harness.js`、`tests/test_webapp*.py`——與 01、02 共用，避免與其他票平行修改。
