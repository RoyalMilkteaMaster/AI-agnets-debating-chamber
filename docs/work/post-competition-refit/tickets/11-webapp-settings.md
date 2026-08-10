# 11 Phase 5c：設定頁（debate_rules 前端編輯）

- 狀態：完成（三方共識：Developer ＋ Reviewer A ＋ Reviewer B；09～12 批次審查十輪、修復七輪）
- Spec：`../spec.md`（Phase 5）
- Blocked by：09

## 目標

前端設定頁可讀寫 `debate_rules.json`（時間門檻、票數階梯、燈號規則），非法值被擋下。

## 使用者價值

「我想要有一個地方可以簡單的修改這個東西」——不開編輯器就能調規則。

## 範圍

1. 設定頁：顯示目前 `debate_rules.json` 全部欄位（時間軸視覺化＋表單）。
2. 寫入前跑 02 的同一個 fail-closed 驗證器；非法值拒絕並顯示具體欄位與原因。
3. 寫入採原子替換；成功後顯示生效提示（下一個 run 生效，不影響進行中 run）。
4. run 進行中鎖定編輯。

## 前置條件（2026-08-05 依 Ticket 02 Review 追加，兩項皆為 blocker）

Ticket 02 的 Review 查出兩項缺口，經 Coordinator 與 Reviewer B 一致同意延後至本票處理。**本票在開放使用者實際寫入設定前，兩項都必須完成，不得只留成備註。**

**B1 — 規則載入器缺公開的 reload／原子切換介面**

實測證據（Ticket 02 Reviewer B）：
```
CACHE same_object= True
PUBLIC_API= [... 'debate_rules', 'load_debate_rules' ...]
HAS_RELOAD= False        CACHE_VAR_IS_PRIVATE= True（_CACHED_RULES）
```
`debate_rules()` 第一次查詢後永久快取；`load_debate_rules(path)` 雖為公開 API 但**不更新快取**，既有消費端仍拿舊物件。設定頁存檔後若不處理，新規則不會生效。

要求：
- 提供**正式的 reload／原子切換 API**；**webapp 不得直接修改 `_CACHED_RULES`**（私有變數）。
- 既有 run 已持有自己的 frozen `rules` 物件，因此應實作「**進行中 run 不變、下一個 run 生效**」——這與本票範圍第 3 條「成功後顯示生效提示（下一個 run 生效，不影響進行中 run）」一致。
- 須有測試證明：寫入設定後，進行中 run 的規則物件不變，新啟動的 run 取得新規則。

**B2 — 舊 run 會被現行規則重新解讀**

實測證據（Ticket 02 Reviewer B）：
```
VERIFIER_USES_CURRENT_RULES= True
HISTORIC_DEFAULT_UNDER_CUSTOM=REFUSED T+10 有效票不足停止語意不一致。
```
`run_verifier` 一律查 `debate_rules()`（目前檔案內容），而 `manifest.json` **沒有記錄該 run 當時執行的規則版本**。使用者一旦能改設定，回頭 `verify-run` 舊 run 就會用新規則判舊資料而誤判失敗。

要求：
- 保存每個 run 實際使用的**規則快照，或至少版本／digest**，讓 `run_verifier` 依該 run 自己的規則驗證。
- 此屬 write-once artifact 契約變更（`manifest.json` 或 run 目錄新增欄位／檔案），須與 §4 的不可變原則相容。
- 須有測試：改設定後驗舊 run 仍 PASS。

Reviewer B 的判定原文：「若 Ticket 11 要讓使用者實際改設定，這項工作必須是 Ticket 11 的 blocker，不能只留成無 owner 備註。」

## 已確認實作決策

- 驗證邏輯唯一來源是 02 的載入器，前端不得複製第二份驗證規則。
- 不做歷史版本管理（git 即版本）。

## 驗收條件

- 在頁面把 6→5 切換時刻提前→存檔→新 fixture run 的狀態機行為跟著變。
- 填時間倒序／票數 0→被拒且指名欄位；檔案未被改動。
- run 進行中設定頁呈現鎖定狀態。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：settings handler 單元測試＋暫存設定檔。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：改規則前後的行為對比輸出、拒絕案例截圖、測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：09（可與 10 平行：本票只碰 settings 路由與頁面，10 只碰 launch/SSE 路由；共用檔案以 09 骨架為準，新增各自模組）
- Blocks：無

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

### B2（規則快照）執行與 Review 紀錄

- execution environment：WSL（Ubuntu），`/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`
- 基準版本：`main @ 9b8a4510`（未提交工作樹）；開工快照 `/tmp/t11b2-baseline/`（117 檔）
- Developer：Claude（臨時 Agent）。共兩輪：第 1 輪交件後由 Coordinator 要求補完 `report_confidence_cap` 缺口；中途因使用者要求暫停被切斷一次，續開時帶完整 context。
- 併行工作：Ticket 10（webapp 直播與 `live_dashboard` 退役），檔案集合零重疊。

### 設計決定：完整快照，不是 digest

Digest 方案要能驗，就得「用 digest 找回當時那份規則」——**而那份規則已經被使用者從設定頁覆寫掉了**。「找不到就驗不了」只是把誤判換成拒驗，沒有解決票面問題。完整快照讓 run 目錄維持自給自足（架構 §4）。

快照即**一份合法的 `config/debate_rules.json` 文件**（由已驗證的 `DebateRules` 正規化重建，`_about` 註解與鍵序皆不影響輸出），外加 `sha256`。

- **舊快照怎麼讀**：快照帶自己的 `schema_version`，讀回來走**同一個** `load_debate_rules`。載入器支援到哪個版本，舊快照就讀到哪個版本；不支援時逐字指名版本並拒絕，不猜。系統內無第二份規則驗證邏輯。
- **摘要純由內容導出**（`sort_keys` 正規 JSON 的 sha256），無時間戳／隨機值／路徑——這是 Ticket 07「同 token 重跑逐檔一致」不被破壞的關鍵。

