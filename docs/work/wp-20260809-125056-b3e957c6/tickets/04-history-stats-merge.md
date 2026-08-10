# 04 歷史與命中率合併頁

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：03

## 目標

把原歷史查詢頁與命中率統計頁合併成單一「歷史與命中率頁」（上方統計卡、下方 run 列表帶命中結果），手動記錄結果表單併入同一頁，`GET /stats` 302 轉跳 `/history`，舊書籤不失效。

## 對應原始需求

- R-001：頁面清單重整：即時辯論主頁、歷史與命中率合併頁、設定、run 詳情（功能保留、版面套新系統）、市場報告與完整辯論（內容不碰、右上角必可點）
- R-007：全繁體中文：webapp 畫面不得出現英文資料原值，標籤從既有權威帶出

- Spec R1 頁面清單表格「歷史與命中率｜合併成單一頁面：上方統計卡、下方 run 列表帶命中結果」，以及其下兩條：「原歷史查詢頁與命中率統計頁合併為『歷史與命中率頁』。」「手動記錄結果的表單併入合併頁。」
- Spec R1 第三條：「沒產出報告的 run：歷史列表顯示其狀態；主頁可回看該 run 的聊天過程。」
- Spec〈實作決策／模組責任與公開介面〉webapp `views.py` 條：「負責合併頁的資料整形（統計卡、run 列表帶命中結果、手動記錄結果表單），routes 不含查詢邏輯。」
- Spec〈實作決策／Schema、API contract 與系統互動〉端點契約前兩條：`GET /history`、`GET /stats` 302 轉跳 `/history`。
- Spec〈產品限制〉：「webapp 零 SQL；唯一取數路徑是 `run_index.query_runs`。」「run artifact 唯讀。」
- Spec〈實作決策／相容、遷移與技術限制〉：「頁尾誠實：會寫檔的頁面不得沿用唯讀頁尾。」
- Spec R7 全繁體中文（合併頁的枚舉值全部翻譯）。
- Spec A5 全部四條（本票負責合併頁部分）。

## 使用者價值

一次看完成績與紀錄，不必在兩頁之間來回；舊的 `/stats` 網址仍然可用。

## 範圍

### 進入範圍

1. `/history` 改為合併頁：上方命中率統計卡、下方 run 列表且每列帶命中結果（命中／未命中／待驗證／不可自動驗證）。
2. 手動記錄結果表單併入合併頁，行為與現行命中率頁一致（沿用 Ticket 12 已結案的寫入路徑，只寫 `outcome.json`）。
3. `GET /stats` 回 302 轉跳 `/history`；不保留第二份頁面實作。
4. 沒產出報告的 run 在列表顯示其狀態，不顯示為空白或英文原值。
5. 既有篩選條件與清除條件行為保留，網址參數相容。
6. 導覽列與頁內連結全部指向 `/history`；不留指向已退場頁面的死連結。
7. 頁尾誠實：合併頁含手動記錄結果表單（會寫檔），不得沿用唯讀頁尾。
8. 合併頁所有枚舉值以既有權威翻成繁體中文（資產類別看 `config/market_scopes.json`、燈號看 `report_contract.CONFIDENCE_LEVELS`、立場看既有立場語彙表）；權威未涵蓋的值要有明確處理方式並說明，不得靜默 fallback 成英文原值。

### 不進入範圍

- 標的選單（Ticket 05）、PDF 匯出（Ticket 06）、關閉伺服器與入口（Ticket 07）。
- 命中率的計算規則、燈號語意、`outcome.json` 結構。
- 新增任何 SQL 或擴充 `run_index`。

## 已確認實作決策

- 唯一取數路徑是 `run_index.query_runs`；webapp 內零 SQL。
- routes 不含查詢邏輯，資料整形留在 `views.py`。
- run artifact 唯讀；本票唯一允許的寫入仍是既有的 `outcome.json` 路徑。
- 設計系統與 token 已由 Ticket 03 定形，本票沿用，不新增第二套樣式來源。

## 驗收條件

