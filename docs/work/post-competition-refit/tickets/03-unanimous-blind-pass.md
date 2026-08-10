# 03 Phase 2a：盲投 7/7 直過＋說服拉票強化

- 狀態：**完成**（第 6 輪達成三方共識，2026-08-06）
- Spec：`../spec.md`（Phase 2 投票）
- Blocked by：02

## 目標

opening 盲投收齊後 7/7 同立場即直接停止產報告；未全票時的辯論 prompt 強化「說服對立席位改票」語意。

## 使用者價值

「第一輪 AI 互不看彼此資料、七票共識就直接寫報告；沒有七票才進第二輪互看討論、質疑說服對方」。

## 範圍

1. `debate_state_machine`：opening 全席發布後檢查 7/7 同立場→停止，`stop_reason=unanimous_blind_pass`；門檻與時點讀 `debate_rules.json`。
2. `debate_driver`：直過時跳過辯論輪直接進報告流程；未直過走現行流程（6→5→4 時間階梯不變）。
3. 辯論 prompt（challenge/response/revote）加入說服目標語意：「你的目標是以證據說服對立席位改票，使己方達到當前門檻」。
4. `run_verifier` 認可 `unanimous_blind_pass` 的時間線（辯論可為空）。

## 已確認實作決策

- 直過取代「即使全票仍須一輪反方挑戰」僅限 7/7 情境；6 票以下維持原規則（含 scrutiny 輪替）。
- challenge 對立配對、改票必附理由、`vote_changes` 全程記錄等機制不動。
- `votes.json` 停止原因欄位新增值，schema 同步。

## 驗收條件

- fixture：七席盲投全同立場→無任何辯論訊息、`votes.json` 記 `unanimous_blind_pass`、報告正常產出、`verify-run` PASS。
- fixture：6/1 盲投→照常進辯論，行為與現制一致。
- prompt 快照測試可見說服語意文字。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：注入時鐘＋fake provider 的既有狀態機測試模式（分鐘壓縮成秒）。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：兩個 fixture 的 votes.json 摘要、verify-run 輸出、測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：02
- Blocks：04

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

### 1. 開始執行

