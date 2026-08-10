# 01 席位方向套組單一權威

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：無

## 目標

讓 `config/agent_roster.json` 成為席位顯示名稱與研究方向的唯一權威：每席具備 `stock`／`crypto`／`open` 三套 `profiles`，載入時 fail-closed；離線報告與 prompt 全部改讀同一個讀取口，依 run 的資產類別自動選套。

## 對應原始需求

- R-005：七席換套：股票／幣圈／開放三套依資產類別自動切換名稱與方向，seat_id 與 output_dir 永不改，含分工細表與提供者對調

- Spec R5 七席換套（含〈七席分工細表（已核准）〉表格全部七列，與 open 套明定段落）。
- Spec R7「標籤必須從既有權威帶出，不得在前端建第二份詞彙表」中的席位名稱部分。
- Spec〈邊界與錯誤〉：「離線報告 renderer（`report_renderer.py`、`report_audit_renderer.py`）與 `run_verifier.py` 的版面與內容不動。**唯一開口**：席位標籤來源須支援換套，台股報告不得印幣圈席名。」
- Spec〈實作決策／資料與所有權〉第 1 條：「`config/agent_roster.json` 是席位資訊的**單一權威**……散落在其他模組的席位標籤寫死表全部刪除。」
- Spec〈實作決策／模組責任與公開介面〉`seats.py`、`report_renderer.py` 兩條。
- Spec〈實作決策／Schema、API contract 與系統互動〉：roster schema 升版、套組選擇規則、顯示規則。
- Spec〈實作決策／相容、遷移與技術限制〉：`seat_id`（含 `counter-evidence`）與 `output_dir` 永不改；roster schema 升版後預檢與測試 fixtures 同步更新。
- Spec A3 第 1、2、3、5 條。

## 使用者價值

台股題不再印出幣圈席名；席位名稱與研究方向只有一份，改一處不會漏改另一處；第七席正式轉為基本面研究員。

## 範圍

### 進入範圍

1. **roster schema 升版**：每席新增 `profiles`，含 `stock`／`crypto`／`open`，每套為 `{display_name, focus}`；`roster_version` 提升。內容以 Spec R5 表格為準：
   - 股票套顯示名稱＝表格第一欄；`focus` 需同時涵蓋該表「台股」與「美股」兩欄的研究方向（台股美股共用一套）。
   - 幣圈套顯示名稱與方向＝表格「幣圈套（名稱／方向）」欄。
   - open 套＝前六席沿用現行 roster 敘述與幣圈套顯示名稱；第七席顯示名稱「基本面研究員」、`focus`「關鍵數據與事實查核」。
2. **第七席轉職**：`seat_id: counter-evidence` 的三套方向改為基本面研究語彙（股票：月營收、財報與財測、估值 vs 同業、產業供需；幣圈：TVL、協議收入、代幣解鎖與供給日曆、開發活動；開放：關鍵數據與事實查核）。`seat_id` 與 `output_dir` 一個字都不改。
3. **`hoya_market_agents/seats.py`**：
   - `load_roster` 讀取並驗證 `profiles`，fail-closed：七席齊、每席三套齊、每套 `display_name` 與 `focus` 皆非空；任一不滿足即拋 `RosterError`，錯誤訊息指出缺哪一席、哪一套、哪個欄位。
   - 新增「資產類別 → 套組」的**單一判定處**與「依資產類別取得席位顯示名稱與研究方向」的**單一讀取口**。
   - 移除 `SEAT_IDENTITIES` 中寫死的 `display_name`（`agent_number`、`avatar`、`provider` 等非方向性欄位如何保留由實作決定，但顯示名稱必須來自 roster）。
4. **離線報告標籤開口**：`report_renderer.py` 的 `SEAT_LABELS` 與 `report_audit_renderer.py` 的席位標籤映射刪除，改走同一讀取口。**只動標籤來源**，版面、章節、欄位順序、輸出字串結構一律不動；`run_verifier.py` 完全不動。
5. **`prompt_builder.py`**：席位 prompt 中的 `focus` 改為依該 run 資產類別選出的套組 `focus`。
6. **`report_fixtures.py`** 與相關測試 fixtures 同步升版。

