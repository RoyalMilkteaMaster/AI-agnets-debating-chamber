# 02 Phase 1：debate_rules.json 設定檔化（行為零變化）

- 狀態：完成（兩方複核，非三方共識——見執行紀錄 §5）
- Spec：`../spec.md`（Phase 1）
- Blocked by：01

## 目標

把辯論時間門檻、票數階梯、燈號映射與降級規則從硬編常數搬進 `config/debate_rules.json`，成為全系統唯一規則來源。

## 使用者價值

「我想要有一個地方可以簡單的修改這個東西」——之後改門檻改時間只動設定檔，不碰程式。

## 範圍

1. 新增 `config/debate_rules.json`：內容＝現行常數等價搬移（第一輪相對窗 180s、6→5 切換 T+8:00、最終輪 T+8:45／9:45、強停 T+10:00、門檻階梯 6/5/4、現行燈號映射與降級規則佔位）。
2. 新增規則載入器模組（讀檔→驗證→凍結物件），比照 `research_deadlines` 唯一權威模式。
3. `debate_state_machine`、driver、`run_verifier` 與相關測試改讀載入器；刪除被取代的模組常數，不得殘留第二來源。
4. 載入 fail-closed：欄位缺漏、時間非遞增、票數非法→拒絕啟動並回報具體欄位。

## 已確認實作決策

- 本票行為零變化是硬驗收：預設設定下所有既有測試不改斷言值即全綠（測試改的只是取值來源）。
- 燈號新制內容不在本票（03/04 才改行為），但 schema 欄位先預留。
- 設定檔進 git（屬可提交設定）。

## 驗收條件

> **2026-08-05 使用者裁定修訂**（見「執行與 Review 紀錄 §3」）：本票範圍**只涵蓋辯論時間軸與票數階梯**。燈號映射與降級規則**不在本票搬移**，只在 `config/debate_rules.json` 內預留「有明確鍵名與型別校驗的欄位結構」，由 Ticket 04 填入新制值並遷移消費端。Spec 第 48 行「燈號映射與降級規則」的搬移責任據此順延至 Ticket 04。

- `config/debate_rules.json` 存在且含全部現行的**時間軸與票數階梯**值；燈號區塊具備穩定鍵名與型別校驗的結構（值可為空，由 Ticket 04 填入）。
- grep 全 repo 無殘留被取代的硬編**時間／門檻**常數（測試中鎖定行為的斷言值除外）。燈號硬編（`report_contract.py`）**不在本票驗收範圍**，順延至 Ticket 04。
- 對設定檔填入時間倒序／票數 0 等非法值，啟動被拒且錯誤訊息指名欄位。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數 ≥681＋載入器新增案例。

## 測試與證據

- 測試接縫：暫存目錄的 `debate_rules.json` 變體注入載入器。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：測試結果、非法設定拒啟動的實際輸出、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：01
- Blocks：03

## Agent 分工

- Developer：負責實作、驗證、修正或以證據反駁 Findings
- Reviewer A：使用隔離上下文執行 Review
- Reviewer B：使用另一個隔離上下文執行 Review
- Reviewer 標準：兩者都載入 `$milktea-skills-code-review`，並同時執行 Standards 與 Spec Review
- CLI 與模型：由執行 Task 的 Coordinator 依目前 Task 分工與實際可用能力決定

## 完成規則

- 三個角色已處理所有可重現且有證據的問題。
- 沒有未解決的正確性、可執行性、可讀性、架構或衍生風險。
- 三個角色對完成狀態達成共識。

## 執行與 Review 紀錄

### 1. 開始執行（Coordinator，2026-08-05）

