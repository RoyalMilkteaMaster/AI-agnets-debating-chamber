# 05 標的選單發問

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：04

## 目標

主頁發問區改為「資產類別選單＋標的輸入」，經 T05 的 `assets`／`asset_class` 接縫啟動 run，webapp 端不再做純文字資產解析；標的建議清單由 `run_index.query_runs` 結果在 Python 端去重取得。

## 對應原始需求

- R-006：標的選單發問：資產類別選單＋標的輸入框，接 T05 的 assets／asset_class 接縫，建議清單來自過往 run 標的
- R-007：全繁體中文：webapp 畫面不得出現英文資料原值，標籤從既有權威帶出

- Spec R6 標的選單（全文）：「發問改為以選單選定資產類別，再選／填標的，取代純文字資產解析。接 T05 的 `assets`／`asset_class` 接縫。標的輸入依資產類別給對應格式提示；建議清單來自過往 run 的標的——**取得方式**：webapp 呼叫既有 `run_index.query_runs` 後在 Python 端對標的欄位去重，不新增 SQL、不擴充 `run_index`（維持零 SQL 與唯一取數路徑）。」
- Spec〈實作決策／模組責任與公開介面〉：「`launcher.py` 接縫：標的表單送出後，以 T05 的 `assets`／`asset_class` 參數啟動 run；webapp 端不再執行純文字資產解析。」
- Spec〈產品限制〉：「webapp 零 SQL；唯一取數路徑是 `run_index.query_runs`。」
- Spec R7 全繁體中文（選單項目與格式提示）。
- Spec A3 第 4 條：「發問流程可用選單選定資產類別與標的，不依賴純文字解析。」
- Spec A5（選單枚舉值必翻；標的代號不在此限）。

## 使用者價值

系統不必猜標的；重複追蹤同一標的時少打字；選錯類別的失敗提前到表單而不是 run 中途。

## 範圍

### 進入範圍

1. 主頁發問區改造：資產類別選單（值來自 `config/market_scopes.json` 等既有權威，顯示為繁體中文）＋標的輸入框，依所選類別顯示對應格式提示。
2. 標的建議清單：呼叫既有 `run_index.query_runs`，在 Python 端對標的欄位去重後供前端建議使用；**不新增 SQL、不擴充 `run_index`**。
3. 表單送出 → `webapp/launch.py` → `launcher` 啟動，明確帶上 `assets` 與 `asset_class`；不再依賴題目文字推斷資產。
4. 沿用既有 `question.inspect_question(..., assets=..., asset_class=...)` 接縫傳遞呼叫端答案。
5. 表單驗證與錯誤訊息全繁體中文；未選類別或標的格式明顯不符時，在表單即回可讀錯誤，不啟動 run。
6. 零 inline script；建議清單以原生 HTML 機制呈現，不引入外部資源。

### 不進入範圍

- 修改 `hoya_market_agents/question.py` 的解析邏輯（既有接縫已接受呼叫端提供的 `assets`／`asset_class`；若實作後確認必須修改，屬方向裁決，寫入「阻擋與裁決紀錄」再處理）。
- 擴充 `run_index` 或新增任何 SQL。
- PDF 匯出（Ticket 06）、關閉伺服器與入口（Ticket 07）。

## 已確認實作決策

- 唯一取數路徑是 `run_index.query_runs`；去重在 Python 端完成。
- 資產類別詞彙不建第二份表，一律從既有權威帶出。
- 設計系統與 token 已由 Ticket 03 定形，本票沿用。
- 標的代號（`2330`、`AAPL`、`BTC`）屬資料本體，不在「英文資料原值」禁令範圍內。

## 驗收條件

1. 主頁發問區存在資產類別選單，選項為繁體中文，且值域與既有權威一致。
2. 選定不同資產類別時，標的輸入框顯示對應的格式提示。
3. 標的建議清單內容來自過往 run 且已去重（同一標的只出現一次）。
4. 表單送出後，`launcher` 實際收到的 `assets` 與 `asset_class` 等於使用者所選，與題目文字內容無關（以刻意「文字與選單不一致」的案例驗證）。
5. webapp 端不再有純文字資產解析的呼叫路徑。
6. webapp 內無任何 SQL 字串；`run_index` 公開介面未變更。
7. 未選資產類別或標的為空時，表單回可讀中文錯誤且不啟動 run。
8. 發問區渲染後 grep，無資產類別枚舉的英文原值；標的代號不在此限。
9. 渲染後 HTML 零 inline script、無外部資源。
10. 既有測試全綠。

## 測試與證據

