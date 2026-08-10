# 04 Phase 2b：燈號純票數制（藍綠黃橘紅＋兩條降級＋輿情豁免）

- 狀態：**完成**（第 3 輪達成三方共識，2026-08-06）
- Spec：`../spec.md`（Phase 2 燈號）；ADR 0003
- Blocked by：03

## 目標

燈號改為採納立場票數直接映射（7藍/6綠/5黃/4橘/<4紅），僅保留兩條來源降級，消滅現行 elif 級聯 bug。

## 使用者價值

「7 票藍燈、6 票綠燈、5 票黃燈、4 票橘燈、不足 4 紅燈」——票數與燈號一眼對上，報告端 bug 修掉。

## 範圍

1. `report_contract`：`CONFIDENCE_LEVELS` 改為 red/orange/yellow/green/blue；`confidence_cap` 改純票數基準＋降級①（獨立網域 <2 →降一級）＋降級②（引用非 tier 1/2 來源→降一級，social-macro 席證據豁免）；移除類別數級聯、30 天時效與其餘品質降級。
2. 燈號映射與降級開關由 `debate_rules.json` 提供。
   > **2026-08-05 使用者裁定追加**：Ticket 02 只在 `debate_rules.json` 預留燈號欄位結構、不搬入舊制值（Spec Phase 1 已同步修訂）。因此**本票同時承接「把燈號規則寫入設定檔」與「把 `report_contract.py` 的硬編消費端改為讀設定」兩項責任**，不只是改值。完成後 `report_contract.py:15-21, 113-169` 不得再是燈號映射與降級的第二來源。
3. 同步：`_validate_confidence`、`debate_driver.confidence_ceiling` 撰稿提示、`report_renderer` 樣式（新增藍色＋圖示＋文字，移除 yellow_green）、`report_audit_renderer`、`run_verifier`、報告 schema。
4. 輿情席識別以 `config/agent_roster.json` 的 social-macro 席位 ID 為準，不硬編。

## 已確認實作決策

- 「7 票但證據類別單一」拿藍燈是核准的行為（ADR 0003 記載代價）。
- 降級以「採納立場引用的證據」為判定範圍（與現制相同），豁免只排除輿情席貢獻的證據卡，不豁免整份報告。
- 紅燈語意不變：<4 票或流程失敗。

## 驗收條件

- fixture 矩陣：7/6/5/4 票→藍/綠/黃/橘；3 票→紅＋未達共識。
- 降級 fixture：7 票單網域→綠；7 票含低可信來源→綠；同樣低可信證據全部掛 social-macro 席→維持藍。
- 「7 票、類別單一但來源充足」→藍（舊 bug 情境不再誤降）。
- report.html 顯示藍燈含圖示與文字；yellow_green 不再出現於任何輸出。
- **燈號級別集合一致性**（2026-08-05 依 Ticket 02 Reviewer B 建議追加）：`debate_rules.json` 的 `confidence.light_scale` 內每個 `level` 值都必須屬於 `report_contract.CONFIDENCE_LEVELS`；不得缺少必要級別；`report_renderer`、`report_audit_renderer`、報告 schema 與 `run_verifier` 使用**同一個集合**。須有測試鎖定，填入未知級別字串（如 `grene`）必須被拒絕。
  > 背景：Ticket 02 的載入器對 `level` 只驗「非空且不重複字串」，未綁 enum。該取捨已由 Coordinator 核准，理由是綁 `CONFIDENCE_LEVELS` 會把整條研究管線拉進設定載入器。**一致性檢查的責任因此轉移到本票**——Ticket 02 實測 `[ACCEPTED] 未知level字串 -> configured=True`，本票接上消費端時必須把這個缺口關掉。
  >
  > **另一項轉移責任**（2026-08-05 Ticket 02 Reviewer B 第 4 輪指出）：Ticket 02 的載入器允許**單級** `light_scale`（例如 `[{min_votes: 0, level: "red"}]`），因為單級在通用結構上確實覆蓋所有票數且符合「非空時末級必須為 0」。**但這只代表 Ticket 02 的通用結構合法，不代表本票可以用它取代 ADR 0003 規定的五級出貨映射。** 本票必須鎖定出貨設定為 `7→blue／6→green／5→yellow／4→orange／0→red` 五級完整映射，並有測試確保缺級（例如少了 `blue`）會被拒絕。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：`confidence_cap` 純函式直測（既有 ConfidenceCapTests 模式擴充）。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：燈號矩陣測試輸出、報告渲染截圖或 HTML 片段、測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：03