1. 開 `/history` 一頁同時看到統計卡與 run 列表，且列表每列顯示命中結果。
2. `GET /stats` 回應 302，`Location` 為 `/history`；跟隨轉跳後看到合併頁。
3. 在合併頁提交手動記錄結果，該 run 的 `outcome.json` 更新，且 run 目錄內其他檔案未被修改。
4. 沒產出報告的 run 在列表中顯示可讀的中文狀態。
5. 合併頁渲染後 grep，無 `tw_stock`、`us_stock`、`crypto`、`open`、`green`、`hit`、`miss`、`pending`、`unverifiable` 等枚舉英文原值。
6. 合併頁頁尾為「會寫檔」版本，非唯讀版本。
7. webapp 內無任何 SQL 字串；資料只經由 `run_index.query_runs` 取得。
8. 篩選與清除條件行為與改版前一致，既有網址參數仍可用。
9. 全站無指向已退場統計頁的死連結。
10. 既有測試全綠。

## 測試與證據

- 測試接縫：合併頁的資料整形函式（可注入 `query_runs` 結果）；`/stats` 轉跳；手動記錄結果表單 handler。
- 迭代期快速檢查：`python3 -m unittest tests.test_webapp_history_merge`（WSL，秒級）。
- Ready for Review 完整驗收：`cd <Code Root> && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`（WSL）。若專案仍沿用 Ticket 13 的 T08 攔截器，依該慣例補上對應環境變數，實際值由 Coordinator 提供，不寫入票面。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：完整驗收結果與退出碼、合併頁渲染後 HTML 存檔與關鍵元素斷言、302 回應標頭、繁中 grep 結果、SQL 字串掃描結果為零、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票直接改變使用者頁面結構與網址行為（Spec R1），但原生瀏覽器「代驗」不適用——平台無任何 Claude／Codex 原生瀏覽器工具（已於 2026-08-10 如實回報），由使用者本人於 2026-08-10 以本機瀏覽器親自實機操作驗收並明示「結案」授權；輔以票面原核准慣例之渲染後 HTML 存檔＋關鍵元素斷言證據（十項前端證據含 /stats 302 標頭與篩選六案，見執行與 Review 紀錄）。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
  1. 開 `/history` → 上方統計卡、下方 run 列表帶命中結果，同一頁看完。
  2. 開 `/stats` → 自動轉跳到 `/history`，網址列變成 `/history`。
  3. 在合併頁對一筆到期 run 手動記錄結果並送出 → 頁面顯示更新後的結果，列表該列同步變更。
  4. 套用篩選條件與清除條件 → 行為與改版前一致。
  5. 找一筆沒產出報告的 run → 列表顯示中文狀態，非空白、非英文原值。
- 操作結果：不適用
- 操作證據：不適用

## 依賴

- Depends on：03
- Blocks：05

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：
  - `hoya_market_agents/webapp/pages.py`
  - `hoya_market_agents/webapp/views.py`
  - `hoya_market_agents/webapp/server.py`
  - `hoya_market_agents/webapp/outcome.py`（僅表單接入所需，不改計算規則）
  - 新增 `tests/test_webapp_history_merge.py`