- 測試接縫：標的表單 → launcher 參數傳遞（可注入假 launcher，斷言收到的 `assets`／`asset_class`）；建議清單去重函式（可注入 `query_runs` 結果）。
- 迭代期快速檢查：`python3 -m unittest tests.test_webapp_asset_picker`（WSL，秒級）。
- Ready for Review 完整驗收：`cd <Code Root> && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`（WSL）。若專案仍沿用 Ticket 13 的 T08 攔截器，依該慣例補上對應環境變數，實際值由 Coordinator 提供，不寫入票面。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：完整驗收結果與退出碼、發問區渲染後 HTML 存檔與關鍵元素斷言、「文字與選單不一致」案例的 launcher 參數輸出、去重前後的建議清單、SQL 字串掃描結果為零、繁中 grep 結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票直接改變使用者的發問操作方式（Spec R6），但原生瀏覽器「代驗」不適用——平台無任何 Claude／Codex 原生瀏覽器工具（已於 2026-08-10 如實回報），由使用者本人於 2026-08-10 以本機瀏覽器親自實機操作驗收並明示「結案」授權；輔以票面原核准慣例之渲染後 HTML 存檔＋關鍵元素斷言證據（選單四類別提示切換、去重前後對照、launcher 實收參數，見執行與 Review 紀錄）。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
  1. 開主頁 → 發問區出現資產類別選單與標的輸入框。
  2. 選「台股」→ 標的輸入框顯示台股格式提示；輸入框出現過往台股標的建議。
  3. 選「虛擬貨幣」→ 提示與建議清單跟著換成幣圈。
  4. 題目文字刻意寫別的市場、選單選台股並送出 → 啟動的 run 的 `asset_class` 為 `tw_stock`，`assets` 為選單所選標的。
  5. 不選類別直接送出 → 表單顯示中文錯誤，未啟動 run。
- 操作結果：不適用
- 操作證據：不適用

## 依賴

- Depends on：04
- Blocks：06

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：
  - `hoya_market_agents/webapp/pages.py`
  - `hoya_market_agents/webapp/views.py`
  - `hoya_market_agents/webapp/server.py`
  - `hoya_market_agents/webapp/launch.py`
  - 新增 `tests/test_webapp_asset_picker.py`
- Shared resource locks：`hoya_market_agents/webapp/pages.py` 與 `hoya_market_agents/webapp/server.py`（Tickets 03～07 共用熱點，以 Blocked by 串成單鏈）；`tests/test_webapp.py`（既有斷言若需更新，僅限本票持鏈期間）；全專案 unittest 套件。
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`
- model_reasoning_effort：`high`
- 路由理由：涉及公開接縫（launcher 參數契約）、輸入驗證與零 SQL 邊界，且改變 run 啟動的資料來源，屬跨模組且有資料風險的改動。
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

### 開始執行（2026-08-10 00:50 +0800）

- Execution environment：沿用 Task 設定（`source: auto_current`）：Windows 宿主＋`wsl.exe -d Ubuntu-24.04 --` 前綴；WSL 路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；python3 3.12.3。
- 並行批次：batch-5（單票；上游 01–04 完成，`pages.py`／`server.py` 鏈與全專案測試鎖釋放予本票）。
- 基準版本：main @ `9b8a451`＋工作樹（含 T01–T04 成果），基準樹 `674b21aa46aeb68eb070501e45dc9e72d8a84917`（已驗證）。基準全套：2322 tests OK (skipped=1)、exit 0（Ticket 04 修正輪驗收；第三輪僅文案／測試變更後 test_webapp* 646 OK）。
- 開發角色：Developer＝Claude Code 子 Agent（`milktea-build`），model `claude-opus-5`（票面偏好）；effort＝後端預設（派工工具無 effort 欄位，原因同前）。Reviewer A／B＝Codex（gpt-5.6-sol／high）。
- **裁定六（承 Ticket 04 移交）**：`launch.py:52` 忙碌訊息仍寫舊頁名「歷史查詢」；`launch.py` 屬本票 Exclusive write scope……（更正：票面 scope 為 webapp/launch.py，該行在其中）——本票一併把該文案同步為「歷史與命中率」，屬最小文案修正，非範圍擴充。
- Exclusive write scope：依票面（webapp/pages.py、views.py、server.py、launch.py、新增 tests/test_webapp_asset_picker.py）。未追蹤檔 diff 用 `git show <基準樹>:<path>`＋`git diff --no-index`。
- Shared resource locks：`pages.py`／`server.py`（本票持鏈）；`tests/test_webapp.py`（既有斷言若需更新，僅限本票持鏈期間）；全專案 unittest 套件。
- 必跑指令（Ready for Review 完整驗收）：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`（WSL，腳本檔取碼）。

### Ready for Review（2026-08-10 02:00 +0800）

