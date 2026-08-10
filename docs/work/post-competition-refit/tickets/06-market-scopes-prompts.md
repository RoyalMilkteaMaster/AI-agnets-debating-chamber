# 06 Phase 3b：market_scopes.json 語意注入（代號解析＋交易時段）

- 狀態：**完成**（第 2 輪達成三方共識，2026-08-06）
- Spec：`../spec.md`（Phase 3）
- Blocked by：05

## 目標

七席研究 prompt 依資產類別自動帶上正確語意：代號解析、交易時段、市場慣例。

## 使用者價值

問 2330 時七席知道那是台積電（2330.TW），不會拿週末沒行情當跌的證據。

## 範圍

1. 新增 `config/market_scopes.json`：各資產類別的語意提示（代號解析指引如 2330→台積電/TSMC/2330.TW、交易時段語意如台股週末休市／美股盤前盤後、常用可信來源類型提示）。
2. 經 `build_attempt_prompt` 唯一入口注入；辯論與報告 prompt 不注入（僅研究階段需要）。
3. 附 schema 驗證：載入失敗 fail-closed。

## 已確認實作決策

- 代號解析是「提示七席自行查證」，不是程式端維護對照表——不建立代號資料庫。
- 零外部套件；設定檔進 git。

## 驗收條件

- prompt 快照測試：tw_stock 題含台股語意段、us_stock 題含美股語意段、crypto 題含幣種語意段、開放命題不帶市場語意。
- 對 `market_scopes.json` 填非法結構→啟動被拒且指名欄位。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：`build_attempt_prompt` 純函式快照測試（既有 test_prompt_builder 模式）。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：三類 prompt 片段、測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：05
- Blocks：07

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

## 執行與 Review 紀錄

### 1. 開始執行

| 項目 | 值 |
|---|---|
| Execution environment | Windows 10 host ＋ WSL `Ubuntu-24.04`，Python 3.12.3；`PYTHONDONTWRITEBYTECODE=1` |
| 基準版本 | `main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`（**全樹未 commit**） |
| 開工實測基準 | `Ran 1080 tests / OK` |
| Developer | Claude 一般臨時 Agent（`claude-opus-5`，模型預設 reasoning effort） |
| Reviewer A／B | Codex CLI `0.146.0`，`review_engine: native`，**各自全新的隔離上下文** |
| OCR Delegation | 未使用 |
| 輪次 | 2 輪 Developer 交付 ＋ 2 輪雙 Reviewer 複查 |
| 併行 | 與 Ticket 07（鏈 Q）在同一工作樹併行，零檔案重疊 |

**Coordinator 的排程判斷（記錄在案）**：本票原標示 `Blocked by: 05`、且 Ticket 07 標示 `Blocked by: 06`。Coordinator 讀票後判定 **06 與 07 的檔案零重疊**（06 動 `prompt_builder.py` ＋ 新增 `config/market_scopes.json`；07 動 `run_store.py`／`codex_bridge.py`／`run_verifier.py`／`live_dashboard.py`），票上的 `Blocked by` 是排序慣例而非真依賴，因此改為併行。08 才是真依賴 07（它索引的就是 07 建立的目錄結構）。

**另一項判斷更正**：Coordinator 原先向使用者表示「標的選單會讓本票內容重寫，先攑起」。讀票後撤回——本票是**依資產類別注入研究語意**，而選單改變的是「`asset_class` 怎麼決定」，不是「給定 class 要注入什麼語意」。選單日後只會讓其中一項提示（代號解析）變得多餘，無害。

### 2. Developer 的範圍決定：票面字面指示會弄壞既有契約

票上寫「經 `build_attempt_prompt` 唯一入口注入」，但該函式住在 `real_provider.py`（**Developer 權限外**）。Developer 查證後改在 `prompt_builder._shared_section` 實作、以 `phase == "research"` 為閘，理由**不只是權限**：

> `codex_bridge.py:263` 也會直接呼叫 `build_seat_prompt(package, seat, "research")`，**只改 `build_attempt_prompt` 會讓三個 Codex 席與四個本地席的 shared prompt 不再 byte-identical**，違反既有契約。