**舊 run（manifest 無此欄位）**：`load_run_rules` 回傳 `None`＝**未知**，不是「用預設規則」。JSON `null` 與缺席同義。

### 三個 manifest 寫入點與「自己會紅」

```
run_controller.py:250    write_json("manifest.json", ..., source="final run manifest")
debate_driver.py:1872    write_json("manifest.json", ..., source="fast-path competition manifest")
competition_drill.py:252 write_json("manifest.json", ..., source="fake competition drill manifest")
```

三個皆涵蓋，且**清單不是手數的**。`tests/test_contract_validator.py::ManifestWritePointsTest` 以 AST 掃描整個 package 尋找 `write_json("manifest.json", ...)`，權威來源是原始碼本身：

- `test_no_manifest_write_point_escapes_the_known_set` —— 集合變動即紅，訊息直接告訴作者要蓋快照＋補行為測試。
- `test_every_module_that_writes_a_manifest_records_the_rules` —— 推導檢查，不看手寫清單：寫 manifest 的模組必須引用 `run_rules_record`，搬到新模組照樣成立。
- `test_the_scan_finds_the_write_points_it_is_supposed_to_guard` —— FP 方向，防掃描器空轉。

欄位名只有一個來源（`RUN_RULES_FIELD`），三個寫入端共用。

### `rules=None` fallback 已移除

`_verify_competition_timeline`、`_verify_stop_semantics`、`_verify_report_lineage` 的 `rules` 皆改為必填。理由：

> 這些函式回答「這份 artifact 對**某一套**規則是否成立」——**規則是輸入，不是環境**。留一個現讀的預設值等於在一個呼叫點之外重新種下同一個 bug，忘了傳會靜靜換一套規則；現在忘了傳是 `TypeError`。

`None` 現在是明確的第三種值＝「該 run 沒記錄規則」，由 `verify_run` 顯式傳入。`run_verifier` 已不再 import `debate_rules()`（僅保留 `DebateRulesError` 例外型別）。

### `report_confidence_cap` 缺口的補完（第 2 輪）

第 1 輪交件時，四項 `RULE_DEPENDENT_CHECKS` 中僅三項誠實跳過，`report_confidence_cap` 仍由 `report_contract` 現讀，導致沒有記錄規則的舊 run 在使用者改嚴降級規則後產生**假失敗**：

```
REFUSED: report contract failed: 信心 green 高於資料上限 red
```

Coordinator 判定這不是「已宣告的邊界」而是同一個 bug 換一個檢查項目（把「我不知道」變成有自信的錯誤失敗宣稱），要求補完，並授權擴充白名單至 `report_contract.py` / `tests/test_report_contract.py`，同時要求先回答「這個修法真的需要動 `report_contract` 嗎」。

**Developer 的回答：需要，並附三條路的證據。**

- **路 A — 在 `run_verifier` 層整段跳過 `validate_market_report`**：FN 災難。該呼叫內只有 `_validate_confidence` 最後兩行是規則的函數；其餘為 schema、run_id 對齊、UTC 格式、period、證據回查、票數交叉比對、辯論交叉比對、方向一致性。跳過它＝為關掉一項假失敗而丟掉八項與規則無關的檢查。
- **路 B — 傳一份「空的」`ConfidenceRules` 當「未知」**：實測 `_checked_scale` 判為 `confidence.light_scale 缺少燈號：red、orange、yellow、green、blue`。**空值不是「未知」，是「非法」。**
- **路 C — 只從呼叫端組裝其餘檢查**：那些是私有函式，在 `run_verifier` 重寫編排等於把「什麼叫一份合法報告」分叉成兩個定義，比原缺口更糟。

**根因判定**：

> 根因不是「那個函式太大」，是 `rules` 這個參數的**值域少了一個值**。它只有「一份 `ConfidenceRules`」與「`None`＝省略、現讀」，說不出「不知道」。缺一個值，就只能在「假失敗」與「丟掉八項檢查」之間選——兩個都錯。

處置：把該值補進值域而非在呼叫端繞過——`report_contract.RULES_NOT_RECORDED`（獨立型別哨兵，以 `is` 比對）。改動面 **+45/−0**，`_validate_confidence` 內僅多一個 early return，**只放掉「燈號不得高於上限」一項**；級別、圖示、說明文字與報告契約其餘部分一項不減。

`_checked_scale` 是 `confidence_scale` 與 `confidence_cap` 的共同漏斗，哨兵在該處直接丟 `DebateRulesError`——「算不出來就別假裝算得出來」。

### 目標行為（實測）

```
有記錄規則的 run，使用者改設定後
  status                        = VERIFIED
  digest unchanged by the edit  = True
  rule_checks_without_run_rules = []

沒有記錄規則的舊 run（門檻＋燈號都改過）
  status                        = VERIFIED
  rules_sha256                  = None
  rule_checks_without_run_rules = ['debate_stop_reason', 'debate_stop_at_ms_upper_bound',
                                   'stop_semantics', 'report_confidence_cap']
```

**四項一致地誠實跳過。** 第 1 輪那句 `REFUSED: 信心 green 高於資料上限 red` 消失。

### Coordinator 獨立驗證

**FN 方向（檢查沒有被整個關掉）** —— 五個模組逐模組實跑：

```
tests.test_report_contract        Ran 53 tests  OK
tests.test_verify_run             Ran 97 tests  OK
tests.test_contract_validator     Ran 39 tests  OK
tests.test_competition_drill      Ran 20 tests  OK
tests.test_debate_driver          Ran 88 tests  OK
```