| 項目 | 值 |
|---|---|
| Execution environment | WSL2 / Ubuntu，Python 3.12.3；專案路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；`source: auto_current` |
| 基準版本 | `main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`（全程未 commit、未 `git add`） |
| Developer | Claude（平台原生臨時 Agent），識別 `ace13181273a93f38` |
| Reviewer A | Codex CLI `0.146.0`，`review_engine: native`，session `019fd432-1f10-7f21-8eb0-c5137c5f2d57` |
| Reviewer B | Codex CLI `0.146.0`，`review_engine: native`，session `019fd432-2e9e-7452-9af6-6328fd93b1ac` |
| 模型 / reasoning effort | 三個角色均為各自 CLI 預設，未指定 `model_reasoning_effort` |
| Open Code Review | 未啟用（無 `settings_update: open_code_review`），Reviewer B 使用 `native` |
| 必跑指令 | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests` |

**平行執行說明**：本票屬「鏈 X」，與「鏈 Y」的 Ticket 05 在**同一個工作樹**上並行。以獨佔檔案清單防衝突：

- 鏈 X 獨佔：`debate_state_machine.py`、`debate_driver.py`、`run_verifier.py`、`debate_rules.py`、`live_dashboard.py`、`config/debate_rules.json` 及其測試
- 鏈 Y 獨佔：`question.py`、`tests/test_question.py`、`tests/test_question_package.py`
- 兩鏈皆禁改：`run_store.py`、`cli.py`、`run_controller.py`、`report_contract.py`

因此本票的 Review 一律排除 `question.py` 系列的失敗，反之亦然。

### 2. Ready for Review（第 1 輪）

Developer 以 `$milktea-skills-tdd` red-green-refactor 完成四項範圍。自檢採 **mutation testing**：對 30 個判定點各植入一個變異體，確認至少一個測試轉紅（M 批 30 個全滅）。

**Coordinator 的錯誤與更正（記錄在案）**：Coordinator 曾向 Developer 與使用者宣稱第 1 輪的 M26 改動「弄壞了合法的 run」。Developer 以真值表反駁，Coordinator 查 `git show 9b8a4510:hoya_market_agents/run_verifier.py` 第 341 行確認 `is True` 在基準版本就已存在。

> 正確敘述：**不是「你改壞了」，是「你把壞的釘住了」**——第 1 輪加的測試把既有的錯誤行為固定住，缺陷本身是 pre-existing。

Developer 的反駁成立，Coordinator 公開更正。

### 3. 第 1 輪 Review（兩份獨立報告）

兩位 Reviewer 各自載入 `$milktea-skills-code-review`，就同一固定 Snapshot 執行 Standards 與 Spec 兩軸 Review。Developer 逐項重現、修正或反證後提出第 2 輪，並對第 2 輪的 6 個新判定點再跑一批 mutation（R 批 6 個全滅）。

### 4. 第 2 輪 Review：兩位 Reviewer 各自找到對方沒找到的缺陷

這一輪具體證明了雙 Reviewer 的價值——兩份報告的 `[重要]` 完全不重疊：

| 來源 | Finding | 性質 |
|---|---|---|
| **僅 Reviewer A** | `challenge_completed` 的誠實性完全未被驗證 | bundle 可以對「這場有沒有辯論過」說謊，兩個方向都無人擋 |
| **僅 Reviewer A** | `attempt_ids` 用 membership 而非 equality | 可**追加**不存在的 attempt 而不被發現 |
| **僅 Reviewer B** | `_verify_first_round_lineage` 不驗順序 | 正式票可以排在它所依據的第一輪**之前** |
| **僅 Reviewer B** | tally enum 未驗（`[建議]`） | bundle 可以捏造額外的立場欄位 |

### 5. Ready for Review（第 3 輪）

#### 5.1 流程事故：mutation harness 污染共用工作樹

Developer 第 1、2 輪使用的 mutation harness **會改寫磁碟上的來源檔**（其他所有 Agent 都用記憶體 monkeypatch）。在共用工作樹的並行架構下，這造成 Coordinator 與另一鏈的多次**假紅**全套結果；再疊加過期 `__pycache__`，一度出現虛假的 `Ran 255 tests / 25 errors`。

清掉 `__pycache__` 後的真實狀態為 `Ran 939 tests, 19 failures`，全部屬鏈 Y 當時的預期紅燈。

**Coordinator 處置**：下令立即停止 harness、不等它跑完，並改以現有證據回報，明示 **mutation 自檢不是本輪的驗收條件**。

Developer 回報已停止並還原，附獨立雜湊（不依賴 harness 自己的比對）：

```
c5e11e00a49472fa9dee2c4a0cf630cef1b576c81fbe32d4f4e76f79582fc510  debate_state_machine.py
c4eb903445ac95e29cc4c1e1c4b35a69c1c35d2cdf5b27ac5f52c849c1cb2380  debate_driver.py
76613745ba496921175cf730074bb2e47dd1216ffaf6c2ca3f03b21bd0463d2c  run_verifier.py
f35f3aa44c3bdd65ad468bc27c851188a8ed5af6396fa19133e2c4f896d0aa39  debate_rules.py
12 個變異點逐一比對：S01…S12 全部 FINAL，零殘留
```

harness 執行區間約 09:32–10:35，**該區間內產生的全套數字一律不採信**。記憶體版 harness（以 `sys.modules` 注入編譯後 mutant，全程唯讀）已寫好但本輪未使用。Coordinator 已要求兩位 Reviewer **獨立驗證還原屬實**，不採信自陳。

#### 5.2 三個 `[重要]` 的修正

**① `attempt_ids` membership → equality**

`run_verifier.py:424`：

```python
("attempt_ids", row.get("attempt_ids") == [opening.get("attempt_id")])
```

單元層 `test_every_field_of_the_opening_must_match_its_official_vote` 涵蓋四種形狀：換掉／**追加不存在的 attempt**／**重複同一個 attempt**／空清單——四者全部被拒且指名 `attempt_ids`。`message_ids`、`content_sha256` 一併補上「追加」案例。

端對端 `test_a_blind_pass_bundle_may_not_invent_a_replacement_attempt` 重現 Reviewer A 的完整 bundle（追加 `spot-technical-a2-phantom`、同步 `report.replacement_attempt_ids`、重渲染三頁、修 index）→ 拒於 `spot-technical …：attempt_ids`。

**② `challenge_completed` 誠實性**

新增 `_verify_challenge_completed(debate, votes)`，**從公開紀錄重算**（重算式逐字對齊 `summary()`：參與者＝有 position 的席位；完成＝各至少一則 challenge 與一則 response），要求 `is` 精確相等。

同時**移除** `_verify_stop_semantics` 裡較弱的 `type(...) is bool` 與 `challenge_completed is False`——讓這條規則只有一個權威，而不是兩處各驗一半。

兩個方向都端對端：`test_a_bundle_may_not_lie_about_whether_the_room_challenged`（真辯論過→謊稱 False）、`test_a_blind_pass_bundle_may_not_claim_it_challenged`（直過→謊稱 True）。

**③ `_verify_first_round_lineage` 無序 → 有序**

改為逐席取「最早的 position(r0)／challenge(r1)／response(r1)／final_vote(r1-3)」，同時驗**紀錄先後**與 **elapsed_ms 先後**。Reviewer B 的兩種形狀都被拒：`final_vote` 前移 →「正式投票排在它所依據的第一輪之前」；`round=0` →「缺少 round 1-3 的正式投票」。

另補三個相鄰形狀：`round: true` 不得冒充 r1（因 `True == 1`）、時間戳前移、非整數 `elapsed_ms`。

端對端 `test_a_vote_recorded_before_its_own_first_round_is_refused`：**只改順序**（訊息一則不多不少）、重算整條雜湊鏈與三頁 → 拒於 `spot-technical …：第一輪生命週期`。

#### 5.3 `[建議]` tally enum：本輪一併做掉

新增 `_verify_tally_enum(votes)`：要求 `votes["stances"]` 是非空、無重複的 list，且 `set(tally) == set(stances)`。`_verify_blind_pass_record` 的重算字典改由 `stances` 建立，**不再沿用受驗資料自己的 tally 鍵**——否則等於拿被驗的東西當標準答案。

端對端 `test_a_bundle_may_not_invent_an_extra_tally_column` 重現 Reviewer B 的 `"fabricated": 0`（votes／report／manifest 三份同步、三頁重渲染）→ 拒於「立場 enum」。

#### 5.4 合法 §5.2 replacement 未被誤擋

`test_a_replacement_finishing_the_vote_in_a_later_attempt_is_accepted`：a1 完成第一輪、a2 才投正式票（`final_vote` 在 r2 且 elapsed 較晚）→ 接受。逐席檢查只看公開紀錄的先後與回合，不看 attempt 邊界。

#### 5.5 Developer 主動列出的「刻意不升級」兩處

- 普通路徑**不**驗 `attempt_ids` 精確相等——替補跨 attempt 是 §5.2 核准形狀
- **不**驗全域 `elapsed_ms` 單調性——逐席已涵蓋，全域有誤擋風險

#### 5.6 Developer 誠實標示的未解項

S 批 12 個變異點：**10 killed、2 SURVIVED**。Developer 選擇誠實標示而非掩蓋：

- **S12**（`recomputed` 改用 bundle 自己的 tally 鍵）— 自評為**冗餘而非缺陷**：`_verify_tally_enum` 先行保證 `set(tally) == set(stances)`，通過該檢查後兩來源不可區分。
- **S11**（拿掉「第一輪訊息排在該席開場之前」）— **自認是真實測試缺口，本輪未補**。所需形狀是「challenge 排在自己的 position 之前、其餘全部合法」。**程式碼本身有這道檢查且正確**，缺的是守住它的測試。

Coordinator 已把這兩點列為兩位 Reviewer 的指定攻擊目標，並要求**明確裁定 S11 的嚴重度**（若程式碼確實正確而只缺測試則為何級；若發現程式碼其實不正確則為 `[阻擋]`）。

#### 5.7 第 3 輪 Snapshot

```
main @ 9b8a4510ec9406f19506e21d50af7918da2385d4（未 commit、未 git add）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
Ran 944 tests in 153.622s / OK

