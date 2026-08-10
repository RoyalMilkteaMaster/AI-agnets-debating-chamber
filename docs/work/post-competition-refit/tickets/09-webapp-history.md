# 09 Phase 5a：webapp 骨架＋歷史查詢＋run 詳情＋正式 Log

- 狀態：完成（三方共識：Developer ＋ Reviewer A ＋ Reviewer B；09～12 批次審查十輪、修復七輪）
- Spec：`../spec.md`（Phase 5）
- Blocked by：08

## 目標

常駐本機網頁上線：可查歷史 run、開 run 詳情（報告／投票／證據／辯論），伺服器具正式 Log。

## 使用者價值

「平時就是可以開的前端，有歷史的可以去查看當時的內容、報告」。

## 範圍

1. 新增 `webapp` 模組：127.0.0.1 常駐伺服器（stdlib http.server 模式，埠可設定，預設沿用 8765）；route 只處理輸入輸出，查詢邏輯走 `run_index`。
2. 歷史查詢頁：依日期／標的／燈號／關鍵字查 index.db；列表顯示日期、題目、標的、燈號、採納立場、票數。
3. run 詳情頁：呈現 report.html 內容、七席投票與改票、證據卡、辯論逐字稿連結。
4. 正式 Log：`_data/logs/webapp.jsonl`（JSONL：timestamp/level/event/source/message），日期輪替、保存 30 天、啟動時清逾期；記啟停與請求錯誤。
5. UI 重新設計：語意 HTML、鍵盤可操作、足夠對比；視覺品質高於比賽版。

## 已確認實作決策

- 零外部套件；不建前端框架專案，vanilla HTML/CSS/JS。
- webapp 對 run artifact 一律唯讀。
- 啟動埠被占用時：明確報錯退出並寫 log，不靜默重試（比賽版 8765 殘留教訓）。

## 驗收條件

- 啟動伺服器→瀏覽器開首頁→查詢條件過濾正確→點入 run 詳情各區塊完整。
- index.db 不存在時頁面顯示明確空狀態，不噴 traceback。
- `webapp.jsonl` 出現啟動事件；製造一個 404 後 log 出現該筆；輪替與逾期清理有測試。
- 埠占用時啟動失敗且訊息明確。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：直接呼叫 handler 的 http 層單元測試＋暫存 log 目錄。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：頁面截圖（首頁＋詳情）、log 樣本、測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：08
- Blocks：10、11

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

- execution environment：WSL（Ubuntu），`/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`
- 基準版本：`main @ 9b8a4510`（未提交工作樹）；開工快照 `/tmp/t09-baseline/sha256.txt`（145 檔）
- Developer：Claude（臨時 Agent `afd89fb00d95859be`）
- 必跑指令：`PYTHONPATH=/tmp/t08-intercept PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`

### Ready for Review — 變更摘要

新增 `hoya_market_agents/webapp/` 套件五個模組與 `tests/test_webapp.py`；修改 `cli.py`、`tests/test_cli.py`（票面允許）。新增 CLI：`python3 -m hoya_market_agents webapp --data-root ... [--port 8765]`。

| 檔案 | +/− | sha256（前 12） |
|---|---|---|
| `hoya_market_agents/webapp/__init__.py` | +51（新增） | `69f17d94c6e1` |
| `hoya_market_agents/webapp/log.py` | +265（新增） | `3cc2974aee71` |
| `hoya_market_agents/webapp/views.py` | +417（新增） | `6661dbcbb49b` |
| `hoya_market_agents/webapp/pages.py` | +633（新增） | `3c4fea9613a0` |
| `hoya_market_agents/webapp/server.py` | +209（新增） | `de64da142094` |
| `tests/test_webapp.py` | +1283（新增） | `408b8bda64be` |
| `hoya_market_agents/cli.py` | +36 −0 | `6008a87c9c22` |
| `tests/test_cli.py` | +61 −1 | `4c39c4554612` |

