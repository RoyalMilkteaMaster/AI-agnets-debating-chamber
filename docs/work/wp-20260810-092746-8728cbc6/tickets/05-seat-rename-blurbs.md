# 席位定案名與白話說明

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：Ticket 04

## 目標

把 `config/agent_roster.json` 中股票套與幣圈套的 `display_name` 換成使用者定案名，三套 profile 各補上不可缺漏的 `blurb` 欄位；`seats.py` 的 fail-closed 驗證擴充為缺 `blurb` 即拒絕啟動，讀取口回傳 `blurb`；賽前預檢與測試 fixtures 同步升版；即時辯論頁席位卡顯示 blurb；離線報告席名隨 roster 自動一致。`seat_id`／`output_dir`／提供者／`focus` 不動。

## 對應原始需求

- R-005：席位名稱與白話說明：股票套與幣圈套顯示名稱換為使用者定案名；即時辯論頁席位卡新增白話說明（僅顯示用，`focus` 權威不動）。

## 使用者價值

對應 User Story 6：使用者要在辯論頁看到自己取的席位名，並在卡片下方用一句白話知道每一席在看哪方面資訊，一眼看懂七席分工。

## 範圍

包含：

- `config/agent_roster.json`：股票套與幣圈套 `display_name` 換定案名；三套 profile 各新增不可缺漏的 `blurb` 欄位（開放套名稱留舊值、補 blurb）。
- `seats.py`：fail-closed 驗證擴充為「七席齊、三套齊、每套具 `display_name`／`focus`／`blurb`」；既有讀取口回傳 `blurb`。
- 賽前 fail-closed 預檢與測試 fixtures 同步升版。
- `pages.py` 即時辯論頁席位卡顯示 blurb。
- 對應測試（roster 載入器 blurb fail-closed）。

不包含：

- `seat_id`、`output_dir`、提供者、`focus` 的任何變更。
- 把 blurb 帶進研究 prompt（blurb 僅顯示用）。
- 開放套顯示名稱重新命名（Spec 明列不在範圍內）。
- 舊 run 逐字稿回溯改名（已核准代價：舊 run 逐字稿舊席名與新標籤並存）。

## 已確認實作決策

- roster schema 升版：每套 profile 新增不可缺漏的 `blurb` 欄位（白話說明，僅顯示用，不進研究 prompt）。
- 載入 fail-closed 驗證擴充為「七席齊、三套齊、每套具 `display_name`／`focus`／`blurb`」；缺任一項載入即失敗並給可讀錯誤。
- 席位名稱與 blurb 的唯一權威是 `config/agent_roster.json`；離線報告席名隨 roster 自動一致，不另存副本。
- 即時辯論頁席位卡經 `seats.py` 既有讀取口取得 blurb，不新增讀取路徑。
- 不耦合 roster JSON 鍵順序。
- 定案內容逐字採用下表（取自 Spec〈R-005 席位名稱與白話說明〉，Spec 為文案權威）：

| # | seat_id | 股票套名稱｜blurb | 幣圈套名稱｜blurb | 開放套名稱（留舊）｜blurb |
|---|---|---|---|---|
| 1 | `spot-technical` | 技術面分析師｜看線圖：價量、均線、支撐壓力，判斷走勢強弱與關鍵價位 | 技術面分析師｜同左 | 圖表偵探｜看價量與技術結構，判斷走勢強弱 |
| 2 | `derivatives` | 籌碼面分析師｜看期貨與選擇權部位、融資融券：判斷大戶與散戶各押哪一邊 | 合約槓桿分析師｜看合約市場的槓桿狀態：多空部位、資金費率、清算風險 | 槓桿雷達｜看衍生品部位與槓桿狀態 |
| 3 | `onchain` | 法人動向分析師｜看法人與大股東的錢往哪走：買賣超、持股變化、資金流向 | 鏈上資金追蹤師｜看鏈上的錢往哪走：巨鯨動向、交易所進出、籌碼供給 | 鏈上獵人｜看鏈上資金與供給動向 |
| 4 | `official-events` | 官方公告哨兵｜盯官方公告與行事曆：重大訊息、財報法說日程、主管機關動作 | 官方公告哨兵｜盯官方消息：項目方公告、監管動作、重大事件時程 | 官方哨兵｜盯官方公告、監管與重大事件 |
| 5 | `news` | 新聞探員｜查證具名媒體報導、整理事件時間線，過濾未經證實的消息 | 新聞探員｜同左 | 新聞探員｜查證具名媒體報導、整理事件時間線 |
| 6 | `social-macro` | 輿情與大盤觀察員｜看散戶討論風向與大環境：論壇情緒、宏觀消息、大盤與產業連動 | 輿情與幣市社群觀察員｜看社群風向與大環境：討論熱度、宏觀消息、BTC 連動 | 社群觀察員｜看社群情緒與宏觀環境 |
| 7 | `counter-evidence` | 基本面分析師｜看公司本身體質：營收財報、估值比較、產業供需 | 項目體質分析師｜看項目本身體質：鎖倉量、協議收入、代幣解鎖時程 | 基本面研究員｜查核題目相關的關鍵數據與事實 |

