# 05 Phase 3a：拔白名單＋資產類別偵測＋命題主路徑

- 狀態：**完成**（第 17 輪達成三方共識，2026-08-06）
- Spec：`../spec.md`（Phase 3）
- Blocked by：04（**已由使用者授權改為與鏈 X 併行，見執行紀錄 §1**）

## 目標

任何標的（crypto 全幣種／台股／美股／開放命題）都能被接題，正反面命題自動訂定。

## 使用者價值

「幫我分析 2330 未來七天會不會漲，他也要幫我分析」——不再被五種幣鎖死。

## 範圍

1. `question` intake：移除 `SUPPORTED_ASSETS` 白名單；改為資產類別偵測（`crypto`／`tw_stock`／`us_stock`／開放命題），無法歸類走 open 模式，不 fail-closed 拒收。
2. `question_package`、`contract_validator` 及所有 import 白名單處同步；題型分類（single_asset_market_state／two_asset_comparison／event_impact／open_proposition）保留並適配新類別。
3. `open_proposition` 命題訂定（Core 以 codex exec 無搜尋產生正方／反方／無法決定詞彙）升為所有題型主路徑；失敗 fallback 題目原文（現制沿用）。
4. run_id 的資產 slug 生成適配非幣種標的（如 `2330`、`nvda`）。

## 已確認實作決策

- 研究仍由七席自行搜尋，本票不引入任何行情 API。
- 四套立場詞彙（market/comparison/event/open_proposition）保留；命題主路徑產生的正反面詞彙優先，訂不出來才用套版。
- 封存後禁搜尋等安全邊界不動。

## 驗收條件

- fixture：「2330 未來七天會不會漲」被接題、question.json 記 `tw_stock`、命題含正方＝會漲／反方＝不會漲語意、全流程跑通。
- fixture：DOGE（白名單外幣種）、NVDA（美股）、開放命題各一題跑通。
- 原五幣題目行為不變（回歸）。
- grep 全 repo 無殘留 `SUPPORTED_ASSETS` 引用。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：intake 純函式直測＋fixture launch。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：四類題目的 question.json 摘要、測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：04
- Blocks：06

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

### 1. 開始執行與依賴變更（Coordinator，2026-08-05）

- **Execution environment**：Windows 10 host ＋ WSL `Ubuntu-24.04`（Python 3.12.3）；command prefix `MSYS_NO_PATHCONV=1 wsl.exe -e bash -lc '...'`；專案路徑（WSL）`/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；來源 `source: auto_current`。
- **基準版本**：`main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`；工作樹既有 8 項＝7 項 Planner 產出 ＋ Ticket 01 的 `README.md`。
- **測試基準**：681 全綠。
- **開發角色**：Developer＝Claude 一般臨時 Agent（`claude-opus-5`，模型預設 reasoning effort）。
- **必跑指令**：`python3 -m unittest discover -s tests`（WSL）。

**依賴變更（使用者授權）**：本票原宣告 `Blocked by: 04`。使用者指示「可以併行的都併行，有順序的等順序；不限 developer 數量，只要不產生檔案衝突」，Coordinator 依實際檔案歸屬分析判定本票與鏈 X（02→03→04）無檔案交集，改為併行：

```
01 → [ 鏈 X：02→03→04  ∥  鏈 Y：05→06 ] → 鏈 Z：07→08 → 09 → [10 ∥ 11] → 12
```

**本票（鏈 Y）獨佔範圍**：`question.py`、`question_package.py`、`contract_validator.py`、`real_provider.py`、`prompt_builder.py`、`competition_drill.py`、`launcher.py`、新增 `config/market_scopes.json` 與對應測試檔。
**禁止碰鏈 X 的**：`debate_state_machine.py`、`debate_driver.py`、`report_contract.py`、`report_renderer.py`、`report_audit_renderer.py`、`report_workflow.py`、`report_fixtures.py`、`run_verifier.py`、`live_dashboard.py`、`debate_rules.py`、`config/debate_rules.json` 與其測試。
**兩鏈皆禁**：`cli.py`、`run_store.py`、`codex_bridge.py`、`codex_inbox.py`、`codex_exec_adapter.py`、`research_scheduler.py`、`seats.py`、`clock.py`、`system_preflight.py`、`recovery_state_machine.py`、`run_controller.py`、`fake_provider.py`、`provider_gateway.py`、`claude_adapter.py`、`antigravity_adapter.py`、`config/agent_roster.json`（唯讀）、`tests/test_reviewer_complete_attack.py`（跨鏈共管）。

**Coordinator 另行釐清的範圍邊界**：Ticket 範圍第 4 條「run_id 的資產 slug 生成適配非幣種標的」——經查 `asset_slug` 是 `question.py:87` 與 `question_package.py:70` 的 property（本票地盤），而 `new_run_id()` 與 run 目錄命名在 `run_store.py`（Ticket 07 地盤）。指示 Developer 只改前者；若發現 slug 契約變動會逼 `run_store` 跟著改，必須停止回報。**Developer 回報 `run_store.py` 未動，Coordinator 以 `git status` 確認屬實。**

### 2. Ready for Review（Developer，Snapshot 凍結）

**檔案歸屬（Coordinator 以 `git diff --ignore-cr-at-eol --numstat` 獨立核對）**

原始碼 6：`question.py`（199/93）、`question_package.py`（32/56）、`contract_validator.py`（14/7）、`real_provider.py`（16/7）、`launcher.py`（36/24）、`competition_drill.py`（9/12）
測試 7：`test_question.py`（134/44）、`test_question_package.py`（83/21）、`test_contract_validator.py`（8/2）、`test_contracts.py`（8/2）、`test_real_provider.py`（21/5）、`test_launcher.py`（111/13）、`test_competition_drill.py`（53/5）

`prompt_builder.py` 在獨佔範圍內但**不需改**——它透過 `to_dict()` 泛型消費 package。

**邊界事項與 Coordinator 裁定**

Developer 主動回報：另改了 `tests/test_cli.py`（11/4）與 `tests/test_run_controller.py`（8/2）。這兩個**測試檔**不在其允許清單，也不在禁改清單（禁改清單列的是 `cli.py`／`run_controller.py` 兩個**模組**）。它們未 import `SUPPORTED_ASSETS`（故不在 Coordinator 提供的消費點名單內），但直接斷言舊白名單行為（DOGE 題必須 exit 2／raise）；白名單移除後必然轉紅，不改就達不到驗收條件 5。Developer 只改測試、未碰生產模組，並明確請 Coordinator 裁示。

**Coordinator 裁定：可接受。** 理由：①那兩個測試斷言的正是 Ticket 05 明文要移除的行為（「無法歸類走 open 模式，不 fail-closed 拒收」）；②生產模組 `cli.py`、`run_controller.py` 零改動（`git status` 確認）；③Developer 揭露而非利用模糊地帶。此裁定已交 Reviewer 覆核，要求確認改動是「移除已不適用的舊斷言」而非「刪掉仍有效的保護」。

**變更摘要**

1. **`question.py` 重寫 intake**：刪 `SUPPORTED_ASSETS`。新增公開 `ASSET_CLASS_CRYPTO/TW_STOCK/US_STOCK/OPEN`、`ASSET_CLASSES`、`NON_ASSET_TOKENS`、`OVERALL_MARKET_SLUG`、`asset_slug_for()`；`QuestionScope` 新增 `asset_class`。標的辨識明文優先序：4–6 位數字碼（`2330`／`2330.TW`，後接年／月／日／元等單位則不算）→ 全大寫 2–6 字母代號（扣除 `NON_ASSET_TOKENS` 如 SEC／ETF／FED）→ 小寫或混大小寫只在句子標記它時才升格（比較配對，或緊接 `幣`／`股價`／`市場狀態`／`過去`／`未來` 等市場詞）。排序改為**首次出現順序**（白名單消失後即無正規順序）。
2. **類別判定**：台股訊號→`tw_stock`；美股訊號→`us_stock`；幣圈訊號→`crypto`；有標的但無任何市場詞→`crypto`（本產品預設市場，題目寫「美股／台股」即可覆寫）；完全無標的→`open`。
3. **`question_package.py`**：`asset_class` 隨 package 並進 `to_dict()`；`_open_proposition` 不再 fail closed；刪 `_asset_order`／`_SUPPORTED_ASSET_PATTERN`。四套立場詞彙與全部公開名稱原封不動。
4. **`contract_validator.py`**：`asset` 由 enum 改為非空字串；`_assets_problems` 只驗形狀（允許空陣列＝開放命題，不允許空字串元素）。
5. **`real_provider.py`**：EvidenceCard schema 的 `asset` enum 改為附說明的字串；`RealEvidenceGateway` 允許 `allowed_assets=()`（此時只綁 `run_id`），資產比對改大小寫不敏感。
6. **`launcher.py`**：命題訂定升為**所有題型主路徑**（移除 `question_type != OPEN_QUESTION_TYPE` 的 early return）；`_proposition_prompt` 改用 `_ballot_sides()` 取該題型自己的正反面詞彙；`question.json` 新增 `asset_class`；`_allowed_assets` 回傳 `package.assets`，不再回退白名單。
7. **`competition_drill.py`**：`FALLBACK_EVIDENCE_ASSET = "OVERALL-MARKET"`；對所有題型記 degraded 命題（消掉一個 if）。
8. **`run_store.py` 未動**；slug 由 `asset_slug_for()` 正規化為 `[a-z0-9-]`（`2330.TW`→`2330`、`BRK.B`→`brk-b`），為合法單一路徑片段，通過 `codex_bridge._require_safe_segment`。

**驗收條件證據**

**條件 1 — 2330 題** ✅ 真實 launcher（注入 seams，離線）`exit=0`，`run_id=20260314T015926Z-2330-abc123`，七席全數採用：

```json
{ "asset_class": "tw_stock", "assets": ["2330"], "period_days": 7,
  "question_type": "open_proposition",
  "proposition": "2330 未來七天股價將上漲。",
  "open_proposition": { "affirmative_means": "認為 2330 未來七天會漲。",
                        "negative_means": "認為 2330 未來七天不會漲。",
                        "proposition": "2330 未來七天股價將上漲。", "source": "codex" },
  "stance_labels": {"affirmative":"正方","negative_side":"反方","undecided":"無法決定"} }
