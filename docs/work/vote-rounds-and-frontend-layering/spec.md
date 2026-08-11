# 四輪投票制、概述摺疊與前端分層

- 狀態：已核准（2026-08-10）
- 需求依據：`docs/planning/requirements.md`〈四輪投票制、概述摺疊與前端分層（2026-08-10 核准）〉
- 架構依據：`docs/planning/architecture.md` §14；`docs/adr/0008-discrete-vote-rounds.md`
- 前置：wp-20260810-092746-8728cbc6（前端白話化與 Google 風重設計）已完成的成果

## 問題

1. 辯論規則與使用者期望不符：現行是「封存後連續辯論＋門檻隨時間遞減（6→5→4）＋指派式反方挑戰」，期望是「離散四輪投票（7／6／5／4）＋第一輪開票前各席資料隔離＋未達共識才交換資料互相說服」。
2. 「結論在前＋全文展開」的直播訊息呈現在現版缺席；工作區已有一份未提交、全套件測試綠的實作（live.py＋pages.py＋174 行測試），「說話順序結論在前」已在 HEAD（debate_driver 強制 `public_reason` 第一句為 30–60 字核心結論）。
3. webapp 前端 3256 行擠在 pages.py 單檔、無分層，難維護；視覺仍不滿意。

## 目標

新四輪投票流程上線；辯論訊息預設顯示結論、可展開全文；webapp 分層（模板／靜態資產／頁面組裝）＋視覺升級；CSP 順勢收緊。

## User Stories

1. 身為使用者，我希望七席在第一輪投票前只依自己查到的資料獨立判斷，以便結論反映獨立多方查證而非從眾。
2. 身為使用者，我希望辯論在固定時刻分四輪開票、門檻逐輪遞減（7／6／5／4），以便 10 分鐘內拿到票數與可信度清楚對應的結論。
3. 身為使用者，我希望第一輪未達共識時各席交換整理好的 JSON 資料、用證據互相說服（不盲從也不死守），以便最終判斷吸收全部證據。
4. 身為觀看直播的使用者，我希望每則發言預設只顯示核心結論、按一下展開全文，以便快速掃讀辯論進展。
5. 身為維護者，我希望前端拆成 HTML 模板、靜態 CSS/JS 與頁面組裝模組，以便日後改版不必在單一巨檔內動刀。
6. 身為使用者，我希望介面互動與細節更精緻（過渡動畫、聊天氣泡、表格 hover），以便閱讀更舒適。
7. 身為使用者，我希望設定頁的新規則鍵有中文標籤與白話說明，以便看得懂自己在調什麼。

## 需求與行為

### R-001 四輪投票時間表（錨定封存時刻）

所有時刻錨定該 run 的證據封存時刻（單幣題 4:00 封存、比較題 4:30，`research_deadlines` 仍是封存唯一權威）：

| 錨定式 | 單幣題字面時刻 | 事件 |
|---|---|---|
| ～封存 | 0:00–4:00 | 各席研究；來源規則沿用 |
| 封存～+60s | 4:00–5:00 | 各席整理資料並交開場票 |
| +60s | 5:00 | 第一輪開票，門檻 7 |
| +150s | 6:30 | 第二輪開票，門檻 6 |
| +240s | 8:00 | 第三輪開票，門檻 5 |
| +330s | 9:30 | 第四輪開票，門檻 4 |
| +360s | 10:00 | 硬停＋最終結算 |

比較題全部後移 30 秒（硬停 10:30）。任一輪領先立場票數達該輪門檻即結案寫報告。

### R-002 盲投直過保留（提前開票）

七席開場票全部到齊且同一立場時，立即結案產藍燈報告，不必等第一輪牆；未全同立場則等牆上開票。

### R-003 證據可見性閘門（提示詞層）

- 第一輪開票前：每席提示詞只含**自席**證據卡（以證據卡既有 `seat_id` 欄位過濾）；封存快照檔案照舊產生，run 檔案契約零改動。
- 第一輪未過後：恢復完整快照注入（＝JSON 資料交換），各席重新判斷；之後所有輪都可看全部證據與全部公開發言。