模組計數（標明計入哪些）
  A：本票修改的 4 個   test_debate_state_machine(30) + test_debate_driver(61)
                       + test_verify_run(50) + test_debate_rules(94)                      = 235
  B：A + 鏈 X 獨佔但本票未改的 2 個  + test_vote_thresholds(11) + test_live_dashboard(39)  = 285
```

| 檔案 | numstat |
|---|---|
| `debate_state_machine.py` | 131/65 |
| `debate_driver.py` | 72/25 |
| `run_verifier.py` | 290/27 |
| `tests/test_debate_state_machine.py` | 241/19 |
| `tests/test_debate_driver.py` | 747/29 |
| `tests/test_verify_run.py` | 662/0 |

未碰的鏈 X 檔案維持 Ticket 02 原值（`live_dashboard 44/20`、`test_live_dashboard 6/5`、`test_vote_thresholds 12/7`）；`report_contract.py` diff 為空；禁改清單自檢 `exit=1`（零命中）。

Snapshot 已凍結。

### 6. 第 3 輪 Review（兩份獨立報告）

兩位 Reviewer 就同一固定 Snapshot 獨立作業，首輪完成前未互看結論。兩人都**獨立驗證了工作樹還原屬實**（四個雜湊逐字比對、掃描 mutant 殘留、並確認測試前後雜湊不變），未採信 Developer 自陳。

#### 6.1 Developer 反證成立的部分

| 項目 | 兩位的裁定 | 證據 |
|---|---|---|
| **S11「程式碼正確、只缺測試」** | **兩位一致：`[建議]`，Developer 的自陳成立** | 兩位都各自造出「challenge 排在自己 position 之前」的形狀，都被現行程式拒絕。Reviewer A 進一步以記憶體 monkeypatch 移除 `run_verifier.py:531` 的 guard 後重跑整個 `FirstRoundLineageTest` → `SURVIVED, tests=11 failures=0 errors=0`，逐字證實「缺口限於沒有回歸測試」 |
| **S12「冗餘而非缺陷」** | **兩位一致：論證成立** | 兩位都查了正式呼叫順序 `_verify_tally_enum`（`:283`）→ `_verify_blind_pass_record`（`:285`），並確認全 repo 只有這一個 production call site。沒有跳過 enum 檢查仍進入 blind-pass record 的正式路徑 |
| `challenge_completed` 重算式與 `summary()` 是否漂移 | 兩位都直接讀原始碼比對，確認目前語意一致，無誤擋證據 | `summary()` 參與者＝`seat.initial is not None`；verifier 參與者＝公開紀錄有 position。對狀態機正式產出的公開紀錄兩者等價 |
| 移除舊型別檢查是否留洞 | 未留洞 | `True` ACCEPTED；`False`／`1`／`0`／`"true"`／`None` 全部 REFUSED |

兩位都提醒：`summary()` 日後若改定義，兩份重算式有同步維護需求（建議以共享純函式或 parity 測試守住）——但這是維護風險，不是當前錯誤，未開 Finding。

#### 6.2 三個原 `[重要]` 全部關閉

`attempt_ids` 相等比較：兩位實測 `replace`／`append phantom`／`duplicate`／`empty list`／`tuple`／`None` 六種形狀**全部 REFUSED 且指名 `attempt_ids`**。
`challenge_completed`：真辯論記 `False`、直過記 `True` 兩個方向的完整 bundle 都 REFUSED。
逐席 lineage：final-before-r1、`round=0`、缺 position、多則 challenge 中任一排在 position 前——都 REFUSED；`round=3 vote`、七席全域交錯——正確 ACCEPTED。

#### 6.3 兩位各自獨立找到的新 `[重要]`

這一輪再次證明雙 Reviewer 的價值：**R1、R2 兩人各自獨立重現，R3 只有 Reviewer A 找到。**

| # | 位置 | 缺陷 | 來源 |
|---|---|---|---|
| **R1** | `run_verifier.py:425` | opening 缺 `attempt_id` 時，票列寫 `[None]`，`[None] == [None]` 成立而通過 | **A 與 B 各自獨立** |
| **R2** | `run_verifier.py:485` | 普通路徑完全不驗票列中的 attempt 是否真的出現在公開紀錄 | **A 與 B 各自獨立** |
| **R3** | `run_verifier.py:531` | 逐席 elapsed 只比 final vote 與前置訊息，未驗 position 與 challenge／response 之間 | **僅 Reviewer A** |

**三個都不是戳私有函式戳出來的，是完整 bundle 端對端重現的**（重算 content／public-history hash、重渲染三頁、修 artifact index 後仍取得 `VERIFIED`）：

```text
R1  移除 opening 頂層與 content 內的 attempt_id、票列改 [null]
    → blind_missing_attempt_forged = VERIFIED