### 不進入範圍

- 提供者對調（Ticket 02）。
- `webapp/pages.py`、`webapp/live.py` 端的席位標籤改讀（Ticket 03）。
- 離線報告版面任何調整。
- 為開放題重新設計中性研究方向（Spec〈不在範圍內〉）。

## 已確認實作決策

- 套組選擇規則（單一判定處，不得複製字面值）：`tw_stock` 與 `us_stock` → stock；`crypto` → crypto；`open` 或跨類 → open。
- 顯示規則：所有 run（含歷史 run）一律以**現行套組**顯示，不回溯保存當時的套組；舊逐字稿自稱與標籤不一致是已核准代價。
- 品質把關不因第七席轉職而變：第一輪反方挑戰是 `debate_driver` 的全席共用機制，燈號降級規則不變——本票不得碰這兩處。
- `ANTIGRAVITY_SEAT_IDS` 仍綁 `counter-evidence`，本票不改。

## 驗收條件

1. roster 七席各具備三套 `profiles`，`load_roster` 載入成功並回傳核准順序。
2. 移除任一席的任一套組、或把該套的 `display_name` 或 `focus` 清空，`load_roster` 皆拋 `RosterError`，訊息中可讀出席位與套組名稱；不回退成預設值、不靜默降級。
3. 傳入 `tw_stock` 與 `us_stock` 取得股票套；`crypto` 取得幣圈套；`open` 與跨類取得 open 套。
4. 以台股 fixture 產生離線報告，席位標籤全部為股票套名稱，且輸出中不含「鏈上獵人」「反證稽核員」「槓桿雷達」等幣圈或舊名。
5. 以幣圈 fixture 產生離線報告，席位標籤與改版前逐字相同。
6. 離線報告除席位標籤字串外，其餘輸出與改版前逐字相同（以既有 renderer 測試為準）。
7. 全庫不存在第二份席位顯示名稱表：`seats.py` 與 `report_renderer.py` 內原有的寫死映射已移除。
8. 七席的 `seat_id` 與 `output_dir` 值與改版前逐一相同。
9. 同一席位在 `tw_stock` 與 `crypto` 兩種 run 下，`prompt_builder` 產生的 prompt 中 `focus` 內容不同且對應正確套組。
10. 既有測試全綠。

## 測試與證據

- 測試接縫：roster profiles 載入器（可指定設定路徑，缺套／缺席／缺欄位 fail-closed）；資產類別 → 套組選擇規則；席位標籤讀取口。
- 迭代期快速檢查：`python3 -m unittest tests.test_seats tests.test_prompt_builder`（WSL，秒級）。
- Ready for Review 完整驗收：`cd <Code Root> && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`（WSL）。若專案仍沿用 Ticket 13 的 T08 攔截器，依該慣例補上對應環境變數，實際值由 Coordinator 提供，不寫入票面。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：完整驗收結果與退出碼、台股與幣圈兩份 fixture 報告的席位標籤片段、fail-closed 四種缺漏情境的錯誤訊息、`seat_id`／`output_dir` 前後比對、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票不產生任何網頁畫面變更；webapp 端的席位標籤改讀屬 Ticket 03。離線報告的席位標籤變化由 renderer 測試與 fixture 輸出比對覆蓋。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
- 操作結果：不適用
- 操作證據：不適用

## 依賴

- Depends on：無
- Blocks：02、03

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：
  - `config/agent_roster.json`
  - `hoya_market_agents/seats.py`
  - `hoya_market_agents/prompt_builder.py`
  - `hoya_market_agents/report_renderer.py`（僅席位標籤來源）
  - `hoya_market_agents/report_audit_renderer.py`（僅席位標籤來源）
  - `hoya_market_agents/report_fixtures.py`
  - `tests/test_seats.py`、`tests/test_prompt_builder.py`、`tests/test_report_renderer.py`、`tests/test_report_audit_renderer.py`、`tests/test_contract_validator.py`
  - 新增 `tests/test_seat_profiles.py`
