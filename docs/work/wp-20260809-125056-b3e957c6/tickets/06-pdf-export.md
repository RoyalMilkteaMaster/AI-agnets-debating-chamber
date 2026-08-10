# 06 離線報告 PDF 手動匯出

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：05

## 目標

在報告頁提供「匯出 PDF」按鈕，經 `POST /run/<id>/export-pdf` 以 Edge 無頭模式把該 run 現成的 HTML 轉成 `report.pdf` 與 `debate.pdf` 寫入 run 資料夾；轉換器可注入；失敗回誠實錯誤頁且不留半成品。

## 對應原始需求

- R-008：PDF 手動匯出：報告頁按鈕以 Edge 無頭模式轉出 report.pdf 與 debate.pdf 存入該 run 資料夾

- Spec R8 PDF 手動匯出（全文）：「報告頁提供「匯出 PDF」按鈕，以 Edge 無頭模式把該 run 現成的 HTML 轉成 `report.pdf` 與 `debate.pdf`，存入該 run 資料夾。HTML 與報告版面不動。」
- Spec〈邊界與錯誤〉第 2、3 條：「PDF 匯出成為 run 資料夾第三個寫入路徑（現行唯二：launch 起子程序、`outcome.json`）；只新增 `.pdf`，不改任何既有檔案；不得踩到 `run_verifier` 的檢查。」「匯出失敗時回誠實錯誤頁，不得留下半成品檔或假稱成功。」
- Spec〈實作決策／資料與所有權〉：「run artifact 維持唯讀。run 目錄的核准寫入路徑由兩條（launch 起子程序、`outcome.json`）增為三條，第三條只新增 `.pdf`。」
- Spec〈實作決策／模組責任與公開介面〉：「webapp 新增 PDF 匯出模組：接受可注入的轉換器，把該 run 現成 HTML 轉為 PDF；預設實作為 Edge 無頭模式，在 WSL 下以 `wslpath` 轉換路徑。」
- Spec〈實作決策／Schema、API contract 與系統互動〉端點契約第 3 條與「系統互動」條。
- Spec〈實作決策／相容、遷移與技術限制〉：「頁尾誠實：會寫檔的頁面不得沿用唯讀頁尾（PDF 匯出頁屬會寫檔）。」「離線 bundle 導覽只能包含真實存在的檔案；`run_verifier` 會檢查每個相對連結目標存在。」
- Spec A2 第 2、3、4 條。

## 使用者價值

分析完直接產生兩份 PDF，可以原樣傳給別人，不必自己另存或列印。

## 範圍

### 進入範圍

1. 新增 webapp PDF 匯出模組：公開介面接受「run 識別」與「轉換器」，把該 run 現成的 `report.html`／`debate.html` 轉為 `report.pdf`／`debate.pdf`。
2. 預設轉換器實作為 Edge 無頭模式；在 WSL 下以 `wslpath` 轉換輸入與輸出路徑。
3. 新增端點 `POST /run/<id>/export-pdf`；報告頁（或 run 詳情頁的報告入口處）新增「匯出 PDF」按鈕，零 inline script。
4. 只新增 `.pdf`：不得改寫、移動或刪除 run 目錄內任何既有檔案。
5. 失敗處理：轉換失敗、找不到來源 HTML、Edge 不可用等情況一律回誠實錯誤頁，說明實際原因；不得留下 0 位元或半寫的 `.pdf`。
6. 頁尾誠實：提供匯出按鈕的頁面屬會寫檔頁面，不得沿用唯讀頁尾。
7. 匯出後 `run_verifier` 對該 run 的檢查仍通過。

### 不進入範圍

- 修改 `report_renderer.py`／`report_audit_renderer.py`／`run_verifier.py`（Ticket 01 已用掉唯一開口：席位標籤來源）。
- 離線報告版面、內容或 bundle 導覽結構。
- 自動匯出、排程匯出或匯出後上傳。
- 關閉伺服器與入口（Ticket 07）。

## 已確認實作決策

- 轉換器可注入；測試以假轉換器驗證，不真的呼叫 Edge。
- 唯一外部互動是 Edge 無頭轉檔（本機程序），無任何網路資源請求。
- run artifact 其餘部分維持唯讀。

## 驗收條件

