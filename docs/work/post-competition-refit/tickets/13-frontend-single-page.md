# 13 前端回到一頁式辯論室（使用者指示）

- 狀態：已核准（使用者於 09～12 結案後直接指示）
- Blocked by：09、10、11、12（皆已結案）

## 使用者原話

> 現在改得太糟糕了 還不如我原版的前端 你用我原版前端的格式去做微調就好了 多給他一個設定的頁面 讓要調東西去裡面調 然後我全部都要繁體中文 別給我一堆看不懂的英文

> 回到一頁式 然後我當初不是右上角就有報告跟紀錄可以看嗎? 這些我全都要保留 你就在右上角還是哪裡多一個設定頁 讓使用者調就好了 **辯論框才是我的專案主題 其他都是衍生的 我要我所有原本辯論框的規則**

> 先放到另一頁吧 就是命中率 另外再開一頁給他的小框 不要放在我的主頁 我東西夠多了

> 我要這兩份離線報告 是我分析來 他都會生出來給我 **我可以直接複製給別人的版本**

## 目標

把網頁前端從「五個各自很乾淨但資訊很稀的頁面」改回**原版的一頁式即時辯論室**，並新增設定頁與命中率頁。全繁體中文。

## 原版在哪

Ticket 10 退役了 `live_dashboard.py`，但它在 git HEAD 裡。已取出備份：

```
/tmp/orig-frontend/live_dashboard.py        783 行（含 HTML／CSS／JS）
/tmp/orig-frontend/test_live_dashboard.py   856 行（52 個案例）
```

也可自行 `git show HEAD:hoya_market_agents/live_dashboard.py` 取得。

## 範圍

### 一、主頁：一頁式即時辯論室

照原版版面重建（原版 `_LIVE_HTML`，第 709 行起）：

```
頁首      eyebrow「HOYA BIT 即時研究流程」＋ h1「即時 Agent 辯論室」＋ 題目
          右上角：分頁列 ＋ 連線狀態
run 列    目前 run ＋ 歷史 run 下拉 ＋ 回到目前 run
焦點列    領先立場 ＋ 票數 ＋ 信心燈號 ＋「查看下一規則」
四個計時  十五分鐘剩餘時間 / 報告期限剩餘時間 / 目前階段 / 目前共識門檻
主版面    公開辯論直播（含「有新發言 ↓」跳轉鈕）│ 側欄：即時票數 ＋ 七席研究 Agent
摺疊區    規則與時間線 / 票數變化 / 可驗證證據
```

**加上提問輸入**（Ticket 10 的功能，原版沒有）——放在頁首題目附近。

原版的語意配色一併沿用：`--bull` `--bear` `--neutral` `--brand` `--ink` `--paper` `--wash` `--line` `--muted`。

### 二、右上角分頁

```
網頁前端    即時辯論 │ 市場報告 │ 完整辯論 │ 設定 │ 命中率
離線 bundle 市場報告 │ 完整辯論                    ← 只有這兩個
```

`市場報告`／`完整辯論` 連到該 run 的 `report.html`／`debate.html`，沿用原版的 `aria-disabled` 行為（尚未產生時停用）。

### 三、設定頁

Ticket 11 已做好，改成原版視覺語彙。行為不變：驗證走 02 的載入器、原子替換、run 進行中鎖定、下一個 run 生效。

### 四、命中率頁

Ticket 12 已做好，**獨立一頁**（使用者明確要求不要放主頁）。改成原版視覺語彙。

### 五、全繁體中文

`tw_stock`→台股、`us_stock`→美股、`crypto`→虛擬貨幣、`open`→開放題、`green`→綠燈、`hit`→命中、`miss`→未命中、`pending`→待驗證、`unverifiable`→不可自動驗證。

**標籤必須從權威帶出來，不得在前端建第二份詞彙表**：資產類別看 `config/market_scopes.json`，燈號看 `report_contract.CONFIDENCE_LEVELS`，立場看原版的 `_stance_labels`（每種題型有自己的語彙）。

權威沒有涵蓋的值（例如 `market_scopes.json` 沒有 `open`）**要有明確的處理方式並說明**，不要靜默 fallback 成英文原值。