- Shared resource locks：`hoya_market_agents/webapp/pages.py` 與 `hoya_market_agents/webapp/server.py`（Tickets 03～07 共用熱點，以 Blocked by 串成單鏈，任何時刻只有一張票持有寫入權）；`tests/test_webapp.py`（既有斷言若需更新，僅限本票持鏈期間）；全專案 unittest 套件。
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`
- model_reasoning_effort：`high`
- 路由理由：改動公開端點契約（新增 302 轉跳、頁面合併）、涉及寫檔頁面的頁尾誠實規則與零 SQL 邊界，非局部低風險修改。
- 升級路徑：`claude-opus-5`／`xhigh`；`max` 需使用者明確核准
- Research 證據：無
- 執行時覆寫：無

## Agent 分工

- Developer：負責實作、驗證、修正或以證據反駁 Findings
- Reviewer A：`both` 時只執行 Spec Review；`a_only` 時執行 Spec 與 Standards
- Reviewer B：`both` 時只執行 Standards Review；`b_only` 時執行 Spec 與 Standards
- Reviewer 啟用規則：由執行 Task 最新 `settings_update: reviewers` 決定；預設 `both`，Ticket 不自行固定或搜尋設定
- Reviewer 標準：每位啟用 Reviewer 都載入 `$milktea-skills-code-review`，只執行 Coordinator 指定的 `review_axis`
- CLI 與模型：Developer 初始模型與推理強度以上述配置為準；Reviewer 由執行 Task 的角色設定獨立決定

## 完成規則

- Developer 與各 Finding 的原 Reviewer 已處理所有可重現且有證據的問題。
- 沒有未解決的阻擋或重要正確性、可執行性、可讀性、架構或衍生風險。
- Developer 與各 Finding Owner 對關閉或撤回事由達成共識。

## 執行與 Review 紀錄

### 開始執行（2026-08-09 21:20 +0800）

- Execution environment：沿用 Task 設定（`source: auto_current`）：Windows 宿主＋`wsl.exe -d Ubuntu-24.04 --` 前綴；WSL 路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；python3 3.12.3。
- 並行批次：batch-4（單票；上游 01–03 完成，`pages.py`／`server.py` 寫入鏈與全專案測試鎖釋放予本票）。
- 基準版本：main @ `9b8a451`＋工作樹（含 T01–T03 成果），基準樹 `242789385c6b8b24e957adb221864d042517cb94`（已驗證）。基準全套：2264 tests OK (skipped=1)、exit 0（Ticket 03 結案驗收）。
- 開發角色：Developer＝Claude Code 子 Agent（`milktea-build`），model `claude-opus-5`（票面偏好）；effort＝後端預設（派工工具無 effort 欄位，原因同前）。Reviewer A／B＝Codex（gpt-5.6-sol／high）。
- **裁定五（承 Ticket 03 移交）**：`views.py` 席位標籤來源（原 413 行一帶，讀模組層 open 套視圖 `SEAT_DISPLAY_NAMES`）納入本票工作項，改讀 `seats` 依 run `asset_class` 的讀取口，消除 run 詳情頁與辯論室席名不一致。`views.py` 本屬本票 Exclusive write scope，此為工作項補列而非範圍擴充。
- Exclusive write scope：依票面（webapp/pages.py、views.py、server.py、outcome.py 僅表單接入、新增 tests/test_webapp_history_merge.py；`tests/test_webapp.py` 依 Shared locks 註記僅限本票持鏈期間更新既有斷言）。webapp 檔案與測試多為未追蹤檔，diff 用 `git show <基準樹>:<path>`＋`git diff --no-index`。
- Shared resource locks：`pages.py`／`server.py`（本票持鏈）；`tests/test_webapp.py`（既有斷言更新）；全專案 unittest 套件。
- 必跑指令（Ready for Review 完整驗收）：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`（WSL，腳本檔取碼）。

### Ready for Review（2026-08-09 22:20 +0800）

- 端點：`GET /history` 合併頁（統計卡→燈號命中率→查詢條件→結果列表帶命中結果→人工輸入表單，會寫檔頁尾）；`GET /stats` 302→`/history`（`render_stats_page` 刪除，無第二份實作）；表單移 `POST /history`；**`POST /stats` 回 404**（redirect 會靜默丟表單內容）。導覽三 tab，死常數刪除。
- 資料整形：`history_data` 統一取代雙頁函式（先掃期後讀 index，三次讀取共用 state）；`row_view` 公開為接縫；新增 `outcome_state`、`report_available`（index 的 report_path 對 runs_root 一次 `is_file()`）。**裁定五完成**：`_seat_views` 改讀 `seats.seat_display_names(run asset_class)`，同一 tw_stock fixture 詳情頁＝辯論室＝roster(stock) 七席逐席一致，幣圈套獨有名稱兩處皆零；定向反證（暫改回 open 套→3 紅）在案。
- 繁中：列表新增「狀態」「命中結果」欄；篩選欄 datalist→`<select>`（畫面中文、送出原鍵；權威未涵蓋值以原值 selected，不靜默丟棄）；統計卡 `WHOLE_INDEX_NOTE` 明示涵蓋全庫。繁中 grep：枚舉英文原值畫面 0 命中（僅 CSS 識別字與 run_id datalist，A5 豁免）。
- 完整驗收：**exit 0，Ran 2314 tests，OK (skipped=1)**（基準 2264＋50；skip 為既有大小寫敏感條件跳過，已 -v 逐條確認）。零 SQL 掃描 0 命中（含可失敗性反證）；手動記錄僅新增 outcome.json、既有 7 檔位元不變、重複送出 409。
- 前端證據：渲染 HTML 存檔（/tmp/t04-evidence/）＋十項關鍵元素斷言全 PASS（詳 Developer 回報：302 標頭、篩選六案、無報告 run 中文狀態、零死連結）。
- 變更檔案 6：pages.py(+150/−55)、views.py(+139/−106)、server.py(+49/−30)、outcome.py(+1/−1 僅 docstring)、test_webapp.py(+136/−77)、新增 test_webapp_history_merge.py(+778)。寫入範圍稽核：全庫僅此 6 檔與基準不同。
- Review snapshot：基準樹 `24278938`＋blob：pages=2e85a1d3、views=53a9cf42、server=424f981b、outcome=3064f209、test_webapp=cbb762ee、test_webapp_history_merge=f2eca64c；未動鄰檔 live.py=c0171e16、launch.py=73c0fedb、run_index.py=c0adb745。
- Developer 提請 Review 特別檢視：`POST /stats` 404 取捨；統計卡全庫 vs 列表受篩選的語意；`<select>` 繁中處理；`report_available` 每列一次 `is_file()`；掃期搬到 `GET /history` 的副作用（開頁可能寫 outcome.json 與 sweep cursor——合併後唯一看時鐘的頁）。
- Coordinator 歸屬裁定：`report_renderer.py:38` 死別名（零 importer）不在本票範圍，留待結案報告列殘留；`launch.py:52` 舊頁名文案納入 Ticket 05 派工。