- Blocks：05

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
| Execution environment | Windows 10 host ＋ WSL `Ubuntu-24.04`，Python 3.12.3；`PYTHONDONTWRITEBYTECODE=1` |
| 基準版本 | `main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`（**全樹未 commit**） |
| 開工實測基準 | `Ran 1007 tests / OK`（非交接稿的 999——鏈 Y 的 Ticket 05 期間仍在增補） |
| Developer | Claude 一般臨時 Agent（`claude-opus-5`，模型預設 reasoning effort） |
| Reviewer A／B | Codex CLI `0.146.0`，`review_engine: native`，CLI 預設模型與 reasoning effort，**各自全新的隔離上下文** |
| OCR Delegation | 未使用 |
| 輪次 | 3 輪 Developer 交付 ＋ 3 輪雙 Reviewer 複查 |

**檔案歸屬方法（Developer 自行提出，兩位 Reviewer 沿用）**：

> `git diff --quiet` 對禁改檔會因 Ticket 02／03／鏈 Y 的未 commit 變更而回傳 1，**不是有效的歸屬證據**。

整棵樹沒有 per-ticket commit，因此改用「**開工前後全樹逐檔 sha256 比對**」。第 2 輪起 Developer 另保存受影響檔案的完整副本，使 numstat 由推導值變為逐行量測（消除第 1 輪的 R5）。

### 2. 核心設計：詞彙與映射的切分

- `report_contract.CONFIDENCE_LEVELS` 擁有「**有哪些燈、誰比誰好、配哪個圖示**」——圖示、CSS class、Core 輸出 schema enum 都綁它
- `config/debate_rules.json` 擁有「**幾票對哪一級**」與降級參數

`confidence_cap` 因此只剩兩步：票數查階梯 → 逐條套設定檔的降級。**類別數 elif 級聯、30 天時效、致命反證、矛盾旗標全部刪除**（連同 `_lower`／`_is_stale`／`_parse_utc` 與 `datetime` import），本票的原始目標「消滅 elif 級聯 bug」由此達成。

### 3. 兩項移交責任的處置（Ticket 02 → 04）

| | Ticket 02 的狀態 | Ticket 04 的處置 |
|---|---|---|
| **A：燈號規則入設定檔** | 只預留欄位結構（`light_scale: []`／`downgrades: {}`）＋ `_example`，未搬入實際值 | 填入 ADR 0003 五級與兩條降級；`report_contract.py` 硬編消費端改為讀設定；**刪除 `_example`**（已成重複的第二份值） |
| **B1：`level` 未綁 enum** | 載入器只驗「非空且不重複字串」，實測 `[ACCEPTED] 未知level字串 -> configured=True` | **載入器行為原封不動保留**（維持葉節點性質），缺口在**消費端**關閉 |
| **B2：允許單級 `light_scale`** | 通用結構上合法 | 出貨設定鎖定五級完整映射，缺級／單級／空階梯／錯序全部在消費端拒絕 |

**載入器 vs 消費端實測對照**（本票的關鍵設計決定：不把整條研究管線拉進設定載入器）：

```text
未知level字串 grene    載入器=ACCEPTED(configured=True)  消費端=REFUSED -> 含未核准燈號：grene
缺 blue 級            載入器=ACCEPTED(configured=True)  消費端=REFUSED -> 缺少燈號：blue
單級 light_scale      載入器=ACCEPTED(configured=True)  消費端=REFUSED -> 缺少燈號：blue、green、yellow、orange
空 light_scale        載入器=ACCEPTED(configured=False) 消費端=REFUSED -> 缺少全部五級
藍綠順序顛倒           載入器=ACCEPTED(configured=True)  消費端=REFUSED -> 燈號順序必須由好到壞
ADR 0003 完整五級      載入器=ACCEPTED(configured=True)  消費端=ACCEPTED
```