## 硬約束

### A. 不得重蹈 B1 花十三輪防的那個缺陷

原版第 53 行是：

```python
RULES = rules_for()          # ← 模組層，import 當下就把規則凍住
```

**照抄會讓設定頁存檔後規則不生效**——那正是 B1 的稽核工具在防的東西，而 `tests/test_debate_rules.py` 會直接抓到。

規則的**值**一律走現在的 reload-aware API 讀；只有**版面與呈現**照原版。

### B. 不得破壞 09～12 剛以三方共識結案的性質

- webapp 內**零 SQL**，唯一取數路徑是 `run_index.query_runs`
- run artifact **唯讀**（唯二寫入：launch 起子程序、stats 寫 `outcome.json`）
- **CSP 逐 directive 釘死**；只有需要 JS 的頁面用寬鬆那份，且**零 inline script**
- **對比度是 `ContrastTest` 的斷言**——原版的配色搬過來要實際量並達到 WCAG AA，數字報出來
- **無殘留執行緒**
- **頁尾誠實**：會寫檔的頁面不得沿用唯讀頁尾
- 價格守衛、seam 掃描、`_fetch` 的單一出口契約**一律不得動**

### C. 離線 bundle 的導覽不得回到原版寫法

原版的 bundle 導覽有 `<a href="live.html">即時辯論</a>`，**而 `live.html` 從來不在 bundle 裡**。剛結案的 `run_verifier` 已加上「導覽裡每個相對連結目標都必須真的存在」的檢查。

**使用者明確說這兩份是要複製給別人的版本**，所以離線 bundle 的導覽只能有 `market報告`／`完整辯論` 兩個真實存在的檔案。

**`report_renderer.py`／`report_audit_renderer.py`／`run_verifier.py` 本票不得修改**——使用者說那兩份離線報告現在就是他要的。

## 驗收條件

1. 主頁一頁看完：題目、提問輸入、run 下拉、焦點列、四個計時、聊天室、即時票數、七席、三個摺疊面板。
2. 右上角五個分頁；`市場報告`／`完整辯論` 在該 run 尚未產生時為停用狀態。
3. 設定頁改規則 → 存檔 → **新 run 的狀態機行為跟著變**（沿用 Ticket 11 的端到端驗收）。
4. 命中率為獨立頁。
5. **畫面上沒有英文資料原值**（實際渲染後 grep 驗證）。
6. `python3 -m unittest discover -s tests`（WSL）全綠。

## 測試與證據

- 必跑指令：`T08_INTERCEPT_LOG=<私有> PYTHONPATH=/tmp/t08-intercept PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`
- 必交證據：渲染後 HTML 存檔＋關鍵元素斷言（環境無瀏覽器，**不得假稱有截圖**）、對比度實測數字、繁體中文 grep 驗證、變更摘要
- 保存位置：本 Ticket 的「執行與 Review 紀錄」

## 原版 52 個案例的處置

Ticket 10 退役時盤點：28 條行為保留已搬家、3 條部分保留、**21 條行為隨模組消失**。

使用者說「**我要我所有原本辯論框的規則**」，所以那 21 條要逐條盤點，判斷哪些要回來：

- 規則與時間線面板、倒數、封存里程碑 → **要回來**
- 看板公開票數門檻 → **要回來**（四個計時裡的「目前共識門檻」）
- 證據 chip／證據查表 → **要回來**（可驗證證據面板）
- 演練重播（`?replay=1&speed=20`）→ 使用者沒提，**判斷後說明**
- `/api/runs` 端點 → 由 run 下拉取代，**判斷後說明**

**逐條盤點是本票最重要的證據之一。**

## 執行與 Review 紀錄

- 2026-08-09 跨工作包註記：本票「`report_renderer.py`／`report_audit_renderer.py`／`run_verifier.py` 不得修改」的限制**只約束本票**。後續工作包 `wp-20260809-125056-b3e957c6`（前端重設計與七席換套）已另行核准對 renderer「席位標籤來源」的定向修改（版面不動），依據 `docs/adr/0006-seat-profile-sets.md`。實作該工作包時不得以本票此條為由拒改。