1. 報告可用的 run 頁面上存在「匯出 PDF」按鈕；報告尚未產生時該按鈕為停用狀態。
2. 按下匯出後，該 run 資料夾出現 `report.pdf` 與 `debate.pdf`。
3. 匯出前後比對該 run 目錄，除新增的兩個 `.pdf` 外，所有既有檔案的內容與時間戳未被修改。
4. 匯出後對該 run 執行 `run_verifier` 檢查仍通過。
5. 注入會失敗的假轉換器 → 回誠實錯誤頁，頁面說明實際失敗原因，且 run 資料夾內不存在任何新增或半寫的 `.pdf`。
6. 來源 HTML 不存在時 → 回誠實錯誤頁，不建立空檔。
7. 端點只接受 `POST`；以 `GET` 存取不會觸發寫檔。
8. 提供匯出按鈕的頁面使用「會寫檔」版本頁尾。
9. 渲染後 HTML 零 inline script、無外部資源。
10. 既有測試全綠。

## 測試與證據

- 測試接縫：PDF 匯出 handler（注入**假轉換器**，不真的叫 Edge）；匯出模組的公開介面；暫存目錄承載 run artifact。
- 迭代期快速檢查：`python3 -m unittest tests.test_webapp_pdf_export`（WSL，秒級）。
- Ready for Review 完整驗收：`cd <Code Root> && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`（WSL）。若專案仍沿用 Ticket 13 的 T08 攔截器，依該慣例補上對應環境變數，實際值由 Coordinator 提供，不寫入票面。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：完整驗收結果與退出碼、匯出前後 run 目錄檔案清單與雜湊比對、失敗路徑的錯誤頁內容與目錄狀態、`run_verifier` 執行結果、報告頁渲染後 HTML 存檔與按鈕停用／啟用兩種狀態的斷言、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。
- 註：測試不得斷言 Edge 無頭模式的實際命令列組成（Spec〈測試決策／不應耦合的實作細節〉），只透過注入的轉換器介面驗證。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票直接涉及使用者在報告頁的操作與可觀察結果（Spec R8／A2），但原生瀏覽器「代驗」不適用——平台無任何 Claude／Codex 原生瀏覽器工具（已於 2026-08-10 如實回報），由使用者本人於 2026-08-10 以本機瀏覽器親自實機操作驗收並明示「結案」授權；輔以票面原核准慣例之渲染存檔＋斷言證據，且真實 Edge 轉檔已在拋棄式 Data Root 實測通過（真 PDF 落地、既有檔案雜湊不變，見執行與 Review 紀錄），未假稱截圖。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
  1. 開一個已產出報告的 run 頁面 → 右上角「市場報告」「完整辯論」可點，「匯出 PDF」按鈕可用。
  2. 按「匯出 PDF」→ 頁面回報成功，run 資料夾出現 `report.pdf` 與 `debate.pdf`。
  3. 開一個尚未產出報告的 run → 「匯出 PDF」為停用狀態。
  4. 以注入的失敗轉換器重跑 → 顯示誠實錯誤頁，資料夾無新增 `.pdf`。
- 操作結果：不適用
- 操作證據：不適用

## 依賴

- Depends on：05
- Blocks：07

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：
  - 新增 `hoya_market_agents/webapp/pdf_export.py`
  - `hoya_market_agents/webapp/server.py`
  - `hoya_market_agents/webapp/pages.py`（匯出按鈕與頁尾）
  - 新增 `tests/test_webapp_pdf_export.py`