**兩位 Reviewer 都裁定這個決定正確**，並**各自獨立找出全部五條 research prompt 路徑**：

```text
1. launcher._write_codex_prompts → build_attempt_prompt → build_seat_prompt
2. RealSeatRunner._prompt        → build_attempt_prompt → build_seat_prompt
3. codex_bridge.build_codex_handoff → 直接 build_seat_prompt   ← 完全不經過 build_attempt_prompt
4. 舊式 RunController._research      → 直接 build_seat_prompt
5. build_provider_prompt            → build_seat_prompt
```

**只改 `build_attempt_prompt` 確實會漏掉第 3、4 條。**

**但兩位都要求把契約措辭精確化**：真正存在的契約是「**基底 `shared_section` byte-identical**」，不是「每席完整 prompt byte-identical」。Reviewer B 的實測：

```text
七席 base shared_section 相異數 : 1        base 長度 : 3514
codex bridge 版長度            : 4400      差 +886      bridge 版以 base 開頭 : True
```

Developer 第 2 輪照改，docstring 現在明寫「That is deliberately narrower than 'every seat's finished prompt is identical', **which is not true and never was**」。

保證的來源：`test_all_seats_share_a_byte_identical_shared_section`、`test_codex_prompt_files_carry_the_byte_identical_shared_section`（raw bytes 比對）、`codex_bridge.py:268` 對三個 GPT 席不一致直接 fail-closed、架構 §6。

### 3. 第 1 輪 Review：六項 `[重要]`，其中三項是事實錯誤

| # | 內容 | 來源 |
|---|---|---|
| **P1** | 重複 JSON 鍵被 `json.loads` 靜默覆蓋（fail-open） | **兩位** |
| **P2** | 台股「4–6 位數字」＋「`2330.TW` 是正式代碼」 | **兩位** |
| **P3** | Crypto「任何一段時間內都應該找得到成交資料」 | **兩位** |
| **P4** | 「找不到當日行情**只代表**當天沒有交易」因果過度 | 僅 Reviewer B |
| **P5** | 區塊鏈**瀏覽器**一律當 Tier 1 原始鏈資料 | 僅 Reviewer A |
| **P6** | 沒有測試鎖住「非法設定在任何席位派工前中止 launch」 | 僅 Reviewer A |

**P6 是本票最重要的方法論發現**。Reviewer A：

> repo 中所有 `MarketScopesError`／`market_scopes` 測試都只在 `test_prompt_builder.py` **直接呼叫 loader**；`test_launcher.py` 沒有非法 market config 案例。**未來若 loader 被移到 `scheduler.start()` 之後，現有 39 例仍可能全綠，卻違反驗收條件 2。**

驗收條件 2 講的是**啟動被拒**（launch 層行為），但測試只覆蓋 loader 層。

**兩位一致裁定、不列為缺陷的三項**：
- **沒有偷渡代號對照表**。兩位給的界線一致：「`2330` 在某報價系統寫成 `.TW`」是**格式／供應商慣例**；「`2330＝台積電`」才是**實體身分對照**。現況落在前者。
- **`NVDA`／`DOGE` → `open` 拿不到市場語意**：屬上游 Ticket 05 已明文核准的輸入契約。Reviewer B 的理由最精準：**驗收條件同時要求「`us_stock` 帶美股段」與「`open` 不帶市場段」，裸 NVDA 歸 open 後不注入正是本票該守住的類別閘**；且本票明文禁止建 resolver，「不應由 Ticket 06 偷加 resolver 修正」。列為產品限制。
- **語意文字長度無上限**（20 萬／100 萬字元可載入）：B 列 `[建議]`、A 明確不列缺陷（票面無長度要求）。

### 4. 三個事實錯誤的共同形狀（Coordinator 歸納，寫入委派）