- 發問區：題目＋資產類別 `<select>`（值域 `question.ASSET_CLASSES`、顯示走既有 `asset_class_label`／market_scopes 權威）＋每類別標的框＋格式提示（權威 `symbol_resolution` 第一句原文）＋`<datalist>` 建議（`query_runs` limit=200 Python 端去重）。切換用 `:has()` 生成規則，退化安全側（不支援時全顯示、伺服器讀所選類別指名欄位）。
- 送出：`read_request`→五段 fail-closed 驗證（順序：空題目→未選類別→權威拒絕→空標的→READY 憑證，全繁中、寫 `launch_refused`、不啟動程序）→子程序內呼叫 `run_launch(assets=…, asset_class=…)`。子程序由 `-m` 改 `-c` bootstrap（runpy 重複執行警告實測）。**webapp 端零純文字資產解析**（AST 掃描測試釘住：每個 `inspect_question` 呼叫必帶兩參數、禁 `analyze_question`）。裁定六完成（BUSY_MESSAGE 舊頁名同步）。
- 關鍵反解析證據：「題目寫幣圈＋選單選台股」→ launcher 實收 `{"assets":["2330"],"asset_class":"tw_stock"}`；真子程序煙霧測試（無 READY→exit 2、零目錄、零 RuntimeWarning）。
- 完整驗收：**exit 0，Ran 2399 tests，OK (skipped=1)**。基準經 Developer 以 `git archive` 重測為 **2324**（Coordinator 先前記 2322 為 T04 第二輪數字，第三輪+2；以 2324 為準）；2399=2324+75 新測試。question.py／run_index.py 未動（雜湊釘住）。
- 前端證據：渲染 HTML 存檔（/tmp/t05-evidence/）＋選單值→繁中文字對照、四類別框／提示／建議、去重前後對照、未選類別與空標的的中文錯誤、零 inline script 無外部資源、SQL 掃描零命中（含反向檢查）。
- 變更檔案 6：launch.py(+246/−28)、pages.py(+233/−24)、server.py(+9/−5)、views.py(+55/−0)、test_webapp.py(+51/−12)、新增 test_webapp_asset_picker.py(+942，75 tests)。
- Review snapshot：基準樹 `674b21aa`＋blob：launch=098f8ec6、pages=0da9dd48、server=553575de、views=a7bd3302、test_webapp=6adb09e5、test_webapp_asset_picker=70ef093b；未動鄰檔 question.py=04a39777、run_index.py=c0adb745、live.py=c0171e16。
- **Coordinator 裁定七（風險 2）**：標的一律必填（含開放題）依票面驗收 7 字面維持；「開放題無標的」自 webapp 發起的既有能力因此收斂（CLI 不受影響）。此為票面明文與既有能力的取捨，記錄於此並列入結案報告供使用者確認；Reviewer A 於 spec 軸獨立評估是否與 Spec 衝突。
- Developer 提請 Review 特別檢視：`:has()` 依賴與退化方向、子程序命令列改變（`-c` bootstrap）、多標的分隔輸入、建議清單 200 上限。

### Review 紀錄（2026-08-10）

- Reviewer A（spec 軸、native、codex CLI 0.146.0／gpt-5.6-sol／high、隔離 session）：9 檔 blob 逐行吻合、diff 行數與紀錄一致。驗收 1–10 逐條通過（自行重跑 75 tests OK）；裁定六通過；**裁定七判定與 Spec 相容、不構成缺陷**（Story 6／R6／A3 皆為「類別＋標的」、驗收 7 明文拒絕空標的、Spec 無 open 免標的明文；底層 CLI 能力不推翻票面字面）；接縫正確（`-c` bootstrap 有效預設與舊 CLI 完全相同、session 邊界未變）；多標的輸入判定為 R6 合理實作（拆成多個既有接縫元素、逐一權威驗形）。**Findings：無。結論：通過。**
- Reviewer B（standards 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：9 檔雜湊吻合、diff 基準檔比對相符。六項全過（launch 重構 🟢——自行重現 runpy 警告證實 `-c` 理由；`:has()` 退化安全性論證成立；建議清單成本合理；多標的 regex 含全形空白處理；test_webapp 改動無弱化）。**結論：通過**，唯一 Finding 建議級（Owner＝Reviewer B，明示不影響交付）：
  - **B-01［建議］test_webapp_asset_picker.py:100**：測試額外鎖死子程序命令列組成（`-c`／前三 argv／`--x=y`）；接縫斷言（launcher kwargs）與真子程序煙霧已各自覆蓋，建議拆三層。

### 修正輪與共識（2026-08-10 03:10 +0800）

- B-01 修正（Developer 選修，理由：本票內 `-m`→`-c` 那次改動已實證「換寫法就得改斷言」的測試不該存在）：三層拆分完成——route/spawn 只斷言「指定值都離開程序＋子程序是本解譯器」；main→launcher 用 dropwhile 取旗標；煙霧測試改拆真命令。耦合模式 grep 零命中；產品碼一行未動（diffstat 與首輪相同）；75＋586 tests OK。
- Reviewer B 定向複驗（resume 原 session）：2 檔雜湊吻合、三層確認、自行重跑 75 tests OK。**B-01 closed**；「standards 軸最終結論維持：通過。」
- 共識成立：A 首輪通過零 Finding、B 通過且唯一建議級 Finding 已 closed。最終 Snapshot：launch=098f8ec6、pages=0da9dd48、server=553575de、views=a7bd3302、test_webapp=bd0590d4、test_webapp_asset_picker=24918d98（基準樹 `674b21aa`）。完整驗收 2399 tests OK (skipped=1)、exit 0（產品樹自該輪未變）。
- 未解風險（記錄）：裁定七取捨（開放題標的必填）列入結案報告供使用者確認；`:has()` 依賴（退化安全側）；`webapp/__init__.py` 模組清單補述屬範圍外殘留。

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

## 阻擋與裁決紀錄
