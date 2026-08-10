# 10 Phase 5b：提問啟動＋聊天室直播＋live_dashboard 退役

- 狀態：完成（三方共識：Developer ＋ Reviewer A ＋ Reviewer B；09～12 批次審查十輪、修復七輪）
- Spec：`../spec.md`（Phase 5）
- Blocked by：09

## 目標

在前端輸入問題就能啟動七席分析，聊天室直播即時看七席發言、輪次、票數與最終燈號；舊直播頁退役。

## 使用者價值

「有問題就直接上前端去問，就可以開始跑」＋「聊天室直播的功能一定要有」。

## 範圍

1. 提問啟動：頁面輸入問題→伺服器呼叫 launch 管線（背景程序）→顯示 run 進度；launch 觸發寫入 webapp log。
2. SSE 即時進度：沿用 events.jsonl 事件流推送——七席狀態、輪次、票數變化、燈號結果；倒數由瀏覽器本地更新。
3. 聊天室直播視圖：v5-chat 版面語彙重製（席位頭像＋氣泡＋輪次分隔＋立場色），呈現公開發言與改票；不顯示隱藏思考。
4. `live_dashboard` 退役：launch 不再自動開舊直播頁，模組與其測試移除；§4.0.1 唯讀直播邊界由新前端繼承（直播故障不得影響 run）。

## 已確認實作決策

- 同一時間只允許一個進行中 run（前端鎖，避免 inbox write-once 衝突）。
- 提問啟動需 `latest-ready.json` 有效；缺憑證時顯示明確指引而非啟動失敗 traceback。
- webapp 對 run 流程的介入僅限「啟動」與「唯讀觀察」。

## 驗收條件

- 頁面送出 fixture 題目→run 啟動→直播區即時出現七席事件→結束後顯示燈號並可跳轉 run 詳情。
- 直播中殺掉瀏覽器分頁再重開→SSE 重連並補上目前狀態。
- run 進行中前端當掉→run 照常完成（artifact 完整、verify-run PASS）。
- repo 中不再存在 live_dashboard 模組；launch 不再開舊頁。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：SSE handler 單元測試＋fixture launch 端到端。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：直播截圖、端到端執行輸出、verify-run 結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：09
- Blocks：12

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
- 基準版本：`main @ 9b8a4510`（未提交工作樹）；開工快照 `/tmp/t10-baseline/`（117 檔）
- Developer：Claude（臨時 Agent）。執行中曾因使用者要求暫停而被 Coordinator 中途切斷一次，續開時帶完整 context 與中斷點快照。
- 併行工作：Ticket 11-B2（manifest 規則快照），檔案集合零重疊，雙方均被告知對方擁有哪些檔案。

### Ready for Review — 變更摘要

| 檔案 | +/− | sha256（前 12） |
|---|---|---|
| `hoya_market_agents/seats.py` | +57 / −1 | `6e1271519168` |
| `hoya_market_agents/report_audit_renderer.py` | +13 / −8 | `c8822824a404` |
| `hoya_market_agents/cli.py` | +7 / −33 | `433714606db9` |
| `hoya_market_agents/launcher.py` | +36 / −31 | `1ae1e42739c8` |
| `hoya_market_agents/webapp/__init__.py` | +24 / −5 | `f0754a962e9b` |
| `hoya_market_agents/webapp/views.py` | +11 / −6 | `3059909f555f` |
| `hoya_market_agents/webapp/pages.py` | +533 / −4 | `3f11060d2ba8` |
| `hoya_market_agents/webapp/server.py` | +317 / −16 | `0ca2e5f1ec7a` |
| `hoya_market_agents/webapp/live.py`（新） | +541 | `16e2c103eeef` |
| `hoya_market_agents/webapp/launch.py`（新） | +182 | `9ecfa4c3c0c1` |
| `tests/test_webapp.py` | +1735 / −9 | `4171f4bc2bb6` |
| `tests/test_seats.py`（新） | +113 | `7873910fd9b8` |
| `tests/test_launcher.py` | +99 | `bf589ef15fa6` |
| `tests/test_cli.py` | +30 | `7cce09b7603a` |
| `tests/test_report_audit_renderer.py` | +15 / −5 | `a2df8216cb99` |
| `tests/test_debate_rules.py` | −1（單行例外授權） | `cdc69a271956` |
| ~~`hoya_market_agents/live_dashboard.py`~~ | −873 | **已刪除** |
| ~~`tests/test_live_dashboard.py`~~ | −1164 | **已刪除** |