**Developer 主動比票面多做一項並請求裁定**（第 1 輪 R11）：要求階梯順序必須等於 `CONFIDENCE_LEVELS` 反序。理由是「`confidence_cap` 沿階梯降級、`_validate_confidence` 沿 `CONFIDENCE_LEVELS` 比上限，兩條排序不一致時『不得高於上限』沒有唯一解釋」。

**兩位 Reviewer 都裁定接受，不算擴大需求**——「這是使原需求良定義的必要不變量；只驗集合、不驗順序會使同一份設定產生兩種相反排序」。

### 4. 「三個檔案不需改動」——兩位結論相反，Coordinator 裁定

票面範圍 §3 列了 `report_audit_renderer`、`run_verifier`、`debate_driver`。Developer 主張三者都不需改動，並以 `grep -in confidence` 零命中 ＋ bundle 端對端測試舉證。

| | 對 `report_audit_renderer` 的判斷 |
|---|---|
| **Reviewer A** | **不成立**。它不是「已共用集合」，而是**完全忽略 confidence**；傳 `grene` 渲染成功且與合法報告 byte-identical |
| **Reviewer B** | **成立**。「沒有讀取或呈現 confidence，因此不存在另一份燈號集合；**這是非消費者，不是不同步的消費者**」 |

**Coordinator 裁定採 Reviewer B 的結論**（驗收條件 5 的意圖是「不得有第二個真相來源」，非消費者不構成第二個來源），**但採 Reviewer A 的具體強化**——A 的顧慮未被消解：

> 日後若新增**只排除 `orange`** 的第二 allowlist，現有 bundle 矩陣（只有未知值／超上限／合法 blue 三個方向）未必轉紅。

因此要求把 verifier 的 bundle 測試從三個方向擴成**遍歷全部五級**，各自產生對應票數的真實 bundle。**兩人各對一半，兩邊都採。**

第 2 輪驗證強化有效——兩位各自在記憶體植入「只拒絕 `orange`」的第二 allowlist：
```text
A：subTest(level='orange') → ERROR，MEMORY_ALLOWLIST_RESULT tests=1 errors=1
B：accepted != red_audit，PARTIAL_ALLOWLIST_CAUGHT True
```

### 5. 第 1 輪 Review：三個 `[重要]` ＋ 一個假綠

| # | 位置 | 缺陷 | 來源 |
|---|---|---|---|
| **S1** | `report_contract.py:183` | 合法 `3/3/1` 未達共識仍被固定上限為 orange；**且宣稱涵蓋此情境的測試是假綠** | **僅 Reviewer B** |
| **S2** | `tests/test_live_dashboard.py:161` | 退場的 `🟡🟢` 仍被當成合法 live-state 輸出 | **僅 Reviewer A** |
| **S3** | — | bundle 矩陣未遍歷五級（見 §4） | **僅 Reviewer A** |
| **S4** | `report_contract.py:223` | `few_independent_domains.exempt_seat_ids` 具有反向效果 | **僅 Reviewer A** |

**S1 是本票最重要的發現**，Reviewer B 的證據：

```text
出貨 fixture no-consensus-3-3-1：TALLY={'bullish':3,'bearish':3,'neutral':1} valid=7
  DECLARED=orange  CAP=orange  完整 contract 通過 = True     ← 合法發布橘燈

Developer 的測試 test_three_valid_votes_are_red_even_without_consensus 使用不一致資料：
  只改 report，official votes 仍宣告 consensus/bullish
  → 完整 contract 拒絕（16 個問題）→ 該 bundle 根本不可能發布
  → 測試從來沒走過那條路徑
```