> 市場語意的失效模式是「把一條**通常成立**的慣例寫成**永遠成立**的斷言」。七席拿到絕對敘述後，會**把真實的資料缺口當成不可能事件**，進而補造或錯判證據。
>
> 寫法紀律：用「通常／常見／多數情況」＋「仍須向該來源查證」，避免「都是／任何／只代表／必然」。

**這一類缺陷測試永遠抓不到**——內容錨點測試只證明字串存在，不證明內容正確。Reviewer A 的總結：

> 內容錨點只能證明字串存在，**不能證明內容正確**；本輪事實 Findings 正好證明這個限制。

### 5. 第 2 輪：六項全數修正

#### 5.1 P1 — `object_pairs_hook`

`json.loads(text, object_pairs_hook=_object_without_duplicate_keys)`。**hook 對文件中每一個 object 都會執行，所以頂層與巢狀是同一條規則、不是兩段程式。** 專屬例外 `_DuplicateJsonKey` 攜帶 key 名，與「不是合法 JSON」分開報（重複鍵其實是合法 JSON，只是有歧義）。

**Developer 自我修正**：第一次寫的巢狀測試**紅得不對**（紅在 `缺少必要欄位：schema_version`，因為 `where` lambda 把文件截斷了）。重寫後三例才紅在同一個正確原因。`duplicated()` 內建 FP 防線：**未拼接的原文必須先載入成功**。

**兩位各自獨立注入十餘個位置驗證層級覆蓋**（頂層／`scopes` 層／四個欄位／底線註解鍵／array 內 object），全部具名拒絕，合法出貨檔仍正常載入。

#### 5.2 P2～P5 — 四處事實修正的結構性理由

先寫回歸測試、對**舊文字**跑出真 red，再改設定檔。

| # | Developer 的結構性理由 |
|---|---|
| **P2** | ①全稱量化「是 N 位數字」改成「常見為…**部分商品帶字母尾碼**」，把字母尾碼證券**納入而非排除**；②把**官方身分**與**供應商表示法**拆成兩個句子，`.TW` 不再冒充官方代號 |
| **P3** | 拆成**兩個獨立命題**：「週末不是休市理由」（保住票面使用者價值）＋「但資料缺口有其他真實成因」。**原句是拿前者當後者的證明** |
| **P4** | 原句是**無條件**豁免，會順手掩蓋資料品質問題。新句把豁免**附加前置條件**（已向交易所行事曆確認休市），豁免範圍從「永遠」縮到「已驗證的休市日」 |
| **P5** | 把「原始資料」與「索引服務」從同一個括號裡**拆開**，並給 explorer 一條**顯式的分級規則**而非一個等級 |

**Developer 主動處理同形狀的預防性修正**（兩位未列）：`us_stock` 的「代號**是** 1 到 5 個英文字母」→「**常見為**…部分商品另帶分隔符號與後綴」；「正常交易時段**是**」／「週末與美國假日休市」→ 加「通常」；並依 Reviewer A 建議補上盤前盤後範圍（**帶 hedge**：「常見約 04:00 至 09:30 與 16:00 至 20:00 ET，**實際範圍依 venue 與 broker 而異**」）。

**程式層共通告誡強化**（所有市場共用，住在程式不住在設定檔）：
> `…實際代號、名稱、交易時段與假期仍須以你查到的一手來源為準；查不到資料時先查明原因（休市、停牌、維護、代號有誤、資料來源故障或尚未上市），不得逕自當成價格沒有變動。`

**設定檔內寫入寫作紀律**（`_wording` 註解鍵，供日後編輯者遵循）：
> 「一律用『通常／常見／多數情況』並附『仍須查證』，不得寫成『都是／任何／只代表／必然』。市場語意的失效模式就是把通常成立的慣例寫成永遠成立的斷言。」

**Developer 誠實標示的界線（Coordinator 特別認可）**：回歸守衛**只釘住 Review 實際抓到的 5 個字串**，docstring 明說「這**不是**一份禁用詞窮舉表，也**不宣稱**能擋下所有絕對化寫法；新的絕對化說法仍然只能靠人審」。**它刻意沒有把中文絕對化語彙當成封閉集合來枚舉**——這正是 Ticket 05 磨了十二輪學到的教訓用對了地方。