**哨兵防護實測** —— Coordinator 直接以哨兵呼叫各入口：

```
confidence_scale(RULES_NOT_RECORDED)              → DebateRulesError（正確擋下）
confidence_cap(report, sources, rules=哨兵)        → DebateRulesError（正確擋下）
report_workflow._confidence_of(哨兵)               → AttributeError（大聲炸開）
validate_market_report(report, sources, rules=哨兵) → 唯一會靜默放行的路徑
```

**Coordinator 對剩餘風險第 7 項的更正**：Developer 記為「目前沒有機制阻止」誤用，實測四條路徑中**三條會擋**。真正敞開的僅有「直接呼叫 `validate_market_report` 並傳入哨兵」一條。風險真實但範圍比 Developer 自述為窄。

**凍結檔案現值全部相符**：`run_index.py 5a80fd0204e4` / `run_store.py 04fa8001ae59` / `test_run_index.py 40f29983c107` / `test_run_store.py 1f1969da4537` / `debate_rules.py c12fe3fbe7e1` / `seats.py 6e1271519168`。

### 變更摘要（九檔）

| 檔案 | +/− | sha256（前 12） |
|---|---|---|
| `hoya_market_agents/contract_validator.py` | +186 / −2 | `dd84333c3686` |
| `hoya_market_agents/report_contract.py` | +45 / −0 | `400c57764dbe` |
| `hoya_market_agents/run_verifier.py` | +87 / −24 | `6a8983677143` |
| `hoya_market_agents/run_controller.py` | +10 / −1 | `6b87e209cfea` |
| `hoya_market_agents/debate_driver.py` | +10 / −1 | `92777028e47e` |
| `hoya_market_agents/competition_drill.py` | +12 / −1 | `cbcb021be1eb` |
| `tests/test_contract_validator.py` | +303 / −0 | `60b6735e1202` |
| `tests/test_report_contract.py` | +129 / −0 | `d3e2315d6ecf` |
| `tests/test_verify_run.py` | +381 / −28 | `4f1246176e69` |

**動 `contract_validator.py` 的理由**：它是 `validate_run_manifest` 的所在，即 manifest schema 的權威。B2 是 manifest 契約變更，欄位的形狀、序列化與反序列化理應住在那裡；放在 `run_verifier` 會讓三個寫入端反過來 import 驗證器。

### 三個硬約束（重測）

- **Ticket 07（40 檔逐檔一致）**：`files_run_a=40 files_run_b=40 / same_file_set=True / per_file_identical=True`。（注意：跨兩個 data root 比對會假紅，因 manifest 記絕對路徑；正確作法是同一 Data Root 路徑清空重跑。）
- **Ticket 08（finalize 時機）**：`point_latest_at(run)` → `write_json("manifest.json", ...)` → `index_finalized_run(...)` 逐字未動，僅 dict 內容與函式簽章改變；`tests/test_run_index.py`（凍結未改）全綠。
- **架構 §4**：不改寫入所有權、不覆寫既有檔案、僅在既有單一寫入者手上為 manifest 加一個 write-once 欄位；並把「保存內容雜湊以證明未被改寫」從證據延伸到規則。§11.6 的 `outcome.json`（「write-once 新 artifact，不違反不可變原則」）為同型先例。

### Developer 主動揭露的自身錯誤

1. **一條測試因為錯的理由而通過**：`test_a_legacy_run_still_fails_the_report_checks_that_need_no_rules` 原本只改 `report.json`，會先被 artifact index 的雜湊擋下。已改成連 manifest 的 index 一併重蓋，並在 docstring 寫明為何必須如此。（此即本 Task 反覆出現的「測試名字比斷言強」一類。）
2. **歸屬腳本的 `MINE` 清單寫死在授權擴充之前**，導致 `report_contract.py`（`46c590751d02 → 400c57764dbe`）與 `tests/test_report_contract.py`（`d01f03adbc1a → d3e2315d6ecf`）在原輸出中被誤標為 OTHER-DEV。已於變更摘要更正。

### 檔案歸屬

對 `/tmp/t11b2-baseline/`（117 檔）**全樹逐檔比對，非排除清單**：僅上表九檔屬本工作。其餘變動全屬 Ticket 10。

Developer 對 Coordinator 原本「用排除清單比對」指示的反駁（Coordinator 接受）：

> 排除清單會把**自己的**改動一起藏掉。全樹逐檔比對再標歸屬，兩邊天然對稱，不可能有我的改動被清單藏起來。

### 攔截與量測污染

```
INTERCEPT_PRECHECK_EXIT=0     /tmp/t08-intercept/sitecustomize.py
T08_INTERCEPT_LOG=/tmp/t11b2-intercept.log（私有）
Ran 1696 tests in 85.029s     OK (skipped=1)
私有 log 差額 = 39   →  36 × ['codex','exec','-m'] + 3 × ['codex','--version']
```

第 1 輪曾觀測到「逐檔加總 39、全套 75」，一度判讀為跨模組交互作用並展開 bisect。根因為**共用 `/tmp/t08-intercept/calls.log` 的併發污染**（由 Ticket 10 Developer 以決定性測試找出：靜置 25 秒共用 log 自行增長 69 行）。本 Developer 的獨立佐證：同一份程式碼、同一組模組，在對方那輪跑完後量到 39，稍早量到 75——同一指令量出兩個值即排除交互作用假說。

### 待 Reviewer 特別確認

**B1b 一條既有測試的行為契約被反轉**：`test_a_published_reload_is_honoured_by_the_next_verification` → `test_a_published_reload_does_not_change_the_verdict_on_an_old_run`；同組另三條「讀取一次」改為「讀取零次」（零次蘊含一次的保證）。

反轉理由已寫入 docstring：