### 七席身分：合併為單一權威

`AGENT_PROFILES` 與 `report_renderer.REPORT_AGENT_PROFILES` 逐欄機器比對：keys 7/7 相同、`display_name` 7/7 相同、`avatar` 7/7 相同。後者是 4-tuple 對 `(0, 2)` 的**嚴格投影**，不是第二種意見。

權威搬進 `seats.py`，改為具名 `SeatIdentity` frozen dataclass（`seat_id / display_name / agent_number / avatar / provider`），消除 `profile[2]` 這類位置索引。`report_audit_renderer` 與新的 `webapp/live.py` 皆讀它。

**Coordinator 獨立驗證**：在 `hoya_market_agents/question.py` 尾端植入第三份字面席位字典後執行 `tests.test_seats` —

```
FAIL: test_only_the_two_allowed_modules_spell_a_seat_name
AssertionError: Items in the second set but not the first: 'question.py'
FAILED (failures=2)
```

掃描器精準指名犯規檔案。植入已還原，`question.py` sha256 與暫停快照完全相同（`fc8a861097122d02…`）。無植入時 `tests.test_seats` 12 案全綠，含兩條 FP 方向（`test_the_scan_finds_the_authority_itself`、`test_the_scan_reads_every_module_in_the_package`）防止掃描空轉。

### 52 個 `test_live_dashboard` 案例逐條盤點

票面驗收「案例數只增不減」在本票**字面不可能成立**（刪除一個 1164 行的測試檔）。以下逐條盤點是對該條的交代。

- **行為保留、測試已搬家：28 條。** 例：改票歷史→`ChatRoomTest`（三條，含兩個 FP 方向）；半寫入的 JSONL 行→`EventTailTest`；路徑逸出 run 參數→`LiveSnapshotTest`（6 種攻擊字串）；關閉伺服器不留 request 執行緒→`LiveStreamTest` ＋ `LiveFailureIsolationTest`。
- **部分保留、部分消失：3 條。** 倒數與規則時間線消失（票面要求倒數由瀏覽器本地更新）；證據 metadata 僅保留 evidence ids（完整證據卡在 09 的 run 詳情頁與稽核逐字稿）；rules 面板消失。
- **行為隨模組消失：21 條。** 演練重播（`?replay=1&speed=20`）整個功能退役；證據 chip；規則時間線與封存里程碑；`/api/runs` 端點（由 09 歷史查詢頁取代）；CLI `live` 命令；看板公開票數門檻（新前端不公開任何門檻）；「一次 build 只讀規則權威一次」三條（**新前端完全不讀 `debate_rules`，該類缺陷被消滅而非改測**，權威保證仍由 `tests/test_debate_rules.py` 的 package 掃描守）。
- **盤點過程中補上 5 條原本「行為保留但沒測試」的缺口**：問題型別無記錄的 fallback、記錄的立場詞彙優先、未知型別不炸、路徑逸出、`latest.json`／`index.db` 不被誤認為 run。

### 案例數淨值

```
09 結束基準                                        1532
本票新增  test_webapp +153 / test_seats +12 /
          test_launcher +8 / test_cli +4 /
          test_report_audit_renderer +1      小計  +178
本票移除  test_live_dashboard                      −52
本票淨值                                          +126
併行 Ticket 11-B2（非本票）                        +38
全樹實測                                          1696 ✓
```

### 四條驗收條件（真 socket，非模擬）

1. **提問→啟動→直播→燈號→詳情**：`POST /launch` → 200 → `/live`；spawn 收到 `python3 -m hoya_market_agents launch --question ... --data-root ...`；第一個 frame `snapshot`；串流推送七席齊全；done frame 燈號 `orange` 並帶 `/run/<id>` 跳轉；`GET /run/<id>` 200 且頁面含橘燈。
2. **殺分頁再重開**：cursor `20260806T020000Z-btc-acc001@461`；重連第一個 frame 為 `append`，補上 `['news','onchain']`，票數 `{'bullish':2,'bearish':1,'neutral':0}`——**補上而未重複**。
3. **前端當掉不影響 run**：fake drill 全程跑完，中途關閉前端；`verify-run` exit 0、`status: VERIFIED`；10 個 artifact 齊全。
4. **退役確認**：`live_dashboard.py` 與 `test_live_dashboard.py` 皆不存在；`import live_dashboard` → ImportError；`launcher` 不再有 `_default_live_starter`、不再 import `subprocess`。