- Shared resource locks：`config/agent_roster.json`（Ticket 02 亦寫入，以 Blocked by 串接，不並行）；全專案 unittest 套件。
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`
- model_reasoning_effort：`high`
- 路由理由：涉及 Schema 升版、公開介面（`load_roster` 回傳型別與新讀取口）、跨模組改動（seats／renderer／prompt_builder／fixtures）與相容性底線（`seat_id`／`output_dir` 永不改），不符合 Sonnet 的「局部、低風險、不涉及 Schema 與公開介面」條件。
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

### 開始執行（2026-08-09 13:40 +0800）

- Execution environment：`source: auto_current`（本 Task 無 `settings_update: execution_environment`）。Windows 10 宿主；專案命令一律使用 `wsl.exe -d Ubuntu-24.04 --` 前綴；WSL 專案路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；Windows 路徑 `D:\workstationD\hoya bit\hoya-bit-market-agents-final`。WSL python3 3.12.3（系統 Python，未偵測到 venv／conda 隔離環境——已提醒，不阻擋）。
- 並行批次：batch-1（單票）。排程判定：8 票依賴為嚴格線性鏈 01→02→03→04→05→06→07→08，全部 `Dispatch: serialized`、`Can run with: 無`（`config/agent_roster.json`、webapp `pages.py`／`server.py`、全專案 unittest 為共用熱點）；Ready Queue＝[01]，最大安全並行度＝1。
- 基準版本：branch `main`，HEAD `9b8a4510ec9406f19506e21d50af7918da2385d4`；工作樹含前工作（post-competition-refit T13）已實作未提交成果（90 項變更），以臨時 index 固定為基準樹 `f1ccf61d1abf3626d2e9e33371e1154aaaad2b8f`（未動真 index 與 refs）。判定此為 Spec 補充章節明載的預期基準，非並行工作樹衝突。
- 基準全套驗證（Coordinator 派工前執行）：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests` → `OK (skipped=1)`。T08 攔截器位於 WSL `/tmp/t08-intercept/sitecustomize.py`（存在且有效），全套測試必須帶 `PYTHONPATH=/tmp/t08-intercept`，否則會真呼叫 39 次 codex CLI。
- 開發角色：Developer＝Claude Code 子 Agent（`milktea-build`），model `claude-opus-5`（採票面偏好；透過派工工具 `model: opus` 傳遞）；`model_reasoning_effort`：派工平台（Agent 工具）不提供 effort 欄位，票面偏好 `high` 無法逐欄傳遞，採該後端已驗證之預設推理強度並在此記錄原因。後端偵測：Claude（子 Agent）與 codex CLI 0.146.0 皆可用 → 相容預設：Developer＝Claude、Reviewer A／B＝Codex。
- Exclusive write scope：依票面（roster、seats.py、prompt_builder.py、兩 renderer 之席位標籤來源、report_fixtures.py、指名測試檔、新增 tests/test_seat_profiles.py）。
- Shared resource locks：`config/agent_roster.json`（本票持有；02 靠 Blocked by 串接）；全專案 unittest 套件（本票持有）。
- 必跑指令（Ready for Review 完整驗收）：`cd /mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`（WSL）。

### Developer 首輪回報與 Coordinator 裁定（2026-08-09 14:35 +0800）

Developer（Claude／claude-opus-5）回報：進入範圍 1–6 全部實作完成；roster 1.0.0→2.0.0（profiles 三套，內容逐字取自 Spec R5 表格）；seats.py 單一判定處 `profile_set_for` ＋單一讀取口 `seat_profiles`＋fail-closed `RosterError`（八種缺漏情境訊息可讀）；兩 renderer 寫死表（`SEAT_LABELS`／`REPORT_AGENT_PROFILES`／`SEAT_CHAT_NAMES`）刪除改走讀取口；prompt_builder 依 run 資產類別選 focus；fixtures 升版帶 `asset_class`。台股 fixture 報告零幣圈席名；seat_id／output_dir 七席逐一相同。全套 2200 測試僅 1 紅：`tests/test_renderer.py:190-191` 斷言舊詞彙「現貨技術席」「衍生品席」（票面 write scope 遺漏該檔）。

