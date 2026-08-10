# 前端白話化與 Google 風重設計

- 工作識別碼：wp-20260810-092746-8728cbc6
- 顯示名稱：前端白話化與 Google 風重設計
- 狀態：已核准

## 原始需求

- R-001：設定頁白話中文：每個規則項顯示中文標籤＋一句白話說明，分組標題中文化；規則檔新增而未翻譯的鍵顯示原鍵名＋「尚未翻譯」標註、照常可編輯（不 fail-closed）。
- R-002：五導覽全站常駐：即時辯論／歷史與命中率／市場報告／完整辯論／設定；市場報告與完整辯論在無特定 run 頁面指向最新有報告的 run、run 詳情頁指向自身 run、無報告時停用樣式；「伺服器已關閉」頁維持無導覽；離線兩頁經伺服器瀏覽時亦有五導覽，直接開檔／分享維持自足兩分頁導覽，run 檔案唯讀不回溯。
- R-003：設定獨立：「設定」與其他四個導覽分開，放「關閉伺服器」按鈕左邊。
- R-004：全站重新設計：Google 風白底、紅藍綠黃彩色點綴、極簡留白、半透明毛玻璃；含離線兩頁換新裝（新 run 起生效）；深色模式退場只留白底一套；微軟正黑體優先；語意色保留語意只校準色階。
- R-005：席位名稱與白話說明：股票套與幣圈套顯示名稱換為使用者定案名；即時辯論頁席位卡新增白話說明（僅顯示用，`focus` 權威不動）。
- R-006：開放題下架：發問選單只留台股／美股／幣；開放套留舊值當內部填充（fail-closed 檢查仍過）；後端 open 能力不刪；歷史開放題 run 照舊可回看。

## 問題

設定頁全是英文規則鍵看不懂；「市場報告／完整辯論」只在辯論室出現、離線頁回不了站內；設定與關機動線混在導覽裡；深淺雙 palette 舊視覺要淘汰；席位名是 AI 隨手取的、辯論頁看不出每席在研究什麼；開放題套借幣圈名突兀。使用者為本人（本機單人）；離線報告讀者為分享對象。

## 目標

全站 Google 風白底極簡新視覺；五導覽處處可達；設定項一看就懂；七席名字為使用者自取、卡片附白話分工說明；發問選單不再出現用不到的開放題。

## User Stories

1. 身為使用者，我希望設定頁每個規則項有中文標籤與白話說明，以便不用猜英文鍵名在控制什麼。
2. 身為使用者，我希望任何頁面右上角都有五個導覽，以便隨時跳到即時辯論、歷史、最新報告或設定。
3. 身為使用者，我希望「設定」獨立放在「關閉伺服器」左邊，以便瀏覽動線與管理動線分開。
4. 身為使用者，我希望全站是白底極簡、毛玻璃、彩色點綴的一致視覺，以便告別深淺雙 palette 的拼裝感。
5. 身為使用者，我希望經伺服器點開離線報告時也能用五導覽回站內，以便不必按上一頁；分享出去的檔案則維持自足。
6. 身為使用者，我希望七席顯示我自己取的名字、卡片下方一句白話說明他看哪方面資訊，以便辯論頁一眼看懂分工。
7. 身為使用者，我希望發問選單不再出現開放題，以便選項只剩我實際會用的三類。

## 需求與行為

### R-001 設定頁白話中文

分組標題（fieldset legend）：頂層→「基本」；`timeline_ms`→「時間軸（毫秒）」；`vote_thresholds`→「票數門檻」；`confidence`→「燈號規則」；`confidence.light_scale`→「燈號階梯」；`confidence.downgrades.few_independent_domains`→「降級：獨立來源不足」；`confidence.downgrades.low_trust_source`→「降級：低可信來源」。

逐鍵文案（中文標籤｜白話說明）：