**S4 的反向語意**，Reviewer A 的實證：
```text
shipping_empty_domain_exemption => blue
domain_exempts_all_social_cards => green    ← 把來源充足的卡「豁免」掉，網域集合變空，反而觸發降級
```
對集合基數規則（獨立網域 <2）而言，**加入豁免反而讓降級更容易觸發**——一個叫「豁免」的旋鈕做出相反的事。

**兩位獨立以記憶體還原確認 Developer 的 R8／R9 處置正當**（這是 Coordinator 指定的兩個針對性假綠檢驗）：
```text
R8 還原被刪的舊測試 → AssertionError: True is not false，FAILED
   （它只斷言本票明確要改變的舊狀態，理應紅；新的三項測試涵蓋範圍更大）
R9 把兩處 blue 改回 green → 'red_audit' != 'accepted' ＋ FileNotFoundError
   （green 現在等於六票上限而合法，改成 blue 是維持原測試語意所必要，不是假綠）
```

### 6. 第 2 輪：修正 ＋ 三個結構性決定

**S1 的修法與 ADR 論證**。Developer 採「未達共識 → 採納票數為 0 → 階梯最底一級」，不採 Reviewer B 建議的「最大同立場票數」：

> ADR 0003 決策 1 的原文是「燈號＝**最終採納立場**的有效票數」。未達共識時沒有最終採納立場，可數的採納票數就是 0。用最大落敗集團的票數頂替，等於**替一個議場明確沒有採納的立場報告共識強度**——那是 ADR 想廢掉的「票數與燈號對不上」的另一種版本。

**兩位都確認忠於 ADR**，且此版**不需要第二個機制**——`_DIRECTIONLESS_CAP = "orange"` 這個程式字面值連同第 1 輪的 R3 一起消失，燈號只剩「查階梯」一條路。

**S1 的結構性修法：讓假綠不可能再發生。** 根因是「測試自己動手改半套 bundle」，因此新增 `report_fixtures.build_fixture(...)`——七席立場一份資料推導出 tally、有效票數、逐席報告列與證據引用。

**S4 的修法：讓那個狀態無法表達**（不是驗證它、也不是改名）：

> 豁免的意思是「這席貢獻的證據卡不受這條規則判定」，只有**逐卡判定**的規則說得通。`few_independent_domains` 數的是集合基數。與其加一條執行期檢查，不如把欄位拿掉：填了就是未知欄位，載入期就死。

`_DOWNGRADE_PARAMETERS`（rule→單一參數）改為 `_DOWNGRADE_FIELDS`（rule→自己的欄位組）。兩位各自試了 7～8 種注入方式（非空／空 list／`null`／空字串／巢狀／別名／駝峰／塞進別的欄位），全部載入期拒絕。

**S2 判定為純透傳，只改測試、程式零改動。** Developer 的依據：`live_dashboard` 全檔對 confidence 只有兩行 `.get()`，零 enum 零 allowlist；上游 `report_workflow` 寫入前已驗、`run_verifier` 讀取時再驗；在看板加驗證會製造**第二份 allowlist**——正是驗收條件 5 要消滅的東西。**與 §4 的裁定同一把尺。** 兩位都把值改回硬寫字串實測，測試仍過，確認沒有偷換測試目的。

**Mutation 逼出四個真缺口與一個死碼**：

| Mutant | 診斷 | 處置 |
|---|---|---|
| `N01`/`N02` | 沒有覆蓋「valid 但 `final_stance=null` 的偽造票面」——每列都比對成 `None`，會算出 **blue** | 新增測試 |
| `N03` | 沒有覆蓋「狀態說未達共識、卻填了採納立場」——`confidence_ceiling` 直接拿 votes.json 組骨架，**這形狀真的到得了** | 新增測試 |
| **`M11`** | **判定為等價變異體** | **刪掉該守衛**（死邏輯） |
| `S01`/`S02` BROKEN | 改 `_DOWNGRADE_FIELDS` 使出貨設定在載入期被拒 → child 誤報 BROKEN | harness 改為把 `DebateRulesError` 標成 `KILLED fail-closed` |

### 7. `M11` 等價變異體：Coordinator 指定的重點驗證