### Review 紀錄（2026-08-09）

- Reviewer A（spec 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：9 檔 blob 雜湊逐行吻合、6 份 diff 基準 blob 相符。驗收 1–10 逐條通過（另自行重跑合併頁 50 tests＋掃期／唯讀邊界 26 tests 皆 OK）；裁定五通過（定向反證成立，測試含股票／幣圈差異與負向斷言，足以避免「兩頁同錯」假陽性）；裁決項一（`POST /stats` 404）判定為合理 fail-closed 取捨、非未授權擴張；裁決項二（掃期搬 `GET /history`）判定為現行行為的正確延續，寫入仍限核准的 write-once `outcome.json` 與既有 sweep cursor；全庫統計卡＋篩選列表判定符合 R1。**Findings：無。結論：通過。**
- Reviewer B（standards 軸、native、codex CLI 0.146.0／gpt-5.6-sol／high、隔離 session）：9 檔雜湊吻合、diff 統計與 Developer 紀錄一致。DRY（項 3）、繁中（項 5）、衍生風險（項 6，確認沿用 Ticket 12 的 OutcomeCheck／sweep 而非重寫、write-once 與 cursor 原子性成立）通過；資料整形（項 1）不通過、接縫與測試（項 2、4）部分通過。品味 🟡、致命問題無。**結論：不通過（待修正）**，Findings（Owner＝Reviewer B）：
  - **B4-1［重要］views.py:176**：合併頁三次索引讀取（rows／summary／pending）只共用錯誤狀態、不共用資料 snapshot。定向反證：模擬 rows 讀舊資料、summary／pending 讀併發更新後資料 → `state=ok`＋`totals.hit=1`＋同列 `outcome_state=pending`＋`pending_runs=[]` 的混合狀態。期望：三者來自同一索引 snapshot，或偵測版本改變後重試／回報不可用，不得以 STATE_OK 回傳混合狀態。
  - **B4-2［建議］views.py:362**：`_outcome_state` 的 `None→pending／未知→unreadable` 正規化與 `run_index.py:797` 重複。期望：收斂為單一權威正規化。
  - 改進方向另記：`limit` 是預設 50 而非硬上限（`limit=100000` 原樣通過，`report_available` 成本隨之 O(n)）。

### 修正輪（2026-08-09 23:30 +0800，Developer 回報，待 Reviewer B 定向複驗）