R2  普通 6/1 run 的票列與 report.replacement_attempt_ids 加入
    spot-technical-a2-phantom（公開紀錄只有 a1）
    votes_attempt_ids  = ["spot-technical-a1", "spot-technical-a2-phantom"]
    public_attempt_ids = ["spot-technical-a1"]
    → ordinary_phantom_forged = VERIFIED

R3  記錄順序維持 position → challenge → response → final_vote，
    只把 challenge 的 elapsed_ms 改成 position−1ms
    elapsed_base = {'position': 270000, 'challenge': 320000}
    → elapsed_forged = VERIFIED
```

**R2 與 R3 分別推翻了 Developer 在 §5.5 主動列出的兩處「刻意不升級」理由**：

- R2 對「普通路徑不驗 `attempt_ids` 精確相等，因替補跨 attempt 是 §5.2 核准形狀」——前半成立，後半不成立。**不能限制為單一 attempt ≠ 可以完全不驗。** 兩位給出同一個保留合法形狀的修法：按公開 seat messages 的**首次出現順序**重算每席 ordered distinct attempt IDs，要求與票列完全一致。
- R3 對「不驗全域 `elapsed_ms` 單調性，逐席已涵蓋」——Reviewer A 證明逐席**沒有**涵蓋。修法：要求 position 的 elapsed 不晚於該席所採用的 challenge 與 response，不必要求全域跨席單調。

兩位都自行用真實狀態機構造了合法 §5.2 替補形狀並確認仍被接受（`position/challenge/response = a1`、`final_vote round 2 = a2`、`elapsed 420001`、`attempt_ids [a1, a2]` → ACCEPTED），因此上述修法不會誤擋。

Reviewer B 另指出 R1 的關鍵：**狀態機本身明確禁止空或非字串 attempt**（`debate_state_machine.py:628` 起丟 `UnknownAttemptError`），所以 verifier 目前會認證一份狀態機不可能產生的 bundle。

#### 6.4 兩位的結論

| | Standards | Spec | 品味 | 致命問題 | 結論 |
|---|---|---|---|---|---|
| Reviewer A | 不通過 | 不通過 | 🟡 | 無 | **待修正**，不簽署 |
| Reviewer B | 不通過 | 不通過 | 🟡 | 無 | **待修正**，不簽署 |

#### 6.5 兩位的驗證證據

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_debate_state_machine tests.test_debate_driver \
  tests.test_verify_run tests.test_debate_rules
Ran 235 tests / OK / exit=0        （A 與 B 一致）

＋ tests.test_vote_thresholds tests.test_live_dashboard
Ran 285 tests / OK / exit=0        （A 與 B 一致）

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
Ran 944 tests / OK / exit=0        （A 33.622s、B 35.111s）
```

邊界：`report_contract.py`／`report_workflow.py`／`report_fixtures.py` 的 `--ignore-cr-at-eol` diff 為空；Ticket 02 未碰檔案仍為 `44/20`、`6/5`、`12/7`；`debate_rules` 與 94 個 loader 測試未變、`_example` 五級燈號完整；鏈 Y 檔案的獨立變更未計入本票；本輪無鏈 Y 測試失敗。

兩位皆未修改任何檔案、未執行寫入型 git 指令、未派 Agent、未使用 OCR。Reviewer A 說明未刪除專案內 `__pycache__` 是因為硬性規則禁止修改檔案，改以 `PYTHONDONTWRITEBYTECODE=1` 執行——處置正確。

#### 6.6 Coordinator 派工（第 4 輪）

第 4 輪已派出，範圍為 R1／R2／R3 三個 `[重要]` ＋ S11 回歸測試（`[建議]`，成本低故一併做）。要求：三項都要完整 bundle 端對端測試；R2、R3 都要同時證明「偽造被拒 ＋ 合法形狀仍被接受」兩個方向；mutation harness 一律使用已寫好的記憶體版，磁碟版不得再啟動（鏈 Y 正在同一工作樹跑第 13 輪）。

### 7. Ready for Review（第 4 輪）

Developer 接受全部三個 `[重要]`，並更正自己第 3 輪 §5.5 的兩條理由：

> R2 我把「不能限制成單一 attempt」錯推成「所以完全不驗」；R3 我宣稱「逐席已涵蓋」，但逐席當時只比了 final vote，position 與 challenge 之間根本沒有人在比。兩位的證據都成立。

#### 7.1 三個 `[重要]` 的完整 bundle 前後對照

```text
R1  票列 attempt_ids: [None]
    改動前 VERIFIED → 改動後 REFUSED
    「spot-technical 的公開訊息缺少非空字串 attempt_id。」

R2  票表 ['spot-technical-a1', 'spot-technical-a2-phantom']
    公開紀錄 ['spot-technical-a1']
    改動前 VERIFIED → 改動後 REFUSED
    「spot-technical 的 attempt lineage 與公開紀錄不一致」

R3  elapsed_base      position=270000 challenge=320000
    紀錄順序（前兩則）  ['position', 'challenge']   ← 順序完全沒動
    改動前 VERIFIED → 改動後 REFUSED
    「第一輪訊息的 elapsed_ms 早於該席開場」
```

#### 7.2 R1：Developer 主動把同根因掃乾淨

`[None] == [None]` 的陷阱不只在 `attempt_id`。`_blind_pass_seat_problem` 現在**先**要求開場自己的 `message_id`／`content_sha256`／`stance`／`public_reason` 為非空字串、`evidence_ids` 為非空字串陣列，**才**做相等比對。

新測試：`test_an_opening_missing_its_own_identity_fields_is_refused`（4 欄 × 3 種空值＝12 個 subTest）、`test_an_opening_without_usable_evidence_ids_is_refused`（5 種）。