| 鍵路徑 | 中文標籤 | 白話說明 |
|---|---|---|
| `schema_version` | 規則檔版本 | 規則檔的格式版本，目前僅支援 1，平常不需改動 |
| `timeline_ms.debate_start` | 證據封存時刻 | 開賽後多久結束研究、封存證據（毫秒），之後進入辯論 |
| `timeline_ms.round_one_window` | 第一輪挑戰時窗 | 證據封存後，留給第一輪反方挑戰的時間長度（毫秒） |
| `timeline_ms.reduced_threshold_from` | 門檻下調時刻 | 從此時間點起，過關票數由「初始」降為「下調後」 |
| `timeline_ms.final_round_start` | 最終輪開始 | 最後一輪辯論的開始時間 |
| `timeline_ms.final_round_end` | 最終輪結束 | 最後一輪辯論的結束時間 |
| `timeline_ms.force_stop` | 強制結算時刻 | 時間到就強制結算：達強停票數採納立場，否則未達共識 |
| `vote_thresholds.unanimous_blind_pass` | 盲投直過票數 | 開場盲投全數同立場達此票數，直接產出藍燈報告、不進辯論 |
| `vote_thresholds.initial` | 初始過關票數 | 辯論開始時，達成共識所需的有效票數 |
| `vote_thresholds.reduced` | 下調後過關票數 | 門檻下調時刻後，達成共識所需的有效票數 |
| `vote_thresholds.forced_stop` | 強停採納票數 | 強制結算時至少要這麼多票才採納立場，否則未達共識 |
| `confidence.light_scale[].min_votes` | 最低票數 | 拿到至少這麼多有效票，燈號落在這一級 |
| `confidence.light_scale[].level` | 燈色 | 這一級對應的燈色（blue／green／yellow／orange／red） |
| `confidence.downgrades.few_independent_domains.levels` | 降幾級 | 獨立來源網站太少時，燈號往下降的級數 |
| `confidence.downgrades.few_independent_domains.min_independent_domains` | 最低獨立網域數 | 採納立場引用的來源至少要來自幾個不同網站 |
| `confidence.downgrades.low_trust_source.levels` | 降幾級 | 引用低可信來源時，燈號往下降的級數 |
| `confidence.downgrades.low_trust_source.trusted_source_tiers` | 可信來源等級 | 視為可信的來源等級清單（逗號分隔） |
| `confidence.downgrades.low_trust_source.exempt_seat_ids` | 豁免席位 | 不受此降級約束的席位（輿情席職責即蒐集輿情） |

- 標籤表放 `webapp/settings.py`（標籤解析既有位置），key-path 對應 `{中文標籤, 白話說明}`；`light_scale[]` 內欄位以容器語境對應。
- 未涵蓋鍵：顯示原鍵名＋「尚未翻譯」標註，照常可編輯。`_about` 註解顯示照舊。

### R-002／R-003 五導覽與設定分離

- header 結構（除「伺服器已關閉」頁外全站一致）：左起「即時辯論｜歷史與命中率｜市場報告｜完整辯論」；右側「設定」獨立，緊鄰「關閉伺服器」按鈕左邊。
- 報告導覽指向：辯論室與 run 詳情頁→該 run 自身；主頁初始、歷史、設定頁→「最新有報告的 run」（`views.py` 以既有 `run_index.query_runs` 於 Python 端解析，零 SQL）；全站無報告→沿用現行停用樣式（span、不進 tab order）。
- 離線報告：`server.py` 回應注入器——僅對 run artifact 中 `report.html`／`debate.html` 的 text/html 回應，在 `<body>` 後插入五導覽列（純 HTML、零 script、樣式走站內樣式表 route）；找不到插入點原樣送出；磁碟檔案一字不動（ADR 0007）。

### R-004 全站設計系統