### R-004 資料交換與自由辯論（挑戰機制退役）

- `assign_challenges`／`rotation_pairs` 指派式挑戰移除；「每席必須完成一輪反方挑戰票才有效」廢止。
- 輪間為自由辯論 turn：提示詞語意為「用證據說服對方、不盲從也不死守」；改票必附理由、`vote_changes` 全程記錄、Core 只 relay 原文——全部沿用。
- 有效票＝開票當下該席最新公開立場。

### R-005 硬停結算與紅燈

硬停時刻做最終結算：當下最新有效立場任一達末輪門檻（4）即採納；否則未達共識、輸出紅燈失敗報告（不形成市場結論）。替補規則與絕對票數門檻不變。

### R-006 燈號不變

純票數映射（7藍／6綠／5黃／4橘／<4紅）＋兩條來源降級（獨立網域、低可信來源含輿情席豁免）原樣保留；`confidence` 區塊 schema 不動。

### R-007 規則檔 schema v2 與設定頁白話標籤

- `config/debate_rules.json` 升 `schema_version: 2`：`timeline.vote_rounds`（`[{open_offset_ms, threshold}]`，輪數＝陣列長度）＋`timeline.final_settle_offset_ms`；載入 fail-closed 驗證 offset 嚴格遞增、threshold 嚴格遞減、結算＞末輪。
- v1 具名欄位（`debate_start`／`round_one_window`／`reduced_threshold_from`／`final_round_start`／`final_round_end`／`force_stop`／`initial`／`reduced`／`forced_stop`／`unanimous_blind_pass`）全部退役；現行 v1 設定檔一次性遷移為 v2（隨本工作包交付新檔）。
- 設定頁新鍵逐鍵文案（定案，供實作直接使用）：

| 鍵 | 中文標籤 | 白話說明 |
|---|---|---|
| `timeline`（分組） | 時間軸 | 辯論各輪開票時刻，全部從證據封存那一刻起算 |
| `timeline.vote_rounds` | 投票輪清單 | 一列一輪：何時開票、需要幾席同立場才結案 |
| `vote_rounds[].open_offset_ms` | 開票時刻（封存後毫秒） | 封存後過這麼多毫秒開這一輪票；單幣題封存在第 4 分鐘 |
| `vote_rounds[].threshold` | 所需同立場票數 | 這一輪要幾席同立場才能結案寫報告 |
| `timeline.final_settle_offset_ms` | 硬停結算時刻（封存後毫秒） | 時間到直接停止辯論做最終結算；沒有立場達到末輪票數就亮紅燈 |

- 「票數門檻」分組隨 v1 欄位退役；「燈號規則」分組沿用。設定頁時間軸視覺化改由輪陣列繪製（每輪一列：第 N 輪＋開票時刻＋門檻）。未翻譯鍵顯示原鍵名＋「尚未翻譯」照舊。

### R-008 概述摺疊（採用既有基線）

- 採用工作區未提交實作為基線：伺服器端取 `public_reason` 第一句（全形句號／驚嘆號／問號斷句；ASCII 句點僅在後接空白／換行／結尾時算句尾；無句尾時 60 字截斷補省略號）為概述，`<details>`／`<summary>` 原生展開收合（「顯示全文」／「收合」），頁面與 SSE 共用同一份斷句，零 inline script。
- 此行為在前端分層與視覺升級後必須原樣保留（含既有 174 行測試）。

### R-009 webapp 分層（伺服器渲染＋模板＋靜態資產）

- 新增 `webapp/templates/`（頁面骨架 HTML 檔）、`webapp/static/site.css`（全站唯一樣式表，`var()` 引 token）、`webapp/static/live.js`（原 `LIVE_SCRIPT`）、`webapp/pages/` 套件（各頁一組裝模組＋`components.py` 重複元件）。
- `server.py` 新增 `/static/*` 路由，只服務 `webapp/static` 白名單目錄；SSE `/live/events` 與 payload 不變；不新增 JSON API。
- `design_tokens.py` 仍是唯一色彩權威（產 `:root` 區塊與 `site.css` 組合供應）；`pages._tokens` 與 `report_renderer._custom_properties` 重複實作合併為一。
- 頁面行為（路由、表單、導覽、保護區）與分層前一致。

