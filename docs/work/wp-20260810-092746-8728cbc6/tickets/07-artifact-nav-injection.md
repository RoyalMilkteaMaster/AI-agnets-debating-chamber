# 離線報告伺服器側五導覽注入

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：Ticket 02

## 目標

在 `server.py` 新增回應注入器：僅對 run artifact 中 `report.html`／`debate.html` 的 text/html 回應，在 `<body>` 標籤後插入五導覽列（純 HTML、零 script、樣式走站內樣式表 route、導覽目標沿用 pages 的導覽資料）；找不到插入點時原樣送出；磁碟上的檔案位元組不變（ADR 0007）。

## 對應原始需求

- R-002：五導覽全站常駐：即時辯論／歷史與命中率／市場報告／完整辯論／設定；市場報告與完整辯論在無特定 run 頁面指向最新有報告的 run、run 詳情頁指向自身 run、無報告時停用樣式；「伺服器已關閉」頁維持無導覽；離線兩頁經伺服器瀏覽時亦有五導覽，直接開檔／分享維持自足兩分頁導覽，run 檔案唯讀不回溯。

## 使用者價值

對應 User Story 5：使用者在站內點開離線報告時，不必再按上一頁才回得去；同時分享出去的檔案仍維持自足，不被塞入站內連結。

## 範圍

包含：

- `server.py` 回應注入器：判定條件（run artifact 中的 `report.html`／`debate.html`、text/html 回應）、插入位置（`<body>` 標籤後）、插入內容（純 HTML 五導覽列、零 script）。
- 樣式由站內樣式表 route 供應；導覽目標沿用 pages 的導覽資料。
- 找不到插入點時 fail-open 原樣送出。
- 對應測試（注入／略過兩路徑）。

不包含：

- 任何寫入 run artifact 檔案的行為（磁碟檔案一字不動）。
- 離線頁自身的 renderer 換裝（Ticket 06）。
- webapp header 結構（Ticket 02 已完成）。

## 已確認實作決策

- 注入只發生在 HTTP 回應層，run artifact 唯讀，磁碟檔案位元組不變（ADR 0007：`docs/adr/0007-offline-report-nav-injection.md`）。
- 只對 run artifact 中的 `report.html`／`debate.html` 且為 text/html 的回應注入，其他回應一律不碰。
- 插入內容為純 HTML＋連結，零 script；樣式由站內樣式表 route 供應，維持 CSP 與零 inline script。
- 找不到 `<body>` 插入點時 fail-open 到原內容，原樣送出。
- 直接開檔／分享（含 PDF）維持離線自足兩分頁導覽；舊 run 同樣受惠於注入。
- 測試不斷言注入導覽的完整 HTML 字串，只斷言連結目標與零 script。

## 驗收條件

- 經伺服器瀏覽 `report.html`／`debate.html` 時頁面出現五導覽且可回站內；磁碟上的檔案位元組不變；無插入點的 HTML 原樣送出。
- 注入內容不含任何 script 標籤或 inline event handler。
- 直接開啟磁碟上的離線頁（不經伺服器）時沒有五導覽，維持自足兩分頁導覽。
- 非 `report.html`／`debate.html` 的 artifact 回應未被注入。
- 既有測試全綠。

## 測試與證據

- 測試接縫：artifact 回應注入器（給定 HTML 字串斷言插入或略過兩路徑；斷言連結目標與零 script，不斷言完整注入字串）；注入前後的檔案位元組比對。
- 迭代期快速檢查：WSL 執行針對本票模組的單測 `python3 -m unittest tests.test_webapp -v`（秒級；若本票另建獨立測試模組，改跑該模組）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、檔案位元組比對輸出與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：適用
- 判定依據：R-002 要求離線兩頁經伺服器瀏覽時亦有五導覽、直接開檔維持自足，兩種情境都必須實際操作比對。
- 操作環境與實際網址：執行階段填寫
- 使用的原生瀏覽器工具：執行階段填寫
- 操作步驟與預期結果：
  1. 經伺服器開啟某 run 的 `report.html`：頁面頂端出現五導覽，點擊可回到站內頁面。
  2. 經伺服器開啟同一 run 的 `debate.html`：同樣出現五導覽。
  3. 直接以檔案路徑開啟同一份 `report.html`：沒有五導覽，維持自足兩分頁導覽。
  4. 比對注入前後磁碟檔案位元組：完全相同。
  5. 以缺少 `<body>` 的 HTML 經伺服器取用：內容原樣送出、未被破壞。