- 新模組 `design_tokens.py` 為唯一權威：白底單套 palette（深色刪除）、Google 四色裝飾 token（與 affirm／oppose／abstain 及燈號分開命名，不得混用）、毛玻璃 token（含合成後實色供對比測試）、字體堆疊微軟正黑體優先（純系統字型）。
- `pages.py` 與 `report_renderer.py`／`report_audit_renderer.py` 的 CSS 一律取自 design_tokens；renderer 寫死 palette 刪除；DOM 章節結構維持；新 run 起生效，舊 run 檔案與 PDF 不回溯。
- `ContrastTest` 對 design_tokens 計算 WCAG AA（文字 4.5:1、線條 3.0:1），毛玻璃面用合成色。

### R-005 席位名稱與白話說明

roster schema 升版：每套 profile 新增必填 `blurb`（僅顯示用，不進研究 prompt）；fail-closed 驗證擴充為「七席齊、三套齊、每套具 `display_name`／`focus`／`blurb`」。`seat_id`／`output_dir`／提供者／`focus` 不動。定案內容：

| # | seat_id | 股票套名稱｜blurb | 幣圈套名稱｜blurb | 開放套名稱（留舊）｜blurb |
|---|---|---|---|---|
| 1 | `spot-technical` | 技術面分析師｜看線圖：價量、均線、支撐壓力，判斷走勢強弱與關鍵價位 | 技術面分析師｜同左 | 圖表偵探｜看價量與技術結構，判斷走勢強弱 |
| 2 | `derivatives` | 籌碼面分析師｜看期貨與選擇權部位、融資融券：判斷大戶與散戶各押哪一邊 | 合約槓桿分析師｜看合約市場的槓桿狀態：多空部位、資金費率、清算風險 | 槓桿雷達｜看衍生品部位與槓桿狀態 |
| 3 | `onchain` | 法人動向分析師｜看法人與大股東的錢往哪走：買賣超、持股變化、資金流向 | 鏈上資金追蹤師｜看鏈上的錢往哪走：巨鯨動向、交易所進出、籌碼供給 | 鏈上獵人｜看鏈上資金與供給動向 |
| 4 | `official-events` | 官方公告哨兵｜盯官方公告與行事曆：重大訊息、財報法說日程、主管機關動作 | 官方公告哨兵｜盯官方消息：項目方公告、監管動作、重大事件時程 | 官方哨兵｜盯官方公告、監管與重大事件 |
| 5 | `news` | 新聞探員｜查證具名媒體報導、整理事件時間線，過濾未經證實的消息 | 新聞探員｜同左 | 新聞探員｜查證具名媒體報導、整理事件時間線 |
| 6 | `social-macro` | 輿情與大盤觀察員｜看散戶討論風向與大環境：論壇情緒、宏觀消息、大盤與產業連動 | 輿情與幣市社群觀察員｜看社群風向與大環境：討論熱度、宏觀消息、BTC 連動 | 社群觀察員｜看社群情緒與宏觀環境 |
| 7 | `counter-evidence` | 基本面分析師｜看公司本身體質：營收財報、估值比較、產業供需 | 項目體質分析師｜看項目本身體質：鎖倉量、協議收入、代幣解鎖時程 | 基本面研究員｜查核題目相關的關鍵數據與事實 |

即時辯論頁席位卡經 `seats.py` 既有讀取口顯示 blurb；離線報告席名隨 roster 自動一致。

### R-006 開放題下架

發問選單資產類別選項改由 `market_scopes.json` 三市場產生（台股／美股／幣）；launcher 介面、後端 open 路徑、套組選擇規則全部保留；歷史開放題 run 照舊可回看。

## 實作決策