**Coordinator 已要求兩位 Reviewer 特別檢驗這個守衛是否過度拒絕**：`evidence_ids` 被要求為**非空**——合法的開場是否可能沒有 evidence（某席研究失敗但仍發表立場、或某題型不需 evidence）？若會誤擋合法 run，那是新的 `[重要]`。

#### 7.3 R2：把規則合併成單一權威（本輪最大結構性改動）

新增 `_verify_attempt_lineage(debate, votes)`，按公開 seat messages 的**首次出現順序**重算每席 ordered distinct attempt IDs，要求與票列完全一致。採用兩位一致的修法。

它被設計成**直過與普通辯論共用的單一權威**，Developer 因此**移除**了 `_blind_pass_seat_problem` 裡原本那條 `attempt_ids` 檢查（理由：避免同一條規則有兩個來源；第 2 輪的 blind-pass 不存在的 attempt 測試改由新函式發出訊息）。

這是「用資料結構消掉特殊情況」的正確方向，但**合併同時是風險**——舊檢查是「精確等於 `[opening.attempt_id]`」，新檢查是「與公開紀錄重算相符」。直過的公開紀錄恰好只有一則開場，兩者理論上等價。Coordinator 已要求兩位**驗證這個等價在所有形狀下都成立，特別是公開紀錄本身異常時**。

#### 7.4 反方向：合法 §5.2 形狀未被誤擋

```text
attempt_ids: ['spot-technical-a1', 'spot-technical-a2']
_verify_attempt_lineage: ACCEPTED
```

兩層都接受（`AttemptLineageTest.test_a_replacement_that_really_spoke_is_accepted`、`FirstRoundLineageTest.test_a_replacement_finishing_the_vote_in_a_later_attempt_is_accepted`）。順序也釘住：`[a2, a1]` 倒寫被拒（`test_the_recomputed_order_is_first_appearance_order`）。

R3 的正向邊界（同一毫秒）由 `test_a_first_round_message_stamped_with_the_opening_is_accepted` 守著；範圍限定為「position 的 elapsed 不晚於該席所採用的 challenge 與 response」，**不主張跨席全域單調性**。

#### 7.5 `[建議]` S11 回歸測試已補

`test_an_extra_challenge_before_the_opening_does_not_hide_a_valid_one`：某席一則 challenge 在 position 前、另一則在後，其餘全部合法 → 拒於「第一輪訊息排在該席開場之前」（檢查取的是「最早的 round 1 challenge」，所以前面那一則正是被檢查的那一則）。

#### 7.6 Developer 主動撤回自己第 3 輪的一項主張

> 我上輪判定 S12（重算字典的鍵來源）為「冗餘、不可區分」。第 4 輪把「開場識別欄位檢查」移到計票之前後，該 mutant 現在會被殺——**原本的冗餘論證已不成立，我撤回**。

**兩位 Reviewer 在第 3 輪都已接受該冗餘論證**（§6.1）。Coordinator 已要求兩位自行驗證這次撤回是否正確並明確表態。

#### 7.7 mutation 自檢（全程記憶體版，工作樹唯讀）

依 Coordinator 指令改用 `sys.modules` 注入編譯後 mutant 的記憶體版（`child.py`），磁碟版未再啟動。

```text
killed  T01_drop_the_attempt_lineage_check
killed  T02_attempt_id_need_not_be_a_non_empty_string
killed  T03_attempt_lineage_never_compared
killed  T04_attempt_lineage_loses_its_dedupe
killed  T05_drop_the_opening_identity_guard
killed  T06_drop_the_opening_evidence_ids_guard
killed  T07_drop_the_opening_timestamp_check
integrity（工作樹全程唯讀）OK ×4     killed=7 survived=0 broken=0

killed  S10 / S11 / S12              ← S11、S12 由第 3 輪存活轉為全滅
```

#### 7.8 第 4 輪 Snapshot

```text
main @ 9b8a4510ec9406f19506e21d50af7918da2385d4（未 commit、未 git add）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests（已先清 __pycache__）
Ran 958 tests in 242.845s / OK

A：本票修改的 4 個  test_debate_state_machine(30) + test_debate_driver(64)
                    + test_verify_run(62) + test_debate_rules(94)                      = 250
B：A + 鏈 X 獨佔但本票未改的 2 個  + test_vote_thresholds(11) + test_live_dashboard(39) = 300
```

| 檔案 | numstat | sha256 |
|---|---|---|
| `run_verifier.py` | 347/27 | `30208a13…51cc1` |
| `debate_driver.py` | 72/25 | `c4eb9034…cb2380` |
| `debate_state_machine.py` | 131/65 | `c5e11e00…2fc510` |
| `debate_rules.py`（untracked） | 498 行 | `f35f3aa4…0aa39` |
| `tests/test_verify_run.py` | 879/0 | — |
| `tests/test_debate_driver.py` | 858/29 | — |
| `tests/test_debate_state_machine.py` | 241/19 | — |

未碰的鏈 X 檔案維持 Ticket 02 原值；`report_contract.py`／`run_controller.py`／`run_store.py`／`cli.py` diff 皆為空；禁改清單自檢 `exit=1`（零命中）。

#### 7.9 Developer 誠實標示的剩餘風險

| # | 項目 | 說明 |
|---|---|---|
| **Y10（新）** | 跨席全域 `elapsed_ms` 單調性仍未驗 | 本輪只補了同一席自己四則訊息之間的時間軸。跨席形狀（A 席訊息時間戳晚於 B 席卻排在前面）不在守備範圍。Developer：「**這次我不再宣稱『已涵蓋』**」 |
| **Y11（新）** | `_verify_attempt_lineage` 只比對 `votes.attempt_ids` | `report.replacement_attempt_ids` 由 `report_contract` 對回 votes（`attempts[1:]`），本票未直接驗它；`report_contract.py` 屬 Ticket 04 |
| Y7／Y8 | 已裁定納入本票 | 遷移風險限定「目前 Data Root 範圍內為零」，不延伸宣稱外部備份 |
| Y9 | `_publish_position` 的 relay-success gate | 依裁定另票 |
| Y6 | 不宣稱封閉性 | 10 個 mutant 全滅只代表這些形狀有測試守著 |