- **B4-1 fixed（Developer 不反駁）**：先查證競態真實可達（`ThreadingHTTPServer`＋launch 子程序 `index_finalized_run`＋掃期＋手動送出三種索引寫入者），以真實 commit 做紅燈重現（卡片命中 1、六列全待驗證的混合狀態）。修正：`_one_version_read()` 以索引檔自身 `(st_ino, st_size, st_mtime_ns)` 版本戳夾住三次讀取、不同即重讀（上限 3 輪）；成立前提「run_index 刻意不用 WAL、每次 commit 就地重寫」經查證且雙檔案系統實測（ext4／DrvFs 各 4/4 次 commit 被偵測、無寫入不誤報）；三輪皆被插入寫入時誠實降級（畫最後一次讀取＋`MIXED_READ_CAVEAT` 狀態列，不假造錯誤頁）。否決替代方案有據：單次讀取推導＝重新實作篩選語意（模組不變量禁止）；取 writer lock＝讓畫頁擋住收尾 run 寫索引（run_index docstring 明文禁止）。安靜時不多讀（測試釘住只查 2 次）。新增 3 測試紅→綠。用既有公開 `index_db_path`，零 SQL 紅線維持。
- **B4-2 不修＋契約測試**：單一權威正規化只能放 `run_index`（上游），而票面明令其公開介面不得變更、檔案不在寫入範圍。改以契約測試把 `run_index.outcome_summary` 與 `views.row_view` 的判讀釘在一起（5 種可達狀態各 1 筆、任一邊改規則即紅）。若 Coordinator 開 run_index 範圍則照 B 方向改，否則此為最終處理。
- **limit 硬上限**（B 改進方向，主動處理）：`MAX_ROW_LIMIT=500`，超過夾到上限並在頁面說明；`report_available` 成本封頂；附 4 測試。
- 指定重驗：test_webapp* 644 tests OK；**全套 2322 tests OK (skipped=1)、exit 0**（基準 2264＋58）。前端證據重跑全維持。
- 修正輪 Snapshot：views=351afdcc、pages=725058cd、server=424f981b（未動）、outcome=3064f209（未動）、test_webapp=cbb762ee（未動）、test_webapp_history_merge=fcd757a1。

### 定向複驗第一輪（Reviewer B，2026-08-10 00:05 +0800）

- **B4-1 仍未關閉（缺口收斂至文案）**：版本戳包夾＋三次重試方向可接受、前提（rollback journal／就地改寫／rebuild replace）成立、58 tests OK；但降級文案「畫面可能相差一次寫入」過度保證——B 以每讀取窗連續兩筆真實 commit 重現 `card_hit=10 vs row_hit=8（delta=2）` 且 caveat 顯示中。最小修正：文案改為不限定差距、不保證下次必一致（例：「統計與列表可能來自不同索引版本；請稍後重新整理再試」），並補一個讀取窗內至少兩次 commit 的降級測試。重試與版本戳邏輯無須重做。
- **B4-2 withdrawn**：兩份判讀仍存在故非 closed，但接受「run_index 公開介面不得變更」的範圍理由；契約測試（5 狀態各 1 筆、防空過、OUTCOME_STATES 集合斷言）足以控制本票內走鐘風險。
- **limit closed**：硬上限 500、超限明示、成本封頂、測試齊。

### 定向複驗第二輪與共識（2026-08-10 00:40 +0800）

- Developer 第三輪最小修正：新文案只陳述「版本不同、差距未知、稍後再試」（{} 明確為重試次數）；標題與 docstring 同步去掉同一過度保證；新增 2 測試（每讀取窗 2 筆真實 commit 的降級測試＋文案禁止字串釘子）。紅→綠在案：舊文案下行為測試已過而字串測試紅——行為證偽舊文案。重驗 test_webapp* 646 tests OK。
- Reviewer B 二次複驗：3 檔雜湊吻合。**B4-1 closed**（文案無數量與成功保證；「相差一次」「即可取得一致」僅存在於回歸禁止字串；60 tests OK、exit 0；最小證據缺口：無）。Reviewer B 明確回報：「standards 軸最終結論：通過。」
- 共識成立：B4-1 closed、B4-2 withdrawn、limit closed；Reviewer A 首輪通過零 Finding。最終 Snapshot：views=7bc692bd、pages=e3e11eea、server=424f981b、outcome=3064f209、test_webapp=cbb762ee、test_webapp_history_merge=dab726ba（基準樹 `24278938`）。首次完整驗收 2314 OK；修正輪全套 2322 OK (skipped=1)、exit 0；第三輪僅文案／測試變更，test_webapp* 646 OK。
- 未解風險（移交／殘留）：`report_renderer.py:38` 死別名（零 importer，結案報告殘留清單）；`launch.py:52` 舊頁名文案（Ticket 05 處理）；持續寫入下的降級路徑多讀 2 輪（僅索引連續改寫時發生，已有文案與測試）。

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

## 阻擋與裁決紀錄