```

全流程以 drill CLI 驗：`drill --provider-mode fake --question "幫我分析 2330 未來七天會不會漲" --data-root /tmp/t05-drill` → **`VERIFIED`**。

**條件 2 — 三類新題型** ✅

| 題目 | asset_class | assets | slug | launcher | drill |
|---|---|---|---|---|---|
| `DOGE 這個幣未來七天會不會漲` | `crypto` | `["DOGE"]` | `doge` | exit=0 | — |
| `分析 DOGE 過去 14 日市場狀態` | `crypto` | `["DOGE"]` | `doge` | — | `VERIFIED` |
| `NVDA 這檔美股未來七天股價會不會漲` | `us_stock` | `["NVDA"]` | `nvda` | exit=0 | `VERIFIED` |
| `聯準會九月會不會降息` | `open` | `[]` | `overall-market` | exit=0 | — |
| `幫我預測下週樂透號碼` | `open` | `[]` | `overall-market` | — | `VERIFIED` |

**條件 3 — 五幣回歸** ✅ `BTC 過去 14 日的市場狀態如何？` → `exit=0`、`run_id=20260314T015926Z-btc-abc123`（slug 不變）、`question_type=single_asset_market_state`（不變）、`assets=["BTC"]`（不變）、`stance_labels={偏多／偏空／方向不明}`（不變）。**唯二新增**：`asset_class="crypto"`、命題主路徑產生的 `proposition`／`open_proposition`。比較題／事件題／整體市場題的既有 drill 測試全部原樣通過。

**條件 4 — grep 零殘留** ✅ 程式碼／設定／測試零引用；只剩 `docs/` 的敘述文字（spec、本 Ticket、architecture.md §11.4、架構健檢 HTML、Ticket 02 執行紀錄）。

**條件 5 — 全套測試** ✅ Developer：`Ran 744 tests in 47.084s / OK / EXIT=0`（681 → 744，只增不減）。獨佔模組單跑 `Ran 227 tests / OK`。
**Coordinator 獨立重跑確認：`Ran 744 tests in 55.233s / OK / exit 0`**（744 ＝ 681 基準 ＋ 鏈 X 的 31 ＋ 本票的 32）。

**TDD 證據（五個垂直切片，red → green）**

| 切片 | Red | Green |
|---|---|---|
| 1 intake 類別偵測 | `ImportError: cannot import name 'ASSET_CLASS_CRYPTO'` → 實作後 1 fail（`2330.TW` 被拆出 `TW` 假標的）→ 修 lookbehind 加 `.` | `Ran 29 tests / OK`（原 18） |
| 2 question_package | `ImportError: cannot import name 'SUPPORTED_ASSETS'` | `Ran 60 tests / OK` |
| 3 contract／provider | 3 modules `ImportError` | `Ran 113 tests / OK` |
| 4 launcher | `ImportError` → 再 1 fail（`NameError: UNSUPPORTED_QUESTION`，舊拒收常數已無意義） | `Ran 207 tests / OK` |
| 5 competition_drill | `ImportError: cannot import name 'SUPPORTED_ASSETS'` | `Ran 20 tests / OK` |
| 全套收斂 | `FAILED (failures=3)` — `test_cli` ×2、`test_run_controller` ×1 斷言舊白名單拒收 | `Ran 744 tests / OK` |

測試只測公開行為（接題與否、類別、題型、slug、命題落地、五幣回歸），不耦合 regex 內部。

**Developer 回報的已知風險與待判斷事項**

| # | 項目 | Developer 立場 |
|---|---|---|
| Y1 | `question.json` 的 `open_proposition` 欄位名已誤導（現在所有題型都寫） | 保留原名；write-once artifact 既有欄位、全 repo 無讀者，改名屬本票未要求的 userspace 變更。建議於 Ticket 06／09 一併正名 |
| Y2 | 每次 launch 多付一次 codex exec（上限 60s），冷啟動變慢 | 命題升主路徑的直接代價；研究 T+ 時鐘在此之後才起算，**不影響任何 deadline**；失敗仍 degraded 不阻擋 |
| Y3 | **類別偵測是啟發式**：有標的但無市場詞→判 `crypto`；裸寫 `NVDA 未來七天會不會漲` 會被判 `crypto` | 核准判斷權限內的取捨，已寫進 `question.py` module docstring |
| Y4 | 小寫裸代號需市場詞才升格：`btc 過去 14 日…` 可以，`btc 會不會漲` 不行→落入無標的開放命題 | 白名單消失後小寫 `btc` 與小寫 `price` 形狀上不可區分，刻意保守 |
| Y5 | `RealEvidenceGateway` 對無標的題目只綁 `run_id`，資產邊界對開放命題放寬 | 否則剛接題的開放命題每張證據卡都會被拒；封存後禁搜尋等其他安全邊界未動 |
| Y6 | `_assets_problems` 允許空 `assets` 陣列 | 開放命題落地的必要條件；`report_contract.py`／`run_verifier.py` 未驗 assets，全套確認無回歸 |
| Y7 | `UnknownAssetError` 與 `inspect_question(..., allow_unknown_assets=)` 成惰性保留 | 依「不得破壞公開介面」指示保留，已在 docstring 標明為技術債 |
| Y8 | 自行加中文數字「日／天」期間解析（`未來七天`→7） | 否則主打題目會靜默落回 14 日；`未來幾天` 這類不可解析期間仍靜默預設 14（既有行為，未擴大） |
| Y9 | 刪除 `competition_drill.OPEN_QUESTION_TYPE` 重複字面常數 | 全 repo 無引用，`question_package.OPEN_QUESTION_TYPE` 才是權威 |

Coordinator 已於 Reviewer 契約中標明 **Y3／Y4／Y5 為本票風險最高三項**，並要求 Reviewer 自行設計對抗性 intake 案例（邊界代號、含數字中文題目、大小寫混用、多標的、含 SEC／ETF／FED 縮寫、`2330 元`／`2024 年` 這類誤判風險）驗證啟發式。

### 2.9 架構提問與裁定：intake 是否應拆成三個 per-market 模組（2026-08-06）

使用者於第 6 輪期間提問：既然市場詞碰撞反覆出現，把 intake 拆成美股／台股／虛擬貨幣三個獨立模組是否能避免撞車？

**裁定：不拆，維持 architecture.md §11.4 的單一「資產類別偵測」。** 使用者聽取分析後確認照原計畫執行。

依據——把本票第 4～6 輪的全部 Finding 攤開，**沒有一個是跨市場碰撞**：

| Finding | 碰撞的兩個東西 | 性質 |
|---|---|---|
| `defi` 命中 `definite` | crypto 市場詞 vs 英文單字 | crypto 內部 |
| `coin` 命中 `cointegration` | crypto 市場詞 vs 英文單字 | crypto 內部 |
| `nvda stock` → 標的變 `STOCK` | 美股市場詞 vs 標的位置 | 美股內部 |
| `TOKEN` 代號被市場詞排除 | crypto 代號 vs crypto 市場詞 | crypto 內部 |

拆成三個模組這些一個都不會消失，且會新增三項成本：

1. **需要新的仲裁層**。`COIN` 同時是 crypto 市場詞與一檔真實美股代號；現況由 `_TICKER_PATTERN` 的 explicit 判定（全大寫＝代號）一次解決，拆開後 crypto 模組與 us_stock 模組會各自主張所有權，仲裁規則仍是同一條，只是多繞兩層。
2. **雞生蛋**：per-market 模組的前提是「先知道是哪個市場」，但市場類別**本身就是 intake 的產出**而非輸入。三個 parser 全跑再合併，等於把一份結果變成三份待仲裁結果。
3. **切分軸錯誤**。真正的根因是**一份詞表同時服務分類／升格／排除三個用途，而三者需要的匹配強度不同**（見第 6 輪 Developer 的設計說明）。第 6 輪按「功能」切分正好對應此根因；按「市場」切分則與根因無關——`defi` 這個字串長什麼樣，跟它屬於哪個市場無涉。

**per-market 的拆分本就在計畫內，但在正確的層**：Ticket 06 的 `config/market_scopes.json` 依資產類別提供語意提示（台股代號解析與週末休市、美股盤前盤後、crypto 24/7）。語意提示天然是 per-market 的；文字剖析器天然不是。

### 2.95 使用者裁定：移除中文指示語路徑，改用 `$` 顯式語法（2026-08-06）

**背景**：本票五條驗收條件（2330 fixture、DOGE／NVDA／開放命題、五幣回歸、`SUPPORTED_ASSETS` 零殘留、全套測試）**全部不涉及中文指示語**。「`X 這個幣`／`X 這檔美股`」這條 reader 是第 5 輪之後由 Reviewer Finding 衍生出來的需求，不在 Ticket 原文範圍內。

**它耗掉四輪、三種方法全部失敗**：

| 輪次 | 方法 | 失敗原因（Reviewer 實證） |
|---|---|---|
| R9 | `_PHRASE_BREAK` 列終止字 | 中文名詞組後可直接接開放類謂語（`1INCH 這個幣值得買嗎`），封閉清單列不完；且 `的` 反向誤授權（`policy 這個貨幣的政策方向`），**兩個方向都失效** |
| R10 | `_NON_ASSET_HEAD_TERMS` 列非資產詞 | 開放式負面清單，兩位各自列出 19 個漏網（`幣值問題`／`股東會議題`／`stockholder議題`…） |
| R11 | `_reads_as_prose` 形狀判準 | 「不是全小寫」不等於「證明是 ticker」：`A`／`I`／`Xi`／`e2e`／`b2b`／`iPhone`／`eBay` 全部被收成標的 |
| R12 | 「就位」規則（未執行） | Reviewer A 定位真正根因：`_points_at_something_tradable()` 跳過真中心語、一路掃到後段市場詞。修法方向雖正確，但會使 9 個修飾語案例回歸 |

**根本原因**：判斷中文名詞片語的中心語需要 NLP／詞典，本票硬性限制零外部套件。

**使用者裁定（2026-08-06）**：移除整條指示語路徑，改用 `$` cashtag 顯式語法。

**依據的實測**（Coordinator 執行，不用指示語的情況）：

| 寫法 | 結果 |
|---|---|
| `幫我分析 2330 未來七天會不會漲` | `('2330',)` tw_stock ✓ |
| `BTC`／`btc`／`NVDA`／`DOGE`／`BRK.B` | 各自正確 ✓ |
| `比較 BTC 與 ETH 過去七天強弱` | `('BTC','ETH')` crypto ✓ |
| `BABYDOGE`（8 字元）／`1INCH`（開頭數字）／`F`（單字母） | `()` ✗ |

僅三種形狀認不到，改由 `$BABYDOGE`／`$1INCH`／`$F` 解決。`$` 開頭在自然語言中不可能是散文，**零誤判風險，不需要任何清單，不需要判斷中文詞邊界**。此方向為 Reviewer B 於第 11 輪主動建議（「未來另訂 `$DOGE`、`ticker:DOGE` 等明確語法」）。

**移除後結構性消失的缺陷**（兩位 Reviewer 歷輪提出的全部假標的）：`iPhone 這個產品對股票市場的影響`、`A 這個評級…`、`I 這個英文代名詞…`、`X 這個未知數…`、`Xi 這個名字…`、`e2e 這個測試概念…`、`b2b 這個商業模式…`、`eBay 這個品牌…`、`1INCH 這個編號…`、`1000SATS 這個數字…`、`X 這個股東會議題`、`X 這個幣值問題`、19 個 `policy／analysis 這個…` 複合詞、4 個跨子句案例——**全部不再可能發生，因為該路徑不存在。**

**已知代價**（使用者已接受）：`1INCH 這個幣值得買嗎`、`F 這檔ETF表現如何`、`X 這個幣之走勢`、9 個 `F 這檔…股票` 修飾語案例、`DOGE 這種幣`、`F 那支股票` 一律回傳 `assets=()`（退回開放命題，**不綁錯標的**）。使用者改寫為 `$1INCH`、`$F`、`DOGE` 即可。

**最終支援的標的寫法**：

| 標的類型 | 寫法 |
|---|---|
| 台股 | `2330`（含 `.TW`／`-TW` 等後綴） |
| 一般美股／幣種 | `NVDA`、`BTC`、`DOGE`（大寫 2–6 字母） |
| 五幣 | 大小寫皆可（相容 alias） |
| share class | `BRK.B`、`BRK-B` |
| **超長／數字開頭／單字母** | **`$BABYDOGE`、`$1INCH`、`$F`** |
| 比較 | `比較 A 與 B` |
| 開放命題 | 不寫標的 |

**Ticket 五條驗收條件不受此變更影響**，仍須逐條複驗通過。

### 2.96 Coordinator 實測更正：`assets=()` 的後果有兩條相反的路徑（2026-08-06）

Developer 自第 5 輪起反覆使用、Coordinator 也採信並據以裁定多輪的取捨論證是：

> 修飾語漏認 → `assets=()` → **開放命題、gateway 只綁 run_id、七席仍可自由研究** → **可回復**

Coordinator 直接讀原始碼實測後確認：**這句話只在其中一條 intake 路徑成立。**

| 入口 | 函式鏈 | `assets=()` 的實際結果 |
|---|---|---|
| `launch`（冷啟動主路徑）`launcher.py:137` | `build_question_package` → `inspect_question` | 落到 `_open_proposition()`，**開放命題，run 照跑** ✓ |
| 預設 CLI run `cli.py:246` → `run_controller.py:119` | `analyze_question` | 丟 `UnsupportedQuestionError` → `EXIT_REJECTED`，**run 完全不啟動** ✗ |

`analyze_question` 是 `inspect_question` 的嚴格包裝（`question.py:462`），空 assets 直接 fail closed；`RunController._approve` 還額外要求 `len(scope.assets) == 1`，所以**兩個標的的比較題在該路徑上一樣被拒**。

實測（`PYTHONDONTWRITEBYTECODE=1`，第 12 輪 Snapshot）：

```text
REJECT 'X 這支股'             題目未指名任何可辨識的分析標的；fail closed。
REJECT 'F 這檔科技股票'        同上
REJECT 'F 這檔科技股票未來如何' 同上
REJECT '台積電未來一週走勢'     同上
OK     'F 這檔股票'  -> ('F',) us_stock
```

**這對本票的裁定有什麼影響？**

- **取捨方向不變、且更硬**：漏認在兩條路徑上都是**當場可見**的（開放命題／明確的 `EXIT_REJECTED` 訊息，使用者立刻知道要改寫題目）；錯綁在兩條路徑上都是**安靜地跑錯**（slug 與 gateway allow-list 被污染，正確證據被拒，使用者看不出來）。因此「寧可漏認、不可錯綁」的既有裁定**維持**。
- **但論證的措辭必須更正**：不能再說「漏認一律退回開放命題」。Developer 若在 production docstring 寫了這句，需改為只描述 `launch` 路徑，或改述為「漏認的後果是可見的：開放命題或明確拒絕」。
- **`$` cashtag 方案必須兩條路徑都驗**：`$F` 在 `launch` 與 `cli run` 都要能解析成 `('F',)`，否則 `cli run` 那條路徑對單字母標的仍不可用。

`run_controller.py`、`cli.py` 均在本票**禁改清單**內，因此本項不要求本票修改它們，只要求 `$` 語法在兩條路徑上的行為都被測到並記錄。

### 3. Review 派工（Coordinator）

| 項目 | 值 |
|---|---|
| Reviewer A | Codex CLI `0.146.0`，CLI 預設模型與 reasoning effort，`review_engine: native`，session `019fd414-8273-7153-9270-a93bd9fe90b1` |
| Reviewer B | Codex CLI `0.146.0`，CLI 預設模型與 reasoning effort，`review_engine: native`，session `019fd23a-caa4-7882-8d91-a75ddb1be206` |
| 沙箱 | `--sandbox workspace-write --add-dir /tmp -c sandbox_workspace_write.network_access=true` |

**流程偏離與其撤銷（使用者指示，兩次）**：

1. 2026-08-05：使用者指示「之後都只派 Reviewer B，A 就先不派了，整輪作完都是這樣的設置」，理由為控制 token 消耗。Coordinator 說明後果後照辦。**第 6～10 輪為兩方複核。**
2. 2026-08-05（稍後）：使用者指示「你之後還是幫我把 Reviewer A 開啟好了，現在這樣開發我覺得太慢了」。**第 11 輪起恢復雙 Reviewer，回到三方共識。**

**恢復雙 Reviewer 的實證效益**（本票兩輪的 Finding 完全不重疊，各自抓到對方沒抓到的）：

| 輪次 | 僅 Reviewer A 抓到 | 僅 Reviewer B 抓到 |
|---|---|---|
| R11 | 中文裸子字串、跨子句 FP；**定位第 12 輪真正根因**（往後掃） | 價格／數量被當台股代碼；矩陣 19% 覆蓋率不足 |
| R12 | （見 §4） | **殘留形狀不是 2 個而是 18 個**，並證明那是開放式 prefix 類別 |

第 12 輪 Reviewer B 的結果直接推翻了 Coordinator 基於「只剩 2 個殘留」所給的建議——若當時仍是單 Reviewer，本票會在錯誤前提下結案。

OCR Delegation 未使用（本 Task 無 `settings_update: open_code_review`，`delegate_ready` 不為 true）。

Reviewer 契約中已載明的環境事實：①CRLF 陷阱（一律加 `--ignore-cr-at-eol`）；②工作樹同時含鏈 X 變更，只審本票檔案清單，不得把鏈 X 變更算成本票缺陷；③codex 沙箱預設 `--unshare-net` 會使 10 個 live_dashboard 測試假紅，已以 `network_access=true` 解除。

### 4. 第 12 輪 Review：使 A/B 路線定案的決定性證據（2026-08-06）

#### 4.1 背景

Developer 在 Coordinator 的第 13 輪指令送達前先完成了第 12 輪（「就位」規則：刪掉 `_points_at_something_tradable()` 的往後掃描）。結果比 Coordinator 預期好——**11 個歷輪假標的全部結構性修好**，且旗艦案例 `DOGE 這個幣`／`NVDA 這檔美股`／`1INCH 這個幣值得買嗎`／`F 這檔ETF` 全部保住。

Coordinator 因此暫停第 13 輪，向使用者提出 A（收在第 12 輪）／B（照原計畫第 13 輪）二選一，並**建議 A**。

Developer 在交付時主動補上一句保留：

> 那兩個殘留是我**目前已知**的形狀，**不是窮舉證明**。零 NLP 下我無法證明沒有第三個……使用者若把「零錯綁」當硬需求，第 12 輪不滿足。

**這句保留是對的，Coordinator 的建議是錯的。** 兩位 Reviewer 各自獨立、互不知情，一輪就證明「2 個」其實是開放集合。

#### 4.2 決定性證據

| | Developer 揭露 | Reviewer 另外找到 | 合計 | 明確表態 |
|---|---|---|---|---|
| Reviewer A | 2 | **24** | **26** | 「不足以結案，建議恢復第 13 輪」 |
| Reviewer B | 2 | **16** | **18** | 「不足以結案，建議採第 13 輪 cashtag 方案」 |

兩位都指出這是**同一個結構性類別**，不是漏列幾個字：只要複合詞以 `_ASSET_NOUNS` 成員開頭、而完整複合詞不在已知較長詞中，就會錯綁。涉及的資產名詞至少有 `幣`、`貨幣`、`代幣`、`股票`、`股價`、`資產`、`標的`、`美股`、`台股`、`港股`、`coin`、`stock`、`token`。

Reviewer A 的原話：「這證明問題是開放集合。」Reviewer B 的原話：「這是一個開放式 prefix 類別。」

**而且不限於單字母**——Reviewer A 證明正常散文一樣中：

```text
e2e 這個代幣化議題        => ('E2E',) crypto
b2b 這個資產管理問題      => ('B2B',) open
iPhone 這個股價淨值比概念  => ('IPHONE',) us_stock
eBay 這個股票市場案例     => ('EBAY',) us_stock
A 這個標的物概念          => ('A',) open
I 這個個股投資術語        => ('I',) us_stock
```

Gateway 後果兩位都實測重現，證明這不是純分類瑕疵：

```text
X 這個資產管理問題 => allowed_assets=('X',)
card X              => ACCEPT
card OVERALL-MARKET => REJECT
card SPY            => REJECT
```

Reviewer B 另補一個決定性論點——**第 12 輪保留下來的中文指示語已經沒有一致性，使用者無法用可理解的規則預測結果**：

```text
F 這檔股票      可用          X 這個幣        可用
F 這檔科技股票  不可用        X 這個幣值問題  錯綁
X 這支股        不可用
```

#### 4.3 第 12 輪確實修好的部分（兩位都逐字重現確認）

11 個往後掃案例全部 `()`：`iPhone 這個產品對股票市場的影響`、`A 這個評級…`、`I 這個英文代名詞…`、`X 這個未知數…`、`Xi 這個名字…`、`e2e 這個測試概念…`、`b2b 這個商業模式…`、`eBay 這個品牌…`、`1INCH 這個編號…`、`1000SATS 這個數字…`、`X 這個股東會議題`。

對照組全部保住：`1INCH 這個幣值得買嗎`、`F 這檔ETF表現如何`、`X 這個幣之走勢`、`DOGE 這個幣…`、`NVDA 這檔美股…`、`AAPL-HK 這檔股票…`、`F 這項資產未來如何`。

其餘關閉項目：`個股` 跨指示語接縫（`_splits_a_demonstrative` 的「只前進一字元重掃」行為兩位都驗證正確）、literal manifest（252／252，刪一筆會紅）、死抽象與 docstring 矛盾（`_NON_ASSET_HEAD_TERMS`／`_MARKET_WORD`／`_CLASS_MARKET_WORD`／`_GENITIVE_LINKER`／`_MODIFIER_BEFORE_ASSET_NOUN`／`_PHRASE_END`／`market word points`／`They promote` 全部 `rg exit=1`）。

Reviewer A 對「全小寫保守漏認」的裁定：**接受**（漏認可退回 open，沒有錯綁安全風險）。

#### 4.4 Reviewer A 獨有：money span 的雙向缺陷

這是第 12 輪**新引入**的缺陷，且**兩個方向都壞**：

過寬（吞掉真標的，且中的是核心五幣）：
```text
BTC$10000 會不會實現 => ()   ETH$5000 => ()   AAPL$200 => ()
```

過窄（不存在的標的＋錯類別）：
```text
BTC 漲到 AUD 10000 => ('BTC','AUD','10000') tw_stock   （CAD／CHF／SGD／KRW 同）
```

Reviewer B 另找到第三個方向：
```text
BTC 漲到 $10000 HKD => ('BTC','HKD')    （symbol amount 後空格再接 ISO code）
```

Reviewer A 的建議：`$` 的貨幣前綴不要用任意 `[A-Za-z]{1,3}`，限定實際支援的表達；分離的 ISO code 則涵蓋完整標準貨幣代碼集合，並加入雙向回歸測試。

#### 4.5 Reviewer B 獨有：數量修飾詞與 `這個幣` 分類

```text
公司有約 50000 股東       => ('50000',) tw_stock
公司擁有近 50000 股東     => ('50000',) tw_stock
公司共有超過 50000 股東   => ('50000',) tw_stock
公司有逾 50000 股東       => ('50000',) tw_stock
公司持有約 50000 股票     => ('50000',) tw_stock