## 驗收條件

- 台股／美股題席位卡顯示股票套定案名＋blurb；幣題顯示幣圈套定案名＋blurb；離線報告席名與 webapp 一致；roster 缺任一套或缺 `blurb` 時載入即失敗並給可讀錯誤。
- 三套 profile 的 `display_name` 與 `blurb` 與上表逐字一致。
- `seat_id`、`output_dir`、提供者、`focus` 與改版前完全相同。
- 賽前 fail-closed 預檢在升版後的 roster 上通過。
- 既有測試全綠。

## 測試與證據

- 測試接縫：roster 載入器 blurb fail-closed（缺 `blurb`／缺套組時載入失敗並回傳可讀錯誤）。
- 迭代期快速檢查：WSL 執行針對本票模組的單測 `python3 -m unittest tests.test_seats -v` 與 `python3 -m unittest tests.test_seat_profiles -v`（秒級）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、預檢執行輸出、席位卡渲染後 HTML 存檔與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：適用
- 判定依據：R-005 規範即時辯論頁席位卡的顯示名稱與白話說明，必須實際開頁核對。
- 操作環境與實際網址：本票階段無瀏覽器互動工具，依環境註記採渲染後 HTML＋關鍵元素斷言；整包瀏覽器走查列入工作包總驗收（T09 後）
- 使用的原生瀏覽器工具：無（渲染管道產出樣本）
- 操作步驟與預期結果：
  1. 開啟台股題的即時辯論頁：七張席位卡顯示股票套定案名，卡片下方顯示對應 blurb。
  2. 開啟幣題的即時辯論頁：七張席位卡顯示幣圈套定案名與對應 blurb。
  3. 開啟同一 run 的離線報告：席名與 webapp 顯示一致。
  4. 以缺 `blurb` 的 roster 啟動：載入即失敗並顯示可讀錯誤訊息。
- 操作結果：渲染樣本逐席比對通過——台股／幣題席位卡名稱＋blurb 成對、離線報告與完整辯論三處同名全 True；缺 blurb roster 拒載並指名席位／套組／欄位（preflight-r2.log）
- 操作證據：session scratchpad `t05\rendered-r2\`、`verify-r2.log`、`probe-r2.log`、`preflight-r2.log`（scratchpad 根：C:\Users\leslie\AppData\Local\Temp\claude\d--workstationD-hoya-bit\0fbeada5-380e-464b-b8d1-47036237db5d\scratchpad\）
- 環境註記：環境無瀏覽器時，依 Spec 測試決策以渲染後 HTML 存檔＋關鍵元素斷言為操作證據。

## 依賴

- Depends on：Ticket 04
- Blocks：Ticket 08

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：`config/agent_roster.json`、`hoya_market_agents/seats.py`、`hoya_market_agents/webapp/pages.py`（席位卡區）、賽前預檢、測試 fixtures、對應測試
- Shared resource locks：`hoya_market_agents/webapp/pages.py`（熱點鏈）、roster schema（三套 profile 結構）
- Can run with：Ticket 06、Ticket 07

## 初始執行配置

- Developer model：`claude-opus-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：涉及 roster Schema 升版與 fail-closed 啟動契約，牽動預檢與 fixtures，屬於 Schema 與公開契約變更，不適用低風險小修配置。
- 升級路徑：Claude 偏好為 `claude-opus-5`／`xhigh`；實際使用其他後端時由 Implement 採用該後端已驗證可用的升級設定；`max` 需使用者明確核准
- 執行時覆寫：最新使用者角色設定優先；偏好後端不可用時回退到唯一可用平台並留下紀錄
- Research 證據：無