Coordinator 已要求兩位 Reviewer 分別裁定 Y10 的嚴重度與 Y11 的歸屬界線（並明示：若能構造「只改 report 而 votes 合法」的不一致 bundle 並取得 `VERIFIED`，Y11 就是本票的 `[重要]`，不是 Ticket 04 的）。

Snapshot 已凍結。

### 8. 第 4 輪 Review：兩位再次各自找到對方沒找到的

| # | 位置 | 缺陷 | 來源 |
|---|---|---|---|
| R1 | `run_verifier.py:425` | opening 缺 `attempt_id` 時 `[None] == [None]` 通過 | **A 與 B 各自獨立** |
| R2 | `run_verifier.py:485` | 普通路徑不驗 attempt 是否真的出現在公開紀錄 | **A 與 B 各自獨立** |
| R3 | `run_verifier.py:531` | 逐席 elapsed 只比 final vote，未驗 position 與 challenge／response 之間 | **僅 Reviewer A** |

R2、R3 分別推翻 §5.5 的兩處「刻意不升級」理由。兩位都給出保留合法 §5.2 形狀的替代修法（按公開 seat messages 首次出現順序重算 ordered distinct attempt IDs）。

**兩位一致關閉的部分**：S11「程式碼正確、只缺測試」成立（A 以 monkeypatch 移除 guard 重跑 `FirstRoundLineageTest` → `SURVIVED, tests=11 failures=0`）；S12 冗餘論證成立（兩位都查了呼叫順序與唯一 call site）；`challenge_completed` 重算式與 `summary()` 語意一致；`evidence_ids` 非空守衛不會誤擋（兩位都查 `debate_state_machine.py:384`，所有公開訊息本來就要求非空 evidence）。

### 9. 第 5 輪：harness 假 killed 事件與根因

Developer 第 4 輪主動撤回自己第 3 輪的 S12 冗餘論證，宣稱「改動後該 mutant 會被殺」。**兩位 Reviewer 各自獨立重建 mutant，都無法重現**（A：`SURVIVED tests=13`；B：`SURVIVED tests=250`，以記憶體注入完全相同的 mutant 並保留原錯誤訊息）。兩位裁定一致：**撤回不必要，原論證仍成立。**

Coordinator 要求追查 harness 為何誤報。Developer 找到根因：

> `mem_harness` 以 `cwd=ROOT` 啟動 `child.py`，但 Python 把**腳本自己的目錄**（scratchpad）放進 `sys.path[0]`，不是 cwd。`import hoya_market_agents` 因此在**套用 mutant 之前**就 `ModuleNotFoundError`，child 退出碼 1，而父程序的規則是「非零＝killed」。每個 child 都死得一樣，所以每個 mutant 都被報成 killed。

**三層修法**：① child 明確把 repo root 加進 `sys.path`；② **退出碼永不作為判決**，只認 child 印出的 `RESULT=` 標記，crash／import error／anchor 不符／零測試一律歸 `BROKEN`；③ **每輪先跑控制組**——`null` mutant（把一段程式換成它自己）必須 SURVIVED、`poison` mutant（無條件拋錯）必須 KILLED，有一個不如預期就拒絕輸出任何數字。

**作廢範圍（Developer 自行分類，未含糊）**：

| 批次 | 判定 |
|---|---|
| T 批（7 個）＋ S10–S12 重跑 | **全部作廢**，零個 mutant 真正執行過 |
| M（30）／R（6）／S（12）批 | 磁碟版 `-m unittest`＋`cwd=ROOT`，mutant 確實生效——**結構上有效**。但存在第二個假 killed 途徑（鏈 Y 併行編輯造成無關失敗）。該途徑**單向**：只會把 SURVIVED 誤報成 killed，不會反向。因此**「killed 的數字是上界」，「SURVIVED 的結論可信」** |

**Coordinator 裁定**：M／R 不重跑——那是第 1、2 輪的 mutant 而 `run_verifier.py` 已大幅演進，重跑舊 mutant 打新程式資訊價值低；且 mutation 自檢非本票驗收條件。改為要求 U 批（對應實際出貨程式）必須跑完。**兩位 Reviewer 都覆核同意此裁定。**

**這個教訓值得留下**：一個會誤報 killed 的 mutation harness 比沒有 harness 更危險——它讓「測試抓到了」和「根本沒跑起來」看起來一樣。控制組是唯一能分辨的機制。

### 10. 第 5 輪 Review：Y12 與兩個新缺口

Developer 第 5 輪修好 T1（replay 事件配對）、T2（canonical 編號）、T3（`[seal, stop]` 時間窗，採兩位建議中較完整的版本）。

**T2 的 userspace 衝突與 Developer 的處置**：Reviewer B 建議的規則（第 n 個 attempt 必須是 `{seat_id}-a{n}`）與 `tests/test_reviewer_complete_attack.py`（**跨鏈共管、本票禁改**）宣告的 `{seat}-receipt-attempt-99` operational 形狀互斥。Developer **回報衝突並提出更窄的規則**（符合 pattern 就得編號＝序位，其他命名體系放行），未自行修改該測試，並以 `test_a_non_canonical_naming_scheme_is_deliberately_exempt` 釘住豁免。

**兩位裁定**：程序上正確，技術上不足。兩位各自重鑄完整 bundle，證明豁免可被等價字串繞過：

