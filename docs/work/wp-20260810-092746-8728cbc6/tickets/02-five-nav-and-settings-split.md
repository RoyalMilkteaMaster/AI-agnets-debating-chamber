# 五導覽常駐與設定分離

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：Ticket 01

## 目標

重構全站 header：左起「即時辯論｜歷史與命中率｜市場報告｜完整辯論」，右側「設定」獨立且緊鄰「關閉伺服器」按鈕左邊；辯論室與 run 詳情頁的報告導覽指向該 run 自身，主頁初始／歷史／設定頁由 `views.py` 以既有 `run_index.query_runs` 在 Python 端解析「最新有報告的 run」（零 SQL）；全站無報告時沿用現行停用樣式（span、不進 tab order）；「伺服器已關閉」頁維持無導覽例外。

## 對應原始需求

- R-002：五導覽全站常駐：即時辯論／歷史與命中率／市場報告／完整辯論／設定；市場報告與完整辯論在無特定 run 頁面指向最新有報告的 run、run 詳情頁指向自身 run、無報告時停用樣式；「伺服器已關閉」頁維持無導覽；離線兩頁經伺服器瀏覽時亦有五導覽，直接開檔／分享維持自足兩分頁導覽，run 檔案唯讀不回溯。
- R-003：設定獨立：「設定」與其他四個導覽分開，放「關閉伺服器」按鈕左邊。

## 使用者價值

對應 User Story 2 與 3：使用者要在任何頁面都能一步跳到即時辯論、歷史、最新報告或設定，並讓瀏覽動線（四導覽）與管理動線（設定＋關閉伺服器）在版面上分開，不再混在同一組連結裡。

## 範圍

包含：

- `webapp/pages.py` header 區重構：四個瀏覽導覽在左、「設定」獨立在右且緊鄰「關閉伺服器」左邊，全站頁面一致。
- `views.py` 新增「最新有報告 run」解析：走既有 `run_index.query_runs`，在 Python 端篩選，不擴充 run_index、不寫 SQL。
- 有 run 脈絡的頁面（辯論室、run 詳情）報告導覽指向該 run 自身。
- 全站無報告時沿用現行停用樣式（span、不進 tab order）。
- 「伺服器已關閉」頁維持無導覽例外。
- 對應測試（latest-report 解析的有報告／無報告兩況、各頁 header 結構斷言）。

不包含：

- 離線報告經伺服器瀏覽時的導覽注入（Ticket 07）。
- 版面視覺換裝（Ticket 03）。
- 設定頁欄位文案（Ticket 04）。

## 已確認實作決策

- 「最新有報告 run」的解析責任在 `views.py`，使用既有 `run_index.query_runs`，零 SQL、不擴充 run_index。
- 導覽目標分流規則：有 run 脈絡→該 run；無脈絡（主頁初始、歷史、設定）→最新有報告 run；全站無報告→停用樣式。
- 停用樣式沿用現行做法（以 span 呈現、不進 tab order），不新發明樣式。
- 無新端點、無 URL 變更；CSP、零 inline script、零 SQL 沿用。
- 「伺服器已關閉」頁是唯一無導覽例外，維持現狀。

## 驗收條件

- 除「伺服器已關閉」頁外，每個 webapp 頁面 header 有五導覽，「設定」位於「關閉伺服器」左邊；主頁初始／歷史／設定頁的報告導覽指向最新有報告 run，run 詳情頁指向自身 run；無報告時為停用樣式。
- `GET /stats` 轉跳等既有路由行為不變。
- 「伺服器已關閉」頁仍無導覽。
- 停用狀態的「市場報告／完整辯論」不出現在 tab order 中。
- 既有測試全綠。

## 測試與證據

- 測試接縫：latest-report 解析函式（有報告／無報告兩況），以及各頁 header 的導覽項目與順序斷言。
- 迭代期快速檢查：WSL 執行針對本票模組的單測 `python3 -m unittest tests.test_webapp -v`（秒級；若本票另建獨立測試模組，改跑該模組）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、必要執行輸出與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：適用
- 判定依據：R-002／R-003 直接規範使用者介面上的導覽位置與指向，必須逐頁實際檢視。
- 操作環境與實際網址：執行階段填寫
- 使用的原生瀏覽器工具：執行階段填寫
- 操作步驟與預期結果：
  1. 逐一開啟即時辯論、歷史與命中率、設定、run 詳情頁：header 皆出現五導覽，左起「即時辯論｜歷史與命中率｜市場報告｜完整辯論」，「設定」在右側且緊鄰「關閉伺服器」左邊。
  2. 於主頁初始、歷史、設定頁點「市場報告」與「完整辯論」：指向最新有報告的 run。
  3. 於 run 詳情頁點「市場報告」與「完整辯論」：指向該 run 自身。
  4. 在無任何報告的狀態下檢視：兩個報告導覽為停用樣式且無法以 Tab 聚焦。
  5. 開啟「伺服器已關閉」頁：確認維持無導覽。