### R-010 CSP 收緊

webapp 頁面 `style-src` 由 `'unsafe-inline'` 收緊為 `'self'`；`script-src` 維持現制（live 頁 `'self'`、其餘 `'none'`）；其餘 directive 不動。

### R-011 視覺升級（Gemini 建議為參考）

在既有設計系統（Google 風白底、毛玻璃、微軟正黑體、語意色）內落地：按鈕／輸入框過渡動畫與 focus ring、聊天氣泡與頭像對齊優化、表格 hover 與表頭樣式、標籤（badge）精緻化、席位卡 hover。Gemini 程式碼是參考不是驗收標準；與既有約束衝突時以既有約束為準（其 `_message` 版本無概述摺疊——以 R-008 為準）。

### R-012 邊界（保護區與離線報告）

- 保護區沿用「功能凍結、外衣可換」：動態聊天室、燈位、正反棄票數——內容、位置、語意色、計票語意不動。
- 離線 `report.html`／`debate.html` 維持自足單檔（CSS 內嵌），不納入分層；renderer 與 `run_verifier` 版面不動；舊 run 不回溯。
- 消費新規則的顯示端同步：直播頁 `rule_timeline`／門檻標籤、設定頁時間軸改輪陣列版。

## 實作決策

### 資料與所有權

- `config/debate_rules.json`（v2）仍是時間門檻與票數唯一來源；唯一寫入路徑是設定頁 `save_rules`（fail-closed 驗證後原子換檔）。
- run 檔案契約零改動：`evidence.jsonl` 快照、`debate.jsonl`、`votes.json`、manifest 均照舊；manifest 規則快照沿用既有機制，新 run 存 v2 document。
- 靜態資產（`site.css`／`live.js`）與模板檔由 webapp 擁有，隨 Code Root 版控。

### 模組責任與公開介面

- `debate_rules.py`：v2 載入＋驗證；`DebateRules` 改輪陣列結構；`required_votes_at`／`phase_at` 由輪陣列迴圈推導。
- `debate_state_machine.py`：回合驗證與停止判定迴圈化（不寫死輪數）；盲投直過＝第一輪提前開票；硬停改最終結算；`SeatRecord` 有效性不再繫於挑戰完成。
- `debate_driver.py`：`build_turns` 由規則輪陣列推導；挑戰配對移除；輪間自由辯論 turn；證據閘門在組提示詞處依階段過濾；每輪收集預算仍停在該牆前 5 秒。
- `prompt_builder.py`：契約改「**同階段內**七席共用區塊逐位元相同」；新增 per-seat 證據視圖參數。
- `contract_validator.py`：`_rules_document` v2 序列化＋保留 v1 讀取分支。
- `run_verifier.py`：合法 stop_reason 由該 run 規則快照的輪陣列推導，不寫死枚舉；舊 run 依其 v1 快照驗證，行為不變。
- `webapp/live.py`、`webapp/settings.py`、設定頁時間軸視覺化：輪陣列版＋R-007 文案表。
- `webapp/pages/`＋`templates/`＋`static/`：R-009 分層；`server.py` 加 `/static/*` 路由與收緊後 CSP。

### Schema、API contract 與系統互動

- `debate_rules.json` v2 形狀見 R-007／architecture.md §14.1。
- SSE `/live/events` 事件名與 payload 欄位不變（`public_brief` 已在基線內）。
- 不新增 JSON API、不新增外部依賴（stdlib only）。

### 相容、遷移與技術限制

