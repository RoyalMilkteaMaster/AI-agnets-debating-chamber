# 12 Phase 5d：事後驗證（對答案）＋命中率統計

- 狀態：完成（三方共識：Developer ＋ Reviewer A ＋ Reviewer B；09～12 批次審查十輪、修復七輪）
- Spec：`../spec.md`（Phase 5）；ADR 0004
- Blocked by：10

## 目標

預測到期自動記錄實際結果到 `outcome.json` 與 index.db，統計頁顯示各燈號命中率；API 失敗可手動輸入。

## 使用者價值

「知道這套系統到底準不準」——每個燈號的歷史命中率一目了然。

## 範圍

1. 新增 `quote_api_client`：免費公開報價 API 唯一介接點（依資產類別解析來源：crypto／台股／美股），僅事後驗證可呼叫。
2. webapp 到期檢查：伺服器運行時掃描分析期間已到期且未對答案的 run→取價→判定方向對錯→寫該 run `outcome.json`（write-once 新 artifact：實際價格、方向判定、來源、原始回應摘要、時間）→更新 index.db 事後驗證欄。
3. 手動輸入 fallback：API 失敗或使用者要修正時，前端輸入實際結果（同樣寫 outcome.json；已存在則拒絕覆寫並提示）。
4. 統計頁：從 index.db 聚合各燈號命中率、總預測數、待驗證數。
5. 到期檢查與報價失敗寫 webapp log。

## 已確認實作決策

- 報價 API 永不進研究管線；研究、辯論、報告模組不得 import `quote_api_client`。
- outcome.json 遵守 write-once；修正需人工介入而非自動覆寫。
- 開放命題（無可對價格的標的）標記「不可自動驗證」，僅手動輸入。

## 驗收條件

- 假時鐘 fixture：run 到期→outcome.json 出現價格與方向判定→index.db 更新→統計頁命中率變化。
- 模擬 API 失敗→log 有紀錄→前端可手動輸入並生效。
- 對已有 outcome.json 的 run 再次寫入被拒。
- 研究管線模組無任何 `quote_api_client` import（grep 驗證）。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：假 HTTP 回應注入 quote client；假時鐘驅動到期檢查；暫存 run 目錄。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：outcome.json 樣本、統計頁截圖、API 失敗與手動輸入流程輸出、測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：10（需 09 骨架與 08 index；經 10 已滿足）
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

### 執行環境與角色

- execution environment：WSL（Ubuntu）；基準 `main @ 9b8a4510`；開工快照 `/tmp/t12-baseline/`（118 檔）
- Developer：Claude（臨時 Agent）。本批最後一張票，執行期間無其他 Developer 併行。

### 變更摘要

| 檔案 | +/− | sha256（前 12） |
|---|---|---|
| `hoya_market_agents/quote_api_client.py`（新增） | +321 / −0 | `35a7d4fa1de5` |
| `hoya_market_agents/webapp/outcome.py`（新增） | +612 / −0 | `9965c3da4944` |
| `tests/test_quote_api_client.py`（新增） | +396 / −0 | `75901b0271b8` |
| `hoya_market_agents/run_index.py` | +157 / −12 | `78541571f466` |
| `hoya_market_agents/webapp/__init__.py` | +21 / −4 | `07b91952efe3` |
| `hoya_market_agents/webapp/pages.py` | +230 / −1 | `d3bff89ba1fd` |
| `hoya_market_agents/webapp/server.py` | +65 / −2 | `a64e5ab0ae65` |
| `hoya_market_agents/webapp/views.py` | +97 / −1 | `827ecbe9622f` |
| `tests/test_run_index.py` | +360 / −3 | `39b227373f30` |
| `tests/test_webapp.py` | +1099 / −17 | `1285ffa4cafa` |

**Coordinator 特別授權的 `run_store.py` 與 `cli.py` 未被使用。** Developer 的理由：

> `RunDirectory.write_json` 已經用 `os.link` 提供 write-once，所以 `outcome.json` 沿用既有機制，而不是另加一個。

### `run_index.py`：唯一被刻意改變的 Ticket 08 性質

**六個受保護性質全部未動**——`flock` 仍罩住掃描＋安裝、scratch＋`os.replace` 未變、`run_row` 仍在鎖內呼叫、flock 與 SQLite 仍共用同一個 monotonic deadline、`_CONTENTION_ERRNOS` 未變、`FINALIZED_MARKER_NAME` 未變。（Coordinator 獨立核對：`flock` 9 處、`_CONTENTION_ERRNOS` 3 處、`os.replace` 4 處、`FINALIZED_MARKER_NAME` 3 處、`monotonic` 5 處。）