- 操作結果：執行階段填寫
- 操作證據：執行階段填寫
- 環境註記：環境無瀏覽器時，依 Spec 測試決策以渲染後 HTML 存檔＋關鍵元素斷言為操作證據。

## 依賴

- Depends on：Ticket 01
- Blocks：Ticket 03、Ticket 07

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：`hoya_market_agents/webapp/pages.py`（header 區）、`hoya_market_agents/webapp/views.py`、對應測試、`hoya_market_agents/webapp/server.py`（僅 404 與無法啟動頁的渲染呼叫點；2026-08-10 Coordinator 依 F-A02-2 補授權，T07 未派發無併行持有者）
- Shared resource locks：`hoya_market_agents/webapp/pages.py`（熱點鏈）
- Can run with：Ticket 06

## 初始執行配置

- Developer model：`claude-opus-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：涉及跨頁導覽契約與「最新有報告 run」的資料解析，牽動多個頁面的公開行為，不是單點文案或既有模式的小修改。
- 升級路徑：Claude 偏好為 `claude-opus-5`／`xhigh`；實際使用其他後端時由 Implement 採用該後端已驗證可用的升級設定；`max` 需使用者明確核准
- 執行時覆寫：最新使用者角色設定優先；偏好後端不可用時回退到唯一可用平台並留下紀錄
- Research 證據：無

## Agent 分工

- Developer：負責實作、驗證、修正或以證據反駁 Findings
- Reviewer A：`both` 時只執行 Spec Review；`a_only` 時執行 Spec 與 Standards
- Reviewer B：`both` 時只執行 Standards Review；`b_only` 時執行 Spec 與 Standards
- Reviewer 啟用規則：由執行 Task 最新 `settings_update: reviewers` 決定；預設 `both`，Ticket 不自行固定或搜尋設定
- Reviewer 標準：每位啟用 Reviewer 都載入 `$milktea-skills-code-review`，只執行 Coordinator 指定的 `review_axis`
- CLI 與模型：Developer 的上述配置只是相容預設偏好；實際配置依最新使用者角色設定與後端可用性決定，Reviewer 仍獨立決定

## 完成規則

- Developer 與各 Finding 的原 Reviewer 已處理所有可重現且有證據的問題。
- 沒有未解決的阻擋或重要正確性、可執行性、可讀性、架構或衍生風險。
- Developer 與各 Finding Owner 對關閉或撤回事由達成共識。

## 執行與 Review 紀錄

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

### 開始執行（2026-08-10）

- Execution environment：Windows 宿主＋WSL `Ubuntu-24.04`（python3 3.12.3）；前綴 `wsl.exe -d Ubuntu-24.04 --`；source: auto_current
- 並行批次：批次 2（與 Ticket 06 並行；寫入範圍互斥）
- 排程判定：依賴 T01 已完成；`pages.py` 熱點鏈次棒，與 T06（renderer 檔）無檔案重疊，Can run with 06
- 基準版本：branch `main`、HEAD `9b8a451`、基準樹 `3caf0dc2f6f08ff355d871d0b7741f4bf9af0bd9`（T01 最終快照）
- 開發角色：Developer = Claude（milktea-build）、model `claude-opus-5`（Ticket 相容預設偏好）、effort：平台工具無此欄位採後端預設
- 確切寫入範圍：`hoya_market_agents/webapp/pages.py`（header 區）、`hoya_market_agents/webapp/views.py`、對應測試（`tests/test_webapp.py` 等既有 webapp 測試模組）
- 資源鎖：`pages.py` 熱點鏈（本批次獨占）；全套測試執行權（與 T06 以 `.locks/full-suite` 目錄鎖互斥）
- 必跑指令：迭代 `python3 -m unittest tests.test_webapp -v`；Ready for Review `python3 -m unittest discover -s tests`（WSL，取鎖後執行）

### Ready for Review（2026-08-10）

- Snapshot：基準樹 `3caf0dc2f6f08ff355d871d0b7741f4bf9af0bd9` → 快照樹 `b4e0e79642609af53c4ee2cf29ab7975d45af8ce`（path-scoped 於本票四檔）
- 變更：`pages.py +174/-60`（SITE_TABS 拆 BROWSE_TABS＋SETTINGS_TAB、兩個具名 nav 群組、恆為兩個報告 tab 的資料結構收斂、辯論室連線指示器左移）、`views.py +73/-1`（`latest_report_run()` 走 `query_runs` 零 SQL、REPORT_NAV_RUN_LIMIT=200）、`test_webapp.py +368/-15`（新增 22 測試）、`test_webapp_history_merge.py +12/-2`
- 完整驗收（取 `.locks/full-suite` 鎖）：`Ran 2622, OK (skipped=1), RESULT_EXIT=0`（skipped 為基準既有環境條件）；模組級 608 OK；TDD Red 證據＋fallback 突變驗證（已還原）；server.py／settings.py／live.py／seats.py／roster 與基準位元組相同
- Developer 自報風險：404／無法啟動頁報告導覽恆停用（data root 接縫在 server.py 屬 T07，Spec「無特定 run 頁面」語意交 Reviewer A）；pages.py「reads nothing」不變式多一具名例外；歷史頁索引讀取 2→3；200 筆有界搜尋；連線指示器視覺左移
- Diff 全文：scratchpad `t02/ticket02.diff`（895 行）；渲染 HTML 證據 7 檔存 `t02/rendered/`

### Review（2026-08-10）

- Reviewer A｜軸 spec｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot `3caf0dc2`→`b4e0e796`
  - 結論：不通過。Findings：
    - F-A02-1【重要｜Owner: Reviewer A｜未關閉】`views.py:89` 200 筆上限破壞「最新有報告 run」語意（前 200 筆無報告、第 201 筆有→誤回 None，導覽誤停用，違反 R-002）。建議移除上限＋第 201 筆回歸測試。
    - F-A02-2【重要｜Owner: Reviewer A｜未關閉】404 頁（`pages.py:1375`）與無法啟動頁（`:902`）固定 `report_run=None`；依 R-002 屬無 run 脈絡頁應指向最新有報告 run，明定例外僅關閉頁。修法需 `server.py` 呼叫點傳入 data_root（Coordinator 已補窄授權）。
  - 報告：scratchpad `t02/reviewer-a-final.md`（其「A 通過後才執行 B」表述與本 Task 契約不符，B 為獨立並行，不影響其 Findings 效力）
- Reviewer B｜軸 standards｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot 同上
  - 結論：待修正。Findings：
    - F-B02-1【重要｜Owner: Reviewer B｜未關閉】同 200 筆截斷缺陷（有重現：DEFAULT_RESULT=None、UNBOUNDED_RUN_ID='old-report'、MUTATION_CHECK=PASS）。
    - F-B02-2【建議｜Owner: Reviewer B｜未關閉】`pages.py:1` docstring「只讀一件事」不準確（`pages.py:331/370` 呼叫 `market_scopes()` 經 `prompt_builder.py:176` 惰性讀設定檔）。建議刪絕對敘述、準確列出間接資料來源。
  - 驗證：26 項精準測試 OK（fcntl shim）、SQL/inline script 掃描 NONE、compileall exit 0。報告：scratchpad `t02/reviewer-b-final.md`

### Findings 修正（2026-08-10，Developer fixed）

- F-A02-1／F-B02-1：`REPORT_NAV_RUN_LIMIT` 與 `limit` 參數移除，`latest_report_run` 改 `query_runs` 全量（newest first、命中即停；未採分批因 query_runs 無 offset、分批為 O(n²)）；新增 201 筆硬驗收回歸測試（修正前紅）；實測 201 run 0.171s。
- F-A02-2：`render_not_found_page`／`render_launch_problem_page` 增 `report_run` 參數；`server.py` 僅 3 個授權渲染呼叫點（404、launch not-ready、busy 409）傳入 `latest_report_run(root)`；新增指向／停用雙況測試與渲染證據（t02-launch-refused.html）。刻意保留：`server.py:333` 500 錯誤邊界仍 `None`（頁面產生失敗不再依賴讀索引），交 Reviewer A 表態。
- F-B02-2：docstring 據實列出兩個間接來源（market_scopes 惰性載入、latest_report_run），刪絕對敘述；server.py 註解同步修正。
- 修正後快照樹：`6682b69363596a7808e1226520fba85033160728`（pages.py +65/-24、server.py +23/-3、views.py +31/-14、test_webapp.py +121/-17）
- 指定重驗：`tests.test_webapp` 613 OK、`discover -p "test_webapp*.py"` 869 OK、`tests.test_seats`＋`test_frontend_redesign_acceptance` 50 OK，全 RESULT_EXIT=0；未越 webapp 範圍故未重跑全套。

### 定向複驗與完成（2026-08-10）

- Reviewer A：F-A02-1 `closed`（`views.py:425` 上限移除、`:466` 全量 query_runs、`test_webapp.py:3077` 201 筆回歸）；F-A02-2 `closed`（三個呼叫點傳入最新報告、雙向測試＋渲染證據）；500 頁裁決「不構成 Finding」（最後錯誤邊界不重讀可能故障的索引，避免遞迴失敗）。spec 軸：通過。報告：scratchpad `t02/reviewer-a-reverify-final.md`
- Reviewer B：F-B02-1 `closed`、F-B02-2 `closed`，無新 Finding。standards 軸：通過。報告：scratchpad `t02/reviewer-b-reverify-final.md`
- 建議級備考（不阻擋）：A 報告轉述 busy 409 測試可補報告導覽目標斷言（`test_webapp.py:4116`）；現行實作正確。
- 最終 Snapshot：`6682b69363596a7808e1226520fba85033160728`；共識：Developer 與兩位 Reviewer 均通過。

## 阻擋與裁決紀錄

只有真正需要方向裁決時才追加下列欄位；一般 Bug 修正、測試失敗、Review Finding 或同一方案內的迭代不得寫成使用者阻擋：

- 原始需求：
- 目前理解：
- 實際卡住的原因：
- 已嘗試方案與證據：
- 為什麼不能繼續盲修：
- 簡單可行方案：
- Agent 建議：
- 需要使用者決定：