- **Execution environment**：Windows 10 host ＋ WSL `Ubuntu-24.04`（Python 3.12.3）；command prefix `MSYS_NO_PATHCONV=1 wsl.exe -e bash -lc '...'`；專案路徑（WSL）`/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；來源 `source: auto_current`。
- **基準版本**：`main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`。工作樹既有 8 項＝7 項 Planner 產出 ＋ Ticket 01 的 `README.md`（`docs/planning/architecture.md` 內含 Ticket 01 追加的 2 行）。
- **測試基準**：681 全綠（Ticket 01 由 Developer、Reviewer A、Reviewer B、Coordinator 四個獨立角色各驗一次）。
- **開發角色**：Developer＝Claude 一般臨時 Agent（`claude-opus-5`，模型預設 reasoning effort）。
- **必跑指令**：`python3 -m unittest discover -s tests`（WSL）。

#### 併行執行安排（Coordinator 決策，經使用者授權）

使用者指示「可以併行的都併行，有順序的等順序；不限 developer 數量，只要不產生檔案衝突」。Ticket 宣告的依賴是一條鏈（01→02→…→09→{10,11}，10→12），唯一明文允許的併行是 10 ∥ 11。Coordinator 依檔案歸屬分析追加一組併行：

```
01 → [ 鏈 X：02→03→04  ∥  鏈 Y：05→06 ] → 鏈 Z：07→08 → 09 → [10 ∥ 11] → 12
```

判定依據（實際 grep 與 import 圖）：`asset_slug` 屬 `question.py:87`／`question_package.py:70`（鏈 Y），run 目錄命名屬 `run_store.py`（鏈 Z），兩者不同檔；鏈 Z 因與鏈 X 共用 `run_verifier.py`、且 Ticket 08 的索引欄位語意上依賴 04 的燈號與 05 的資產類別，故不併入本波。

**本票（鏈 X）的檔案獨佔範圍**：新增 `hoya_market_agents/debate_rules.py`、`config/debate_rules.json`、`tests/test_debate_rules.py`；修改 `debate_state_machine.py`、`debate_driver.py`、`report_contract.py`、`report_renderer.py`、`report_audit_renderer.py`、`report_workflow.py`、`report_fixtures.py`、`run_verifier.py`、`live_dashboard.py` 與對應測試檔。
**禁止碰鏈 Y 的**：`question.py`、`question_package.py`、`contract_validator.py`、`real_provider.py`、`prompt_builder.py`、`competition_drill.py`、`launcher.py` 與其測試。
**兩鏈皆禁**：`cli.py`、`run_store.py`、`codex_bridge.py`、`codex_inbox.py`、`codex_exec_adapter.py`、`research_scheduler.py`、`seats.py`、`clock.py`、`system_preflight.py`、`recovery_state_machine.py`、`run_controller.py`、`fake_provider.py`、`provider_gateway.py`、`claude_adapter.py`、`antigravity_adapter.py`、`config/agent_roster.json`（唯讀）、`tests/test_reviewer_complete_attack.py`（跨鏈共管）。越界必須停止並回報 Coordinator。

**測試協定**：開發期只跑自己獨佔的測試模組（避免讀到另一鏈的中間狀態造成假紅）；全套只在回報 `Ready for Review` 前跑一次；別鏈模組的失敗只回報、不修。

#### Coordinator 事實澄清：`config/schemas/` 不存在

`docs/planning/architecture.md` §2.1 列出 `config/schemas/{evidence-card,agent-position,report}.schema.json`，實測 `find config -type f` **只有 `config/agent_roster.json`**；實際 schema 內嵌於 `report_contract.py`、`contract_validator.py`、`real_provider.py:203`。因此本票「schema 欄位先預留」解讀為「在 `config/debate_rules.json` 結構中預留燈號欄位位置」，**不建立 `config/schemas/` 目錄或無消費端的 JSON schema 檔**。此為澄清既有事實，未改變 Ticket 範圍。

### 2. Ready for Review（Developer，Snapshot 凍結）

**檔案歸屬（Coordinator 以 `git status` 獨立核對，零越界）**

新增 3：`hoya_market_agents/debate_rules.py`、`config/debate_rules.json`、`tests/test_debate_rules.py`（31 案例）
修改 8：`debate_state_machine.py`、`debate_driver.py`、`live_dashboard.py`、`run_verifier.py`、`tests/test_debate_state_machine.py`、`test_vote_thresholds.py`、`test_debate_driver.py`、`test_live_dashboard.py`

numstat（`--ignore-cr-at-eol`）：`debate_state_machine 59/65`、`debate_driver 22/19`、`live_dashboard 44/20`、`run_verifier 38/20`、`test_debate_driver 12/10`、`test_debate_state_machine 7/3`、`test_live_dashboard 6/5`、`test_vote_thresholds 12/7`。基準既有 8 項與鏈 Y 檔案均未被本票碰觸。

**變更摘要**

`DebateRules` frozen dataclass ＋ `debate_rules()`（快取單例）＋ `load_debate_rules(path)`（測試接縫），比照 `research_scheduler.research_deadlines` 的唯一權威模式。時間軸與票數階梯語意（`required_votes_at`、`phase_at`、`challenge_deadline_ms`）搬到規則物件上。刪除 `debate_state_machine` 的 7 個常數（`DEBATE_START_MS`、`ROUND_ONE_WINDOW_MS`、`CHALLENGE_DEADLINE_MS`、`THRESHOLD_FIVE_FROM_MS`、`FINAL_ROUND_START_MS`、`FINAL_ROUND_END_MS`、`FORCE_STOP_MS`）；`required_votes_at` 與 `phase_at` 保留原公開簽名，改為委派載入器並新增可選 `rules=` 注入。消費端全部改讀載入器：driver（`build_turns`、`DeadlineAlignedClock`、`_settle`、r1 prompt）、`live_dashboard`（`rules_for`、`_phase`、`_seal_milestone_ms`）、`run_verifier`（停止原因集合與 `_verify_stop_semantics` 的 6/5/4 改為衍生）。

Developer 另主動清除三處衍生的第二來源（自陳屬判斷範圍內，要求 Reviewer 特別檢視）：①`_force_stop` 的 `"forced_stop_4_votes"` 改為 `"forced_stop_{}_votes".format(rules.forced_stop_votes)`（預設輸出字串完全相同）；②`live_dashboard` 的「共識門檻降為五票／以四票結算」文案改為設定驅動；③4 條 `DebateLifecycleError` 訊息中寫死的「封存＋2:00」「T+8:45」改為帶入實際毫秒值——其中「封存＋2:00」**原本就是錯的**（窗為 180s＝3:00，架構健檢報告第 98 行已列此為缺陷），無測試斷言這些字串。run `20260802T055930Z` 的實測教訓註解原文保留，隨常數搬到 `debate_rules.py`。

**驗收條件證據**

1. **`config/debate_rules.json` 含全部現行值** ✅ — 240000／180000／480000／525000／585000／600000 ＋ 門檻階梯 6/5/4，等價搬移。燈號欄位依 Coordinator 指示只預留位置（`light_scale: null`、`downgrades: null`）。
2. **grep 無殘留硬編常數** ✅ — 退出碼 0，唯一命中為基準既有的歷史文件 `docs/architecture-reviews/2026-08-05-hoya-bit-refactor.html:98`（非程式碼）。生產碼剩兩筆同值字面量，經確認**均非辯論規則**：`research_scheduler.py:27 SEAL_MS = 240_000`（研究封存，由 `research_deadlines` 所有）、`run_verifier.py:286 report_ms - stop_ms > 180_000`（報告 3 分鐘預算）。
3. **非法設定 fail-closed 並指名欄位** ✅ — 實際輸出逐字：

```
[REFUSED] 欄位缺漏     -> 辯論規則設定檔缺少必要欄位：timeline_ms.force_stop
[REFUSED] 時間倒序     -> timeline_ms.final_round_end（500000）必須大於 timeline_ms.final_round_start（525000）；時間軸必須嚴格遞增。
[REFUSED] 時間倒序     -> timeline_ms.reduced_threshold_from（100000）必須大於 timeline_ms.debate_start（240000）；時間軸必須嚴格遞增。
[REFUSED] 第一輪窗越界 -> timeline_ms.debate_start ＋ timeline_ms.round_one_window（540000）必須小於 timeline_ms.reduced_threshold_from（480000）；否則第一輪牆之後的視窗是空的。
[REFUSED] 票數 0       -> vote_thresholds.forced_stop 必須是 1 到 7 之間的整數票數，收到 0。
[REFUSED] 票數超過席位 -> vote_thresholds.initial 必須是 1 到 7 之間的整數票數，收到 8。
[REFUSED] 階梯不遞減   -> vote_thresholds.reduced（7）必須小於 vote_thresholds.initial（6）；票數階梯必須嚴格遞減。
[REFUSED] 鍵名打錯     -> timeline_ms 含未知欄位：forceStop
```

4. **全套測試** ⚠️ **暫時無法取得** — 兩份證據：
   - 在 Developer 全部程式改動完成、僅差最後 4 條錯誤訊息字串編輯之前跑過一次：**`Ran 712 tests ... OK`**（681 基準 ＋ 31 新增），符合「≥681 ＋ 載入器新增案例」。
   - 之後鏈 Y 開始移除 `question.SUPPORTED_ASSETS`，全套自此無法載入。Developer 以 scratchpad 腳本在**記憶體**注入 HEAD 原值（不寫任何檔案）跑自己全部獨佔模組 → **`Ran 297 tests ... OK`, EXIT=0**。

**鏈 Y 進行中造成的全套阻斷（Developer 只回報、未修，符合測試協定）**

Coordinator 於同一時點獨立確認：`Ran 571 tests / FAILED (failures=17, errors=15)`，根因單一——`question.py` 已移除 `SUPPORTED_ASSETS`，而 `launcher.py`、`competition_drill.py`、`real_provider.py`、`question_package.py` 仍 import 它：

```
ImportError: cannot import name 'SUPPORTED_ASSETS' from 'hoya_market_agents.question'
```

本票的 `tests/test_debate_driver.py`、`test_live_dashboard.py`、`test_renderer.py` 三個模組亦因 transitively import `launcher.py`（本票禁改清單）而在 loader 層出錯，與本票改動無關。

**712 全綠的最終確認留待鏈 Y 收尾後由 Coordinator 重跑，並交由 Reviewer 獨立重現。**

**TDD 證據（含 Developer 主動揭露的不合格片段）**

- Slice 1 Red：先寫 `ShippedRulesTest` → `ModuleNotFoundError: No module named 'hoya_market_agents.debate_rules'`；Green：建立 config ＋ loader → 3 passed。
- **Developer 誠實揭露**：Slice 1 的 Green 一次寫完整個驗證器（over-implementation），導致隨後 23 個 fail-closed 測試（`MissingFieldTest`／`TimelineOrderTest`／`VoteLadderTest`）寫完即 26 passed，**沒有真實 Red**。這一段不算合格的 red-green。
- Slice 3 Red（真實）：`TypeError: required_votes_at() got an unexpected keyword argument 'rules'`、`AssertionError: True is not false : DEBATE_START_MS 應該只存在於 config/debate_rules.json`；Green：刪常數＋消費端改讀載入器 → 31 passed。
- Refactor：`_phase` 的 `rules` 參數更名 `milestones`（模組內原有「里程碑時間線」與「規則物件」兩種 rules，消除一詞兩義）。

**Developer 回報的已知風險與待裁決事項**

| # | 項目 | Developer 說明 |
|---|---|---|
| X1 | 未跑 fixture launch 煙霧測試 | 只有單元測試；Ticket 未要求，且鏈 Y 進行中會污染結果 |
| X2 | `config/debate_rules.json` 的 `_about` 註解鍵 | 讓底線開頭鍵＝註解，其餘未知鍵一律拒絕。屬措辭／結構判斷，若 Reviewer 認為 JSON 不該有偽註解，改動很小 |
| X3 | **燈號欄位留 `null` 而非填入現行映射** | 依 Coordinator 指示預留位置讓 Ticket 04 填值；Developer 主張若複製現行燈號級聯進 JSON 而無消費端，本身就是本票要消滅的第二來源。**惟 Spec 第 48 行字面要求「燈號映射與降級規則」一併等價搬移，需裁決** |
| X4 | 自行新增校驗 `debate_start + round_one_window < reduced_threshold_from` | Ticket 只寫「時間非遞增」；但沒有這條，`phase_at` 的 `first_round_closed` 永不可達。若判定為擴大需求可移除 |
| X5 | 票數上限綁 `len(SEAT_IDS)`＝7 | `debate_rules` 因此唯讀 import `seats`（未修改該檔） |
| X6 | 票數階梯要求嚴格遞減 | 因 `consensus_<n>_votes` 這個 stop_reason 在階梯相等時有兩種讀法，`run_verifier` 無從分辨。預設 6>5>4 不受影響 |

#### Coordinator 對 X3 的初步裁定（仍交由 Reviewer 覆核）

Ticket 02 範圍第 1 條原文為「…門檻階梯 6/5/4、**現行燈號映射與降級規則佔位**」，且「已確認實作決策」明寫「燈號新制內容不在本票（03/04 才改行為），但 schema 欄位先預留」。同時範圍第 3 條列舉的改讀載入器對象為 `debate_state_machine`、driver、`run_verifier`——**不含 `report_contract`**，即燈號規則的消費端不在本票範圍。

據此，Coordinator 認為 Developer 的處理符合 Ticket；Spec 第 48 行的「燈號映射與降級規則」應與 Ticket 的「佔位」合併理解。**惟預留欄位若僅為裸 `null`，Ticket 04 將需自行發明鍵名結構**，此點請 Reviewer 就 Spec 一致性與 Ticket 04 的銜接成本給出判斷。

### 3. X3 燈號欄位爭議：Reviewer 推翻 Coordinator 裁定，升級使用者裁決（2026-08-05）

Reviewer B 不接受 Coordinator 於 §2 的初步裁定，判為 **[阻擋]**。其論證：Spec 第 48 行與 architecture §11.2 的位階高於 Ticket 的「佔位」用詞（Spec 開頭自陳「本 Spec 是本次整改的唯一規格來源；與其他文件衝突時以本 Spec 與 architecture.md §11 為準」），且驗收條件 1 原文寫的是「含**全部現行值**」，現行燈號映射亦屬現行值。裸 `null` 使驗收條件 1、2 皆不成立，且 `report_contract.py:15-21, 113-169` 仍是燈號的第二來源。

Reviewer 提出的兩個解法分別為「完整搬移並遷移消費端」與「先正式修訂核准 Spec」。**兩者皆超出 Coordinator 與 Developer 的權限**（前者擴大已核准 Ticket 範圍，後者變更已核准 Spec），故 Coordinator 未護短原裁定，直接升級使用者。

**使用者裁定（2026-08-05）：維持 `null`（不搬移），修訂 Ticket 驗收條件。**

據此執行的文件變更：

| 文件 | 變更 |
|---|---|
| 本 Ticket「驗收條件」 | 加註本票範圍只涵蓋時間軸與票數階梯；條件 1 改為「含全部現行的時間軸與票數階梯值；燈號區塊具備穩定鍵名與型別校驗的結構」；條件 2 明列燈號硬編不在本票驗收範圍 |
| `../spec.md` Phase 1 | 於「燈號映射與降級規則」項下加註 2026-08-05 裁定：搬移順延至 Phase 2（Ticket 04），Phase 1 只預留欄位結構。理由：Ticket 04 隨即把燈號改為純票數新制，Phase 1 先搬舊制等於做一次即刻被重寫的工，且會擴大「行為零變化」的回歸風險 |
| `04-vote-count-lights.md` 範圍第 2 條 | 加註本票同時承接「把燈號規則寫入設定檔」與「把 `report_contract.py` 硬編消費端改為讀設定」兩項責任，完成後該處不得再是第二來源 |

**Coordinator 追加的一項要求（採納 Reviewer 的實質關切）**：佔位不得停留在裸 `null`，須升級為具明確鍵名與型別校驗的欄位結構，使 Ticket 04 直接填值而非自行發明結構。此要求已下達 Developer。

Reviewer B 的其餘 Findings（mutation 缺口、頂層未知鍵、X4 invariant 不完整）未受本裁定影響，照常由 Developer 修正後複查。

### 4. Review 輪次紀錄（進行中）

本票採**兩方複核**（Developer ＋ Reviewer B），依使用者 2026-08-05 指示為控制 token 成本只派單一 Reviewer，**未達 Skill 定義的三方共識**。

| 輪次 | Developer 交付 | Reviewer B 結論 | 未關閉項 |
|---|---|---|---|
| 1 | 載入器＋設定檔，31 loader 案例，全套 712 | 待修正 | [阻擋] X3 燈號欄位；[重要] mutation 缺口；[建議] 頂層未知鍵；[建議] X4 invariant |
| 2 | 結構化佔位、mutation 32 mutant 全滅、頂層 allowlist、X4 改為移除並定義行為；60 loader 案例，全套 788 | 待修正 | [重要] mutation 缺口（4 個更細粒度 bool mutant 存活）；[重要] `schema_version` 接受 bool／float |
| 3 | `schema_version` 型別校驗、mutation harness 升級至 65 mutant 全滅、27 處數值比較全面檢視；71 loader 案例，全套 803 | 待修正 | [重要] mutation 缺口（4 個**正向邊界** mutant 存活） |

**每一輪 Reviewer 都往下切一層 mutation 粒度，每一輪都找到新的存活**，模式如下：

1. 第 1 輪：Developer 自陳「23 個 fail-closed 測試沒有真實 Red」→ Reviewer 用 mutant 量化後果（`MUTANT_allow_missing_schema_version` 存活時 23 案例全綠）
2. 第 2 輪：Developer 交 32 mutant 全滅 → Reviewer 切「只移除複合條件中的型別分支」，4 個 bool mutant 全部存活
3. 第 3 輪：Developer 升級至 65 mutant 全滅（並自行多抓一個 Reviewer 未指出的 `J09 levels 上界`）→ Reviewer 切「把界從含端點改成不含端點」（`<=` → `<`），4 個**正向邊界** mutant 全部存活

第 3 輪存活 mutant 的性質與前兩輪相反：前兩輪是「校驗不夠嚴」（bool 混進 int），第 3 輪是**測試只鎖了「非法值被拒」、完全沒鎖「合法邊界值被接受」**，因此任何收緊範圍的退化（`<=` → `<`）都抓不到。Reviewer 明確定性為「測試防退化缺口，不是現行產品碼錯誤」，並實測確認產品碼接受 `forced_stop=1`／`levels=7`／`debate_start=0`／`min_independent_domains=1` 四個合法邊界。

**Coordinator 對第 4 輪設定的收斂條件**：要求 Developer 除補上四個正向邊界案例外，把「開閉界變更」納入 harness 粒度並全面檢視「只測拒絕沒測接受」的校驗；若能證明「移除界」與「改變界的開閉」兩類 mutant 皆全滅，本 Finding 即可收掉。目的是**讓這條 Finding 有終點**，避免無限往下切粒度。

#### 第 3 輪已關閉的項目

- **`schema_version` Finding：關閉**。修正為 `type(version) is not int or version != SUPPORTED_SCHEMA_VERSION`；Reviewer 實測 `True`／`False`／`1.0`／`'1'`／`99`／`Decimal('1')`／`(1+0j)` 皆 REFUSED，`1` ACCEPTED；型別分支與值分支各有測試守住（B03 由兩個型別測試殺死、B04 由值測試殺死）。
- **「27 處數值比較無遺漏」：查核通過**。Reviewer 以 AST 列出全部 `Compare`、`type()`、`isinstance()`，沿 `load_debate_rules()` 呼叫順序逐一查核，確認所有參與純數值比較的運算元都已先過精確型別校驗，**未找到能繞過前置型別校驗直接進入數值比較的公開載入路徑**。
- **[建議] 頂層未知鍵：關閉**。抽出 `_reject_unknown_keys(mapping, allowed, label)`，最外層與各層級共用同一條規則，`_` 開頭註解鍵仍允許。
- **[建議] X4 invariant：關閉**。Developer 選擇「移除非 Spec invariant 並明確定義重疊行為」，理由是保住 `debate_rules` 只依賴 stdlib ＋ `seats` 的 leaf 性質。Reviewer 覆核後確認 leaf 原則成立，但**更正 Developer 的舉證**：其宣稱的 `contract_validator → question` 在當下 Snapshot 無法重現（Developer 承認是引用第 1 輪鏈 Y 中途狀態當成現況，已認錯）。Reviewer 另獨立驗證「較晚的絕對牆優先命名」對比較題收件無害：門檻在 480000ms 降為五票不會把第一輪慢席的收件牆從 490000ms 提前（`SLOW_R1_AT_485000=ACCEPTED`、`DRIVER_R1_COLLECT_UNTIL=485000`）。

#### 行為零變化的獨立驗證（每輪重複確認）

- 四個既有修改測試檔的 `self.assert*` 行**無增刪**（`rg '^[+-][[:space:]]*self\.assert'` 退出碼 1、輸出空白）
- 8 個既有修改檔的 numstat 三輪逐項完全一致：`debate_driver 22/19`、`debate_state_machine 59/65`、`live_dashboard 44/20`、`run_verifier 38/20`、`test_debate_driver 12/10`、`test_debate_state_machine 7/3`、`test_live_dashboard 6/5`、`test_vote_thresholds 12/7`
- `report_contract.py` 的 scoped diff 三輪皆為空
- **例外並已記錄**：四條 `DebateLifecycleError` 的訊息文字確實改變（`封存＋2:00` → `420000ms`、`T+8:45` → `525000ms`）。未變的是拋出條件、例外型別、`_reject` 記錄的 `reason` 與 `deadline_phase`。原本的 `2:00` 與 180 秒設定不符，是架構健檢報告第 98 行已列的既有缺陷。Reviewer 判定為可接受的診斷修正，非狀態機行為變更。

### 5. 完成（Coordinator，2026-08-06）

**共識形式：兩方複核（Developer ＋ Reviewer B），非 Skill 定義的三方共識。** 依使用者 2026-08-05 指示，為控制 token 成本本輪起只派單一 Reviewer，故未達三方共識。

**結案判準**：使用者 2026-08-06 一度將判準放寬為「只有 `[阻擋]` 才擋」，隨即改回與 `$milktea-skills-code-review` 原始規則一致的「`[阻擋]` 與 `[重要]` 皆須修正，`[建議]` 記錄後順延」。Reviewer 第 4 輪是依放寬版簽署的，Coordinator **未據此結案**，退回第 5 輪處理該 `[重要]`；第 5 輪 Reviewer 依正式判準重新簽署。

#### 完整輪次

| 輪次 | Developer 交付 | Reviewer B 結論 | 未關閉項 |
|---|---|---|---|
| 1 | 載入器＋設定檔，31 loader 案例，全套 712 | 待修正 | [阻擋] X3；[重要] mutation 缺口；[建議] ×2 |
| 2 | 結構化佔位、32 mutant 全滅、頂層 allowlist、X4 移除並定義行為；60 案例，全套 788 | 待修正 | [重要] 4 個細粒度 bool mutant 存活；[重要] `schema_version` 接受 bool／float |
| 3 | `schema_version` 型別校驗、harness 升至 65 mutant、27 處數值比較全面檢視；71 案例，全套 803 | 待修正 | [重要] 4 個**正向邊界** mutant 存活 |
| 4 | 4 個正向邊界案例＋自行掃出 4 個同類、harness 升至 81 mutant；80 案例，全套 816 | 通過（依放寬判準） | [重要] 2 個**集合邊界** mutant 存活 |
| 5 | 集合邊界修正＋自行掃出 3 個同類、14 處集合判準盤點、harness 升至 104 mutant；87 案例，全套 824 | **通過** | 僅 [建議] ×1 |

**五輪的收斂軌跡**：每一輪 Reviewer 切一個新的變異維度，每一輪都找到存活；Developer 每輪除修正外，另自行掃出 Reviewer 未指出的同類缺口共 **8 個**（第 3 輪 J09；第 4 輪 N04／N06／N14／N15；第 5 輪 P05／P17／P21）。mutation 覆蓋由 32 → 65 → 81 → 104。

#### 第 5 輪關鍵驗證（Coordinator 指定，Reviewer 執行）

Coordinator 要求 Reviewer 用**自己發明的新成員**（非已被點名的 `ghost-seat`／`stale_evidence`）測試四個 inventory 斷言，以驗證 Developer「inventory 是任意外擴的完整偵測器」之主張：

```
INVENTORY_exempt_add_nebula_seat  → caught（test_the_exemption_allowlist_is_the_frozen_seven_seat_roster）
INVENTORY_top_add_nebula_key      → caught（test_every_allowed_top_level_key_is_also_a_required_one）
INVENTORY_rules_add_nebula_rule   → caught（test_the_downgrade_rule_names_are_exactly_the_two_from_adr_0003）
INVENTORY_schema_add_version_3    → SURVIVED
```

**主張部分成立**：前三個是真 inventory（釘住具名集合），能抓任意具名集合外擴；第四個不是——`test_the_next_schema_version_is_not_silently_accepted` 只是對 0／2 的黑箱探測，未釘住版本集合，故允許版本 3 時存活。

Reviewer 亦獨立確認 Developer 的**結構性成因診斷成立**：外擴 `_TIMELINE_FIELDS` 得 `failures=48 errors=33`、外擴 downgrade entry 的 `allowed` tuple 得 `failures=15 errors=14`，證實 `_require_section` 把同一 tuple 同時當「必填清單」與「allowlist」，使「allowed ≡ required」不變式自我維持，外擴會結構性自動死亡而非碰巧被殺。

#### 封閉性論證（Developer 撤回原版並修正，Reviewer 判定成立）

Developer 明確撤回第 4 輪「81/81 即證明數值與集合邊界這個維度封閉」的說法，改為：

- **一維單調數值界**：在「界的位置」上已窮盡（移除／外移／內移三種移動各有指名測試），但**限定於運算元與比較方向固定**；方向反轉、換運算元、換邊界來源、改變多條件連接方式均不在此論證內。
- **集合成員**：兩個方向可測性不對稱。內縮可窮盡（有限成員，各一接受案例）；**外擴無法用有限黑箱測試窮盡**——`ghost-seat` 被擋住，`phantom-seat` 仍會存活，這是**原理上的限制，不是測試寫得不夠**。唯一完整偵測器是釘住集合本身的 inventory 斷言。
- 最終結論：`104/104` 只代表這 13 個群組所列的變異形狀全部有測試守著，**不代表任何維度已封閉**。

Reviewer 判定該論證成立，並再限縮一層：inventory 只對「修改被釘住的具名集合」完整，**不能保證消費端沒有另加例外或衍生集合**。Reviewer 實測四個 use-site 衍生 allowlist 的 mutant（`CUSTOM_exempt_use_site_phantom` 等）全部存活，但歸類為 Developer 已列出的第四項已知限制，非第五種新形狀，故不維持 Finding。

#### 未解風險

| # | 風險 | 級別 | 說明 |
|---|---|---|---|
| U1 | inventory 註解與紀錄對保護能力描述過強 | [建議] | Reviewer 實測：四個 use-site 外擴 mutant 與 `schema version 3` 均存活；只有直接修改三個具名集合時 inventory 才可靠失敗。**正確措辭應為「釘住具名權威集合」**，並明列無法防止使用處另建衍生 allowlist；`test_the_next_schema_version_is_not_silently_accepted` 應稱「相鄰版本拒絕測試」而非 inventory。本紀錄已依此措辭撰寫 |
| U2 | 已知未涵蓋的變異形狀（四項） | — | ①比較方向反轉與運算元替換 ②多欄位交互作用組合 ③JSON 剖析層退化輸入（Reviewer 已抽驗 `Decimal('1')`／`(1+0j)` 皆被拒） ④無 inventory 保護的 use-site 衍生 allowlist 外擴 |
| U3 | singleton 快取無公開 reload 介面 | — | 已列為 **Ticket 11 blocker B1** |
| U4 | 舊 run 被現行規則重新解讀 | — | 已列為 **Ticket 11 blocker B2** |
| U5 | `level` 未綁 `CONFIDENCE_LEVELS` enum；單級 `light_scale` 在通用結構上合法 | — | 已寫入 **Ticket 04 驗收條件**（含「不得取代 ADR 0003 五級映射」） |
| U6 | 未跑 fixture launch 煙霧測試 | — | architecture §11.9 將其列為 Phase 0 驗證，Reviewer 第 1 輪已接受本票不需另跑 |

#### 最終 Snapshot

- 基準與 revision：`main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`（未 commit）
- 本票新增 3：`hoya_market_agents/debate_rules.py`、`config/debate_rules.json`、`tests/test_debate_rules.py`（**87 案例**）
- 本票修改 4 個模組 ＋ 4 個測試檔，numstat **六輪逐項一致**：`debate_driver 22/19`、`debate_state_machine 59/65`、`live_dashboard 44/20`、`run_verifier 38/20`、`test_debate_driver 12/10`、`test_debate_state_machine 7/3`、`test_live_dashboard 6/5`、`test_vote_thresholds 12/7`
- `report_contract.py` diff 為空（六輪）；既有 4 測試檔 assertion 行無增刪（六輪）
- `debate_rules.py` SHA256 `5a065d4bc82af3cfe3d8193cbfc78f3e6b49e892e6c526eb5ae3290918cc25aa`，mtime `2026-08-05 22:46:36`；`config/debate_rules.json` mtime `2026-08-05 22:23:15`——第 4、5 輪確實未動產品碼

#### 必跑指令最終結果

| 執行者 | 結果 |
|---|---|
| Developer（第 5 輪） | `Ran 824 tests / OK / exit 0`；`tests.test_debate_rules: Ran 87 tests OK` |
| Reviewer B（第 5 輪） | `Ran 828 tests / OK / exit 0`；`tests.test_debate_rules: Ran 87 tests OK` |

（案例總數在兩者取證之間仍在變動，因鏈 Y／Ticket 05 併行補測試中；兩者皆全綠、只增不減。）

#### 四條驗收條件最終判定（依 2026-08-05 使用者裁定修訂後的條文）

1. ✅ `config/debate_rules.json` 含全部現行時間軸與票數階梯值；燈號區塊具穩定鍵名與型別校驗結構（值留空，`_example` 為 ADR 0003 全文且實測可載入）
2. ✅ grep 無殘留被取代的硬編時間／門檻常數（僅餘 `research_scheduler.py:27 SEAL_MS`、`run_verifier.py:286` 報告預算，經 Reviewer 確認均非辯論規則）
3. ✅ 非法設定 fail-closed 並指名欄位
4. ✅ 全套全綠、案例數 ≥681 ＋ 載入器新增案例（87）

**未 commit**：依專案 git 規則與本 Task 授權範圍，變更保留在工作樹。

**角色結束**：Developer（Claude 臨時 Agent）與 Reviewer B（Codex session `019fd23a-bb4d-7ee1-b842-48100631c5d8`）於本票結案後結束。Ticket 03 建立全新實例。