**基準樹缺陷（Coordinator 認錯並記錄）**：先前記錄的基準樹 `f1ccf61d` 經查證＝`HEAD^{tree}`（快照命令靜默失敗），並未捕捉 T13 的 90 項未提交變更；該快照已不可重建。補救：Review 證據改以工作樹現況＋Developer 變更檔案清單＋`git diff HEAD -- config/agent_roster.json`（該檔 HEAD 版＝動工前狀態，乾淨）為基礎；後續所有快照一律驗證「樹雜湊 ≠ HEAD^{tree} 且含標記檔」才採用。

**裁定一（寫入所有權擴充）**：`tests/test_renderer.py` 納入本票 Exclusive write scope，僅限席位標籤斷言兩行（190-191）改為從 roster 讀取口取名，保留測試原意（標籤為繁體中文）。理由：該檔斷言的舊詞彙在核准 Spec 下已無合法來源，票面測試清單漏列同類 renderer 測試檔屬拆票遺漏；線性鏈下無其他票擁有此檔，所有權分配屬 Coordinator 職權。

**裁定二（驗收 5 解讀，交 Reviewer A 覆核）**：「幣圈 fixture 席位標籤與改版前逐字相同」中的「現行語彙」＝roster／逐字稿既有語彙（圖表偵探、槓桿雷達、鏈上獵人…），非 `report_renderer.SEAT_LABELS` 第二份詞彙表（現貨技術席…——Spec 明令刪除的散落寫死表）。故：前六席逐字稿名稱不變＝驗收 5 成立；第七席改名＝ADR 0006 已核准代價；report.html 功能席標籤由第二份表換成套組名稱＝驗收 6「除席位標籤字串外逐字相同」明示允許。此解讀是唯一同時滿足 Spec R5 表格、A3、驗收 4（槓桿雷達／鏈上獵人屬幣圈套名）與「刪除寫死表」的讀法；Reviewer A 於 spec 軸 Review 時專項覆核。

**裁定三（條件式範圍擴充）**：Developer 發現正式 run 路徑 `debate_driver.assemble_market_report` 未把 `asset_class` 寫入報告契約，正式台股 run 的離線報告仍會落到 open 套。授權本票最小補齊：debate_driver 該處加一行契約欄位（絕不觸碰第一輪反方挑戰與燈號降級兩處）；`contract_validator.py` 僅在需認識新欄位時最小同步；以範圍內測試檔釘住。若改動擴散超出上述範圍，立即停手回報。理由：A3 第 3 條在本票對應清單內，欄位的生產端應與消費端（renderer 讀取口）同票收口，拖到 Ticket 08 必然退票、浪費整輪。

**已記錄之已知風險（不阻擋本票）**：webapp 第七席顯示名將隨權威改變（ADR 0006 預期結果，票面「不產生網頁畫面變更」的判定依據就前六席仍成立，UI 實測由 T03／T08 覆蓋）；`CATEGORY_LABELS` 證據分類仍幣圈語彙（非席名，Spec 只開席位標籤一口，屬範圍外既有現況）；`codex_bridge`／`run_controller`／`build_report` 用通用 focus（範圍外，Spec 未要求 tracer／manifest 換套）；讀取口 mtime 快取 0.66ms/查（reload-aware 為 Spec 明文要求）。

### Ready for Review（2026-08-09 14:55 +0800）

三項裁定全部落地：裁定一改 `tests/test_renderer.py` 席位標籤斷言兩行＋一個 import；裁定二無程式變更；裁定三只動 `debate_driver.py` 四個 hunk（`assemble_market_report` 增 `asset_class` 參數與契約欄位、唯一呼叫端帶 `package.asset_class`、一行 import），兩個禁區（第一輪反方挑戰 77 行、燈號降級 12 行）前後雜湊相同（`429cf54ca9579e19`／`3e3aefb413028fe0`）；`contract_validator.py` 經查為必填欄位檢查、不拒絕未知欄位，無需同步。