- Shared resource locks：`hoya_market_agents/webapp/pages.py` 與 `hoya_market_agents/webapp/server.py`（Tickets 03～07 共用熱點，以 Blocked by 串成單鏈）；run 目錄寫入路徑（本票新增第三條，與 launch 起子程序、`outcome.json` 並存）；全專案 unittest 套件。
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`
- model_reasoning_effort：`high`
- 路由理由：新增 run 目錄的第三條寫入路徑（資料風險）、新增公開端點、涉及外部程序呼叫與失敗處理的不可逆性，明確不屬於低風險局部修改。
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

### 開始執行（2026-08-10 03:20 +0800）

- Execution environment：沿用 Task 設定（`source: auto_current`）：Windows 宿主＋`wsl.exe -d Ubuntu-24.04 --` 前綴；WSL 路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；python3 3.12.3。
- 並行批次：batch-6（單票；上游 01–05 完成，`pages.py`／`server.py` 鏈、run 目錄寫入路徑與全專案測試鎖釋放予本票）。
- 基準版本：main @ `9b8a451`＋工作樹（含 T01–T05 成果），基準樹 `231095289450784ba9e0c5b49ada53f8e435394f`（已驗證）。基準全套：2399 tests OK (skipped=1)、exit 0（Ticket 05 驗收）。
- 開發角色：Developer＝Claude Code 子 Agent（`milktea-build`），model `claude-opus-5`（票面偏好）；effort＝後端預設（派工工具無 effort 欄位，原因同前）。Reviewer A／B＝Codex（gpt-5.6-sol／high）。
- Exclusive write scope：依票面（新增 webapp/pdf_export.py、server.py、pages.py 匯出按鈕與頁尾、新增 tests/test_webapp_pdf_export.py；`tests/test_webapp.py` 依 Shared locks 慣例僅限既有斷言牽動部分）。未追蹤檔 diff 用 `git show <基準樹>:<path>`＋`git diff --no-index`。
- Shared resource locks：`pages.py`／`server.py`（本票持鏈）；run 目錄寫入路徑（本票新增第三條，僅 `.pdf`）；全專案 unittest 套件。
- 必跑指令（Ready for Review 完整驗收）：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`（WSL，腳本檔取碼）。

### Ready for Review（2026-08-10 04:20 +0800）

- `pdf_export.py` 新模組：`export_run_pdfs(data_root, run_id, convert=None)` 四狀態；`EXPORTS` 表為唯一權威（按鈕停用條件與端點來源檢查共讀）；兩階段寫入（mkstemp→全部成功才 os.replace、失敗 finally 清暫存、0 位元也算失敗）；預設 `EdgeConverter` 四接縫、WSL 走 wslpath。
- `server.py`：`POST /run/<id>/export-pdf`＋`convert_pdf` 注入接縫＋三級記錄；GET 回 404 誠實句（沿用既有慣例）。`pages.py`：匯出卡（純 POST 表單、零 inline script、未新增 CSS）＋`RUN_DETAIL_FOOTER` 會寫檔頁尾。
- 完整驗收：**exit 0，Ran 2473 tests，OK (skipped=1)**（基準 2399＋74，新檔零 skip）。
- 前端證據十項全過（可用態／停用態渲染、成功後目錄僅增兩 .pdf 且其餘雜湊 mtime 全等、失敗 500 誠實頁零殘留、來源缺 200 指引、GET 不寫檔、**真 RunController run 匯出前後 VERIFIED 且 required_artifacts 雜湊未變**、頁尾誠實、零 inline script）。
- **真實 Edge 實測**（拋棄式 Data Root）：真報告 168KB→report.pdf 887KB、debate.pdf 1.2MB、%PDF-1.4、12.1 秒；Windows 磁碟與 WSL UNC 兩種 Data Root 形狀都通過；使用者真實 Data Root 零 .pdf/.part（find 確認）。**過程中修掉真 bug**：Edge 未給獨立 `--user-data-dir` 時會借使用者瀏覽器 session 印 PDF 且程序永不結束（90 秒逾時實測）；修後 6.4 秒退出碼 0；回歸測試以介面釘住機制、不斷言旗標拼字（Spec 測試紅線遵守）。
- Mutation check：關路徑轉換／忽略退出碼／不查 0 位元／失敗保留半套 → 分別被 3/2/1/1 個測試抓到。
- 變更檔案：新增 pdf_export.py(+464)、server.py(+130/−10)、pages.py(+110/−2)、新增 test_webapp_pdf_export.py(+988，74 tests)、test_webapp.py(+9/−1 僅頁尾清單)。
- **裁定八**：`webapp/__init__.py` docstring 經 T04/T06 已與事實不符（寫入例外數、模組清單缺 pdf_export），授權本票最小文件同步（僅 docstring、不動程式），Developer 補齊後再固定 snapshot。
- Developer 判斷點供 Review：重複匯出覆寫自己的舊 .pdf（僅 .pdf 檔）；停用條件要求兩份 HTML 齊（較字面嚴）；GET 404 而非 405（既有慣例）；匯出同步阻塞 ~12 秒（本機單人）。
- **裁定八完成**：`webapp/__init__.py` docstring 同步（+23/−9，兩個具名寫入例外、五個具名檔案、pdf_export 條目、launch 補述）；「僅 docstring」經 AST 比對證明（剝掉 docstring 後其餘語句 ast.dump 與基準完全相同）；兩個讀原始碼文字的測試實跑 285 OK (skipped=1 為既有)。順手發現的既有瑕疵「Four boundaries／實為六 bullets」裁定不修（基準樹既有、非本工作包後果），記入殘留清單。
- Review snapshot：基準樹 `23109528`＋blob：pdf_export=6b7b58be、server=5ffc1b3c、pages=ba6225ec、__init__=b17213b1、test_webapp_pdf_export=254fd678、test_webapp=6666033c；未動鄰檔 report_renderer=c9934670、run_verifier=d5253594、views=a7bd3302。