### SSE 設計

- **cursor 格式 `<run_id>@<offset>`**。原本用 `#`，被自己的測試抓到——`#` 在 query string 會被當 fragment 截掉。
- 重連來源優先序：`Last-Event-ID` > `?after=`。
- 讀法 `open(path,"rb").seek(offset).read()`，只吃完整行、回傳每筆結束 offset，**不整檔重讀 diff**。一條連線只做一次全讀，之後純增量。
- 串流上限 300 秒後關閉，靠 `EventSource` 自動重連（`retry: 2000` ＋ `Last-Event-ID`）續上。

### 前端鎖：照實寫成「不是互斥保證」

`new_run_id` 含隨機 token，兩次同時 launch 會拿到不同 run_id、不同目錄、不同 inbox，**兩個都會跑完**。`run_store` / `codex_inbox` 的 write-once 只擋一個 run 覆蓋自己，對第二個 run 沒有意見。

此鎖買到的唯一效果：按兩次按鈕會得到一句話，而不是安靜地再開七席。docstring 明寫「Anyone who needs a real guarantee … has to look outside this module — and there is nothing here that claims to be it.」兩個方向皆有測試。

### CSP：恰好三處差異，其中一處是收緊

```
歷史頁／詳情頁／404（未改）
  default-src 'none'; script-src 'none'; style-src 'unsafe-inline';
  img-src 'self' data:; frame-src 'self'; frame-ancestors 'self';
  form-action 'self'; base-uri 'none'

直播室與 /live.js（新增）
  default-src 'none'; script-src 'self'; style-src 'unsafe-inline';
  img-src 'self' data:; connect-src 'self'; frame-ancestors 'self';
  form-action 'self'; base-uri 'none'
```

`script-src 'none'→'self'`（放寬）、`connect-src 'self'` 新增（放寬，EventSource 必要）、`frame-src 'self'` **移除**（收緊）。`test_the_room_loosens_exactly_two_directives_and_no_others` 逐 directive 比對釘死，第四處差異即紅。

**未使用 `unsafe-inline`**：JS 置於獨立路由 `/live.js`，頁面只有 `<script src="/live.js" defer>`，零 inline script。09 的零 script 性質未被破壞——`test_no_other_page_carries_a_script_at_all` 仍斷言 `/`、`/run/<id>`、`/nope` 完全沒有 `<script`。所有 run 資料一律以 `textContent` 寫入，`test_the_script_never_assembles_markup_from_run_data` 斷言 script 內無 `innerHTML`／`insertAdjacentHTML`／`document.write`。

### 立場色對比（AA 文字門檻 4.5:1，已進 `CONTRAST_REQUIREMENTS`）

| | affirm | oppose | abstain |
|---|---|---|---|
| light（page / surface） | `#0c6b3d` 5.67 / 6.59 | `#a02128` 6.59 / 7.66 | `#6a5200` 6.41 / 7.45 |
| dark（page / surface） | `#5fd39b` 10.14 / 9.17 | `#ff9d9d` 9.50 / 8.59 | `#e3c05f` 10.76 / 9.74 |

顏色非唯一訊號：泡泡同時寫出立場文字（`test_a_stance_is_named_as_well_as_coloured`）。

### 唯讀邊界

`LiveReadOnlyTest` 將整棵 `runs/` chmod 唯讀後跑 `/live`、`/live.js`、`/live/events`（含 `?run=`），**並且把 `POST /launch` 一併納入，沒有除外**。主張不是「launch 不寫東西」，而是「**這個 process** 在 `runs/` 底下不寫任何東西」——子程序才擁有 run 目錄，注入的 spawn seam 只記錄命令。

### 全套與攔截

```
攔截器前置檢查 → /tmp/t08-intercept/sitecustomize.py ，exit 0
Ran 1696 tests    failures=0 errors=0 skipped=1    OK
攔截差額 = 39（36 exec + 3 --version）
殘留執行緒：['MainThread']
```

### 重要發現：共用攔截 log 造成的量測污染

前一次量測出現「逐檔加總 39、全套 75」的異常，一度被判讀為跨模組交互作用。Developer 以**決定性測試**推翻：

> 什麼都沒跑，靜置 25 秒，共用 `calls.log` 自己長了 69 行。
> 同一次全套跑：私有 log = 39，共用 log 差額 = 108。更早一次：私有 39，共用 78（= 39 + 39）。