**刪掉活的保護比留下死碼嚴重得多**，因此 Coordinator 要求兩位獨立驗證等價性，特別是配上可載入的有洞階梯（`7/6/4/3/0`）。

**兩位都先把命題修正成正確形式**——要驗的不是「有效票與採納票永遠映射相同」（那當然不同），而是：

```text
light(有效票) == 最底一級  ⟹  light(採納票) == 最底一級
```

證明鏈：採納票 ⊆ 有效票 → `adopted_count ≤ valid_count`；載入器強制 `min_votes` 嚴格遞減；contract 強制燈號順序由好到壞；`_light_for` 因而對票數單調不減。

**兩位各自窮舉全部 35 種可載入五級門檻（含有洞階梯）**：
```text
Reviewer A：1260 組 adopted ≤ valid  →  red_implication_violations=0
Reviewer B：MONOTONIC_VIOLATIONS=0；valid light == red 但 adopted light != red：0
            （有效票與採納票映射不同的組合確實存在 840 個，但都不構成舊 guard 能額外保護的情況）
```

**兩位一致裁定：刪除正確。**

### 8. Developer 的過度宣稱與更正

Developer 第 2 輪寫「兩種修法在**所有合法 bundle** 上結果相同」。**兩位都找到反例，Reviewer B 的更硬**——不是假想階梯，是真的跑出來的 bundle：

```text
載入 initial=7／reduced=6／forced_stop=5，實際產生 4/3/0：
accepted / forced_stop_no_consensus / tally={'bullish':4,'bearish':3,'neutral':0}
confidence=red / verify_run=VERIFIED
→ 0 票法 red，最大集團法 orange
```

Reviewer A 的版本：可載入的 `7/6/4/3/0` 階梯下 `3/3/1` → zero=red、max-leader=orange。

**兩位都確認實作選擇仍然正確**（ADR 本身就是獨立理由，不依賴等價性），但措辭必須限定為「在出貨 `forced_stop=4` 下等價」。Developer 第 3 輪自行重現反例並改寫 `confidence_cap` 註解，同時把 `M11` 的註解從「等價變異體」改寫成兩位修正後的蘊含式。

### 9. 第 2 輪 Review：防假綠機制自己的假綠

Reviewer A 找到最後一項——**為了防假綠而建的 `build_fixture`，自己守不住宣稱的邊界**：

```text
docstring 寫「產物必然通過 validate_market_report」，但建構器從來沒驗過

Reviewer A：adopts_without_consensus → REFUSED    ← 推翻「必然通過」
            unknown_confidence       → REFUSED    ← 推翻「必然通過」
            wrong_adopted_stance     → report contract 竟然 ACCEPT
Reviewer B：BUILD_FIXTURE_BAD_ICON=REFUSED
```

而且守門測試只列舉 12 個預選案例，**對任意 `build_fixture` 呼叫零約束**。

**兩位對嚴重度分歧**（A 判 `[重要]`、B 判 `[建議]`）。**Coordinator 裁定採 A**，理由不是「A 比較嚴」，而是**這正是鏈 Y 的 Ticket 05 剛燒掉三輪的失效類別**——docstring 寫下一個聽起來對的宣稱、實作只做到一部分（該票的 R13／R15／R16 全是同一形狀）。修法便宜，沒有理由留著。

### 10. 第 3 輪：Developer 選路 A 並拒絕其中一個子選項

`build_fixture` 回傳前把成品送進 `validate_market_report`，通過才回傳；另拆出 **`build_forged_fixture`**（同樣的票面、不驗，名字就吵）。

**Developer 拒絕了 Coordinator 建議裡的「由票面推導 adopted stance」子選項並說明理由**：

> `consensus_with(3)`（3 票偏多 / 4 票偏空、報告宣告採納偏多）是「報告宣稱少數立場達成共識、上限必須拒絕獎勵它」的直接單元測試，推導版會讓這個形狀造不出來，違反「既有呼叫端行為完全不變」。