這個幣值問題 => assets=(), asset_class=crypto   （移除「這個」後為 open）
```

B 明確指出：**刪除指示語 reader 不會自動解決這兩項**，第 13 輪必須一併處理。

#### 4.6 兩位對 Developer 證據數字的更正

| 項目 | Developer 宣稱 | 兩位實測 |
|---|---|---|
| 最大控制巢狀深度 | 2 | **3**（`_class_matches()` 的 `for → while → if`） |
| `assertGreaterEqual(..., N)` | 227 | 已落後於實際的 252 |

兩位都認定巢狀 3 未超過 code-review skill 的三層界線，未開 Finding，僅要求證據數字誠實。

#### 4.7 兩位的結論

| | Standards | Spec | 品味 | 阻擋 | 重要 | 結論 |
|---|---|---|---|---|---|---|
| Reviewer A | 不通過 | 不通過 | 🟡 | 0 | 2 | **待修正**，不簽署 |
| Reviewer B | 不通過 | 不通過 | 🟡 | 1 | 4 | **待修正**，不簽署 |

兩位都確認本票五條驗收條件的基本 fixture 通過（2330／DOGE／NVDA／開放命題／五幣回歸／`SUPPORTED_ASSETS` 零殘留／`944 tests OK` 相對 681 基準只增不減），但 Spec Review 仍不通過，理由是**錯誤 assets 會破壞 gateway 綁定**。

#### 4.8 Coordinator 裁定與派工

**採納兩位一致建議，恢復第 13 輪。** Coordinator 撤回自己「建議 A」的判斷——該建議建立在「只剩 2 個殘留」的前提上，該前提已被兩位獨立推翻。使用者對第 13 輪的核准（2026-08-06）依然有效，本裁定是回到使用者自己核准的計畫，不是新決策。

第 13 輪範圍：①刪除中文指示語**授權**路徑（保留分類用途）；②從零建 `$` cashtag reader；③money span 雙向修正；④數量修飾詞；⑤`這個幣值問題` 分類；⑥docstring 依 §2.96 正名兩條 intake 路徑；⑦`assertGreaterEqual` 更新為 252；⑧巢狀深度數字更正為 3。

**Coordinator 已標明本輪最大設計風險**：`$` 同時是 cashtag 標記與貨幣符號（`$F` vs `$10000` vs `BTC$10000` vs `NT$10000`）。此碰撞必須以一條明確可判定的規則解決並雙向測試——前十二輪的震盪全部來自「只修被回報的那個方向」。

Coordinator 實測的 `$` 現況基準（第 13 輪是從零建，沒有現成東西可靠）：

```text
$F 未來七天會不會漲    => ()          $NVDA 表現如何  => ('NVDA',)  ← 靠裸 ticker reader，與 $ 無關
$1INCH 值得買嗎       => ()          $BTC …          => ('BTC',)   ← 同上
$BABYDOGE 未來如何    => ()          $BRK.B 未來如何  => ('BRK.B',) ← 同上
$2330 未來七天會不會漲 => ()
比較 $F 與 $NVDA 過去七天強弱 => ('NVDA',)   ← 比較題掉一半
```

### 5. 第 13～17 輪：路線變更與結案

第 12 輪的雙 Reviewer 結論（§4）使 Coordinator 撤回「建議收在第 12 輪」的判斷，恢復使用者已核准的第 13 輪方案。以下是五輪的軌跡。

#### 5.1 第 13 輪：刪除指示語授權路徑、建立 `$` cashtag

刪掉 `_points_at_something_tradable()` 與整條「指示語＋資產名詞 ⇒ 授權命名」路徑。兩位確認刪乾淨：各自重跑 50 個／全部歷輪錯綁案例，`nonempty assets=0`；十個舊常數 grep 全零；`_asset_candidates()` 只剩台股數字碼、比較配對、cashtag、裸大寫 ticker、五幣 alias。

**`$` 的碰撞規則（Developer 先定規則再寫測試）**：

> `$` 後面那串內容決定它是什麼：含至少一個 ASCII 字母 → cashtag；純數字 → 金額。不看上下文、不看句子其他部分、不依賴哪個 reader 先跑。

**Developer 誠實標示一條未滿足的要求**：`$2330` → `()`，因為 `$2330` 與 `$10000` 在該規則下形狀完全相同、無法區分。它依本票一貫紀律（錯綁 > 漏認）選擇「數字視為金額」並請求裁定。**兩位 Reviewer 都明確接受此取捨**，並各自驗證沒有「裸寫救不到、又只能靠 `$`」的台股形狀（`2330`／`0050`／`006208`／`2330.TW`／`2330-TW` 全部正常）。Reviewer A 另發現 `$91APP` 可用而裸 `91APP` 不行，正好證明 `$` 的存在價值。

**但兩位都找到同一個新的 `[阻擋]`**：`$token` 沒有被原子消費——`_MONEY_PATTERN` 先占用 `$2330`，剩餘尾段被 bare ticker reader 撿走：

```text
$2330-TW => ('TW',)      $2330:AAPL => ('AAPL',)     $BRK_B   => ('BRK',)
$2330/TW => ('TW',)      $2330-2454 => ('2454',)     $1_INCH  => ('INCH',)
$ABC_DEF => ('ABC','DEF')                            17 個探針中 10 個錯綁、6 個漏認
```

**這同時推翻了 Developer 自己 docstring 的不變量**。Reviewer A 的原話：「這也證明實作仍依賴 reader 順序，與 docstring 不符。」

兩位並特別指出：這**不在** `$2330` 的可接受漏認範圍內——`$2330.TW` 含有已知交易所 suffix，不屬於那個不可消歧案例。

#### 5.2 Coordinator 實測更正 ①：C1～C4

Coordinator 在凍結 Snapshot 上直接跑 `inspect_question`，找到 Developer 未回報的四件事，其中 C1（`$2330-TW → ('TW',)`）與 C2（`.` 與 `-` 處理不一致）被兩位確認為同一根因，C3、C4 兩位都裁定不是缺陷。

**C3 的處置值得記**：Coordinator 曾指控 Developer「改測試去配合程式」（把 DOGE fixture 措辭從 `分析 DOGE 過去 14 日市場狀態` 改成 `DOGE 幣價…`）。**兩位逐項查證後推翻了這個指控**——舊輸入仍在 package test 與矩陣中，並明確斷言 `assets=('DOGE',), class=open`，沒有把失敗案例藏掉。兩位裁定：沒有 registry 就無法從裸符號推導市場，`open` 是 `architecture.md §11.4` 明定的誠實 fallback。**Coordinator 公開更正該指控。**

兩位並警告：若要「任意裸幣種自動判 crypto」，必須引入**權威 resolver／registry**，**不能再暗中恢復另一份白名單**。

#### 5.3 第 14 輪：三個開放集合的分類

Developer 修好原子化、貨幣集合、數量修飾詞。**Reviewer A 對「什麼是開放集合」做了一個很銳利的區分**：

| 項目 | 裁定 |
|---|---|
| cashtag 字元文法 | **封閉** |
| ISO 4217 代碼 | **封閉、有限、官方定義**，只是手工子集列不完整 |
| Unicode 貨幣符號 | 可用 **`Sc` 類別結構判定**，不必手列 |
| `MX$`／`MEX$`／`TT$` 地方前綴 | **真的開放**，且與 `BTC$10000` 有真實歧義 |
| 中文數量近似詞 | **真的開放**，與前十二輪已否決的失效模式相同 |

Coordinator 據此指出一條可判定的消歧規則：**前綴在 ISO 4217 內 → 金額；不在 → 前面那個是 ticker**。這不是枚舉開放集合，因為 ISO 4217 有權威邊界。代價（`MX$10000` 命名 `MX`）寫進 docstring。

#### 5.4 停損規則與其觸發

第 14 輪交付後，Coordinator 獨立實測發現：**數量修飾詞的枚舉從修飾語格搬到了動詞格**——Reviewer 的 7 個修飾語與 Coordinator 自己新想的 13 個全部修好，但 `公司募得／釋出／增資／配發／認購／申購／回購／質押／減資 50000 股` 九個全部錯綁。

使用者據此訂下停損規則：

> X1 修好、X3 若再度失敗 → 直接結案，X3 寫成已知殘留風險。不開第 15、16 輪追動詞清單。

Coordinator 裁定 X3 的殘留可接受，**依據是「`公司募得 50000 股` 不是市場分析題」**，並把這個依據交兩位 Reviewer 驗證。

**兩位各自獨立推翻了它**：

```text
台積電回購 50000 股是否有利未來股價？        => ('50000',) tw_stock
台積電增資 50000 股後會不會稀釋每股盈餘？      => ('50000',) tw_stock
蘋果公司回購 50000 股會不會推升股價？          => ('50000',) tw_stock
福特回購 50000 股後，股價會不會上漲？          => ('50000',) tw_stock
分析 tsmc 回購 50000 股後股價是否上漲
公司增資 50000 股後，每股盈餘與股價會如何變化？
分析 NVDA 回購 50000 股對未來股價的影響       => ('NVDA','50000')
2330 回購 50000 股是否有利未來股價？          => ('2330','50000')

