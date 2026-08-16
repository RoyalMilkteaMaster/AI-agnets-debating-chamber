# 02 — 規則時間線與階段／門檻／焦點列即時推進

- Spec：`../spec.md`
- Blocked by：無

## 交付成果

辯論進行中，「規則與時間線」的 current 標記隨規則切換即時前進（已過里程碑淡化為 `past`），「目前階段」「目前共識門檻」與焦點列（headline／tally 文字／「下一步」）同步更新：

- SSE frame 追加 `current_rule_index`、`phase_label`、`threshold_label`、`focus`（headline／tally_text／next_label）；每條 stream 的第一個 frame（snapshot 或 resumed append）追加 `rules` 完整時間線。
- `_follow` 在沒有新事件但時鐘跨過里程碑（階段／門檻／current 索引任一改變）時也發 frame（架構 §4.0.1 規則切換由伺服器推送）。
- `live.js` 依 frame 重畫時間線與上述各格；current 索引只由伺服器計算，JS 不自行比對 `at_ms` 重算；焦點列 tally 文字改以 frame 的 `focus.tally_text` 為準。

## 驗收條件

- 以注入時鐘讓進行中 run 跨過規則里程碑且無新發言：客戶端仍收到 frame，「規則與時間線」current 標記前進、階段／門檻／「下一步」同步改變，字樣與 `live` 模組權威（`phase_label`／`threshold_label`／`focus_state`）一致。
- 換 run 後時間線先重置為既有等待字樣，新 run 第一個 frame 送達即以該 run 的時間線（含比較題後移 30 秒的版本）重畫。
- 回看已完成 run 的「規則與時間線」與 metrics 伺服器渲染與修正前逐字相同。
- WSL 下 `python3 -m unittest` 全綠，`node tests/js/live_harness.js` 通過（含無事件規則切換推送與時間線重畫案例）。

## 必要寫入範圍

- `hoya_market_agents/webapp/server.py`（`_room_payload`、`_follow`）、`hoya_market_agents/webapp/static/live.js`、`tests/js/live_harness.js`、`tests/test_webapp*.py`——與 01、03 共用，避免與其他票平行修改。