**刻意改變的一項**：`_UPSERT_COLUMNS` 現在包含 `outcome`（Ticket 08 將其排除）。理由是從 Ticket 08 自己留下的註解裡讀出來的——那個排除本來就在等這張票：

> Ticket 08 excluded `outcome` here, because at that point a verdict existed nowhere but in this file and re-indexing a run would have erased it. Ticket 12 gave the verdict a home on disk, which is what that exclusion was waiting for: **a rebuild empties the table, so a value only this file held could never have survived one anyway.** Now that `run_row` reads it back, **the live path and the rebuild agree by construction** — which is the property this module is built around — and there is no column left that has to be tiptoed around.

**這是消掉一個特殊情況，不是加一個。**

### `tests/test_run_index.py` 原有案例的處置

105 → 131 個測試方法。**原有 105 條裡 104 條名稱未變且全部重跑通過。**

替換的一條：`test_re_indexing_a_run_leaves_a_later_ticket_s_outcome_alone`（它用 raw SQL 注入一個只存在於 DB 的判定）→ `test_re_indexing_a_verified_run_keeps_its_verdict`（同樣的保證，改由磁碟紀錄證明），**並補上誠實的 FP 方向** `test_a_verdict_that_exists_only_in_the_index_is_not_preserved`。

結果：`Ran 131 tests ... OK`。

### backfill 不會洗掉對答案的結果（本票硬前置）

`run_row` 現在透過新的公開函式 `outcome_verdict(run_dir)` 從 `outcome.json` 導出 `outcome`。`BackfillKeepsTheOutcomeTest` 的 docstring 說明它守的是**索引的可拋棄性，不是一個功能**。

四條測試全部 `ok`：

```
test_a_full_rebuild_reproduces_every_recorded_verdict
test_verifying_then_backfilling_leaves_the_verdict_in_place
test_deleting_the_index_entirely_still_recovers_the_verdict
test_the_rebuild_would_notice_if_run_row_stopped_reading_the_record
```

**突變驗證**：把 `run_row` 改回 `"outcome": None` → 4/4 失敗，`AssertionError: 'hit' != None`。

### 「只有允許的模組能 import 報價 API」——用機制而非 grep

票面驗收只寫「grep 驗證」。Coordinator 要求改用機制（grep 是一次性的，擋不住下一個人），並指定沿用 Ticket 10 `SingleIdentityAuthorityTest` 的形狀。

`QuoteApiStaysOutOfTheResearchPipelineTest` 掃描 package 內每一個 `*.py`，允許清單恰為 `{quote_api_client.py, webapp/outcome.py}`。

**兩條 FP 方向**：掃描器確實在權威檔本身找到 `QuoteUnavailableError`／`QUOTE_SOURCES`；掃描器確實讀到 ≥20 個 package 檔案（含 `run_index.py`、`debate_driver.py`、`webapp/server.py`）——防止一個永遠掃不到東西的掃描器也通過。

**突變驗證**：把關鍵字加進 `webapp/views.py` → 如預期失敗。

**封存後禁搜尋邊界**：到期檢查僅能由 `GET`/`POST /stats` 觸達，無 thread、無 timer、無管線 hook；`sweep_due_runs` 只被一個模組指名（有斷言）。

### 真實網路請求 = 0

Coordinator 要求自建一道與 codex 攔截器等價的防護。Developer 的做法：`/tmp/t12-guard/sitecustomize.py` 替換 `socket.socket.connect`、`connect_ex` 與 `socket.create_connection`，放行 loopback（計數）、**其餘一律 raise**，並以路徑鏈載 codex 攔截器。

**實證**：一個真的 `urlopen` 到 `api.binance.com` 被擋下並記錄。全套跑完該 log **0 行**——一次連線都沒有嘗試。

### 三種狀態、三個答案

`outcome_verdict` 從**一次**讀取就分辨出三種狀況，無 TOCTOU：

| 狀況 | 判定 |
|---|---|
| `FileNotFoundError` | 不存在，可以寫 |
| 其他 `OSError` ／ decode 失敗 | **`unreadable`——「這個檔案在但讀不懂」** |
| 合法內容 | 已經對過答案 |

`record_manual_outcome` 回傳 `WRITTEN` / `ALREADY_RECORDED` / `RECORD_UNREADABLE`，並有測試斷言三個訊息互異、且 unreadable 那個**不說「尚未」**。

### 「不可自動驗證」是第四種狀態

`SCORED_OUTCOMES = (hit, miss)`——**分母是一個常數**。

可驗證性是**推導**的，不是列舉的：`quote_api_client.QUOTE_SOURCES` 的鍵由測試釘住等於 `question.ASSET_CLASSES − {ASSET_CLASS_OPEN}`；`STANCE_DIRECTIONS` 釘住為 `question_package.MARKET_STANCES` 的真子集。**全案無任何手寫標的清單。**