### Review 紀錄（2026-08-10）

- Reviewer A（spec 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：9 檔雜湊吻合。驗收 1、2、4、6–10 通過；判斷點 (b)(c)(d) 通過；測試紅線通過（無旗標拼字斷言）；裁定八結構同步完成且 AST 無行為變更。**結論：不通過**，Findings（Owner＝Reviewer A，皆阻擋級）：
  - **A-F01［阻擋］pdf_export.py:216**：重複匯出以無條件 `os.replace` 覆寫匯出前已存在的 PDF（定向重現：v1→v2 兩次 exported、兩份 SHA-256 全變），違反票面範圍 4「不得改寫、移動或刪除任何既有檔案」（無自產例外）；`test_webapp_pdf_export.py:188` 還把違規行為釘成預期；頁面同時宣稱「既有的檔案一個都不會改」。期望：目標已存在即拒絕並誠實說明，或先取得明確覆寫規格。
  - **A-F02［阻擋］pdf_export.py:220**：第二份 promotion 失敗留下單邊 `report.pdf`（注入 OSError 重現：state=conversion_failed 但 written=('report.pdf',)），違反「失敗不留半成品」與驗收 5；既有測試只蓋轉換階段失敗。期望：任何失敗都恢復匯出前狀態。
  - 連帶：docstring「only ever add／失敗 adds nothing」的如實性被上述行為推翻，修正後需同步。
- Reviewer B（standards 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：9 檔雜湊吻合；EdgeConverter 與 server 整合 🟢；自行重做 AST 比對確認 docstring 外零變更。**結論：不通過**，Finding（Owner＝Reviewer B）：
  - **B6-01［重要］pages.py:1835／pdf_export.py:199**：使用者文案不符實際磁碟狀態——重匯出覆寫後頁面仍稱「既有的檔案一個都不會改」；已有兩份 PDF 時轉換失敗，回覆卻稱「沒有留下任何 PDF」（舊檔明明還在）；部分 promotion 成功時標題「這次沒有匯出」與 written 清單矛盾。三個 /tmp 定向重現在案。期望：文案與磁碟狀態一致＋補三類回歸測試（第二次 promotion 失敗、既有 PDF 後失敗、重匯出）。
- **Coordinator 對政策分歧的釐清**：B 誤以為覆寫政策已被 Coordinator 裁定（實為 Developer 判斷點交 A 裁決，A 裁定違反票面範圍 4）。政策以 Owner A 的裁決為準：**目標已存在即拒絕**。此政策同時消解 B6-01 的大部分場景（拒絕後「不改既有檔案」為真；配合 A-F02 的完整回滾，「失敗零殘留」為真）。run artifact 不可變，重匯出產物本就相同，拒絕不損失能力。

### 修正輪（2026-08-10 06:10 +0800，Developer fixed 全部三項、無反駁，待定向複驗）

