# 02 提供者對調（資金流獵人→claude、新聞探員→codex）

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：01

## 目標

把相似資訊面拆到不同模型家族：`onchain` 席（資金流獵人／鏈上獵人）由 codex 改為 claude，`news` 席（新聞探員）由 claude 改為 codex，維持 codex 3／claude 3／gemini 1，且所有提供者路由來源與 roster 一致。

## 對應原始需求

- R-005：七席換套：股票／幣圈／開放三套依資產類別自動切換名稱與方向，seat_id 與 output_dir 永不改，含分工細表與提供者對調

- Spec R5〈七席分工細表（已核准）〉提供者欄的兩個「（對調）」標記，與其後說明：「提供者對調原則：相似資訊面跨模型家族（籌碼↔資金流、官方↔新聞），維持 codex 3／claude 3／gemini 1。」
- Spec〈實作決策／Schema、API contract 與系統互動〉：「提供者對調（onchain 席→claude、news 席→codex）在同一批完成，維持 3／3／1。」
- Spec〈實作決策／相容、遷移與技術限制〉：「`seat_id`（含歷史名 `counter-evidence`）與 `output_dir` **永不改**：歷史 run 目錄、提供者對應（`ANTIGRAVITY_SEAT_IDS`）、賽前預檢全部綁著它。」「roster schema 升版後，預檢與測試 fixtures 同步更新。」

## 使用者價值

相似資訊面不再由同一模型家族研究，降低七席同時犯同一種錯的機率。

## 範圍

### 進入範圍

1. `config/agent_roster.json`：`onchain` 席的 `provider`／`target_model`／`allowed_tools`／`required_skills` 改為 claude 家族設定；`news` 席改為 codex 家族設定。兩席的 `seat_id`、`output_dir`、`profiles` 不動。
2. `hoya_market_agents/real_provider.py`：`CODEX_SEAT_IDS`、`CLAUDE_SEAT_IDS` 兩個寫死分組同步更新；`ANTIGRAVITY_SEAT_IDS` 維持 `("counter-evidence",)` 不動。
3. `hoya_market_agents/codex_bridge.py`：`CODEX_SEAT_IDS` 同步更新。
4. `hoya_market_agents/system_preflight.py`：賽前預檢的提供者對應與計數同步更新。
5. 相關測試與 fixtures 同步（提供者分組、預檢、供應商路由）。
6. 若 `seats.SEAT_IDENTITIES` 仍保留 `provider` 欄位作為 CSS class 來源，該欄位一併同步（顯示名稱已於 Ticket 01 移入 roster）。

### 不進入範圍

- roster `profiles` 內容與席位顯示名稱（Ticket 01）。
- 任何 webapp 畫面改動。
- 席位職能、投票、辯論與燈號規則。

## 已確認實作決策

- 家族配額維持 codex 3／claude 3／gemini 1，不得因對調變成 2／4／1。
- `seat_id` 與 `output_dir` 不改：歷史 run 目錄與 `ANTIGRAVITY_SEAT_IDS` 綁著它。
- 提供者資訊的權威是 `config/agent_roster.json`；程式內的分組常數必須與它一致，不得成為第二份事實。

## 驗收條件

1. roster 中 `onchain` 席的 `provider` 為 claude 家族、`news` 席為 codex 家族。
2. 依 roster 統計提供者家族數量為 codex 3、claude 3、gemini（antigravity）1。
3. `real_provider` 與 `codex_bridge` 的席位分組與 roster 的 `provider` 欄位完全一致；存在測試直接比對兩者，roster 改了而常數沒改會紅。
4. `ANTIGRAVITY_SEAT_IDS` 仍為 `("counter-evidence",)`。
5. 七席 `seat_id` 與 `output_dir` 與改版前逐一相同。
6. 賽前預檢在對調後通過，且預檢報告顯示的提供者對應與 roster 一致。
7. 既有測試全綠。

## 測試與證據

- 測試接縫：roster 載入結果 → 提供者分組常數的一致性斷言；預檢的提供者對應輸出。
- 迭代期快速檢查：`python3 -m unittest tests.test_real_provider tests.test_codex_bridge tests.test_system_preflight`（WSL，秒級）。
- Ready for Review 完整驗收：`cd <Code Root> && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`（WSL）。若專案仍沿用 Ticket 13 的 T08 攔截器，依該慣例補上對應環境變數，實際值由 Coordinator 提供，不寫入票面。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：完整驗收結果與退出碼、對調前後的家族計數輸出、roster 與分組常數一致性測試的執行結果、預檢輸出片段、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票只改提供者路由與預檢，不涉及任何使用者介面需求。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
- 操作結果：不適用
- 操作證據：不適用

