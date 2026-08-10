# 端到端驗收與證據

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：Ticket 03、Ticket 04、Ticket 05、Ticket 06、Ticket 07、Ticket 08

## 目標

對整包改動做一次端到端驗收並留存證據：渲染台股／幣圈題的全部 webapp 頁面與離線兩頁樣本存檔、A5 標準渲染後繁中 grep、對比度實測表、保護區行為回歸、發問選單斷言、導覽注入器實測、既有測試全綠；所有證據存入 `docs/work/wp-20260810-092746-8728cbc6/acceptance/`。

## 對應原始需求

- R-001：設定頁白話中文：每個規則項顯示中文標籤＋一句白話說明，分組標題中文化；規則檔新增而未翻譯的鍵顯示原鍵名＋「尚未翻譯」標註、照常可編輯（不 fail-closed）。
- R-002：五導覽全站常駐：即時辯論／歷史與命中率／市場報告／完整辯論／設定；市場報告與完整辯論在無特定 run 頁面指向最新有報告的 run、run 詳情頁指向自身 run、無報告時停用樣式；「伺服器已關閉」頁維持無導覽；離線兩頁經伺服器瀏覽時亦有五導覽，直接開檔／分享維持自足兩分頁導覽，run 檔案唯讀不回溯。
- R-003：設定獨立：「設定」與其他四個導覽分開，放「關閉伺服器」按鈕左邊。
- R-004：全站重新設計：Google 風白底、紅藍綠黃彩色點綴、極簡留白、半透明毛玻璃；含離線兩頁換新裝（新 run 起生效）；深色模式退場只留白底一套；微軟正黑體優先；語意色保留語意只校準色階。
- R-005：席位名稱與白話說明：股票套與幣圈套顯示名稱換為使用者定案名；即時辯論頁席位卡新增白話說明（僅顯示用，`focus` 權威不動）。
- R-006：開放題下架：發問選單只留台股／美股／幣；開放套留舊值當內部填充（fail-closed 檢查仍過）；後端 open 能力不刪；歷史開放題 run 照舊可回看。

## 使用者價值

對應全部 User Stories：使用者要的是「整包一起是對的」——每一頁都換好裝、導覽處處可達、設定看得懂、席位名與說明正確、選單乾淨，而且有可回查的證據證明這次改版沒有弄壞既有行為。

## 範圍

包含：

- 渲染並存檔台股題與幣圈題的全部 webapp 頁面樣本（即時辯論、歷史與命中率、設定、run 詳情、錯誤頁、關閉頁）。
- 渲染並存檔新 run 的離線兩頁（`report.html`／`debate.html`）樣本。
- A5 標準的渲染後繁中 grep 結果。
- 對比度實測表（含毛玻璃合成色）。
- 保護區（聊天室、燈位、三種票數）行為回歸結果。
- 發問選單斷言結果、導覽注入器實測結果。
- 既有測試全套執行結果。
- 全部證據寫入 `docs/work/wp-20260810-092746-8728cbc6/acceptance/`。

不包含：

- 任何產品程式碼修改（發現問題時退回對應 Ticket 修，不在本票直接改實作）。
- 舊 run 回溯重製。

## 已確認實作決策

- 本票只做驗證與證據彙整，不改任何實作檔案；寫入範圍僅限 `acceptance/` 目錄。
- 驗收方法沿用 Spec 測試決策：既有測試全綠＋渲染後繁中 grep＋對比度實測數字；環境無瀏覽器時以渲染後 HTML 存檔＋關鍵元素斷言作為操作證據。
- 不耦合不應耦合的實作細節：CSS 類名順序與色碼字面值、DOM 巢狀細節、roster JSON 鍵順序、注入導覽的確切 HTML 字串。
- 發現不符時，由 Coordinator 退回對應 Ticket 修正，再重跑本票驗收。

## 驗收條件

1. 設定頁渲染後：每個規則項顯示上表中文標籤＋白話說明、分組標題為中文；以測試設定檔加入未知鍵時，顯示原鍵名＋「尚未翻譯」且可編輯。
2. 除「伺服器已關閉」頁外，每個 webapp 頁面 header 有五導覽，「設定」位於「關閉伺服器」左邊；主頁初始／歷史／設定頁的報告導覽指向最新有報告 run，run 詳情頁指向自身 run；無報告時為停用樣式。
3. 經伺服器瀏覽 `report.html`／`debate.html` 時頁面出現五導覽且可回站內；磁碟上的檔案位元組不變；無插入點的 HTML 原樣送出。
4. 全站與新 run 離線頁呈現白底新設計；作業系統設深色時仍為同一套白底；成品樣式表無 `@media (prefers-color-scheme: dark)`；對比實測達 WCAG AA（含毛玻璃合成色）。
5. 台股／美股題席位卡顯示股票套定案名＋blurb；幣題顯示幣圈套定案名＋blurb；離線報告席名與 webapp 一致；roster 缺任一套或缺 `blurb` 時載入即失敗並給可讀錯誤。
6. 發問選單無「開放題」；歷史開放題 run 仍可開啟回看。
7. 保護區（聊天室、燈位、三種票數）行為與改版前一致；既有測試全綠；渲染後繁中 grep（A5 標準）通過。