- **A-F01 fixed（拒絕政策）**：`ALREADY_EXPORTED` 新狀態，目標任一存在即拒絕並指名；端點 200 指引（不用 409 的語意理由入註解）；按鈕停用與端點拒絕經 `existing_targets()` 同源；覆寫測試刪除改釘拒絕。定向重現：v1→v2 第二次拒絕、SHA-256 兩次相同、轉換器 0 呼叫。
- **A-F02 fixed（可回滾）**：`_promote` 失敗走 `_undo()` 移除已上名新檔→目錄復原、written=()；回滾安全性依賴拒絕政策（promotion 前目標必不存在），依賴關係入 docstring；連移除都失敗時指名並算進 written。定向重現：注入 OSError→目錄前後清單相同、.pdf=[]、既有檔雜湊全等。
- **B6-01 fixed**：`EXPORT_NOTICES` 四態文案表（比照 SETTINGS_NOTICES），標題各異、僅真寫入態 role=status；頁尾改拒絕語意；三類回歸測試補齊；`__init__.py` docstring 同步（裁定八授權內）。
- 紅→綠：定向 red 14 項→綠；測試 74→89（刪 1 增 16）；**全套 2488 tests OK (skipped=1)、exit 0**（基準 2399＋89）；牽動 TestCase 另跑 833 OK；真 Edge 政策改動後重測兩種 Data Root 形狀皆過。
- 修正輪 Snapshot：pdf_export=fbcd07f6、server=8aec6634、pages=c643dce6、__init__=2c96a0cd、test_webapp_pdf_export=e425af0a、test_webapp=6666033c（未再動）。

### 定向複驗第一輪（2026-08-10 07:00 +0800，兩位 Owner 皆維持未關閉）

- 三個 Finding 的**原始場景全部確認修好**（序列重匯出拒絕且雜湊不變、單請求回滾復原、文案三態如實、89 tests OK、`__init__` AST 無行為變更）。
- 但兩位 Reviewer 各自以並行探針推翻修正完整性（根因相同）：
  - **A-F01 維持阻擋**：`:159` 存在檢查與 `:276` `os.replace` 非原子；雙同步請求皆回 exported，後者覆寫前者剛建立的 PDF（OVERWRITE_OBSERVED=True）。
  - **A-F02 維持阻擋**：`_undo()`（:318）只按檔名刪、不確認檔案屬本次請求；「成功＋失敗」交錯下失敗方刪掉成功方的 report.pdf、留單邊 debate.pdf、錯誤頁謊稱已復原。
  - **B6-01 維持重要**：同一 TOCTOU（barrier 假轉換器重現：race_states 兩個 exported、一方覆寫另一方）。
- 兩位給的最小關閉條件一致：per-run 排他涵蓋「檢查→轉換→promotion／rollback」全段（或原子 no-clobber 機制）＋並發回歸測試（雙請求僅一個 EXPORTED 另一拒絕、勝者兩份 PDF 不被改寫；成功＋失敗交錯下勝者保留且失敗回應與磁碟一致）；`__init__` docstring 的並行如實性隨修正恢復。

### 第二修正輪與共識（2026-08-10 08:20 +0800）

- Developer 第二輪（全盤接受並行證據）：per-run 非阻塞鎖（解析後 run 目錄為 key、IN_PROGRESS 409、敗方零轉換）＋`os.link` no-clobber 上名（DrvFs／WSL 實測）＋inode 歸屬回滾（foreign 檔不刪不冒算）。紅→綠 6 項（burst 測試重現六請求全 exported）；mutation 矩陣證明三層各自獨立有效；測試 89→98；**全套 2497 tests OK (skipped=1)、exit 0**；真 Edge 重測過。
- Reviewer A 二次複驗：重放雙請求 TOCTOU（winner=exported／loser=in_progress、零呼叫、PDF 位元不變）與成功＋失敗交錯（foreign 保留、無謊稱回復）→ **A-F01、A-F02 皆 closed**；`__init__` 敘述與實際行為一致。「spec 軸最終結論：通過。」
- Reviewer B 二次複驗：重放 barrier 探針（A=exported／B=in_progress、勝方位元、零殘留）＋鎖／no-clobber／inode 三處 file:line 確認 → **B6-01 closed**。「standards 軸最終結論：通過。」
- 共識成立。最終 Snapshot：pdf_export=fafd8b67、server=e3dda505、pages=a2785288、__init__=04be79b6、test_webapp_pdf_export=095a8c08、test_webapp=6666033c（基準樹 `23109528`）。首次完整驗收 2473 OK；兩輪修正後 2488、2497 OK (skipped=1)、exit 0。
- 未解風險（記錄）：同 run 第二請求被拒非排隊（刻意，頁面明示）；lock 表不回收（本機單人可忽略，理由入註解）；跨程序僅靠 no-clobber（現況唯一寫入者；未來出現第二寫入者時行為是誠實拒絕）；重匯出需自行移走舊 PDF（webapp 無刪除權，刻意）。

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

## 阻擋與裁決紀錄