> B1b 當時要求「下一趟驗證要看到新規則」，因為那時規則只有一個來源。B2 之後該 run 自己就記著它遵守過的規則，拿新規則去判舊資料正是票面要修的誤判——同一份 5/4/3 在改動前後都必須得到 VERIFIED。

**這是行為契約的反轉，Developer 主動要求 Reviewer 特別確認，Coordinator 同意列入最終審查。**

### 剩餘風險

1. **快照是 run 自我宣告的。** 能同時竄改 manifest 快照與摘要者，可換一套**合法但不同**的規則自我審核。載入器擋得住非法規則，擋不住「另一套合法規則」。與 manifest 既有自我宣告欄位（`competition_timeline`、`question_type` 等）同級——manifest 本身不在任何 artifact index 的雜湊保護內（`run_verifier.py:139-140` 明確跳過）。B2 未使此事變好也未變壞。
2. **`validate_run_manifest` 不強制此欄位**，只在「有填」時驗結構（且只驗結構，不驗規則合法性）。強制會弄壞白名單外的 `tests/test_contracts.py`，也會讓舊 manifest 讀不下去。漏掉寫入點的守門靠兩條 meta 測試。
3. **`contract_validator` import 了 `debate_rules._DOWNGRADE_FIELDS`（私有名）。** 該表是「每條降級規則擁有哪些參數」的唯一權威，抄一份會各自漂移。改名會在 import 時大聲炸開，不會靜靜錯。
4. **快照驗證會寫一個暫存檔**（為走 `load_debate_rules(path)` 這個唯一驗證權威）。`verify_run` 非熱路徑，但唯讀 `TMPDIR` 環境下會失敗。
5. **`RULES_NOT_RECORDED` 是新的公開介面值。** 目前只有 `run_verifier` 傳它。誤用風險經 Coordinator 實測收窄為：`confidence_scale` / `confidence_cap` / `report_workflow._confidence_of` 三條路徑皆會大聲失敗，**僅「直接呼叫 `validate_market_report` 並傳入哨兵」一條會靜默關掉上限把關**。

### 設定頁本體 執行與 Review 紀錄

- execution environment：WSL（Ubuntu）；基準 `main @ 9b8a4510`；開工快照 `/tmp/t11ui-baseline/`（117 檔）
- Developer：Claude（臨時 Agent）
- 併行工作：Ticket 11-B2 尾段，檔案集合零重疊

### 變更摘要

| 檔案 | +/− | sha256（前 12） |
|---|---|---|
| `hoya_market_agents/webapp/settings.py`（新增） | +636 / −0 | `ce2727e398b9` |
| `hoya_market_agents/webapp/pages.py` | +317 / −15 | `d7cf63aca631` |
| `hoya_market_agents/webapp/server.py` | +74 / −7 | `02f2bc4d3fc0` |
| `hoya_market_agents/webapp/live.py` | +21 / −1 | `57c4347df902` |
| `hoya_market_agents/webapp/__init__.py` | +15 / −5 | `cc65bdc753eb` |
| `tests/test_webapp.py` | +953 / −3 | `beb67007c3ae` |

`cli.py` / `test_cli.py` **未動**——設定頁只是既有 `webapp` 指令下的一條路由。

### 「沒有第二份驗證邏輯」的行為證據

票面硬要求「驗證邏輯唯一來源是 02 的載入器，前端不得複製第二份驗證規則」。Coordinator 要求提出**行為證據而非結構宣稱**。

**A. 逐字相同**：20 個非法輸入，頁面顯示的句子與 `load_debate_rules` 自己丟出的句子**逐字相同**，不一致案例數 **0**。其中多項是 Developer 未寫過任何分支的理由——燈號最後一級不是 0 票、燈號名稱重複、豁免席位不是席位、`schema_version` 填 `true`、票數階梯沒有遞減、來源等級填 0。全部被拒、全部指名欄位、檔案全部未動。

**B. 鑑別力（決定性的一項）**：把 `load_debate_rules` 換成「什麼都接受」後，**同一批 20 個輸入全部通過並寫進檔案**——`timeline_ms.debate_start` 真的被寫成 `'abc'`。

> 載入器被拿掉後 webapp 自己攔不下任何一條，證明第二份驗證邏輯不存在。**結構宣稱無法證明這件事。**

**輸入框刻意用 `type="text" inputmode="numeric"` 而非 `type="number"`**——後者等於讓瀏覽器決定什麼能送出，載入器的拒絕就永遠不會發生。

**欄位定位不是手寫對照表**：路徑集合由文件走訪推導，比對方式是「載入器的句子裡出現了哪些路徑」，最具體者勝。時間軸倒序時兩端都被指名（`final_round_start` 與 `reduced_threshold_from` 都拿到 `aria-invalid`）。

### 原子替換：明寫它不保證什麼

候選檔先在**同一個目錄**寫完、先過載入器，才 `os.replace` 換名。

- **保證**：讀者不會讀到半個檔，也不會讀到本函式放進去的非法檔。
- **明寫不保證**：兩個並行存檔不會互斥。後完成者勝，**先存的人被告知「已存檔」卻永遠不會知道自己的改動不見了**。

沿用 Ticket 10 的措辭形狀：需要真正互斥的人得往這個模組外面找，這裡沒有任何東西自稱是它。兩個方向皆有測試（含 `test_two_saves_are_not_serialised_and_the_later_one_simply_wins`）。

### 鎖：拒絕共用 `LaunchLock`，但也拒絕造第二個判準

Developer 讀過 `launch.py` 後判定**不能共用 `LaunchLock`**：它只看得到「本 process 啟動的 launch」，而**從 CLI 起的 run 同樣會被改規則影響、它卻看不見**。