- 資料與所有權：設計 token 唯一權威在 `design_tokens.py`；規則標籤表在 `webapp/settings.py`；席位名稱／blurb 唯一權威在 `config/agent_roster.json`；「最新有報告 run」由 `views.py` 走 `run_index.query_runs` 解析。run artifact 唯讀，注入只發生在 HTTP 回應層。
- 模組責任與公開介面：`pages.py` 新 header 與樣式表、席位卡 blurb；`server.py` artifact 回應注入器；`settings.py` 標籤解析與「尚未翻譯」fallback；兩個 renderer 換裝取 token；`seats.py` 讀取口擴充 blurb；`run_verifier.py` 不動。
- Schema、API contract 與系統互動：roster profiles 每套新增必填 `blurb`（預檢與 fixtures 同步升版）；無新端點、無 URL 變更；CSP／零 inline script／零 SQL 沿用。
- 相容、遷移與技術限制：`seat_id`／`output_dir`／提供者永不改；歷史 run 以現行套組顯示（沿用）；深色 palette 與 `@media (prefers-color-scheme: dark)` 區塊刪除；零外部資源（毛玻璃＝原生 CSS `backdrop-filter`）；已核准代價：舊 run 逐字稿舊席名與新標籤並存、舊 run 紙白頁上浮新設計風注入導覽列。

## 驗收條件

1. 設定頁渲染後：每個規則項顯示上表中文標籤＋白話說明、分組標題為中文；以測試設定檔加入未知鍵時，顯示原鍵名＋「尚未翻譯」且可編輯。
2. 除「伺服器已關閉」頁外，每個 webapp 頁面 header 有五導覽，「設定」位於「關閉伺服器」左邊；主頁初始／歷史／設定頁的報告導覽指向最新有報告 run，run 詳情頁指向自身 run；無報告時為停用樣式。
3. 經伺服器瀏覽 `report.html`／`debate.html` 時頁面出現五導覽且可回站內；磁碟上的檔案位元組不變；無插入點的 HTML 原樣送出。
4. 全站與新 run 離線頁呈現白底新設計；作業系統設深色時仍為同一套白底；成品樣式表無 `@media (prefers-color-scheme: dark)`；對比實測達 WCAG AA（含毛玻璃合成色）。
5. 台股／美股題席位卡顯示股票套定案名＋blurb；幣題顯示幣圈套定案名＋blurb；離線報告席名與 webapp 一致；roster 缺任一套或缺 `blurb` 時載入即失敗並給可讀錯誤。
6. 發問選單無「開放題」；歷史開放題 run 仍可開啟回看。
7. 保護區（聊天室、燈位、三種票數）行為與改版前一致；既有測試全綠；渲染後繁中 grep（A5 標準）通過。

## 測試決策

- 公開行為：注入器插入／略過兩路徑；latest-report 解析（有／無報告）；規則標籤 fallback（未譯鍵）；roster blurb fail-closed；套組選擇規則不變；設計 token → WCAG AA 斷言（含毛玻璃合成色）；發問選單只列三市場；保護區行為回歸。
- 測試接縫（新增）：artifact 回應注入器（給定 HTML 字串斷言）、latest-report 解析函式、標籤表 fallback、roster 載入器 blurb 驗證。沿用：unittest、暫存目錄、注入時鐘、fake provider。
- 既有測試模式：`tests/` unittest；驗收＝既有全綠＋渲染後繁中 grep＋對比度實測數字；環境無瀏覽器，證據以渲染後 HTML 存檔＋關鍵元素斷言。
- 不應耦合的實作細節：CSS 類名順序與色碼字面值（斷言對比結果不斷言色碼）、DOM 巢狀細節、roster JSON 鍵順序、注入導覽的確切 HTML 字串（斷言連結目標與零 script，不斷言完整字串）。

## 不在範圍內

- 「Core 直答＋按需加派」開放題新流程（未來獨立工作包候選）。
- 保護區任何行為改動；行動版佈局；舊 run 檔案回溯重製；開放套重新命名；`run_verifier.py` 修改。

## 補充

- 來源：`docs/planning/requirements.md`〈前端白話化、五導覽常駐與 Google 風重設計（2026-08-10 核准）〉、`docs/planning/architecture.md` §13、`docs/adr/0007-offline-report-nav-injection.md`、`CONTEXT.md`。
- §13 與先前章節衝突時以 §13 為準；§13 未提及的規則沿用 §11／§12。
- 建立在 wp-20260809-125056-b3e957c6 已完成驗收的成果之上。