**兩位都以實際資料確認理由成立**：
```text
consensus_with(3): tally={bullish:3, bearish:4}, declared_adopted=bullish
                   majority_stance=bearish, cap=red, contract=True
→ 若自動改採多數 bearish，攻擊形狀消失，上限會變成 orange
```

**雙向表改成掃參數空間，不是列舉今天合法的呼叫**——這是對「守門只列舉 12 個案例」的正面回應。Coordinator 另指定驗證「會不會是空掃」，兩位的實測：

```text
合法且不高於上限：56 組（含取上限共 92 次建構器呼叫）
高於上限：22 組全拒        5 級 × 其餘四圖示：20 組全拒
4 種無方向狀態 × 3 種立場：12 組全拒

空掃防線本身也被測了：
  Reviewer A：zero_guard=RED
  Reviewer B：在記憶體令 range() 回傳空集合再跑原測試方法
              EXACT_TEST_EMPTY_SCAN_KILLED: 0 not greater than 50
```

**既有呼叫端行為不變（直接量測）**：Developer 開工前把 `consensus_with`／`no_consensus_with` 的 18 個組合輸出做正規化 JSON 雜湊，改完重跑比對逐一相同。兩位各自獨立驗證：
```text
Reviewer B：OBSERVATIONAL_EQUIVALENCE cases=18 mismatches=[]
```

**Mutation 證明新守衛承重**：
```text
Z01-builder-skips-its-own-contract-check   want:KILL  KILLED tests=219 failures=63
Z02-builder-validates-only-the-report-half want:SURV  SURVIVED   ← 等價改寫控制組
Z03-forged-builder-starts-validating-too   want:KILL  KILLED

as-expected=48 unexpected=0 broken=0 total=48
```
`Z01` 拿掉建構器自驗打紅 **63 個測試**；`Z02` 與 `N06` 兩個「應存活」控制組如預期存活，證明 `Z01` 的死不是模組壞掉造成的。

Developer 誠實標示：`Z03` 第一次跑是 `BROKEN SyntaxError`（自己的 replacement 括號不平衡），修正 anchor 後才是有效判決，第一次那個數字不算數。

### 11. 第 3 輪 Review：三方共識

| | Standards | Spec | 品味 | 新 Findings | 結論 |
|---|---|---|---|---|---|
| Reviewer A | **通過** | **通過** | 🟢 | **無** | **簽署 Ticket 04 通過** |
| Reviewer B | **通過** | **通過** | 🟢 | **無** | **簽署 Ticket 04 通過** |

**R15 的相依代價——Coordinator 指定裁定，兩位都明確表態接受路 A**：

`report_fixtures` 現在 import `report_contract`。兩位各自追了 import 圖確認無循環：
```text
report_fixtures → report_contract → {contract_validator, debate_rules, seats}
（下游均未反向 import report_fixtures）
```

並實測 Coordinator 擔心的 import 期副作用：
```text
Reviewer A：after_import  _CACHED_SCALE=None  _CACHED_RULES=None
            after build_fixture  tuple / DebateRules
Reviewer B：IMPORT_READ_TEXT_CALLS []   （攔截實際檔案讀取，不只看快取值）
            REPORT_CONTRACT_CACHE None  DEBATE_RULES_CACHE None
```
**import fixture 不會提前讀設定**，只有正式驗證建構器被呼叫時才載入。

兩位的裁定理由一致：建構器宣稱的正是「通過該正式契約」，**正式 validator 應是唯一真相來源**；另寫平行驗證會製造第二份契約，路 B 則無法關閉原 Finding。**Reviewer A 附加一個條件**：「現有直接 contract 攻擊測試仍須保留，以補足 R15 的同生共死風險。」

### 12. 六條驗收條件（兩位各自獨立實測，非採信 Developer 表格）