處置：設定頁的鎖讀 Data Root 自己的狀態，即直播室本來就在公開的 `STATUS_RUNNING`。為避免生出第二個「什麼算進行中」的判準，把 `FINISHED_MARKER` 的判定抽成 `live.run_finished()`，由 `live_snapshot`、SSE stream 與新的 `live.in_progress_run_id()` 共用同一份實作。

有測試釘住「兩者答案永遠相同」，也有測試釘住「`LaunchLock` busy **不等於**設定頁鎖定」。

### 「進行中 run 不變、下一個 run 生效」的兩個獨立斷言

用 run 真正會走的 seam（`debate_state_machine.required_votes_at`），非同一個含糊測試：

1. **進行中 run**：`held = debate_rules()`（`run_controller.execute` 在 run 起點做的同一件事）→ 存檔把 reduced 5→4 → `required_votes_at(480_000, rules=held)` **仍為 5**，`held.reduced_votes` 仍為 5。
2. **下一個 run**：同一次存檔後 `required_votes_at(480_000)` **為 4**，`debate_rules().reduced_votes` 為 4。
3. **identity 補證**：`assertIsNot(held, debate_rules())`——證明真的換了物件，不是同一份被就地改寫。
4. **FP 方向**：被拒絕的存檔兩者都不動（`assertIs(held, debate_rules())`）。

`webapp` 完全不碰 `_CACHED_RULES`，發佈只走 `reload_debate_rules(path)`。**Coordinator 獨立驗證**：`grep -rn "_CACHED_RULES" hoya_market_agents/webapp/` → 零命中；package 靜態守門測試通過。

### 四條驗收條件（實際輸出）

**（1）端到端**——非單元測試，真的跑 `run_fake_competition_drill`（真 ResearchScheduler、真 seal、真 `DebateStateMachine`），透過真的 `POST /settings` 改設定，前後各一次：

```
[改設定前] stop_reason=consensus_6_votes  threshold=6  tally={affirmative:6, negative_side:1}  valid_vote_count=7
[POST]     debate_start 240000→200000、reduced_threshold_from 480000→220000
           HTTP 200、CSP=script-src 'none'、提示「已存檔」、檔案內新值 220000
[改設定後] stop_reason=consensus_5_votes  threshold=5  tally={affirmative:5, negative_side:1}  valid_vote_count=6
           late=訊息於 240000ms 抵達，deadline 已先結算；fail closed。
```

狀態機在第 5 張同向票就結算，第 7 席的票被 fail-closed 擋下——這是「6→5 切換時刻提前」在**狀態機行為**上的實際差異，不只是檔案內容變了。（註：`reduced_threshold_from` 必須 > `debate_start`，故同時把 `debate_start` 下移。）

**（2）非法值**——兩例，檔案皆未改動：

```
時間倒序 → 200 /「這次沒有存檔」/ timeline_ms.final_round_start（1000）必須大於
           timeline_ms.reduced_threshold_from（220000）；時間軸必須嚴格遞增。
           指名欄位 = ['timeline_ms.final_round_start','timeline_ms.reduced_threshold_from']
票數 0   → 200 / vote_thresholds.initial 必須是 1 到 7 之間的整數票數，收到 0。
           指名欄位 = ['vote_thresholds.initial']
```

**（3）run 進行中鎖定**：

```
[進行中] GET  /settings → 200、「設定頁目前鎖定」、鎖定它的 run=20260806T020000Z-btc-live01
                          輸入框 26 個，disabled 26 個，送出鈕 disabled=True
         POST /settings → 409，檔案未改動
[結束後] GET  /settings → 無鎖定訊息，disabled 輸入框 0 個
         POST /settings → 200，force_stop=660000
```

**（4）全套**：`Ran 1820 tests, OK (skipped=1)`。基準 1696 → **+124**，只增不減。

### Coordinator 獨立驗證（決定性全套，無其他 agent 併行）

```
攔截器前置檢查 → /tmp/t08-intercept/sitecustomize.py ，exit 0
T08_INTERCEPT_LOG=/tmp/coord-final.log（私有）
Ran 1820 tests in 85.539s     OK (skipped=1)
攔截差額 = 39  →  36 × ['codex','exec','-m'] + 3 × ['codex','--version']
```

**凍結檔案現值全部相符**：`run_index.py 5a80fd0204e4` / `run_store.py 04fa8001ae59` / `test_run_index.py 40f29983c107` / `test_run_store.py 1f1969da4537` / `debate_rules.py c12fe3fbe7e1` / `seats.py 6e1271519168` / `test_seats.py 7873910fd9b8` / `prompt_builder.py b61352bfac84` / `config/debate_rules.json dcb8d0baf155`。

### Developer 主動揭露的兩項自我發現

1. **頁尾寫著「本頁只讀取 run artifact」——設定頁會寫檔，這是假話。** 已改成專屬頁尾並加測試（雙向：`test_the_settings_page_says_it_writes_the_rule_file` ／ `test_every_page_that_only_reads_still_says_so`）。
2. **載入器一次只回報第一個問題。** Developer 原本假設兩個非法欄位會同時被指名，Wave 2 的行為紅推翻了它。**修的是測試不是實作**——理由：在前端跑第二輪檢查就是第二份驗證邏輯。另補兩條測試把此行為寫下來（兩個都錯 → 只報時間軸；修好第一個 → 第二個才浮出來）。

### TDD 與突變驗證

| 階段 | 紅 | 綠 |
|---|---|---|
| Wave 1（模組核心 61 案） | `ImportError: cannot import name 'settings'`——**機制紅**，主動標示 | 316 |
| Wave 2（頁面／路由／端到端 58 案） | **1 個行為紅**（真發現，見上） | 374 |
| 補測（限制與誠實性 5 案） | **事後補證，無自然紅**——主動標示 | 379 |