## 依賴

- Depends on：01
- Blocks：08

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：
  - `config/agent_roster.json`（僅 `provider`／`target_model`／`allowed_tools`／`required_skills` 欄位）
  - `hoya_market_agents/real_provider.py`
  - `hoya_market_agents/codex_bridge.py`
  - `hoya_market_agents/system_preflight.py`
  - `tests/test_real_provider.py`、`tests/test_codex_bridge.py`、`tests/test_system_preflight.py`、`tests/test_antigravity_adapter.py`
- Shared resource locks：`config/agent_roster.json`（與 Ticket 01 共用，靠 Blocked by 串接避免同時寫入）；`hoya_market_agents/seats.py` 的 `provider` 欄位（Ticket 01 先定形，本票僅同步值）；全專案 unittest 套件。
- Can run with：無。**理由**：本票的檔案範圍雖與 Ticket 03 不重疊，但兩者共用同一份 roster 讀取路徑與全專案測試套件——本票改動 roster 期間，03 的全套驗收會因不相干原因變紅，過去本專案已因「同一資源同時進入兩張票的範圍」出過事，故不並行。

## 初始執行配置

- Developer model：`claude-opus-5`
- model_reasoning_effort：`high`
- 路由理由：涉及供應商路由（權限與外部 CLI 對應）、賽前預檢與跨模組寫死常數的一致性，屬跨模組設計與風險面，不符合 Sonnet 的低風險條件。
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

### 開始執行（2026-08-09 16:10 +0800）

- Execution environment：沿用 Task 設定（`source: auto_current`）：Windows 宿主＋`wsl.exe -d Ubuntu-24.04 --` 前綴；WSL 路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；python3 3.12.3。
- 並行批次：batch-2（單票；線性鏈，上游 Ticket 01 已完成並釋放 `config/agent_roster.json` 與全專案測試鎖）。
- 基準版本：main @ `9b8a451`＋工作樹（含 T01 完成成果），基準樹 `5c74fd8b607584a73afcd4d895eeeb8f92104b79`（已驗證 ≠ HEAD^{tree}、含標記檔）。基準全套：2214 tests OK (skipped=1)（Ticket 01 結案驗收）。
- 開發角色：Developer＝Claude Code 子 Agent（`milktea-build`），model `claude-opus-5`（票面偏好）；`model_reasoning_effort`：派工工具無 effort 欄位，採後端預設並記錄原因（同 Ticket 01）。Reviewer A／B＝Codex（gpt-5.6-sol／high，同 Ticket 01 配置）。
- Exclusive write scope：依票面（roster 四欄位、real_provider.py、codex_bridge.py、system_preflight.py、四個指名測試檔）；另依票面 Shared resource locks 註記，`seats.py` 的 provider 值僅同步（T01 後該值位於 `_SEAT_BADGES`）。
- Shared resource locks：`config/agent_roster.json`（本票持有）、`seats.py` provider 欄位（僅同步值）、全專案 unittest 套件（本票持有）。
- 必跑指令（Ready for Review 完整驗收）：`cd /mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`（WSL）。

### Developer 首輪回報與 Coordinator 裁定（2026-08-09 16:55 +0800）

Developer 回報：票面範圍 1–6 全部完成（roster 兩席對調且其餘五席逐欄未動、3/3/1 維持、四份常數同步、+9 一致性測試），但全套 2223 tests 出現 14 個失敗，全部是對調的直接後果、全部在票面白名單之外。七席 seat_id／output_dir 前後完全相同；`ANTIGRAVITY_SEAT_IDS` 不動。

**裁定四（寫入所有權擴充，A＋B＋C 三組）**：
- **A（功能缺陷）**：`hoya_market_agents/claude_adapter.py` 的 `CLAUDE_SEAT_SESSIONS` 納入本票，僅限該表同步：移除 `news`、新增 `onchain`（全新 UUID，不得沿用 news 的 session 歷史；`official-events`／`social-macro` 的既有 UUID 一字不改）。證據：對調後 onchain 走 claude 通道被 `_session_id` 拒絕（`provider_error: seat_id 不是三個固定 Claude 席`），正式 run 將丟失一席、`run_claude_preflight` 冒煙不到 onchain。既有兩條一致性斷言正確抓到漂移，修表即綠、不改測試。
- **B（fixture 同步，票面範圍 5 已涵蓋、白名單漏列）**：`tests/test_codex_inbox.py`、`tests/test_reviewer_complete_attack.py`、`tests/test_seat_profiles.py`（368–369 徽章行）、`tests/test_report_renderer.py`（406 徽章行）、`tests/test_competition_drill.py`（`actual_models` 家族值——現雖綠但通過原因已偏移，須恢復測試原意）。同步不得弱化任何斷言語意。
- **C（比賽操作文件）**：`.agents/skills/hoya-market-research/SKILL.md`、`references/preflight-checklist.md`、`references/codex-bridge-contract.md` 的提供者對應行同步。理由：這些是 Core Agent 比賽時實際照著開執行緒的文件，不同步等於運行時仍按舊對應派工；屬核准對調的必然後果，非範圍擴張。
- 全部修正交原 Developer 繼續（同一 Ticket、同一批次）；Reviewer 派發延後到全套轉綠。