#### 5.3 P6 — launch 層測試（未碰 `test_launcher.py`）

`launcher.py` 當時被 Ticket 07 佔用，Coordinator 要求「優先在不碰該檔的前提下達成」。Developer 做到了：新增 `MarketScopesLaunchRefusalTest` **寫在 `tests/test_prompt_builder.py` 內**、自備 launcher 注入接縫、全程離線（無 provider、無子行程、無牆上時鐘）。

三個測試含**FP 方向**：`test_shipped_config_still_lets_a_launch_reach_seat_dispatch`（合法設定必須走到 `runner_factory`，否則下面兩個拒絕證明不了是設定的錯）。

**Developer 中途修正**：FP 測試原本等 `runner_factory` 的 `AssertionError` 逸出，但 `run_launch` 對所有例外都回報 exit code（冷啟動不吐 traceback），改成**直接斷言 factory 被呼叫過**。

### 6. Mutation：M9 是 P6 存在理由的直接證明

```text
control-null   ran=47 fail= 0 -> SURVIVED ✅      control-poison ran=47 fail=23 -> KILLED ✅
M1~M7 全部 KILLED（同第 1 輪）
M8 允許重複 JSON 鍵 (新)   ran=47 fail= 4 -> KILLED
M9 非法設定 fail-open (新) ran=47 fail= 2 -> KILLED
```

**M9 把 `MarketScopesError` 吞掉回傳 `[]`**——兩位都各自重現，分布一致：

```text
Reviewer B：M9-loader-class ran=18 failures=0
            M9-all-non-launch ran=44 failures=0
            M9-launch-layer  ran=3  failures=2
Reviewer A：M9 full run=47 failures=2；M9 non-launch run=44 failures=0
```

**loader 層抓不到「launch 選擇 fail-open」這種錯誤；只有新增的 launch 層測試抓得到。**

**擊殺者比對**（證明測試對得起自己的名字，兩位都抽驗屬實）：M1 唯一 killer 是 phase 對名測試；M2 唯一 killers 是兩個 open 對名測試；M4 唯一 killers 是兩個 class loader 測試；M8 是三個 duplicate loader 測試 ＋ launch 層那一個；M9 只有兩個 launch 層測試。

### 7. 第 2 輪 Review：三方共識

| | Standards | Spec | 品味 | 新 Findings | 結論 |
|---|---|---|---|---|---|
| Reviewer A | **通過** | **通過** | 🟢 | 3 個 `[建議]` | **簽署 Ticket 06 通過** |
| Reviewer B | **通過** | **通過** | 🟢 | 2 個 `[建議]` | **簽署 Ticket 06 通過** |

**兩位各自獨立查證一手來源**（Coordinator 只要求「以自己的知識檢視、不必上網查證」，兩位都做得更多）：

| 修正 | Reviewer A | Reviewer B |
|---|---|---|
| 台股字母尾碼 | TWSE 證券編碼原則 PDF（含 L／R 尾碼） | TWSE 編碼原則 ＋ **`00631L` 官方商品頁** |
| 台股時段 | TWSE 交易制度 | TWSE 集中市場交易制度 |
| 美股盤前盤後 | Nasdaq ＋ **NYSE extended hours** ＋ SEC EDGAR | Nasdaq 官方時程 |
| explorer 性質 | Ethereum 官方文件 | Ethereum 官方文件 |

**三條驗收條件兩位獨立實測全過**：
1. `tw_stock`／`us_stock`／`crypto` research prompt 只帶自身類別語意；open、debate、vote 均不帶；七席 base shared section 相異數 = 1
2. 一般 schema 錯誤與**所有層級**重複鍵均 fail-closed 且具名，且在 research seat 派工前中止
3. 全套 `1150 / OK`（Developer 交件時 1135，差額 15 例為 Ticket 07 併行增補）；`test_prompt_builder` 17 → 39 → 47，零刪除