### Coordinator 粗檢（由 Coordinator 獨立重跑，非採信 Developer 數字）

1. **全套（攔截開啟）**：`Ran 1532 tests in 85.743s` / `OK (skipped=1)`。攔截差額 **39 = 36 exec + 3 --version**，與前票一致（36 次來自 `test_debate_driver`，已列為獨立待決事項）。
2. **檔案歸屬**：對 `/tmp/t09-baseline/sha256.txt` 逐檔比對，排除 `__pycache__` 後**基準內僅 `cli.py` 與 `tests/test_cli.py` 變動**（皆在白名單）；新增檔僅 `webapp/`（5 檔）與 `tests/test_webapp.py`。`tests/test_debate_rules.py` 開工與結束皆 `505bdd6ce91e`（併行 B1 工作，本票未動）。
3. **無第二份查詢實作**：`webapp/` 內零 `sqlite`／`SELECT`／`LIKE`／`ESCAPE` 命中；唯一取數路徑為 `from ..run_index import RunIndexError, index_db_path, query_runs`。
4. **全稱句掃描**（B1 反覆出現的「docstring 宣稱超過實作」一類）：6 個命中全為 UI 文案陳述資料事實（如「votes.json 沒有任何席位紀錄」），**零筆為 docstring 涵蓋宣稱**。

### Coordinator 更正兩項自身帳務錯誤

- **白名單重疊**：本票委派將 `cli.py`／`test_cli.py` 列入白名單，而併行的 B1 工作將同兩檔列為受保護。B1 Developer 因此停手回報（正確處置）。裁定：兩檔歸本票，B1 受保護清單移除該兩檔。
- **受保護 SHA 過期**：委派所載 `tests/test_debate_driver.py 5f14526eb5c1`、`tests/test_verify_run.py 8e320063f7b2` 為舊快照。開工基準實測已是 `85576abe52c9`／`09c6c2a51c4b`（B1 產品本體改快照傳遞時的合法變更），本票結束時值未變。**非本票所為。**

### 四條驗收條件（Developer 實測輸出）

1. **啟動→首頁→過濾→詳情**（真 drill run）：首頁 200／3 筆；`?keyword=2330`→1 筆、`?asset_class=tw_stock`→1 筆、`?confidence=green`→3 筆、`?date_to=2026-07-31`→0 筆；詳情 200／七席 7 列／證據卡 7；`report.html` 位元組與 run 目錄內檔案 sha256 相同；`votes.json` 404（不在 allowlist）；`/run/..%2f..%2fetc` 404。
2. **`index.db` 不存在**：HTTP 200、Traceback 出現 0 次、顯示 `index-backfill` 可複製指令；「查無符合條件」與「沒有索引」為兩個不同畫面。
3. **`webapp.jsonl`**：六類事件皆五欄位齊全；輪替與逾期清理 14 條測試（時鐘可注入，日期判定改讀檔內最後一筆 record 的 `timestamp` 而非 mtime——mtime 是真實時間、注入時鐘是假時間，永不相等）。
4. **埠占用**：exit 1、訊息指名 host:port 且明說不自動改埠；log 事件序 `[server_start, server_start_failed]` 證明 log 先於綁埠開啟；EACCES 不謊稱「被占用」。

### 唯讀與可及性

- **唯讀**：`ReadOnlyRunTest` 將整棵 `runs/` chmod 為唯讀（目錄 0500／檔案 0400）後跑 6 條路由＋2 條錯誤路由，全部 200，且 `runs/` 底下每檔 sha256 前後相同。artifact 路由為兩名 allowlist（`report.html`／`debate.html`）。
- **可及性**：22 組對比（11 對 × 深淺兩主題）全數通過 WCAG AA，且為 `ContrastTest` 的斷言而非一次性量測。最低值：`border/surface` dark **4.40:1**（非文字需 3.0）、`muted/surface` light **7.07:1**。零 JavaScript，故 CSP `script-src 'none'` 為實。skip link 為 body 第一個可聚焦元素、六個輸入框各有 `<label for>`、無 `tabindex > 0`。