上列七條全部通過，且每一條都有對應證據檔留存於 `docs/work/wp-20260810-092746-8728cbc6/acceptance/`。

（第 1 條所稱「上表」即 Spec〈R-001 設定頁白話中文〉的逐鍵文案表。）

## 測試與證據

- 測試接縫：沿用本工作包各票建立的接縫（注入器插入／略過、latest-report 解析、標籤表 fallback、roster blurb fail-closed、design_tokens → WCAG AA、發問選單三市場、保護區行為回歸）。
- 迭代期快速檢查：WSL 執行針對本票驗收測試模組的單測（例：`python3 -m unittest tests.test_frontend_redesign_acceptance -v`，秒級）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、渲染後 HTML 樣本、繁中 grep 輸出、對比度實測表、注入前後檔案位元組比對與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」（證據檔本體存於 `docs/work/wp-20260810-092746-8728cbc6/acceptance/`）。

## 前端實際操作驗收

- 適用性：適用
- 判定依據：本票要驗的是 R-001～R-006 的最終使用者可見結果，必須做一次全站走查。
- 操作環境與實際網址：執行階段填寫
- 使用的原生瀏覽器工具：執行階段填寫
- 操作步驟與預期結果：
  1. 全站走查台股題：即時辯論、歷史與命中率、設定、run 詳情、錯誤頁、關閉頁皆為白底新設計，header 五導覽與設定位置正確。
  2. 全站走查幣圈題：席位卡顯示幣圈套定案名與 blurb，其餘同上。
  3. 開啟設定頁核對逐鍵中文標籤與白話說明，並以未知鍵驗證「尚未翻譯」fallback 可編輯。
  4. 經伺服器開啟新 run 的 `report.html`／`debate.html`：出現五導覽可回站內；直接開檔則維持自足兩分頁導覽。
  5. 開啟發問頁確認只有台股／美股／幣三個選項，並開啟歷史開放題 run 確認可回看。
  6. 檢視即時辯論頁保護區：聊天室、燈位、三種票數與改版前一致。
  7. 將作業系統切為深色後重走 1、4：畫面仍為同一套白底。
- 操作結果：執行階段填寫
- 操作證據：執行階段填寫
- 環境註記：環境無瀏覽器時，依 Spec 測試決策以渲染後 HTML 存檔＋關鍵元素斷言為操作證據。

## 依賴

- Depends on：Ticket 01、Ticket 02、Ticket 03、Ticket 04、Ticket 05、Ticket 06、Ticket 07、Ticket 08
- Blocks：無

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：`docs/work/wp-20260810-092746-8728cbc6/acceptance/`（新增目錄）
- Shared resource locks：全套測試執行權、渲染樣本產生所需的暫存 run 目錄
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：跨模組整包驗證與證據彙整，需判讀七條驗收條件是否真的成立並定位不符來源，不屬於局部低風險小修。
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

- Developer 結論：Claude（milktea-build／opus）驗收彙整交付。首輪快照 `1fdce30f030fec18d5d0c449db3c6daced484f20`（43 新增檔全在 acceptance/）；F-B09-1 修正後最終快照 `9214747e03b53942500a483423a6a2c3f7d3bb53`（對基準樹 `1ac0192e024320a913e3211c9c72c668b7c25432` 共 46 A／0 M／0 D，acceptance/ 以外零異動）。七條驗收條件全 PASS；全套 `Ran 2729 / OK (skipped=1) / SUITE_EXIT=0`；生成器與重跑入口已入 repo（acceptance/tools/），從 /tmp 重跑 11 項判定全 PASS、41 檔中 36 檔逐位元組重現。
- Reviewer 模式：both，皆 Codex CLI 0.146.0 native（A session `019feb70-e4bc-74b2-84c9-f05ffdfa13a6`、B session `019feb70-f0d6-7ce3-bf32-2222a21591c5`）。
- Reviewer A 結論（Spec 軸）：零 Finding、PASS、🟢——獨立抽驗：A5 重掃 18 頁 0 命中、四份離線原檔 SHA-256 比對、checks.json 11 項逐一追到證據。
- Reviewer B 結論（Standards 軸）：通過、🟢——獨立 WCAG 重算 41 組全達標（最小餘裕 4.7729）、A5 植入式鑑別、位元組比對核實；1 個建議級 F-B09-1（「機器可重跑」措辭與腳本位置不符）修正後定向複驗 closed、無新 Findings。
- 未關閉阻擋或重要 Findings：無。
- Ticket 最終驗收：完成。最終快照樹 `9214747e03b53942500a483423a6a2c3f7d3bb53`；三方共識成立。
- Coordinator 裁決（本票）：checks.json 不記載「包含自身之樹」的雜湊（自指問題），最終快照 SHA 以本票面紀錄為權威登記——認可 Developer 落法。
- Developer 誠實揭露之驗證邊界（列入結案報告）：未實機瀏覽器走查（由工作包總驗收補）；深色模式驗至「無第二套樣式」；渲染兩趟 run 的外部邊界為注入替身；未知鍵測試頁附載入器紅字（R-001「可編輯不 fail-closed」仍成立）。

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