### 8. 最終 Snapshot

```text
main @ 9b8a4510ec9406f19506e21d50af7918da2385d4（未 commit、未 git add）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
Ran 1150 tests / OK / exit 0
```

| 檔案 | 累計 numstat | 最終 sha256 |
|---|---|---|
| `hoya_market_agents/prompt_builder.py` | +235 / −1 | `b61352bf…` |
| `tests/test_prompt_builder.py` | +549 / −1 | `85febfb6…` |
| `config/market_scopes.json` | 新增 **25** 行 | `e4d5aa12…` |

> Reviewer A 更正 Developer 摘要的「新增 26 行」為計數誤差，實測 `git diff /dev/null` 為 `25/0`。

**24 個禁改檔逐檔雜湊自檢全數未經 Developer 寫入**，含 `real_provider.py`（`build_attempt_prompt` 所在，mtime 全程維持 `2026-08-05 22:19:14`）。Ticket 07 併行檔的變更兩位都確認屬對方，未計入本票。

**共識**：Developer ＋ Reviewer A ＋ Reviewer B **三方共識達成**。

### 9. 未解風險

#### 9.1 已裁定接受

| # | 項目 | 說明 |
|---|---|---|
| **裸代號無市場語意** | `NVDA`／`DOGE` → `open` → 拿不到市場語意 | 上游 Ticket 05 的輸入契約（代號決定 target、題目措辭決定 market class、裸代號不猜市場）。**兩位都裁定非本票缺陷**。`NVDA 美股走勢` 或呼叫端明確傳 `asset_class` 時正確注入。**要改變此行為必須引入權威 resolver／registry——那是 Ticket 05 與本票都明文禁止的。** 兩位都指出合理替代是**由 UI 要求選市場類別**，而非在本票猜測 |
| **事實正確性的最終防線是人審** | 語意文字未由 Developer 逐條對一手來源查證 | 第 2 輪修正**採信兩位 Reviewer 的判定**（兩位另自行查證一手來源）。新文字已全面 hedge 並要求七席自行查證，但**若某條慣例本身寫錯，這道防線只能降低傷害、不能消除** |
| **絕對化守衛只涵蓋 5 個已知字串** | 不是禁用詞窮舉表 | 刻意不把中文絕對化語彙當封閉集合枚舉。新措辭仍需人工審查 |
| **語意文字與 prompt 無長度／token budget 上限** | 100 萬字元 label 可載入 | 票面無長度要求，兩位皆未列為缺陷。市場題 shared prompt 增加約 600 中文字 |
| **`_CACHED_MARKET_SCOPES` 不熱更新** | 首次載入後改檔不生效 | **與 Ticket 04 的 R1（`_CACHED_SCALE`）、Ticket 02 的 U3 同類——設定頁那張票（Ticket 11）必須一次處理三個** |
| **缺獨立 system preflight** | `system_preflight.py` 在權限外 | P6 已把 launch 層時點鎖死，但仍非「開跑前就檢查」 |
| **孤兒 run 目錄無 failed 標記** | 非法設定會留下部分產物 | 兩位查明實際殘留為：`question.json`、`.run-claim`、七席空目錄、空的 inbox `prompts/requests/results`。**沒有** Codex prompt、attempt artifact、runner factory 呼叫、看板、handshake。P6 測試已把這個狀態釘成**已知且被接受**，日後有人改動順序會立刻反映 |

#### 9.2 `[建議]` 級（兩位提出，記錄後順延）