gateway：allowed_assets=('50000',)
         2330／台積電／TSMC／F／OVERALL-MARKET 全部 REJECT
```

**庫藏股回購、增資、配發對股價與 EPS 的影響，是標準市場研究題。** Coordinator 的依據不成立，裁定撤回。

Reviewer A 另補一句 Coordinator 無法反駁的：**「未來會有選單」尚未降低目前 Snapshot 的錯綁風險。**

#### 5.5 使用者裁定：新增「明確標的參數」接縫（X5）

兩位都不建議再補動詞 regex。Reviewer B：

> 不建議開第 15 輪繼續補動詞 regex。最小結構解應是讓已核准的標的選單成為 authoritative assets，文字 parser 不再決定數字標的。

使用者裁定：**做那個接縫，然後結案。**

```python
inspect_question(question, assets=None, asset_class=None)
build_question_package(question, assets=None, asset_class=None)
run_launch(..., assets=None, asset_class=None)
```

- **有傳＝完全接管**：只做 `normalize_asset`，不增、不減、不改寫
- **沒傳＝行為零變化**：全部矩陣列再跑一次 `assets=None`（Reviewer B 在記憶體篡改一筆結果驗證該測試會紅，證明不是拿同一來源比自己的無效測試）
- **雙向證明**：`台積電回購 50000 股後股價會不會上漲？` ＋ `assets=('2330',)` → `('2330',)`，X3 的錯綁在此路徑上不存在
- **C3 一併解決**：`分析 DOGE 過去 14 日市場狀態` ＋ `asset_class='crypto'` → `crypto`
- **已知限制**：`cli.py` 在禁改清單內，本票只有 in-process 接縫，shell 呼叫端用不到

#### 5.6 第 15 輪：NFKC 與「等長不等於安全」

Developer 用 **Unicode 自己的 NFKC 對應表、只取一字元→一字元的答案**處理全形分隔符（不手列六個字元），並論證等長是設計出來的性質、fold 永不移動 offset。

**兩位各自獨立找到同一個 `[阻擋]`**——Reviewer B 一句話點破：

> **等長只保證 offset，不保證語意。**

```text
選項 ①②③④ 哪個較好？          => ('1234',) tw_stock
第ⅩⅤⅠⅠ章政策對市場的影響        => ('XVII',)
ⒶⒷⒸ方案會成功嗎               => ('ABC',)
測量值 ²³³⁰ 是否異常？          => ('2330',) tw_stock
₂₃₃₀ 未來如何                  => ('2330',) tw_stock
羅馬數字 ⅠⅤ 的意義？            => ('IV',)
ᵀᴼᴾ 未來如何                   => ('TOP',)
```

Reviewer A 量化範圍：3691 筆 fold 中，每個 ASCII 數字各有 10 個相容來源，另有 449 個來源會折成 ASCII 大寫字母。

**Reviewer B 給的修法仍是結構化的**：只收 decomposition tag 為 `<wide>`／`<narrow>` 的一對一映射，不收 `<font>`／`<circle>`／`<super>`／`<sub>`／`<compat>`。理由：**全形／半形是同一個字的排版變體；圈號、上標、數學體、羅馬數字是不同的字。**

兩位並各自找到 X5 的容器契約缺口，其中最嚴重的是 **set 造成不確定的 run_id**——Developer 後續以 `PYTHONHASHSEED=1..8` 實測，8 個程序產生 6 個不同的 run 目錄名稱。

#### 5.7 第 16 輪：Developer 自己抓到同族漏洞

Developer 採 B 的 tag 方案（fold 表 **3691 → 225**），第 15 輪 FN 案例逐一保住。**並且自己的測試抓到一個同族漏洞**：

> `𝟐𝟑𝟑𝟎` 收窄後仍被命名為標的——因為 **Python 的 `\d` 匹配全 Unicode 十進位數字**，台股 reader 直接收下數學粗體數字。同一原理也讓 `٢٣٣٠`（阿拉伯-印度）、`२३३०`（天城體）變成台股代碼。

**全部 30 處 `\d` 改為 `[0-9]`**，理由與 fold 同源。兩位都獨立追了執行順序，確認 `inspect_question()` 先建 folded `reading`、所有判定 reader 讀這份 view、raw `scope.question` 的消費者只有 artifact／prompt／report 顯示。

Developer 對「確定性測試」的判斷兩位也都接受：

> 跨程序漂移在單一程序內測不出來（set 順序在同程序內穩定），所以確定性測試不用單程序迴圈——**那證明不了任何事**。改為釘住真正的機制：給定的順序就是使用的順序。

#### 5.8 第 17 輪：驗證順序，以及「宣稱與實作不符」的第三次

**Reviewer A 找到最後一項 `[重要]`**：

```text
docstring 宣稱：非法字元 refused rather than sanitised
實際：驗證在 normalize_asset() 之後才做，Unicode upper() 把非法原字元洗成合法 ASCII

