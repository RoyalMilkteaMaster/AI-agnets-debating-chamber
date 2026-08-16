# 即時辯論頁三面板進行中即時更新

來源：`docs/planning/requirements.md`「即時辯論頁三面板進行中即時更新（2026-08-16 核准）」；架構依 `docs/planning/architecture.md` §4.0.1 即時可視化邊界，無新增架構決策。

## 問題

即時辯論頁的「規則與時間線」「票數變化」「可驗證證據」三個摺疊面板在辯論進行中完全不更新：

- SSE frame（`server._room_payload`）只帶 `messages`／`seats`／`tally`／`round`／時鐘欄位，不帶改票紀錄、證據、階段與門檻。
- 前端 `live.js` 沒有這三個面板的繪製程式；同根因的「目前階段」「目前共識門檻」與焦點列（headline／下一步）也凍住。
- 從本頁啟動 run 或換 run 後，面板被 `data-reset` 重置為等待字樣，整場不變，直到 run 結束觸發整頁 reload。

回看已完成 run 由伺服器整頁渲染，已驗證正常，不在修正範圍。

## 目標

辯論進行中，三個摺疊面板與階段／門檻／焦點列隨 run 事件即時更新，無需手動重新整理；規則切換由伺服器推送（架構 §4.0.1），瀏覽器不自建第二套規則權威。

## User Stories

1. 身為本機觀看即時辯論的操作者，我希望辯論進行中看到每筆改票即時出現在「票數變化」，以便追蹤共識形成過程。
2. 身為操作者，我希望「規則與時間線」的 current 標記隨規則切換即時前進，以便知道現在流程走到哪一關。
3. 身為操作者，我希望證據快照封存後「可驗證證據」立即顯示證據卡，以便在辯論時對照各席引用的證據。
4. 身為操作者，我希望「目前階段」「目前共識門檻」與焦點列同步更新，以便頁面各處對流程狀態的說法一致。

## 需求與行為

1. **規則與時間線**：run 開始／換 run 時以 snapshot frame 的時間線重畫；規則切換時 current 標記前進、已過里程碑淡化（`past`），與伺服器渲染同一視覺語彙。
2. **票數變化**：每筆改票／首次表態即時追加一列（`首次表態：X` 或 `A → B` 加「改票」旗標），與伺服器渲染同一列格式。
3. **可驗證證據**：`evidence.jsonl` 出現且含證據卡後（既有顯示閘門），面板即時顯示證據卡；封存內容不可變，送達一次即為最終。
4. **階段／門檻／焦點列**：`目前階段`、`目前共識門檻`、焦點列 headline／tally 文字／「下一步」隨 frame 更新，字樣一律來自伺服器既有權威（`phase_label`、`threshold_label`、`focus_state`）。
5. **無事件的規則切換也要推**：辯論中沒有新發言但時鐘跨過里程碑時，伺服器仍發 frame（架構 §4.0.1「規則切換才由伺服器推送」）。
6. **換 run 重置**：`data-reset` 機制與等待字樣不變；重置後由新 run 的第一個 frame 重新填上。
7. **run 結束**：done frame 攜帶同組欄位；既有 `surfacesReset` → 整頁 reload 行為保留不變。

## 實作決策

- **資料與所有權**
  - `events.jsonl`、`evidence.jsonl` 仍由 controller 單一寫入；webapp 只讀（架構 §4.0.1）。
  - 改票紀錄由 `live.ChatRoom` 的 `changes` 累積衍生（既有），不新增儲存。
  - 證據卡由 `views._read_evidence` 讀取（既有），顯示閘門不變：檔案存在且可解析出卡片。
- **模組責任與公開介面**
  - `server._room_payload` 擴充為 frame 的唯一組裝點；規則、階段、門檻、焦點字樣一律呼叫 `live` 模組既有權威（`rule_timeline`、`phase_label`、`threshold_label`、`next_milestone`、`focus_state`）。
  - current 規則索引只有一個計算權威（現為 `pages.live_page._current_rule_index`）；由 server 端計算後放入 frame，JS 不得自行比對 `at_ms` 重算。
  - `server._follow` 負責推送時機：新事件、`debate_started` 翻轉、階段／門檻／current 索引改變、證據首次可見，四者任一成立即發 frame。
  - `static/live.js` 新增三面板與階段／門檻／焦點列的繪製函式；繪製結構與伺服器渲染同形（列格式、class 語彙一致），仍無 inline script。