1. `7/6/5/4/3 → blue/green/yellow/orange/red`；**合法 `3/3/1`（七張有效票）與「總共只有 3 張有效票」兩種形狀都是 red** ✓
2. 7 票單網域 → green；7 票含低可信來源 → green；低可信全掛 social-macro → blue；豁免僅排除該席證據卡、不外溢 ✓
3. 7 票、證據類別單一、來源充足 → blue（兩位都讀 ADR 0003 確認是核准行為，非放寬規則）✓
4. `report.html` 有 `class="confidence blue"`＋`<strong>🔵</strong>`＋文字＋aria-label；Markdown 有 `🔵`；CSS 有 `.blue{color:#0b4a8f}`；`grep -rn "yellow_green|🟡🟢" hoya_market_agents config` **退出碼 1，零命中** ✓
5. 出貨五級完整映射；`grene`／缺級／單級／空階梯／錯序全部消費端拒絕；Core schema 直接使用 `list(CONFIDENCE_LEVELS)`；renderer 遍歷 enum 要求每級有 CSS；**五級真實 bundle 全部可達並通過 verifier** ✓
6. 全套 `Ran 1080 tests / OK / EXIT=0`，較開工基準 1007 增加 73，只增不減 ✓

### 13. 最終 Snapshot

```text
main @ 9b8a4510ec9406f19506e21d50af7918da2385d4（未 commit、未 git add）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
Ran 1080 tests / OK / EXIT=0
```

| 檔案 | 累計 vs HEAD | 最終 sha256 |
|---|---|---|
| `hoya_market_agents/report_contract.py` | 137/64 | `53b9f401` |
| `hoya_market_agents/report_fixtures.py` | 52/3 | `b3b59c3d` |
| `hoya_market_agents/report_renderer.py` | 3/3 | `f509e955` |
| `hoya_market_agents/debate_rules.py`（untracked） | 僅 docstring／註解 | `1f3dfb63` |
| `config/debate_rules.json`（untracked） | 46→43 行 | `dcb8d0ba` |
| `tests/test_report_contract.py` | 208/1 | `c389f58a` |
| `tests/test_report_validator.py` | 468/48 | `e9e050a0` |
| `tests/test_report_renderer.py` | 48/0 | `efd2755e` |
| `tests/test_debate_rules.py`（untracked） | +3 出貨鎖定、-1 過期 | `6ed0aa5e` |
| `tests/test_debate_driver.py` | 1361/36 | `5f14526e` |
| `tests/test_live_dashboard.py` | 17/7（Ticket 02 為 6/5） | `e76323de` |

**禁改／非消費者檔案逐檔雜湊確認未變**：`question.py`、`launcher.py`、`run_verifier.py`、`debate_driver.py`、`live_dashboard.py`、`report_workflow.py`、`report_audit_renderer.py`、`contract_validator.py`、`seats.py`、`cli.py`、`run_store.py`、`run_controller.py`、`config/agent_roster.json`、`test_reviewer_complete_attack.py`、`test_verify_run.py`、`test_vote_thresholds.py`、`test_debate_state_machine.py`。

**共識**：Developer ＋ Reviewer A ＋ Reviewer B **三方共識達成**。

### 14. 未解風險（兩位共同要求寫入）