```text
spot-technical-a2   REFUSED        spot-technical-b2    VERIFIED  ✗
spot-technical-a02  REFUSED        spot-technical-A2    VERIFIED  ✗
spot-technical-a01  REFUSED        spot-technical-a1x   VERIFIED  ✗
                                   Spot-technical-a2    VERIFIED  ✗
                                   news-a1（別席名稱）    VERIFIED  ✗
```

Y12 兩位都裁定 **`[重要]` 必修**：「相較舊版沒有減少保護」在歷史比較上成立，但不能用來關閉既有 `[重要]`——這個修正只擋住特定拼字，改一個字元便重新接受狀態機不可能產生的稽核資料。

**Reviewer B 做了決定性實驗**：在記憶體中只把 helper 的兩處 `receipt-attempt-99` 改為 canonical `a1`，結果仍為 `canonical_operational VERIFIED True ['provider_runtime_attestation']`。**那個測試用 canonical 命名照樣通過，它不需要那個豁免。** 兩位也都讀了 `_promote_with_complete_local_claim`，確認該形狀是**測試 helper 主動改寫出的合成形狀**，不是真實 drill 或狀態機的輸出，也沒有任何 planning／ADR 宣告它合法。

**Coordinator 據此授權修改該跨鏈共管檔案**，範圍限定為把兩處合成名稱對齊 canonical，並要求 Developer 證明「這不是刪掉仍有效的保護」。

另有兩個新缺口：**V2**（僅 A）replacement event 只要求早於新 attempt、未驗原子相鄰，`a1 → event → 另一席 final_vote → a2` 仍 `VERIFIED`；**V3**（僅 B，A 判 scope boundary）移除 manifest 的 `competition_timeline` 就能跳過整條時間驗證。**Coordinator 裁定採 B**，理由是把本輪剛加的規則補齊到所有路徑是完成同一條規則，不是擴大範圍。

### 11. Ready for Review（第 6 輪）與結案

#### 11.1 三項修正

**V1 canonical 精確化**：拿掉 pattern 豁免，第 n 項一律要求精確等於 `f"{seat_id}-a{n}"`。兩位打穿的 10 種形狀全部 REFUSED，合法 `['a1','a2']` ＋配對 replay event ACCEPTED。

**V2 相鄰性**：`index >= first_seen` → `index + 1 != first_seen`，對齊 `_record()` 同步且連續的寫入。端對端測試**把 replay hash 補成正確值，確保擋下它的是相鄰性而非 hash**。

**V3 時間戳底線**：`_verify_message_timestamps` 拆兩層——**無條件**要求 `type(x) is int and x >= 0`（`True` 因精確型別比對被擋）；**有 timeline 時**追加 `[seal, stop]`。無條件層由 `verify_run` 讀完 debate 後直接呼叫，不再受 `competition_timeline` 存在與否影響。

#### 11.2 跨鏈測試改動：Coordinator 要求的「證明不是刪掉保護」檢查抓到真陷阱

Developer 在動手時發現：

> 第 209 行**兩個測試共用**。若無條件改成 canonical，`test_local_receipts_with_unadopted_attempt_are_rejected` 會失去它要製造的 receipt↔evidence 不一致（實測該測試確實變紅）。因此我把 `attempts` 提到 `if` 之外、依 `align_formal_attempts` 分歧：**對齊時用 canonical，不對齊時維持合成名稱**。

**兩位 Reviewer 都獨立還原成「無條件 canonical」實測證實**（A：`AssertionError: RunVerificationError not raised`；B：`verify: VERIFIED ⇒ 原本 assertRaises 會失敗`），並確認全庫只有這兩個 caller。負向測試的保護原封不動。

#### 11.3 U 批 mutation（第 5 輪欠件）

```text
self-check null   -> SURVIVED tests=142 failures=0 errors=0     ← 控制組正確
self-check poison -> KILLED   tests=142 failures=7  errors=19   ← 控制組正確

killed U01_drop_the_message_timestamp_check        killed U05_drop_the_replay_event_pairing
killed U02_timestamps_allow_negative_values        killed U06_drop_the_replaced_attempt_check
killed U03_timestamps_never_checked                killed U07_drop_the_replay_event_ordering_check
killed U04_drop_the_canonical_numbering_check      killed U08_drop_the_replayed_history_check

integrity（工作樹全程唯讀）OK ×4     killed=8 survived=0 broken=0
```

### 12. 第 6 輪 Review：三方共識

| | Standards | Spec | 品味 | 新 Findings | 結論 |
|---|---|---|---|---|---|
| Reviewer A | **通過** | **通過** | 🟢 | **無** | **簽署 Ticket 03 通過** |
| Reviewer B | **通過** | **通過** | 🟢 | **無** | **簽署 Ticket 03 通過** |

#### 12.1 V1 的過嚴回歸風險：兩位都用真實狀態機驗證

Coordinator 指定的最大回歸風險是「精確規則會不會誤擋合法 run」（**過嚴比過鬆更難發現**）。兩位都不是人工拼 verifier fixture，而是用 `DebateStateMachine.relay()`／`_record()` 產生公開紀錄：

```text
單 attempt                              ACCEPTED [1,1,1,1,1,1,1]
a1 → a2                                 ACCEPTED [2,1,1,1,1,1,1]
a1 → a2 → a3                            ACCEPTED [3,1,1,1,1,1,1]
七席分別具有 1…7 個 attempts              ACCEPTED [1,2,3,4,5,6,7]
一席完全沒有公開訊息、attempt_ids=[]        ACCEPTED  state=missing
一席只有 position、state=provisional       ACCEPTED
```

兩位都確認精確規則與 `debate_state_machine.py:626/633` 的 producer 契約完全一致，未找到合法 producer 會產生其他命名的路徑。

#### 12.2 V2 相鄰性不過嚴