ß→SS   ı→I   ſ→S   ﬀ→FF   ﬁ→FI   ﬂ→FL   ﬃ→FFI   ﬄ→FFL   ﬅ→ST   ﬆ→ST
```

**這是同一種形狀的錯誤在本票第三次出現**：

| 輪次 | docstring 宣稱 | 實際 |
|---|---|---|
| 13 | 「不依賴 reader 順序」 | 依賴——money reader 先吃掉 `$2330`，殘片被撿走 |
| 15 | 「等長 fold 安全」 | 只證了 offset，沒證語意——造出 `('XVII',)`、`('2330',)` |
| 16 | 「非法字元 refused rather than sanitised」 | sanitised——`ß`→`SS`、`ﬃ`→`FFI` |

**三次都是「先寫下一個聽起來對的宣稱，然後實作只做到一部分」；三次都是 Reviewer 去驗那句宣稱本身、而非驗個別案例才抓到的。**

修法：對 `asset.strip()` 的**原文**套 `_RAW_IDENTIFIER_ONLY`，通過才正規化。Developer 指出一個關鍵細節：

> **`re.ASCII` 是必要的，不是裝飾**：單獨用 `re.IGNORECASE` 時 `[A-Za-z]` 會匹配 `ſ`(U+017F) 與 `K`(U+212A)——正是這道檢查要擋的那一類字元。

**三方各自窮舉掃描全 Unicode，都得到「`upper()` 改寫成 ASCII 的非 ASCII 字元恰好 10 個、ACCEPTED 0」。**

Developer 一度以為找到第 11 個（Kelvin sign），窮舉證明那是 shell 傳遞的假象並自我更正。**Reviewer B 進一步指出它寫下的原因不精確**：ASCII `K` 是 U+004B，`K`(U+212A) 的 `upper()` 仍是 `K`；真正的風險來自 `re.IGNORECASE` 的 Unicode matching，不是 `upper()` 轉 ASCII。結論（只有 10 個）正確，機制敘述已修正。

#### 5.9 「測試名稱比斷言強」——本票的第四類系統性缺陷

Coordinator 要求 Developer 全面檢查負向測試後，**它自己又找到一個**：

> `test_every_unicode_currency_sign_is_read_as_money` 只斷言 `assets == ("BTC",)`，但**金額規則與台股 reader 的 lookbehind 任一個成立都會通過**。現在直接斷言 `_MONEY_PATTERN` 匹配該符號＋數字，讓測試對得起自己的名字。

兩位 Reviewer 又各自掃到更多同類（列為 `[建議]`，見 §7）。這個模式在本票共出現四次：

1. NFKC 負向 probe 靠句尾全形 `？` 通過（Reviewer A 找到）
2. Unicode `Sc` 測試靠雙重保險通過（Developer 自己找到）
3. ISO 179 碼測試靠 `_CURRENCY_LOOKALIKE_PATTERN` 通過（兩位都找到）
4. `$` 原子性測試靠 ticker reader 的 `.` lookbehind 通過（Reviewer A 找到）

### 6. 第 17 輪 Review：三方共識

| | Standards | Spec | 品味 | 阻擋 | 重要 | 結論 |
|---|---|---|---|---|---|---|
| Reviewer A | 通過 | 通過 | — | 0 | 0 | **簽署 Ticket 05 通過** |
| Reviewer B | 通過 | 通過 | 🟡 | 0 | 0 | **簽署 Ticket 05 通過** |

**五條驗收條件（兩位各自獨立實測，非採信 Developer 表格）**：

1. `2330 未來七天會不會漲` → launcher exit 0、`assets=["2330"]`、`tw_stock`、`period_days=7`、`proposition=2330 未來七天股價將上漲。`、正方＝會漲／反方＝不會漲；drill 全流程 `VERIFIED` ✓
2. DOGE（`幣價` fixture → crypto）、NVDA（→ us_stock）、開放命題三類 launcher 與 drill 共 8 項全通過；裸 DOGE 走 `open` 是已記錄的輸入契約 ✓
3. 五幣 BTC／ETH／SOL／BNB／XRP 的 class、期間、題型、stances、labels、slug 全部維持原行為；小寫相容測試全綠 ✓
4. `hoya_market_agents/`、`tests/`、`config/`、`scripts/` 無 `SUPPORTED_ASSETS` 程式引用；只剩 planning／architecture 的「已移除該白名單」歷史敘述 ✓
5. 全套 `Ran 1059 tests / OK / exit 0`，相較 681 基準只增不減 ✓

**最終 Snapshot**：

```text
main @ 9b8a4510ec9406f19506e21d50af7918da2385d4（未 commit、未 git add）
本票改動 6 檔：question.py +1058/-95、question_package.py +53/-62、launcher.py +49/-26 ＋ 三個測試檔
question.py：1165 行 / 34 函式 / 最大控制巢狀 3 / 最長 _stated_assets 68 行（37 行 docstring ＋ 30 行程式碼）
跨輪矩陣 380 列／literal manifest 380／集合相等；記憶體刪一列必紅
邊界 64,464 探針 VIOLATIONS 0｜$ 原子性 448 探針 FRAGMENTS 0
全形對稱 152 探針 ASYMMETRIES 0｜normalize 冪等 320 組 0 違反｜fold 改變長度條目 0
import 0.121s（依 Reviewer B 意見維持單次掃描，未預先產生常數）
16 個共用禁改檔案 git diff --quiet 全部 exit=0
```

`run_store.py`／`cli.py`／`run_controller.py` 零 diff；`tests/test_reviewer_complete_attack.py` 的 modified 屬鏈 X（Ticket 03 授權改動），兩位都依指示排除。

**共識**：Developer ＋ Reviewer A ＋ Reviewer B **三方共識達成**。

### 7. 未解風險（兩位共同要求寫入）

#### 7.1 已由使用者或 Coordinator 明確裁定接受

| # | 項目 | 說明 |
|---|---|---|
| **X3** | 文字路徑會把股數錯綁成台股代碼 | `台積電回購 50000 股後股價會不會上漲？` → `('50000',)`，gateway 拒絕 2330 的正確證據。**兩位都證實這會出現在自然市場分析題**。四次詞彙嘗試（R9／R10／R11／R12）都失敗，計數動詞與後方數字的轄域無法純句法判定。**明確 `assets` 參數路徑不受影響。** production docstring 用真實回購題逐字記錄，三條錯讀釘進矩陣（`r15-buyback-misread-tsmc`／`-code`／`-nvda`），刪一條 manifest 會紅 |
| `TOP`／`ALL`／`XAU`／`XAG` | 緊鄰數字時讀為金額 | ISO 4217 與真實 ticker 的 namespace collision。方向是**安全漏認**：`assets=()` 時 gateway 只綁 run、仍接受 TOP 卡片。無鄰接數字時仍正確命名 |
| `$2330` | 讀為金額而非台股代碼 | 與 `$10000` 形狀完全相同、不可消歧。裸寫 `2330`／`0050`／`006208`／`2330.TW` 全部正常，兩位都驗證沒有「只能靠 `$`」的台股形狀 |
| `$10000-TW`／`$2330.US`／`MX$10000` | 前輪已議定取捨 | 分別為：交易所後綴授權 listing 讀法的代價、normalize 丟棄後綴由代號自身形狀決定、`MX` 不是 ISO code 故前綴視為 ticker |
| 圈號／上下標／羅馬數字／數學字母 | 一律不折 | 刻意的方向選擇（安全漏認優於造出假標的）。若有人真的用圈號寫代號會漏認 |
| 裸幣種 `asset_class` 為 `open` | 輸入契約 | 代號決定 target、題目措辭決定 market class、裸代號不猜市場。五幣 alias 是驗收條件 3 的相容性債。**要改變此行為必須引入權威 resolver／registry，不得暗中恢復白名單** |
| X5 無 CLI 旗標 | `cli.py` 禁改 | 本票只有 in-process 接縫；webapp／選單票會直接呼叫 `build_question_package`／`run_launch` |

#### 7.2 `[建議]` 級（兩位提出的具體修法，供後續票採納）

| # | 項目 | 兩位建議的修法 |
|---|---|---|
| **hostile `str` 子類** | 覆寫 `strip()` 的子類能讓原文驗證與正規化讀到不同內容。A 的示範：`LyingString("../etc/passwd")` 的 `strip()` 回傳 `"NVDA"` → 被接受為 `('../ETC/PASSWD',)`；B 的示範：底層 `ß` 但 `strip()` 回傳 `SS` → 接受為 `('SS',)`。**兩位都裁定 `[建議]`**，理由是這需要呼叫端已能注入自訂 Python 物件，且 slug 仍會路徑安全化 | 拒絕非 exact `str`，或用 base `str` 方法建立**一次不可變 snapshot**，後續空白檢查、raw regex、normalize 全部只讀同一 snapshot |
| **`asset_class` 自訂 equality** | `EqualEverything.__eq__ → True` 會被接受並原樣存入 `scope.asset_class` | 先驗證字串型別，再回傳 `ASSET_CLASSES` 中的 canonical 字串 |
| **ISO money 測試 proxy-green** | 停用 `_MONEY_PATTERN` 後 179 碼測試仍全綠（`_CURRENCY_LOOKALIKE_PATTERN` claim 同一 span） | 像 `Sc` 測試一樣直接斷言 `_MONEY_PATTERN` |
| **`$` 原子性測試 proxy-green** | 只略過 `$2330.ABC` 的 `_dollar_tokens` claim，`test_a_dollar_token_is_claimed_whole_whatever_it_says` 仍全綠（靠 ticker reader 的 `.` lookbehind） | 直接核對 `_dollar_tokens` 回傳的完整 span 與 verdict |
| **`MAX_ASSET_SLUG_BYTES = 205`** | token 長度以 32 保守高估（實際 production token 為 6，真正剩餘 231 bytes）。若 `run_store` 改用更長 token 需同步 | 推導來源已在 docstring；兩位都驗證 `16 + 1 + 205 + 1 + 32 = 255` 且 205 接受／206 拒絕 |
| **W1 防線的前提** | 「原文必須是 ASCII identifier」。若日後放寬 `_IDENTIFIER` 讓非 ASCII 進來，這道保證會一併失效 | `_RAW_IDENTIFIER_ONLY` 與 `_IDENTIFIER_ONLY` 共用同一份 pattern 來源（兩位都驗證 `pattern` 字串完全相同），正是為了讓兩者不會各走各的 |

### 8. 執行環境與角色（最終）

| 項目 | 值 |
|---|---|
| Execution environment | Windows 10 host ＋ WSL `Ubuntu-24.04`，Python 3.12.3；`PYTHONDONTWRITEBYTECODE=1` |
| 基準版本 | `main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`（**未 commit、未 `git add`**） |
| Developer | Claude 一般臨時 Agent（`claude-opus-5`，模型預設 reasoning effort） |
| Reviewer A | Codex CLI `0.146.0`，`review_engine: native`，CLI 預設模型與 reasoning effort |
| Reviewer B | Codex CLI `0.146.0`，`review_engine: native`，CLI 預設模型與 reasoning effort |
| 沙箱 | `--sandbox workspace-write --add-dir /tmp -c sandbox_workspace_write.network_access=true` |
| OCR Delegation | 未使用 |
| 輪次 | **17 輪** Developer 交付 ＋ 對應輪次的 Reviewer 複查 |

### 9. 本票的四類系統性缺陷（供後續 Ticket 參考）

這張票磨了 17 輪，缺陷可歸為四類，每一類都不是「寫錯一行」：

1. **枚舉開放集合**（R9／R10／R11／R12／R14 動詞格）——中文複合詞、中文數量近似詞、計數動詞都是開放集合，regex 收斂不了。**識別方法**：問「這份清單有權威邊界嗎？」ISO 4217 有（179 個，官方定義），中文副詞沒有。
2. **只修被回報的那個方向**（R4～R7 的震盪）——每次修好 FP 就冒出 FN，反之亦然。**破解方法**：強制**雙向失敗表**，每條規則同時列出 FN 與 FP 並各自寫測試。這條紀律導入後震盪立刻停止。
3. **宣稱與實作不符**（R13／R15／R16，三次）——先寫下一個聽起來對的 docstring 宣稱，實作只做到一部分。**破解方法**：Reviewer 去驗**那句宣稱本身**，而不是驗個別案例。
4. **測試名稱比斷言強**（四次）——測試名稱說 A，斷言卻只能證明「A 或 B 其中之一」成立，於是 A 回歸時被 B 遮住。**破解方法**：對規則本身直接斷言（例如斷言 `_MONEY_PATTERN` 匹配），而非只斷言最終輸出。

### 10. 雙 Reviewer 的實證效益（本票逐輪統計）

| 輪次 | 僅 Reviewer A 抓到 | 僅 Reviewer B 抓到 |
|---|---|---|
| 11 | 中文裸子字串、跨子句 FP；**定位第 12 輪真正根因**（往後掃） | 價格／數量被當台股代碼；矩陣覆蓋率不足 |
| 12 | 24 個額外殘留形狀；money span 雙向缺陷（`BTC$10000 → ()`，核心五幣被吞） | 16 個額外殘留形狀；尾接 ISO code |
| 13 | `$token` 未原子消費的**根因定位**；開放集合三分類 | `$BRK_B` 截斷、`$2330-2454`；`這個幣值` 分類 |
| 14 | `$2330-TW` 等 17 探針；`TOP`／`ALL`／`XAU` 同形代號 | 小寫 ISO 碼**錯綁**（非漏認） |
| 15 | NFKC FP（`①②③④`／`ⅠⅤ`／`ᵀᴼᴾ`）；X5 重複標的與 run ID 長度 | NFKC FP（`ⅩⅤⅠⅠ`／`ⒶⒷⒸ`／`²³³⁰`）；folded intake 與題型判定不一致 |
| 16 | **驗證順序 `[重要]`**（`ß`→`SS`）；防假綠測試自己的假綠 | `FlipList` 子類；`UnknownAssetError` docstring 失真 |
| 17 | `$` 原子性測試 proxy-green | ISO money 測試 proxy-green；Kelvin sign 機制敘述不精確 |

**七輪之中每一輪，兩位都各自抓到對方沒看到的東西。** 第 12 輪尤其關鍵：Coordinator 基於「只剩 2 個殘留形狀」建議結案，兩位一輪就把它打成 26 個與 18 個，若當時只派一位，本票會在錯誤前提下結案。

### 11. Coordinator 在本票的三次錯誤與更正（記錄在案）

1. **建議收在第 12 輪**——依據「只剩 2 個殘留形狀」，被兩位以 40 個反例推翻。Developer 交付時主動標示「這是目前已知的形狀，不是窮舉證明」，那句保留是對的。
2. **指控 Developer「改測試去配合程式」**（C3）——兩位查證後推翻，舊輸入仍在測試與矩陣中並明確斷言 `class=open`。Coordinator 公開更正。
3. **裁定 X3 動詞格殘留可接受**——依據「不是市場分析題」，兩位各自構造出庫藏股回購／增資的自然市場題推翻。Coordinator 預先訂下了「若能構造反例就重新裁定」的條件並依約撤回。

三次的共同點：**Coordinator 用推論代替實測**。第 3 次因為事先把推論的依據明確交給 Reviewer 驗證，所以錯誤在造成損害前被攔下。