- v1 設定檔被 v2 載入器 fail-closed 拒絕；新 v2 設定檔隨本工作包交付（一次性遷移，預設值＝R-001 時間表）。
- 舊 run manifest 內 v1 規則快照照舊可讀可驗（`contract_validator` 跨版本分支）；`test_verify_run.py` 既有相容測試必須維持全綠。
- 零外部資源、零 SQL、run artifact 唯讀、零 inline script、WCAG AA 斷言、規則值走 reload-aware API、全繁體中文——全部沿用。

## 驗收條件

1. 模擬 run（假時鐘）：單幣題各輪開票時刻與門檻＝5:00/7、6:30/6、8:00/5、9:30/4、10:00 結算；比較題一律 +30 秒；任一輪達門檻即結案且 stop_reason 可追溯到該輪。
2. 七席開場票全到且同立場→立即結案產藍燈報告（不等牆）；六席同一席異→等牆上開票。
3. 第一輪開票前任何席位的提示詞不含他席證據卡；第一輪未過後提示詞含完整快照——測試斷言。
4. 硬停結算：領先立場 ≥4 →採納（燈號依票數映射）；<4 →紅燈失敗報告。
5. 挑戰配對程式碼與「未完成挑戰票無效」邏輯不存在；改票紀錄與理由照常寫入 `debate.jsonl`。
6. 舊 run 驗證：`test_verify_run.py` 相容測試全綠；帶 v1 規則快照的舊 run 以舊規則判定。
7. 辯論頁訊息預設只顯示第一句核心結論＋「顯示全文」展開、「收合」收回；SSE 新訊息同樣行為（既有 174 行測試通過）。
8. 分層後：`webapp/templates/`、`webapp/static/site.css`、`webapp/static/live.js`、`webapp/pages/` 存在且被實際使用；全站頁面行為與分層前一致；全套件測試綠。
9. CSP：webapp 頁面 `style-src 'self'`（無 `unsafe-inline`）；`/static/*` 只服務白名單，白名單外 404。
10. 設定頁：v2 鍵顯示 R-007 文案表的中文標籤與白話說明；時間軸視覺化按輪陣列繪製；模擬未翻譯鍵顯示原鍵名＋「尚未翻譯」。
11. 視覺：對比度實測達 WCAG AA（含毛玻璃合成色）；渲染後 grep 無英文資料原值；視覺走查由使用者確認。
12. 全套件 `python3 -m unittest discover -s tests`（WSL）全綠。

## 測試決策

- **公開行為**：投票輪時刻與門檻、提前結案、閘門前後提示詞內容、結算與燈號、概述摺疊 DOM 形狀、頁面路由與 CSP header、設定頁標籤。
- **測試接縫**：`FixedClock` 注入時鐘、fake provider、`debate_rules` 載入器（暫存路徑）、per-seat 提示詞內容斷言（fake provider 收到的 prompt）、`/static` 路由經 `PageFixture` 假 socket、樣式測試改讀樣式路由輸出、`LiveScriptTest` 改讀 `static/live.js`。
- **既有測試模式**：unittest、字串／輕量 DOM walker 斷言、`ContrastTest` 由 design_tokens 動態計算、暫存目錄不碰正式 Data Root。
- **不應耦合的實作細節**：輪陣列的內部資料類名稱、模板檔內部註解、`components.py` 私有函式切分、CSS 類名以外的 DOM 結構細節。

## 不在範圍內

行動版佈局、舊 run 回溯重製、離線報告版面內容改動（renderer 不動）、「Core 直答＋按需加派」開放題流程、SPA／JSON API 全面化、深色模式。

## 補充

- 概述基線是工作區未提交修改（live.py＋pages.py＋tests/test_webapp.py），驗收確認後隨本工作包一併納入版控；分層搬家時保留其行為與測試。
- Gemini 建議原文由使用者提供於 2026-08-10 對話，作為 R-011 參考方向；落地時以 `design_tokens` 與保護區約束為準。
- 驗證邊界分四批（規則 v2＋狀態機 → 閘門＋辯論 turn → 分層搬家 → 視覺升級），拆票時依此排依賴。