兩位都直接讀 `_record()`（`debate_state_machine.py:662`）確認：單一同步呼叫內先 append `replacement_replayed_public_history`、立即 append 首則 `seat_message`，**中間沒有 callback、await、yield、queue relay 或任何 entry 插入點**；驗證失敗會在進入 `_record()` 前寫 rejection，不會產生孤立 replay event。B 實測真實狀態機的 `replay index=34、message index=35、delta=1`。

#### 12.3 V3 無條件層不誤擋

型別矩陣（兩位一致）：

```text
True REFUSED｜False REFUSED｜0 ACCEPTED｜0.0 REFUSED｜Decimal('0') REFUSED｜"0" REFUSED｜缺欄位 REFUSED
```

舊 `RunController` 路徑實測 `event_fields=0 → VERIFIED`，既有相容性維持（舊紀錄沒有 `event="seat_message"`，不套用新欄位要求）。非 dict entry 由 `_read_jsonl` 先擋，不可達。

#### 12.4 Y13 裁定：兩位一致 `[建議]`

U 批是對第 5 輪程式跑的，V1 改寫後 U04 語意已變。兩位都不要求本輪補跑，理由一致：mutation 非驗收條件、U 批控制組正常、V1/V2/V3 皆有端對端與正反邊界功能證據。**兩位並各自以記憶體移除最終版 V1 精確檢查驗證保護存在**（A：`failures=9`；B：`tests=149 failures=15`，含 `b2`／`A2`／`a1x` 三種 near-miss 全部殺死該 mutant）。缺的是正式 harness 批次紀錄，不是功能證據。

### 13. 最終 Snapshot 與共識

```text
main @ 9b8a4510ec9406f19506e21d50af7918da2385d4（未 commit、未 git add）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
Ran 999 tests / OK / exit=0        （Developer 交件時為 992；差額為鏈 Y 第 15 輪持續增補）
本票四個測試模組：Ran 271～273 / OK
```

| 檔案 | numstat | sha256（測試前後三次一致） |
|---|---|---|
| `run_verifier.py` | 470/27 | `254afa9a…fbc4c` |
| `debate_driver.py` | 72/25 | `c4eb9034…cb2380` |
| `debate_state_machine.py` | 131/65 | `c5e11e00…2fc510` |
| `debate_rules.py`（untracked） | 498 行 | `f35f3aa4…0aa39` |
| `tests/test_verify_run.py` | 1094/0 | — |
| `tests/test_debate_driver.py` | 1094/29 | — |
| `tests/test_debate_state_machine.py` | 241/19 | — |
| `tests/test_reviewer_complete_attack.py` | 16/5 | Coordinator 授權改動 |

`report_contract.py`／`run_store.py`／`cli.py`／`run_controller.py` diff 為空；`live_dashboard 44/20`、`test_live_dashboard 6/5`、`test_vote_thresholds 12/7` 維持 Ticket 02 原值；兩位都確認未找到 U01–U08 或任何 mutation marker／anchor 殘留。

**共識**：Developer ＋ Reviewer A ＋ Reviewer B **三方共識達成**（Ticket 01 之後本輪第二張）。

### 14. 未解風險（兩位共同要求寫入）

| # | 項目 | 級別 | 說明 |
|---|---|---|---|
| **Y10** | 跨席全域 `elapsed_ms` 單調性未驗 | `[建議]` | 同席內生命週期先後、非負底線與 `[seal, stop]` 場次時間窗均已驗。兩位都構造出跨席時間反轉並取得 `VERIFIED`，但都判定它不改變任何席位自己的生命週期、票數或結論，且正式狀態機用同一 monotonic clock 串行 append 不會產生。可由後續 verifier hardening 處理 |
| **Y13** | V1 改寫後未留下完整正式 mutation 批次報告 | `[建議]` | 兩位各自以記憶體 drop-check mutant 確認精確規則有測試守護 |
| **Y9** | `_publish_position` 的 relay-success gate | 另票 | Reviewer B 曾指出完整修法應讓配對讀 machine／public record，而非在此多一個條件 |
| **Y3** | 藍燈映射 | Ticket 04 | 屬 `report_contract` 工作範圍 |
| **Y7／Y8** | 遷移風險限定「目前 Data Root 範圍內為零」 | 已裁定 | 不延伸宣稱外部備份 |
| **Y6** | 不宣稱封閉性 | — | mutant 全滅只代表這些形狀有測試守著 |

### 15. 執行環境與角色（最終）

| 項目 | 值 |
|---|---|
| Execution environment | WSL2 / Ubuntu-24.04，Python 3.12.3；`PYTHONDONTWRITEBYTECODE=1` |
| 基準版本 | `main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`（**未 commit、未 `git add`**） |
| Developer | Claude（平台原生臨時 Agent），識別 `ace13181273a93f38`，模型預設 reasoning effort |
| Reviewer A | Codex CLI `0.146.0`，`review_engine: native`，CLI 預設模型與 reasoning effort |
| Reviewer B | Codex CLI `0.146.0`，`review_engine: native`，CLI 預設模型與 reasoning effort |
| OCR Delegation | 未使用（本 Task 無 `settings_update: open_code_review`） |
| 輪次 | 6 輪 Developer 交付 ＋ 6 輪雙 Reviewer 複查 |

**雙 Reviewer 的實證效益（本票逐輪統計）**：

| 輪次 | 僅 Reviewer A 抓到 | 僅 Reviewer B 抓到 |
|---|---|---|
| 2 | `challenge_completed` 誠實性、`attempt_ids` membership | lineage 無序、tally enum |
| 4 | R3 逐席 elapsed 缺口 | — |
| 5 | V2 replay event 相鄰性 | V3 無 timeline 路徑 |

**四輪之中有三輪，其中一位抓到另一位完全沒看到的缺陷。** 若本票只派一位 Reviewer，至少四個 `[重要]` 會漏網。