事後補證的 9 條另做**突變驗證**，全部確認會紅：

| 突變 | 結果 |
|---|---|
| 對照組（未突變） | 全綠 |
| 設定頁改用唯讀 footer | `PageFooterHonestyTest` FAILED (1) |
| `STATES` 多一個頁面沒措辭的結局 | `SettingsColourTest` FAILED (1) |
| 欄位改成寫死清單（漏 downgrades） | `SettingsFieldDerivationTest` FAILED (2)、`SettingsEditsValuesOnlyTest` FAILED (1) |
| 鎖永遠回報「沒有進行中」 | `SettingsLockTest` FAILED (4) |
| 先換名後驗證 | `SettingsSaveTest` FAILED (1)+errors(32)、`SettingsAtomicWriteTest` FAILED (3) |

突變後還原，sha256 與交件值一致。

### CSP 與新增顏色

**用嚴格那一份 `CONTENT_SECURITY_POLICY`（含 `script-src 'none'`）。** 表單完全是伺服器端渲染 + 純 `POST`，拒絕訊息由回應該 POST 的頁面帶回來，不需任何 JS；時間軸視覺化用純 CSS。`form-action 'self'` 本來就在裡面，POST 不需放寬任何一條。

另補一條**推導式**測試取代硬編頁面清單：掃 `pages.py` 的 AST 找出把 `scripts=` 傳給 `_document` 的 renderer，斷言集合 == `{render_live_page}`。

新增兩個 token（`danger` / `success`，與 stance 色分開，因為回答的是不同問題）：

| | light on page | light on surface | dark on page | dark on surface |
|---|---|---|---|---|
| `danger` | 6.59:1 | 7.66:1 | 9.50:1 | 8.59:1 |
| `success` | 5.67:1 | 6.59:1 | 10.14:1 | 9.17:1 |

全部 ≥ 4.5:1（AA 內文），已進 `CONTRAST_REQUIREMENTS`，由 `ContrastTest` 逐 theme 計算。**錯誤狀態不只有顏色**：橫幅標題「這次沒有存檔」、每欄「這一欄被拒絕：…」文字、輸入框 `aria-invalid="true"`，並用 `aria-describedby` 把錯誤訊息綁到該欄（測試逐一驗證每個 `describedby` id 在頁面上真的存在）。

### 殘留執行緒

全套跑完（in-process，1820 案）：`threads = ['MainThread']`。

### 檔案歸屬（全樹逐檔比對，非排除清單）

基準 `/tmp/t11ui-baseline/`（117 檔）。全樹差異僅 7 檔：6 個為本工作，1 個為票面檔本身（`11-webapp-settings.md`，由 Coordinator 寫入，Developer 全程未寫 `docs/`）。B2 那一批檔案在本 Developer 拍快照後**無變動**。

### 誠實標示的剩餘風險

1. **設定頁只改值，不改結構。** 無法新增／刪除欄位、`light_scale` 級數或整條 downgrade 規則。載入器把 downgrade 規則視為選填，所以檔案裡沒有的那一條在頁面上就沒有控制項，也**無法從頁面加回來**——已寫進 docstring 並有測試釘住（含「另一條仍在」的 FP 方向）。
2. **一次只報一個問題**（載入器的形狀）。修好第一個才看得到第二個。刻意不在前端跑第二輪。
3. **並行存檔沒有互斥。** 後完成者勝，先存者被告知成功卻不會知道改動消失。明寫的非保證，不是漏洞。要處理得用版本號／CAS。
4. **鎖只跟著最新的 run 目錄**（沿用 `live` 既有的盲點）。舊 run 未完成、新 run 已完成時，設定頁會呈現未鎖定。刻意不另造第二個判準。
5. **第一次存檔會重排版**：`json.dumps(indent=2)` 把 `light_scale` 每列從一行展開成多行，42 → 62 行，內容等價（測試驗證 `json.loads(after) == before`）。git diff 會有一次性噪音。
6. **表單大小 1078 / 上限 8192 bytes。** 超過上限時伺服器會整份丟掉 body——已加 `NOTHING_SUBMITTED` 狀態避免被誤報成「存檔成功但沒改動」，並有測試在表單長大到接近上限時先紅。
7. **`create_webapp_server` 未暴露 `rules_path`**，正式伺服器一律編輯 Code Root 的 `config/debate_rules.json`；`rules_path` 只是 handler 層的測試 seam。若日後要支援多 Data Root 各自的規則檔，需另開票（會動到 `cli.py`）。
8. **無截圖**（環境無瀏覽器）。替代物：三份渲染後 HTML（`/tmp/t11ui-artifacts/settings-{saved,refused,locked}.html`）＋ `RenderedSettingsPageTest` 關鍵元素斷言。**未以任何形式假稱有截圖。**

---

# 09～12 批次審查結案紀錄（三方共識）

**本段為 Ticket 09、10、11、12 共用。** 依使用者指示，四張票的雙 Reviewer 審查併為一次批次執行，故結案紀錄同文附於四張票。

## 共識

```
Developer     七輪修復，最終交件
Reviewer A    第十輪簽署 09～12 通過（native）
Reviewer B    第八、九、十輪皆簽署 09～12 通過（第三～十輪為 open_code_review_delegate）
Coordinator   不計入三方共識
```

**三個隔離角色達成共識。**

## 最終狀態

```
全套      Ran 2115 tests    OK (skipped=1)
攔截      39 = 36 codex exec + 3 codex --version（無真實外部 CLI 呼叫）
殘留執行緒 ['MainThread']
基準      main @ 9b8a4510（本批全部未提交）
案例數    1423（批次起點）→ 2115
```

**09～12 產出的最終 SHA（前 12）**