| # | 項目 | 兩位的建議修法 |
|---|---|---|
| **「區塊鏈瀏覽器是第三方」概括過廣**（兩位） | explorer 的核心屬性是**索引／呈現介面**；營運者是第三方、基金會或專案方並不固定（例如 Solana Explorer 掛在協議官方網域） | 改為「區塊鏈瀏覽器**屬鏈外索引與呈現服務**，不等於鏈本身」。**兩位都確認不影響「不是鏈本身、不得預設 Tier 1」這兩條正確規則** |
| **「各交易所不同」仍是描述性絕對化**（僅 A） | 不同交易所可能採相同資金費率時點或相似規則 | 改成「**可能依**交易所而異」。A 評影響很低，後句已要求查各交易所規則 |
| **P6 註解把「零 research seat 派工」寫成「零訂閱消耗」**（兩位） | `launcher._write_proposition` 在市場設定載入**之前**呼叫 proposition adapter，實測 `proposition_adapter_calls=1`、`research_runner_factory_calls=0`，且會出現「命題撰寫失敗」降級警告 | 改寫為「**任何 research seat 被派工之前**」，或另行把 config 驗證提前到命題 adapter 之前 |
| **`_wording` 的適用範圍未明**（僅 A） | 該註解鍵的限制對象是「**描述性**市場慣例」，與規範性的「不得／一律」（來源優先政策）不是同一類 | 在 `_wording` 內明寫其適用範圍，避免後續編輯者誤把來源優先政策也 hedge 掉 |

> **Coordinator 註**：這四項都是文字與註解層級、兩位都確認不造成錯誤結論，故依結案判準順延。`config/market_scopes.json` 下次被動到時（很可能是**標的選單那張票**，因為選單會提供 `asset_class`）順手做掉成本極低。

### 10. 執行環境與角色（最終）

| 項目 | 值 |
|---|---|
| Execution environment | Windows 10 host ＋ WSL `Ubuntu-24.04`，Python 3.12.3；`PYTHONDONTWRITEBYTECODE=1` |
| 基準版本 | `main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`（**未 commit、未 `git add`**） |
| Developer | Claude 一般臨時 Agent，模型預設 reasoning effort |
| Reviewer A／B | Codex CLI `0.146.0`，`review_engine: native`，CLI 預設模型與 reasoning effort |
| 沙箱 | `--sandbox workspace-write --add-dir /tmp -c sandbox_workspace_write.network_access=true` |
| OCR Delegation | 未使用 |
| 輪次 | **2 輪** |

### 11. 雙 Reviewer 的實證效益（本票逐輪統計）

| 輪次 | 僅 Reviewer A 抓到 | 僅 Reviewer B 抓到 |
|---|---|---|
| 1 | **P5 explorer 分級**、**P6 缺 launch 層測試** | **P4「找不到行情只代表沒交易」因果過度** |
| 2 | 「各交易所不同」仍絕對化、`_wording` 適用範圍未明 | — |

第 1 輪六項 `[重要]` 中，**三項只有其中一位抓到**。P6 尤其關鍵——它不是內容錯誤而是**測試層級錯置**，若漏掉，日後把 loader 移到 `scheduler.start()` 之後會讓 39 個測試全綠地違反驗收條件。

### 12. 本票的方法論收穫

1. **票面的字面指示可能弄壞既有契約**。「經 `build_attempt_prompt` 唯一入口注入」若照字面做，會漏掉兩條路徑並破壞七席 shared prompt 的 byte-identical 契約。**正確處置是查證後改在真正的共同入口，並回報理由**——而不是照字面做或默默改權限外的檔案。
2. **內容型缺陷測試抓不到**。市場語意的正確性只能靠人審；內容錨點測試只證明字串存在。本票三個事實錯誤全程通過所有測試。
3. **絕對化是市場語意的特有失效模式**。把「通常成立的慣例」寫成「永遠成立的斷言」，會讓七席把真實資料缺口當成不可能事件。修法是**拆成兩個獨立命題**或**附加前置條件**，不是刪掉那句話。
4. **驗收條件講的是哪一層，測試就要打在哪一層**。P6 揭露 39 個 loader 層測試無法保證 launch 層行為；M9 mutant 是這件事的直接證明（loader 層 44 例全綠、只有 3 個 launch 層測試中的 2 個轉紅）。
5. **誠實標示守衛的界線比擴大守衛更有價值**。回歸守衛只釘 5 個字串並明說不是窮舉表——這讓後續維護者知道「這裡仍需人審」，而假裝窮舉會讓人以為已經安全。