- 操作結果：執行階段填寫
- 操作證據：執行階段填寫
- 環境註記：環境無瀏覽器時，依 Spec 測試決策以渲染後 HTML 存檔＋關鍵元素斷言為操作證據。

## 依賴

- Depends on：Ticket 02
- Blocks：Ticket 09

## 並行與所有權

- Dispatch：parallel-safe
- Exclusive write scope：`hoya_market_agents/webapp/server.py`、對應測試
- Shared resource locks：全套測試執行權（Ready for Review 執行 `python3 -m unittest discover -s tests` 時與其他 Ticket 互斥）
- Can run with：Ticket 03、Ticket 04、Ticket 05、Ticket 06

## 初始執行配置

- Developer model：`claude-opus-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：在 artifact 回應路徑新增接縫，牽涉唯讀契約（ADR 0007）與 fail-open 行為，錯誤會直接污染唯讀產出，不屬於局部低風險小修。
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
- 並行批次：批次 3（與 Ticket 03 並行；寫入範圍互斥——本票不碰 `pages.py`，T03 不碰 `server.py`）
- 排程判定：依賴 T02 已完成；Dispatch parallel-safe；為避免與 T03 同檔並行寫入，本票測試放新模組 `tests/test_webapp_nav_injection.py`（Coordinator 指定）
- 基準版本：branch `main`、HEAD `9b8a451`、基準樹 `8b9c5df12f15617e56b557edfcb3c6872e1e4f11`（T01＋T02＋T06 完成後；server.py 已含 T02 的三個渲染呼叫點）
- 開發角色：Developer = Claude（milktea-build）、model `claude-opus-5`（Ticket 相容預設偏好）、effort：平台工具無此欄位採後端預設
- 確切寫入範圍：`hoya_market_agents/webapp/server.py`、`tests/test_webapp_nav_injection.py`（新增）
- 資源鎖：全套測試執行權（與 T03 以 `.locks/full-suite` 目錄鎖互斥）
- 必跑指令：迭代 `PYTHONPATH=tests python3 -m unittest tests.test_webapp_nav_injection -v`；Ready for Review `python3 -m unittest discover -s tests`（WSL，取鎖後執行）

### Ready for Review（2026-08-10）

- Snapshot：基準樹 `8b9c5df12f15617e56b557edfcb3c6872e1e4f11` → 快照樹 `06b210e62f50eca73c5a706a5700183677d65c9e`（path-scoped 於本票兩檔）
- 變更：`server.py +93/-7`（純函式 `artifact_with_site_nav(artifact, nav)`：bytes 進出、`BODY_TAG` regex 找 `<body>`、找不到原樣回；導覽用 `pages.site_tabs(path, report_run=views.run_data(root, run_id))` 零重複、指向該 run 自身；判定沿用 `views.artifact_bytes` 既有 allowlist）、`tests/test_webapp_nav_injection.py`（新增 327 行、16 測試）
- 完整驗收（取鎖）：`Ran 2651, OK (skipped=1), RESULT_EXIT=0`（skip 為既有條件式，本票模組 16/16 零 skip）；真 socket HTTP 驗收 18 項 PASS；磁碟位元組比對 15/15 SAME（前後雜湊相同）；注入內容零 script／零 inline handler；非目標 artifact 404；CSP 未變
- Developer 自報（交 Reviewer A 方向裁決）：①「樣式走站內樣式表 route」無法照字面實作（該 route 不存在且 artifact CSP `style-src 'unsafe-inline'` 不含 self；照字面需新增端點＋放寬 CSP 皆被禁）→採沿用 `class="page-tabs"` 由離線頁自身樣式表上色（同 design_tokens 來源；舊 run 為無樣式可用連結列）；② `Sec-Fetch-Dest` iframe 例外（run 詳情頁 iframe 嵌 report.html，不擋會在預覽框長出導覽）；③雙 `aria-label="主要頁面"` landmark（修正點在 pages.py 屬 T03）；④耦合 `site_tabs` 簽名；⑤每請求多一次 run_data；⑥既有測試 `test_webapp.py:1151` 名稱 over-claim（僅因 fixture 無 body 才綠）
- Diff 全文：scratchpad `t07/ticket07.diff`（481 行）；證據 `t07/evidence/`、`t07/acceptance.py`、`t07/full-suite.log`

### Review（2026-08-10）

- Reviewer A｜軸 spec｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot `8b9c5df1`→`06b210e6`
  - 結論：不通過。九條檢核 8 過；自報偏離裁定：iframe 例外不構成 Finding（頂層 document 仍注入、避免整站進預覽框符合使用流程）；雙 landmark 與 test_webapp.py:1151 over-claim 不在本票兩檔範圍不列 Finding。
    - F-A07-1【重要｜Owner: Reviewer A｜未關閉】注入導覽未由站內樣式表 route 供應（Ticket、Spec R-002、ADR 0007 均有此句；但 codebase 無任何 CSS route、`git grep text/css` 零命中；served-report stylesheet link=0；測試 fixture 無 .page-tabs 規則仍通過）。Reviewer 判定為 Spec 內部條文衝突（route vs 無新端點/CSP 沿用），要求正式裁決其一，不自行選邊。
  - 報告：scratchpad `t07/reviewer-a-final.md`
- Reviewer B｜軸 standards｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot 同上
  - 結論：不通過。Findings：
    - F-B07-1【重要｜Owner: Reviewer B｜未關閉】`server.py:254/970` `<body>` regex 被合法 HTML 破壞：屬性值含 `>`（`data-note="1 > 0"`）會插進屬性、註解含 `<body>` 會插進註解。建議 quote-aware＋跳過註解的 byte-level 掃描，無法安全定位時原樣回傳，補兩個回歸案例。
    - F-B07-2【重要｜Owner: Reviewer B｜未關閉】導覽資料失敗使可讀 artifact 變 HTTP 500：每請求經 `run_data` 讀 4 JSON＋evidence.jsonl，votes.json 結構異常即 500（有重現）。建議窄 helper 只解析 artifact 存在性；導覽產生失敗 fail-open 回原始位元組。
    - F-B07-3【重要｜Owner: Reviewer B｜未關閉】完整回應含兩個同名 `aria-label="主要頁面"` landmark（primary_labels=2）；建議注入版本用唯一名稱＋回應層 landmark 唯一性測試；定位在本票注入整合點。
    - F-B07-4【建議｜Owner: Reviewer B｜未關閉】新測試模組 `from test_webapp import ...` 依賴 6500 行跨票檔案；建議最小自足 fixture。
  - 驗證：Windows fcntl shim 重跑 16 tests OK；反例探針與 landmark 計數 exit 0。報告：scratchpad `t07/reviewer-b-final.md`

### F-A07-1 Coordinator 裁決（2026-08-10，釐清核准需求、不擴大範圍）

- 衝突事實：「樣式走站內樣式表 route」（Ticket/Spec/ADR 字面）之 route 在 codebase 不存在（git grep text/css 零命中），而新增 route 與放寬 CSP 被同票「無新端點、CSP 沿用」明文禁止——條文在錯誤前提下寫成，不可能照字面滿足。
- 裁決依據（Spec 文本）：①已核准代價明寫「舊 run 紙白頁上浮**新設計風**注入導覽列」→ 注入導覽在舊 run 也必須呈現新設計風樣式（Developer 現行作法舊 run 無樣式，不滿足）；②artifact CSP `style-src 'unsafe-inline'` 允許行內樣式，Spec 硬約束為零 inline **script**；③色值唯一權威 design_tokens。
- 裁決：注入內容自帶 scoped `<style>` 區塊（值於注入時取自 design_tokens、選擇器限定注入導覽自身、不得影響 artifact 原有樣式；新 run 亦以注入樣式呈現避免雙重來源歧義）；不新增端點、不改 CSP、磁碟不動。此為滿足全部明文約束的唯一機制，屬核准方案內實作決策；「是否要真正的樣式表 route」列入結案報告交使用者複核。

### Findings 修正（2026-08-10，Developer fixed）

- F-A07-1（依裁決）：`site_nav_fragment()` 輸出 `<style>`＋`<nav class="hoya-site-nav" aria-label="站內導覽">`；PALETTE＋SCALE 以 custom property 宣告於導覽自身（非 :root）；`_scoped()` 產生的 7 條選擇器全以 `.hoya-site-nav` 開頭；棄用 `.page-tabs` 借用。舊 build 紙白頁實測：注入後帶新設計風樣式、五導覽齊全、零 script。
- F-B07-1：`re` 移除，`body_tag_end()` byte 掃描（跳註解、引號追蹤、分隔字元表擋 `<bodyguard>`；不確定回 None fail-open）；三反例以舊 regex 對照實測（舊插進屬性值／註解、新正確或 None）並成回歸測試。
- F-B07-2：`run_artifacts()` 窄 helper（resolve＋兩次 is_file）；`views.run_data` 移出 artifact 路徑；`_site_nav()` 例外記 `artifact_nav_unavailable` 回 None → 送原始位元組；三條回歸測試（OSError 仍 200 位元組同、ValueError log 無 request_failed、僅 question/manifest 的 run 可服務）。
- F-B07-3：單一 `<nav aria-label="站內導覽">`（設定連結以 class＋margin 分開）；pages.py 未動（server.py 端用 pages 三張導覽資料表自組）；實測 landmark 唯一；補 `aria-current="page"`。
- F-B07-4：`from test_webapp import` 全移除，模組自足（_Socket/Response/write_run/NavInjectionFixture），39 tests。
- 修正後快照樹：`02d9895f09f9f6e0baee1c6783cb82fc5bf57eb8`（server.py +332、測試 +453）
- 指定重驗：本票模組 39 OK；`test_webapp_*.py` 側模組 295 OK（`test_webapp.py` 本體 4 條設定頁失敗為 T04 併行寫入中的預期紅燈，Coordinator 確認非本票）；seats＋frontend acceptance 50 OK；真 socket 驗收 32/32 PASS；磁碟 14/14 SAME（注入段落 2106B＝樣式 1743B＋標記 363B）。

### 定向複驗（2026-08-10）

- Reviewer A：F-A07-1 `closed`（接受 Coordinator 裁決；核對 site_nav_fragment/_site_nav_tokens/_scoped 無第二份色值、7 選擇器全 scoped、無 :root/.page-tabs、零 script、無新端點、CSP 未變、磁碟不變；獨立 Windows fallback 重跑 InjectedStyleTest 4/4 OK）。spec 軸：通過，無新 Finding。報告：scratchpad `t07/reviewer-a-reverify-final.md`
- Reviewer B：F-B07-1／2／3／4 全部 `closed`（byte 掃描反例通過、fail-open 隔離＋窄 helper 確認、landmark 唯一、測試模組自足；新增 scoped style 未引入新問題）。standards 軸：通過，無新 Finding。Windows shim 重跑 39 tests OK。報告：scratchpad `t07/reviewer-b-reverify-final.md`
- 最終 Snapshot：`02d9895f09f9f6e0baee1c6783cb82fc5bf57eb8`；共識：Developer 與兩位 Reviewer 均通過。遺留備考（非阻擋）：`test_webapp.py:1151` 測試名稱 over-claim（該檔屬他票範圍，列入結案報告備考）。

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