```
webapp/__init__.py     07b91952efe3    webapp/settings.py     ce2727e398b9
webapp/log.py          7d78acc11a94    webapp/outcome.py      8122aeeb11a9
webapp/views.py        bdd87351b137    quote_api_client.py    61df955fbb7d
webapp/pages.py        28c521aed337    seats.py               6e1271519168
webapp/server.py       000550ba1501    run_index.py           78541571f466
webapp/launch.py       9ecfa4c3c0c1    run_verifier.py        3127e91cac74
webapp/live.py         055a7822721b    contract_validator.py  dd84333c3686
                                       report_contract.py     400c57764dbe
```

## 審查與修復的軌跡

| 輪 | 阻擋 | 主要內容 |
|---|---|---|
| 1 | 14 | 兩位皆不簽署。四項為兩人各自獨立找到的同一件事 |
| 2 | 7 | **修了重現，沒修不變式**——七項未關閉 |
| 3 | 4 組 | 兩位對 T1／T2 結論相反，Coordinator 裁定 T1 採 A |
| 4 | 2 簇 | predicate 會拋例外 ＋ 四處 docstring |
| 5 | 1 項 | 兩位收斂到同一個：`_fetch` 的契約仍會漏 |
| 6 | 2 項 | codec registry ＋ 敵意 `symbol` |
| 7 | 4 項 | `MarketSession.zone()` 逸出 ＋ 三處文字 |
| 8 | 1 項 | B 簽署；A 卡 seam 掃描器的宣稱 |
| 9 | 1 句 | B 再簽署；A 卡那段新文字裡的兩個詞 |
| 10 | 0 | **兩位皆簽署** |

**後三輪為純文件修改，AST 剝除 docstring 後逐字相同。**

## 第一輪查出的實質缺陷（節錄）

這些是本批審查真正攔下來的東西：

1. **未到期、未完成的 run 可被寫入 write-once 的 `outcome.json`。** 8/1 開始、7 天期限的 run，8/2 就能標成「命中」，而且**永遠無法更正**。
2. **`_pending_runs()` 把索引讀取失敗折成 `[]`。** 統計頁顯示「沒有待驗證項目」，真相是它讀不到——**「沒看成」被說成「沒有」**。
3. **命中率用到預測時點之後的收盤價。** 美股 run 的 baseline 會吃進預測後 18 小時的資訊。
4. **`Infinity` 被當成合法價格接受**（API 與手動兩條路徑皆是）。
5. **outcome sweep 的舊 run 永久餓死**：最新幾筆持續失敗就佔滿上限，最舊的永遠掃不到。
6. **兩個 webapp 同日輪替 log 會複製一筆並讓一方報錯。**
7. **同分鐘內 `newest_run_id` 依 question slug 排序選錯 run**（不需併發即可觸發）。
8. **`live.html` 是 dead link，而 `run_verifier` 強制每份離線 HTML 都要有連到它的導覽。**
9. **一個完全正常、只是不為真的 opener 會被靜默丟棄，然後真的 `urlopen` 被叫起來**（Developer 自己找到）。

## 反覆出現的缺陷形狀

本批確認了三種在整個 Task 反覆出現的形狀，值得寫進工程紀錄：

**① 修了重現，沒修不變式。** 第二輪七項未關閉全屬此類。典型例：加了排除 `bool` 的價格 validator，但呼叫端先 `float(True)`，**守衛永遠看不到 bool**；加了「相對連結必須存在」的檢查，但只解析雙引號 `href`。

**② 用較弱的工具先處理，再交給較強的工具。** `_PAGE_TABS` 先用 regex 從 HTML 註解裡挖出 nav 再交給 parser，於是**整段註解掉的 nav 照樣通過驗證**。`_FORBIDDEN_HTML_DEPENDENCIES` 的 `src` regex 只認帶引號的寫法，是同一形狀的反向版。

**③ docstring 宣稱超過實作。** 全 Task 累計超過 50 次。**前 4 次被關鍵字抓到，之後全部只有把實作讀一遍再核 docstring 才找得到。** 其中第 16 個（`views.py` 的「Reading is all this module does」）是 Developer 自己重讀時發現的，兩位 Reviewer 當輪都沒抓到。

## Coordinator 記錄的自身錯誤

1. **白名單重疊**：把 `cli.py`／`test_cli.py` 同時列進 Ticket 09 的白名單與併行 B1 工作的受保護清單，導致 B1 Developer 在移動中的樹上量到污染證據而停手。
2. **受保護 SHA 清單過期**（四次）：同一個 Task 跑久了，前面票的收尾值會被後面合法的改動取代，沿用舊值會造成假警報。
3. **攔截器的計數 log 是共用資源**，不在任何白名單裡（因為它不在工作樹裡），導致一位 Developer 為「全套多出 36 次呼叫」bisect 了很久，實際是另一個 agent 同時在跑。
4. **審查書前後矛盾**：Reviewer B 第二輪未走 OCR delegate，因為基礎審查書留有「不要使用 delegate」的句子，而 delegate 段落被附加在後面。
5. **快照建立時機晚了一輪**，使 AST diff 對它必然為零，當不了修復前基準（Reviewer A 抓到）。
6. **自建探測腳本連續兩次參數個數給錯**，兩次都得聲明「這不算驗過」。

## 結案剩餘風險

### 使用者裁定延後（併發相關三項）

1. **設定頁的鎖只檢查「最新」run**，漏掉較舊但仍在執行的 run。
2. **`run_store.py` 的 `latest.json.tmp` 是固定檔名**，兩個 run 同時寫會讓其中一個 `FileNotFoundError`——**這推翻了 Ticket 10 原本「兩次同時 launch 兩個都會跑完」的宣稱**。
3. **`RunController` 的 `rules` 只傳給 `_write_manifest`**，未傳進 `_debate`／`_vote`／`_report`，所以 manifest 宣稱的「本 run 遵守的規則」沒有結構保證。