| # | 項目 | 說明 |
|---|---|---|
| **R1** | `report_contract._CACHED_SCALE` 無公開 reload | 執行期改設定檔不會生效。與 Ticket 02 的 U3、**Ticket 11 的 blocker B1 同類**——設定頁實作時必須一併處理 |
| **R4** | 無 `seat_id` 的證據卡永不豁免 | 兩條規則都以「卡片的 `seat_id` 不在豁免名單」判定；缺欄位＝不豁免＝較容易降級（fail-closed）。Spec 未規定，是 Developer 的選擇 |
| **R6** | Mutation 只覆蓋 4 個快模組 | `test_debate_driver`（單跑約 6.5 分）未納入 harness，因此 **bundle 級的五級燈號測試不在 mutation 證據內**，只有功能證據 |
| **R7** | 不宣稱封閉性 | 48/48 只代表這些形狀有測試守著 |
| **R10** | 未跑 fixture launch 煙霧測試 | 票未要求；與 Ticket 02 的 X1 相同 |
| **R12** | 票數階梯可配置出「洞」 | `7/6/4/3/0` 這種設定可載入，`4→yellow`。屬設定檔可配置能力，出貨值有 `ShippedRulesTest` 精確鎖定；**兩位一致判定不列 Finding** |
| **R13** | 強停 bundle 的輪詢粒度 | `step_ms=30_000` 會讓落在 T+10 的 run 停在 `620000ms` 而被 verifier 拒絕。**兩位都查了產品端確認是測試替身假象**：`POLL_SECONDS = 0.25`、`DeadlineAlignedClock` 將 T+10 後 2 秒內觀測對齊為 `600000ms`（`600250 → 600000`、`602000 → 600000`、`602001 → 602001`）。若真實程序因系統停頓晚超過 2 秒，程式刻意保留真實遲到並由 verifier fail-closed，這是可觀測失敗策略 |
| **R14** | `build_fixture` 的保證止於「通過報告契約」 | 不保證「票面在語意上合理」——「六票偏多卻宣告採納偏空」仍建得出來，由 `run_verifier` 停止語意攔下（縱深防禦）。已寫進 docstring 與具名測試 `test_the_known_limit_is_semantic_plausibility_not_contract_validity`；**依裁定未擴大到改 report contract** |
| **R15** | `report_fixtures` 對 `report_contract` 的相依 | 無循環、無 import 期副作用（兩位都實測）。但**測試用的 fixture 模組從此與被測契約同生共死：契約若有 bug，建構器會一起接受錯誤形狀**。這是路 A 的固有代價，換來「不可能再產出半套 bundle」。**Reviewer A 附加條件：現有直接 contract 攻擊測試必須保留** |
| — | `docs/assets/feasibility/yellow-green.svg` | 歷史 feasibility 資產，無 enum，不進產品輸出。兩位都確認不動 |

### 15. 雙 Reviewer 的實證效益（本票逐輪統計）

| 輪次 | 僅 Reviewer A 抓到 | 僅 Reviewer B 抓到 |
|---|---|---|
| 1 | `🟡🟢` 殘留（S2）、bundle 矩陣未遍歷五級（S3）、豁免反向語意（S4） | **合法 `3/3/1` 仍是橘燈 ＋ 該情境的測試是假綠（S1）** |
| 2 | `build_fixture` 守不住宣稱（判 `[重要]`） | 等價性反例做成真實 VERIFIED bundle（`forced_stop=5` → `4/3/0`） |
| 3 | — | — |

**第 1 輪四個 `[重要]` 完全不重疊**——A 找到三個、B 找到一個，而 B 找到的那個（S1）是本票最嚴重的：一個真實會發生的停止形狀繞過票數映射，且宣稱涵蓋它的測試從來沒走過那條路徑。

第 2 輪兩位對 `report_audit_renderer` 得到**相反結論**，Coordinator 採 B 的結論 ＋ A 的強化，兩邊都採；對 `build_fixture` 的嚴重度分歧則採 A。

### 16. 本票的方法論收穫

1. **`git diff --quiet` 在多票未 commit 的樹上不是歸屬證據**——Developer 自己發現並改用全樹逐檔 sha256，兩位 Reviewer 沿用。第 2 輪起保存開工副本，使 numstat 由推導變為量測。
2. **「證明它們本來就共用」可以代替「改碼」，但要能證明測試鎖得住**——非消費者不構成第二真相來源（B），但涵蓋不足的測試矩陣會讓未來的部分 allowlist 溜過去（A）。兩者都成立。
3. **等價變異體要先把命題修正再驗**——「兩者永遠相同」是錯的命題，「valid=最底 ⇒ adopted=最底」才是被刪守衛真正需要的，而後者可窮舉證明。
4. **防假綠的機制自己會有假綠**——`build_fixture` 宣稱「必然通過契約」卻從未驗過；空掃防線要連「掃描量歸零時是否轉紅」都測。
5. **「讓錯誤狀態無法表達」優於「加一條檢查去擋它」**——S4 把欄位拿掉而非驗證它，R3 把 `_DIRECTIONLESS_CAP` 併回階梯而非留兩套機制。