根因是 `/tmp/t08-intercept/calls.log` 為**共用檔案**，兩個併行 Developer 同時 append。`sitecustomize.py` 讀 `T08_INTERCEPT_LOG` 環境變數，預設才是共用路徑。

**Coordinator 承認調度疏失**：對工作樹檔案衝突查核嚴謹，但未察覺攔截器的計數 log 是共用資源——它不在任何白名單內，因為它不在工作樹裡。往後併行派工一律指定私有 `T08_INTERCEPT_LOG`。

### 檔案歸屬

對 `/tmp/t10-baseline/`（117 檔）比對：新增 3、刪除 2、修改 16，**全部落在白名單內**。另有 9 個檔案變動屬併行的 Ticket 11-B2，Developer 全程未開啟或編輯。

`tests/test_debate_rules.py` 新 SHA `cdc69a271956…`。**Coordinator 獨立驗證**：`git diff --no-index --ignore-cr-at-eol` 對開工基準結果為 `0 1`（零新增、一刪除），刪除內容為

```
-        self.assertIn("hoya_market_agents.live_dashboard", names)
```

單行例外授權未被超出。

### 誠實標示的剩餘風險

1. **`report_renderer.REPORT_AGENT_PROFILES` 仍是第二份字面**（唯一的資料重複）。該檔在白名單外未動。收成單一權威需授權修改 `report_renderer.py` 45–53 行。目前以 pin 測試防漂移，但漂移時是「測試紅」而非「不可能發生」。
2. **`live.html` 是 dead link，而驗證器強制它存在。** `run_verifier.py:227` 的 `expected_tabs` 要求每份 HTML 都帶 `href="live.html"`，但 bundle 內從無此檔（舊看板走伺服器 URL，不是 bundle 檔案）。`report_renderer.py:547` 與 `report_audit_renderer.py:135` 產生該連結。**此缺陷在退役前即存在，本票未製造它，但退役後已無可辯解。** 三檔皆在白名單外，需一張跨檔票處理。
3. **兩處過時註解無法觸及**：`run_store.py:49` 提及 `live_dashboard`；`tests/test_debate_rules.py:3189` docstring 提及 `build_live_state`（該檔僅授權改一行）。
4. **JS 本身未經執行驗證**（環境無瀏覽器、無 node）。伺服器端渲染的 HTML 與 SSE 協定皆有完整斷言，但「JS 收到 append frame 後畫出的泡泡與伺服器渲染一致」只有結構對照，無 runtime 證明。**本票最大的未驗證面。**
5. **`--no-live` 現為不改變任何行為的相容旗標**。保留以不破壞既有呼叫，help 文字明寫「沒有任何差別」並有測試斷言該句存在。`run_launch` 的 `live_starter` 仍是真 seam（預設 `None`），因 `tests/test_prompt_builder.py`（白名單外）會注入它。
6. **重連的體感未驗證**：一場 15 分鐘的 run 期間瀏覽器會因 300 秒上限重連約 3 次；重連的正確性有測試，體感沒有。
7. **`?run=` 未指定時串流開場釘住當下最新 run，之後不跟隨新 run**（舊看板會跟隨）。為使 cursor 語意成立的刻意取捨，列於盤點第 27 條。

### 無截圖

本環境無任何可用瀏覽器（09 已逐一確認 chrome／chromium／firefox／wkhtmltoimage／selenium／playwright 全部不存在）。替代證據：渲染後 HTML 存檔 `/tmp/t10-acceptance-live.html`（13998 bytes，含 7 泡泡、7 席位卡、2 輪分隔、燈號與 `/run/<id>` 連結）＋ `RenderedRoomTest` 關鍵元素斷言。**未以任何形式假稱有截圖。**

### TDD 誠實標示

四個循環有真紅：`test_seats` ImportError（機制紅，主動標示）；單一權威掃描 2 條 FAIL 明確指出 `live_dashboard.py` 是第三份（真紅，抓到目標）；SSE cursor 的 `#` 被 URL fragment 吃掉（真紅，抓到真 bug）；立場詞彙那條是**測試斷言寫錯而非程式錯**（`resolve_stance_labels` 是全有全無），改正斷言後補上 FP 方向。

其餘（chat room／stream／launch／CSP／唯讀）為**事後補證的紅**：實作與測試同批寫，未逐條先跑紅，Developer 主動標示。

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