**這三項會疊起來**：併發 run 可能發生、鎖看不到非最新的、規則沒有下傳——一個非最新的併發 run 可以在跑到一半被改掉規則，而它的 manifest 宣稱的是另一份快照。

### 兩位 Reviewer 核可的宣告邊界

4. **美股半日提早收盤未建模**：固定 16:00 ET 界線，官方提早收盤日的 13:00–16:00 ET 會使用前一交易日收盤價。方向是 stale 不是 look-ahead，`day` 與 `priced_on` 可稽核並人工重判。
5. **`.rotating` claim 可能在行程中止後永久殘留**，系統不自動合併或刪除。它仍保存真實 log bytes、retention 忽略它。**死掉行程留下的 claim 與活著行程手上正在飛的 claim 從外面看完全一樣**（名字帶 pid，pid 會被重用），自動回收會把同一批紀錄追加兩次。
6. **`presentation_version` 維持 `2.0.0`**：這個 repo 沒有任何 commit，沒有對外保存的 2.0 bundle 需要保護；實測 37 份歷史 bundle 在基準版就已驗不過。**Reviewer A 的顧慮列為既有架構債**：任何 renderer 改動都會讓既有 2.0.0 bundle 驗不過，因為 `_verify_report_lineage` 做逐位元組重繪比對，而 `presentation_version` 沒有版本分派。
7. **離線相依檢查只涵蓋 `<script>`、`<link>`、`@import` 與帶引號的 HTTP(S) `src`**。不帶引號的 `src=`、`srcset`、`poster`、CSS `url()` 兩層都不看。現行 renderer 不產出這些形狀，artifact 單獨竄改另由重繪比對拒絕。
8. **JSONL reader 以 `str.splitlines()` 劃分紀錄**，JSON 字串中的原始 Unicode line separator（`U+2028` 等）會被誤切並 fail closed——不會產生假的 VERIFIED，但合法紀錄可能不可讀。**本專案自己的 `ensure_ascii=False` writer 可以合法寫出 U+2028**，所以不是純理論邊界。
9. **cursor fairness 的前提**：在單一 server、`limit > 0`、可讀且完整的 index、行程不中止、log 可寫且 cursor 能在後續 pass 正常讀寫的前提下，rotation 提供 eventual coverage。多 server、行程死亡、未進 index、非正 limit、cursor/log 持續失敗不在此保證內。
10. **caller-owned 的 `asset_class`／`day` 例外刻意逸出** `daily_close` 的單一出口。真實路徑使用精確內建型別（`json.loads` 只產生精確 `str`、`available_close_day` 只產生精確 `date`），外層 per-run boundary 仍會隔離。
11. **`PYTHONDONTWRITEBYTECODE=1` 進不了子行程**：`tests/test_cli.py:459`、`tests/test_run_index.py:1493`、`tests/test_debate_rules.py:1756` 用整理過的 `env={...}` dict 啟動子行程，該 dict 未帶此變數，於是子行程把 `.pyc` 寫進 repo。**一個讀起來像全域的指令被靜默限縮到單一行程。**
12. **UTF-8 codec fast path 是這台 CPython 3.12.3 的實測事實，不是跨直譯器 API 保證**；實際 decode 例外已有 `Exception` 邊界保護。
13. **package 內同一種例外清單形狀出現 44 次**（18 個檔案）。**未逐一稽核，不主張它們是缺陷**，只記錄形狀與數量。
14. **三處 docstring 的文字精確度**（兩位 Reviewer 皆核可為剩餘風險，非阻擋）：`available_close_day` 的 `the only caller's own except Exception` 未加限定；`is_usable_price` 的 `Its caller _priced_payload` 用定冠詞單數但實際有三個呼叫點；module docstring 的 `callee name` 未展開為 `ast.Name.id`／`ast.Attribute.attr`（避免從 scan docstring 抄邊界而形成第二個漂移點的明示取捨）。
15. **「唯一呼叫端」未被完整釘住**：測試釘的是「解析 `outcome.py` 時，含有 callee 名為 `quote` 之 `ast.Call` 的 `ast.FunctionDef` 子樹，其 `node.name` 集合等於 `{"_priced_payload"}`」。**動態取用（`getattr`／`importlib`）不在涵蓋範圍。**
16. **環境無瀏覽器，四張票皆無截圖**。替代證據為渲染後 HTML 存檔＋關鍵元素斷言，四張票均未以任何形式假稱有截圖。
17. **報價端點的回應形狀未對真實服務驗證**（測試全程以注入 opener 進行，`test_quote_api_client.py` 有 module 範圍的 socket 防護）。

## 程序偏離（記錄在案）

**第一輪雙 Reviewer 皆未載入 `$milktea-skills-code-review`。** 該 skill 是 Claude Code 的 skill，codex CLI 沒有對應機制，因此本質上載入不到。Reviewer B 誠實揭露並依審查書條件執行，Reviewer A 未提及。**第一輪因此不是完整規格的 Reviewer 契約。** 使用者於第二輪之間安裝了 codex 版 skill（`milktea-agents-skills-for-codex`），**第二輪起兩位皆成功完整載入**。

**Reviewer B 自第三輪起使用 `open_code_review_delegate`**（OCR v1.8.6）。每輪報告均含 `ocr:` 區塊，如實記錄 preview 的 reviewable 數與 Coordinator 固定清單的落差及其原因（repo 無 commit，workspace preview 必然涵蓋十二張票的全部未提交產出）。**第二輪 B 未走 delegate，原因是 Coordinator 的審查書前後矛盾，非 B 的疏失。**