- 完整驗收（首次成立）：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests` → **exit 0，Ran 2202 tests，OK (skipped=1)**（skip 為既有條件式，與基準相同）。
- 型別檢查：專案無型別檢查設定；替代驗證 `py_compile` 全過、AST 掃描無未使用 import。
- 正式路徑證據：台股題 package→report `asset_class=tw_stock`→報告席名全股票套、零幣圈舊名；幣題→`crypto`→幣圈套。
- Review snapshot（已驗證：≠ HEAD^{tree}、含 `tests/test_seat_profiles.py`）：HEAD `9b8a451`＋工作樹樹雜湊 `113b577086660335297e66a976e1b177bf5062b1`。
- 變更檔案 13 個：`config/agent_roster.json`、`hoya_market_agents/{seats,prompt_builder,report_renderer,report_audit_renderer,report_fixtures,debate_driver}.py`、`tests/{test_seats,test_prompt_builder,test_report_renderer,test_report_audit_renderer,test_renderer,test_seat_profiles}.py`（最後一個為新增）。
- 新增風險（已裁決範圍外，移交 Ticket 08 或另開票）：`report["asset_class"]` 無值層驗證，非法字串會落到 open 套；修正點在 `report_contract.validate_market_report`，不在本票授權檔案內。

### Review 紀錄（2026-08-09）

- 首次 Reviewer 派工被兩位 Reviewer 正確 fail-closed：Coordinator 的整樹驗證指令（`git diff <tree>`）對未追蹤檔不可見，50 個 T13 新增檔被誤報為刪除。Snapshot 本身有效；驗證方法改為 13 個範圍檔的逐檔 blob 雜湊後重派。此為 Coordinator 契約缺陷，非程式問題。
- Reviewer A（spec 軸、native、codex CLI 0.146.0、gpt-5.6-sol／reasoning high、隔離 session 019fe548-89c3→重派 019fe548-*）：Snapshot 於審查前後雙重驗證吻合。進入範圍 1–6 逐項通過（file:line 證據在案）；驗收 1–10 逐項通過；裁定二判定「成立」（Spec R5 幣圈套明文＝圖表偵探等、驗收 4 認定槓桿雷達／鏈上獵人為幣圈名、驗收 6 允許標籤字串改變、第七席改名為 ADR 0006 明文）；裁定三通過（debate_driver 契約擴充最小、禁區未觸碰、run_verifier 未動）；Ticket 02 邊界未越權（onchain 仍 codex、news 仍 claude）。**Findings：無。結論：通過。**
- Reviewer B（standards 軸、native、codex CLI 0.146.0、gpt-5.6-sol／reasoning high、獨立隔離 session）：Snapshot 前後雙重驗證吻合。單一權威紀律通過、讀取口設計通過、可讀性通過（品味評分 🟡、致命問題無）、衍生風險通過。**結論：不通過**，Findings 如下（Owner＝Reviewer B）：
  - **B-1［重要］`seats.py:274`／`test_seat_profiles.py:176`**：`load_roster` 席位層 fail-closed 不完整——非字串型別的 `focus`／`output_dir`（如 list）、純空白欄位、重複合法 `seat_id` 均被接受；最外層 JSON 為陣列逸出 `AttributeError`、非法 JSON 逸出 `JSONDecodeError`，未轉成可讀 `RosterError`。已以 /tmp 最小 roster 重現。期望：席位層必要欄位非空白字串、拒絕重複 seat_id、結構與 JSON 錯誤統一轉 `RosterError`，補公開接縫測試。
  - **B-2［建議］`tests/test_seats.py:92`**：單一權威防退化掃描只取 open 套名稱，漏掉 stock-only 的「籌碼雷達」「資金流獵人」，掃描範圍限 package `.py`；人工全庫搜尋未發現實際影子表，不阻擋。
  - **B-3［建議］`report_fixtures.py:34`**：`load_fixture()` 無參數時 `asset_class == "crypto"` 的預設未被測試直接釘住。
- Coordinator 依影響範圍指定修正後重驗：B-1 修正收緊了全套件共用的 roster 載入路徑，可能波及任何以簡化 roster 建構的既有測試 → 除 `tests.test_seat_profiles`／`tests.test_seats` 迭代檢查外，**須重跑一次全套**（帶 T08 攔截器）。修正只交回 Reviewer B 定向複驗；Reviewer A 的 spec 軸結論不受此修正範圍失效，不重做。

### 修正輪（2026-08-09 15:40 +0800，Developer fixed，待 Reviewer B 定向複驗）

- **B-1 fixed**：三項回報情境全部重現，另發現兩項同類（席位 entry 非物件逸出 `AttributeError`、`seats` 非陣列誤報成缺七席）。修正：`load_roster` 拆為 `_roster_document`／`_seat`／`_required_text`，結構與 JSON 錯誤統一轉可讀 `RosterError`（含檔案路徑或 `seats[index]` 定位）、席位層欄位強制非空白字串、拒絕重複 `seat_id`、驗證順序改 seat_id→席位層→profiles。紅→綠：`RosterFailsClosedTest` 13 項（Red：failures=12, errors=9 → Green 全過）。
- **B-2 fixed**：掃描名稱集合改走三套 profiles（7→18 名，含籌碼雷達／資金流獵人），範圍擴至 config/*.json（排除 roster 自身），加兩項正向控制；刻意不掃 tests/docs/md 並註明理由。重掃：影子表無。
- **B-3 fixed**：新增 fixture 預設 crypto 釘樁（5 案＋`build_fixture`）；突變證明成立（預設改 open → 僅新測試紅 ×6，其餘全綠），突變已還原。
- 指定重驗：迭代 265 tests OK；**全套 2214 tests OK (skipped=1)、exit 0**（收緊未波及既有測試；事前確認無測試依賴舊錯誤字串）。測試數 2202→2214。
- Coordinator 快照比對：本輪實改 4 檔（seats.py、test_seat_profiles.py、test_seats.py、test_report_renderer.py）；`report_fixtures.py` 雜湊改變經查僅行尾 CRLF→LF 正規化，去行尾後與凍結快照逐位元相同，還原聲明屬實。其餘 8 檔雜湊不變。
- **Coordinator 範圍裁定（output_dir 觀察）**：Developer 發現 `output_dir` 值變更在載入期仍被接受。載入期強制 `output_dir == seat_id` 等於新增產品規則，超出本票與 Spec 核准範圍，**本票不做**；該不變量由既有兩處測試（`FROZEN_SEAT_DIRS`、`test_contract_validator` 的相等斷言）守住。Reviewer B 若認定屬 standards 缺陷可另開 Finding。

### 定向複驗與共識（2026-08-09 16:05 +0800）

- Reviewer B 複驗（resume 原 session 019fe54c-6a9d，Owner 連續性保留）：4 檔新 blob 雜湊逐行吻合。B-1 closed（重跑首輪 8 個 /tmp 情境＋2 個新增邊界全部可讀 `RosterError`；`RosterFailsClosedTest` 13 項全過）；B-2 closed（三套 18 名＋config 掃描＋正向控制；`tests.test_seats` 16 項全過）；B-3 closed（5 案＋`build_fixture` 預設 crypto 直接斷言；突變證據合理）。定向驗證合計 31 tests 通過＋10 個 roster 變異情境通過。
- Reviewer B 對 output_dir 範圍裁定表態：接受，不另開 Finding。
- Reviewer B 明確回報：「standards 軸最終結論：通過。」
- 共識成立：Developer 與兩位 Finding Owner 無未解事項。最終 Snapshot：首輪 13 檔中 9 檔維持 Review snapshot 雜湊，修正輪 4 檔新雜湊（9efee1a5／1928e101／ee86f898／43d9488d），`report_fixtures.py` 僅行尾正規化。首次完整驗收 2202 tests OK；Findings 後指定重驗 2214 tests OK (skipped=1)、exit 0。
- 未解風險（移交，不阻擋本票）：`report["asset_class"]` 值層驗證（建議納入 Ticket 08 或另開票）；`CATEGORY_LABELS` 幣圈語彙屬範圍外既有現況；tracer／manifest 用通用 focus 屬範圍外。

### D-1 退回與裁定十（2026-08-10 12:30 +0800，Ticket 08 端到端驗收發現）

- **D-1［阻擋，發現者＝Ticket 08 Developer］**：`report_workflow.py` 產生的 `validation_failed` 報告骨架缺 `asset_class` 欄位——台股 run 在「Core 報告一次 correction 後仍未過客觀驗證」的退化路徑，離線兩頁落到 open 套印出「槓桿雷達」「鏈上獵人」，webapp（讀 question.json）仍顯示股票套；Spec A3-1／A3-3 在此路徑同時不成立。該 run 以 exit 0／FINALIZED 結束，使用者可達。實測輸出在 Ticket 08 回報與 `acceptance/a1-a5-matrix.md`。根因與本票移交殘留同源：契約未要求該欄位，裁定三只補了 `assemble_market_report` 一個生產端。
- **裁定十（範圍擴充）**：`hoya_market_agents/report_workflow.py` 納入本票，僅限「驗證失敗報告骨架帶上該 run 的 `asset_class`」最小修正（來源＝該 run 的 question package，比照裁定三語意）；相應測試釘樁（落點由 Developer 依既有測試檔歸屬判斷，`tests/test_report_workflow.py` 若存在則授權）。契約端值層驗證維持移交殘留不在本輪做。修正擴散超出上述即停手回報。
- 修正由原 Ticket 01 Developer 執行；修正後交原 Reviewer A／B 定向 delta review；Ticket 08 隨後把備好的回歸測試加入其驗收檔並重跑矩陣。

### D-1 修正與重新共識（2026-08-10 14:00 +0800）

- Developer（原 T01 Agent）修正：`report_workflow.py` 純追加（`run_report_workflow`／`build_red_audit_report`／`_red_outcome` 增 `asset_class` 接縫、10 個失敗分支呼叫點一致補欄位）；`debate_driver.py` 三 hunk 接上 `package.asset_class`（兩禁區未進 diff）；`tests/test_report_renderer.py` 新增 2 測試（紅→綠：退化路徑台股全股票套零幣圈舊名、未指名市場→open）。第三生產點查證：report_workflow 骨架生產點僅 1；範圍外 competition_drill（不可達）與 cli render-fixture（潛伏無症狀）如實記錄未越權修。**全套 2548 tests OK (skipped=1)、exit 0**。
- Reviewer A delta review（resume 原 session）：**通過**——A3-1/A3-3 退化路徑成立（完整鏈驗證）、裁定十最小性成立（AST 驗 10 呼叫點、debate_driver 僅三 hunk）、open 預設符合裁定三、範圍外處置可接受；「spec 軸結論在含 D-1 修正的範圍上維持通過」。
- Reviewer B delta review（resume 原 session）：**通過**——「純追加無邏輯改動」經修正前 blob 比對證實、測試落點合理、紅→綠可信；`_red_outcome` 11 參數與契約必填 root 防線記為後續事項不阻擋；「standards 軸結論維持通過」。
- D-1 關閉。最終 Snapshot 增補：report_workflow=bd93f38b、debate_driver=82fe0de5、test_report_renderer=8e715041。
- 移交殘留（結案報告列出）：report_contract 的 `asset_class` 必填／值層驗證（root 防線，兩位 Reviewer 均建議優先排入後續）；cli.py render-fixture 潛伏點（一行修法已記錄）；`_red_outcome` 參數數可讀性負債。

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

## 阻擋與裁決紀錄