- **Schema、API contract 與系統互動**（SSE frame，只加不改）
  - 每個 frame 追加：`changes`（累積全量列表，重畫即冪等）、`phase_label`、`threshold_label`、`current_rule_index`、`focus`（`headline`／`tally_text`／`next_label`）。
  - 每條 stream 的第一個 frame（snapshot 或 resumed append）追加 `rules`（該 run 完整時間線，含 `at_ms`／`label`／`required_votes`）；時間線在 run 內不變，不重複傳送。
  - `evidence`（封存證據卡全量）：每條 stream 首次可見時傳一次（per-stream 已送旗標）；重連開新 stream 自然重送。
  - 客端規則：payload 有該欄位才重畫對應面板；既有欄位（`messages`／`seats`／`tally`／`round`／時鐘）語意不變。
- **相容、遷移與技術限制**
  - 純附加欄位，舊 client 收到新欄位不受影響；無資料遷移。
  - CSP 不變（外部 script、無 inline）；報告頁與 run 詳情頁不動。
  - 焦點列 tally 文字改以 frame 的 `focus.tally_text` 為準，取代 client 端 `syncFocus` 自行拼字（消除第二套字樣來源）；視覺結果與現行相同。

## 驗收條件

1. 對進行中 run 往 `events.jsonl` 追加含改票的 seat_message：不重新整理下，「票數變化」即時多出對應列，格式與伺服器渲染一致。
2. 時鐘跨過規則里程碑且無新發言：仍收到 frame，「規則與時間線」current 標記前進、「目前階段」「目前共識門檻」與焦點列「下一步」同步改變。
3. `evidence.jsonl` 出現含卡片內容後：「可驗證證據」不重新整理即顯示證據卡。
4. 換 run（picker 或 launch）後：三面板先重置為既有等待字樣，新 run 第一個 frame 送達即重畫。
5. 回看已完成 run：三面板伺服器渲染與修正前逐字相同。
6. WSL 下 `python3 -m unittest` 全綠，`node tests/js/live_harness.js` 通過。

## 測試決策

- **公開行為**：frame 欄位內容與推送時機（Python）；面板 DOM 文字與列數隨 frame 變化（JS harness）。
- **測試接縫**：
  - Python：`live_clock`（時鐘注入）、`stream.monotonic`／`stream.sleeper`（follow 迴圈步進）、暫存 run 目錄下的 `events.jsonl`／`evidence.jsonl` 實檔——皆為既有 seam；直接對 `_room_payload` 與 `_follow` 斷言 frame 序列。
  - JS：`tests/js/live_harness.js` 的假 DOM＋`EventSource` 假物件——對 snapshot／append／done 事件斷言面板重畫、追加與重置後回填。
- **既有測試模式**：`tests/test_webapp.py`、`tests/test_webapp_live_runtime.py` 的 live 區段與 harness 既有案例風格；unittest，不新增測試框架。
- **不應耦合的實作細節**：不斷言完整 HTML 字串或內部 helper 呼叫次數；斷言文字內容、列數、class 語彙（`current`／`past`／`changed`）與欄位存在性。

## 不在範圍內

- 報告頁、run 詳情頁、歷史與命中率頁。
- 辯論流程、投票規則、`debate_rules.json` 本身。
- 樣式改版與新視覺元素。
- done → 整頁 reload 機制的重新設計（保留現狀）。

## 補充

- 現況查證（2026-08-16）：最新已完成 run `20260815T163124Z-6739-f14ab0` 的 `live_snapshot` 含 12 條規則、12 筆改票、37 張證據卡，回看渲染正常——「必須保持不變」的基準即此行為。
- `live.js` 既有註解明言三面板「沒有任何 frame 會補寫」；本規格移除該限制後，該註解與 done reload 註解需隨實作同步更新（文件一致性，非行為變更）。