### 誠實標示事項（由 Developer 主動揭露，Coordinator 覆核）

- **本票誤發 36 次真實 `codex exec`**：量開工基準案例數時使用 `PYTHONPATH=/tmp/t09-intercept-baseline`（**不存在的目錄**），sitecustomize 未載入、攔截靜默失效。後續改用「只 discover 不執行」計數法，未再犯。**根因為攔截機制 fail-open：路徑打錯無任何徵兆。** Coordinator 裁定：往後每份委派新增前置檢查 `python3 -c "import sitecustomize, sys; sys.exit(0 if 'intercept' in sitecustomize.__file__ else 1)"`，證明攔截器載入後才得跑全套。
- **無截圖**：本環境無任何可用瀏覽器（chrome／chromium／firefox／wkhtmltoimage／selenium／playwright 逐一確認不存在）。改交渲染後 HTML 存檔 8 份（`/tmp/t09-evidence/` 與 scratchpad `t09-evidence/`）＋關鍵元素斷言。**未以任何形式假稱有截圖。**
- **基準案例數更正**：票面寫 1423，開工快照實測 **1427**（併行 B1 工作已先行加入）。本票淨增 **+105**（`test_webapp.py` 102、`test_cli.py` 21→24）。
- **殘留執行緒**：全套跑完 `threading.enumerate()` 僅 `[MainThread]`。

### 待 Reviewer 裁定

- **架構 §11.8 字面寫「stdlib logging」，實作未用 `logging.Logger`。** 理由：`TimedRotatingFileHandler` 無法注入時鐘（與驗收要求的可測輪替衝突），且 `logging.getLogger` 的 process 全域註冊表為測試污染源。Coordinator 核對 §11.8 實質要求（JSONL／五欄位／日期輪替／30 天保存／啟動清理／測試斷言輪替）**逐項達成**，惟字面偏離屬刻意，**Coordinator 不自行裁定**。

### 未解風險（列入結案紀錄）

1. `server_stop` 僅 SIGINT 寫得到，SIGTERM 不會；CLI 與 docstring 均只宣稱「按 Ctrl+C 停止」，無超出實作的宣稱。日後作背景服務需補 handler。
2. `asset_class` 顯示原始值（`tw_stock` 而非中文）。中文 label 權威在 `config/market_scopes.json` 但僅涵蓋 3 類（缺 `open`），接它會產生 fallback 鏈並使設定檔損壞時首頁連帶損壞。刻意取捨以避免第二份詞彙表。
3. `?limit` 預設 50 筆、無真正分頁；頁面明示「已達本次筆數上限 N，可能還有更多」。票面未要求分頁。
4. `README.md` 與 `docs/operator-runbook.md` 未記載 `webapp` 命令（兩者不在白名單，runbook 為受保護檔）。需由有權者補。
5. `live` 與 `webapp` 預設同為 8765，同時開會撞埠；為票面指定＋`live_dashboard` 至 Ticket 10 才退役的必然結果，撞埠行為即第 4 條驗收。
6. `index_unavailable` 每請求寫一筆 WARNING；索引長期缺席時 log 有重複行。

### Developer 自我稽核修正（對著實作核 docstring 時發現，紅為事後補證，已誠實標示）

C1 `report.json` 的 `confidence` 非 dict 會炸成 500；C2 摘要立場用語未吃 run 的 assets（比較題型顯示「前者較優」而席位表顯示「BTC較優」）；C3／C4 檔案存在但無內容時被說成「尚未產生」（假話）；C5 命中筆數上限時未告知可能還有更多。以 `/tmp/t09-prefix` 還原修正前行為驗紅：4 failures（含 `AssertionError: 200 != 500`）＋比較題型 1 failure。

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