## Agent 分工

- Developer：負責實作、驗證、修正或以證據反駁 Findings
- Reviewer A：`both` 時只執行 Spec Review；`a_only` 時執行 Spec 與 Standards
- Reviewer B：`both` 時只執行 Standards Review；`b_only` 時執行 Spec 與 Standards
- Reviewer 啟用規則：由執行 Task 最新 `settings_update: reviewers` 決定；預設 `both`，Ticket 不自行固定或搜尋設定
- Reviewer 標準：每位啟用 Reviewer 都載入 `$milktea-skills-code-review`，只執行 Coordinator 指定的 `review_axis`
- CLI 與模型：Developer 的上述配置只是相容預設偏好；實際配置依最新使用者角色設定與後端可用性決定，Reviewer 仍獨立決定

## 完成規則

- Developer 與各 Finding 的原 Reviewer 已處理所有可重現且有證據的問題。
- 沒有未解決的阻擋或重要正確性、可執行性、可讀性、架構或衍生風險。
- Developer 與各 Finding Owner 對關閉或撤回事由達成共識。

## 執行與 Review 紀錄

- Developer 結論：Claude（milktea-build／opus）TDD 交付。首輪快照 `c4c20e94a46762ca76ed187c57d22bad09571971`（11 檔、全套 2713 綠）；4 Major Findings 逐項重現後修正，最終快照 `e5d1faf8726ae864f9d0057edfeac3663c15ad68`（12 檔、對基準樹 `33e6ab329d16f559384991cbf344f8fff9c217a6` 累計 diff 1480 行）。全套 `Ran 2717 / OK (skipped=1) / RESULT_EXIT=0`；預檢升版 roster 通過、缺 blurb 拒載、凍結欄位（seat_id／output_dir／provider／target_model／tools／skills／focus）漂移 0。證據：session scratchpad `t05\`（ticket05-r2.diff、full-suite-r2.log、preflight-r2.log、repro/verify/probe-r2.log、rendered-r2\）。
- Reviewer 模式：both，皆 Codex CLI 0.146.0 native（A session `019feb0c-c242-7612-bfa1-aaba3656f5be`、B session `019feb0c-c99e-7ae0-a0d9-7cf802371259`；OCR revision preview 不吃 tree 物件，依規原生審查）。
- Reviewer A 結論（Spec 軸）：首輪 1 Major（F-A05-1 等待頁→新 run 的 blurb 錯配）；定向複驗 closed、通過、無新 Findings、🟢。
- Reviewer B 結論（Standards 軸）：首輪 3 Major（F-B05-1 跨 run 錯配＋測試無鑑別力、F-B05-2 預檢雙讀 TOCTOU、F-B05-3 schema 未升版）；定向複驗全部 closed（withdrawn=0）、通過、無新 Findings、🟢。
- 未關閉阻擋或重要 Findings：無。
- Ticket 最終驗收：完成。最終快照樹 `e5d1faf8726ae864f9d0057edfeac3663c15ad68`；三方對同一快照與證據達成共識。
- Coordinator 裁決（本票，列入結案報告複核）：
  1. 三個既有測試檔（test_report_renderer／test_webapp／test_frontend_redesign_acceptance）舊席名字面值更新授權——R-005 使舊斷言過期。
  2. test_frontend_redesign_acceptance 長短名包含歧義的判定修正授權（次數判定＋雙向鑑別測試）。
  3. 預檢委派 seats.load_roster 單一權威授權。
  4. 修 F-A05-1/F-B05-1 之寫入邊界窄擴充：webapp/live.py（席位欄位組裝）與 LIVE_SCRIPT；server.py 經驗證無需修改。
  5. roster_version 2.0.0→3.0.0（文件僅載明「升版」未指定號碼；新增必填欄位＝不相容變更，semver 主版進位；經 Reviewer B 複驗認可）。

## 阻擋與裁決紀錄

只有真正需要方向裁決時才追加下列欄位；一般 Bug 修正、測試失敗、Review Finding 或同一方案內的迭代不得寫成使用者阻擋：

- 原始需求：
- 目前理解：
- 實際卡住的原因：
- 已嘗試方案與證據：
- 為什麼不能繼續盲修：
- 簡單可行方案：
- Agent 建議：
- 需要使用者決定：