命中率定義：`hit / (hit + miss)`。`pending`、`unverifiable`、`unreadable` 會被計數並顯示，但**永遠不進分母**；沒有可計分項時回 `None` 而非 `0.0`。

### 既有性質維持

- **零 SQL in webapp**：`outcome_summary` 住在 `run_index`，建在 `query_runs` 之上。
- **CSP**：統計頁不傳 `scripts=` 給 `_document`，所以 `OnlyTheRoomIsGivenAScriptTest` 仍為 `{render_live_page}`，統計頁套嚴格 CSP。
- **無新增顏色 token**：沿用 `success`／`danger`／`muted`／`abstain`，皆已在 `CONTRAST_REQUIREMENTS`。實測 light 5.67–7.66:1、dark 7.90–10.76:1，全數 ≥ AA。**每個狀態都帶文字＋符號＋顏色**（`命中 ✔` / `未命中 ✘`），顏色從不是唯一訊號。
- **頁尾誠實**：新增 `pages.STATS_FOOTER`，統計頁不套唯讀頁尾（Ticket 11 踩過的同一個陷阱）。

### Coordinator 獨立驗證

```
攔截器 precheck OK
Ran 1961 tests    OK (skipped=1)          （基準 1820 → +141）
攔截差額 = 39
殘留執行緒 = ['MainThread']
```

**凍結檔案現值全部相符**：`debate_rules.py c12fe3fbe7e1` / `seats.py 6e1271519168` / `prompt_builder.py b61352bfac84` / `run_store.py 04fa8001ae59` / `test_run_store.py 1f1969da4537` / `test_seats.py 7873910fd9b8` / `config/debate_rules.json dcb8d0baf155`。

案例數 +141 = 38 quote + 26 run_index + 77 webapp，**只增不減**。

### Developer 主動揭露的一項測試弱點

> `test_the_sweep_leaves_an_unreadable_record_alone` 在突變 3 之下**仍然是綠的**——`os.link` 的 write-once 仍然保護著檔案；**那條測試證明的是「檔案安全」，不是「狀態被分辨出來」。**

狀態分辨由另外兩條有真的變紅的測試釘住。**這正是本 Task 反覆出現的「測試名字比斷言強」一類，由 Developer 自己抓到並說明。**

### TDD 誠實標示

四個機制紅全部有觸發（突變後檔案皆還原至突變前的精確 hash）：`run_row` 停止讀 `outcome.json` → 4 條 backfill 失敗；`unverifiable` 加進 `SCORED_OUTCOMES` → `0.5 != 0.3333…`；壞掉的紀錄被讀成 pending → `'record_unreadable' != 'already_recorded'`；新模組指名 client → 掃描失敗。

**主動標示**：這些是**事後補證的紅**（retroactive mutation）。最初的紅是 import 失敗（模組／名稱不存在）。

### 誠實標示的剩餘風險

1. **報價端點的回應形狀未經驗證。** Stooq 的日線 CSV，欄位**依名稱**讀取。本環境從未真的呼叫過它（防護禁止）。服務若回不同東西，會丟 `QuoteUnavailableError` 並指名它無法使用什麼——**永遠不會回一個數字**。已明寫於模組 docstring。
2. 加密貨幣以 `<symbol>usd` 請求；不以 USD 計價的幣會 fail closed，不會給出錯價。
3. `GET /stats` 有副作用（觸發掃描）。刻意且有文件，以 `MAX_SWEEP_RUNS = 20` 設界。
4. **`neutral` → `unverifiable`、價格完全相等 → `miss`，是 Developer 的判斷，不是從權威推導的。** 兩者都寫在程式碼裡。
5. `LOOKBACK_DAYS = 10` 有推理依據（台股最長的一般休市）但非權威。
6. **import 掃描是文字掃描**——模組只要在散文裡提到那個名字就會觸發。Developer 在 `webapp/__init__.py` 撞到並選擇改寫措辭，而不是稀釋允許清單；理由寫在該 docstring。
7. `OutcomeCheck.run` 與 `reindex_outcome` 捕捉寬泛的 `Exception`。兩者都有記錄、都不編造判定、都讓該 run 維持 pending。

### 無截圖

本環境無任何可用瀏覽器（前三張票已逐一確認）。替代證據：渲染後頁面存於 `/tmp/t12-stats.html`（13 KB）＋元素斷言——含 `66.7%`、`命中 ÷（命中 + 未命中）`、四個狀態詞、`STATS_FOOTER`，且**不含 `<script`**。**未以任何形式假稱有截圖。**

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