**環境教訓（記錄）**：`wsl.exe -- bash -lc '…; echo EXIT=$?'` 取得的退出碼不可信（sanity 測試 sys.exit(3) 也印 0）；一律以腳本檔內部取 `$?`。Developer 對 `test_codex_bridge.py` 的行尾誤轉已還原並 byte 級比對確認八檔與基準一致。

### Ready for Review（2026-08-09 17:20 +0800）

裁定四 A／B／C 全部落地：A）`CLAUDE_SEAT_SESSIONS` 移除 news、新增 onchain（全新 UUID `a8577ee5-…8299`，全 repo 碰撞檢查 0 命中，既有兩席 UUID 一字未改），兩條既有斷言不改測試即轉綠；B）五個測試檔 fixture 逐行同步且斷言語意未弱化，`test_competition_drill` 通過理由已復原為 provider receipt lineage 防線（spy 攔訊息證實）；C）三份 `.agents` 文件提供者對應行最小同步。過程中 Developer 自行撞出並修正一個回歸（A 的註解拼出席位顯示名，被 Ticket 01 的單一權威掃描測試抓到——該防退化測試證明有效）。

- 完整驗收：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests` → **exit 0（腳本檔取碼，sanity 3/3 通過），Ran 2223 tests，OK (skipped=1)**（基準 2214＋9 一致性測試）。
- 行尾紀律：17 檔逐檔 byte 級比對基準樹，行尾不一致 0 檔。範圍外抽樣 9 檔（含 debate_driver、run_verifier、webapp/pages）與基準逐 byte IDENTICAL。
- 修改檔案 17 個（原 8＋裁定四 9）；家族計數前後皆 3/3/1；七席 seat_id／output_dir 前後完全相同；`ANTIGRAVITY_SEAT_IDS` 不動。
- Developer 提請 Review 特別檢視的三個判斷：`REPLACEMENT_MODELS` 兩席換家族、預檢模組新增「徽章跟隨 roster」斷言的擺放位置、A 的新 UUID 產生方式。
- Review snapshot：基準樹 `5c74fd8b`＋17 檔 blob 雜湊（7f535958、ed84fd31、00a38795、5f2774c9、40d2e38b、5aca5ec6、e381e5a7、81098d3f、114d1d0f、84213f40、f8987946、5da721a7、fb8f81df、fba01b58、60779acd、2b3c75c7、12651f6a）。

### Review 紀錄（2026-08-09）

- Reviewer B（standards 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：17 檔 blob 雜湊逐行吻合。六項全過、品味評分全 🟢：一致性測試確認為 roster 投影比對（非寫死值互比）；+9 測試各有明確回歸接縫；新 UUID 為 RFC 4122 v4、全 repo 唯一、既有兩席未動、表序＝CLAUDE_SEAT_IDS；fixture 語意保真（competition_drill 失敗理由確認落在 receipt lineage）；三份文件僅 2/2、2/2、1/1 行替換；REPLACEMENT_MODELS 與同家族席位慣例一致。**Findings：零。結論：通過。**
- Reviewer A（spec 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：17/17 blob 逐行吻合。範圍 1–6 與驗收 1–7 逐條通過（roster 兩席對調、其餘五席逐欄相同、3/3/1 實測、一致性測試會在 roster 與常數分岔時失敗、預檢 manifest 直接納入已驗證 roster）；裁定四 A／B／C 判定為核准對調的必然後果、未越出最小範圍；Ticket 01 範圍與 webapp／debate_driver／renderer／run_verifier 逐 blob 與基準相同；REPLACEMENT_MODELS 判斷通過（替補不可派發仍由 `_require_primary_model` fail-closed 拒絕）。**Findings：零。結論：通過。**

### 共識（2026-08-09 18:05 +0800）

雙 Reviewer 首輪零 Finding，無修正輪。共識成立。最終 Snapshot＝Review snapshot（基準樹 `5c74fd8b`＋17 檔 blob 雜湊，見 Ready for Review 紀錄）。首次完整驗收 2223 tests OK (skipped=1)、exit 0。

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

## 阻擋與裁決紀錄
