# 08 端到端驗收（A1–A5 逐條）

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：01、02、03、04、05、06、07

## 目標

以股票題與幣題兩組 fixture 跑完整流程，逐條驗證 Spec A1–A5，補齊跨票才看得出來的回歸缺口（換套一致性、保護區行為、繁中 grep、對比度實測數字），並產出可交付的驗收證據。

## 對應原始需求

- R-001：頁面清單重整：即時辯論主頁、歷史與命中率合併頁、設定、run 詳情（功能保留、版面套新系統）、市場報告與完整辯論（內容不碰、右上角必可點）
- R-002：保護區功能凍結：動態聊天室、燈位、正方／反方／無法判斷票數的內容、位置、語意色不動，僅外衣可隨新設計系統統一
- R-003：全部網頁頁面套同一套新設計系統：語意色保留只校準色階、系統字型堆疊、零外部資源
- R-004：入口動線改造：刪除辯論室預覽.html 與開啟辯論室.bat、桌面捷徑隱藏啟動、頁面「關閉伺服器」按鈕與備援關閉捷徑
- R-005：七席換套：股票／幣圈／開放三套依資產類別自動切換名稱與方向，seat_id 與 output_dir 永不改，含分工細表與提供者對調
- R-006：標的選單發問：資產類別選單＋標的輸入框，接 T05 的 assets／asset_class 接縫，建議清單來自過往 run 標的
- R-007：全繁體中文：webapp 畫面不得出現英文資料原值，標籤從既有權威帶出
- R-008：PDF 手動匯出：報告頁按鈕以 Edge 無頭模式轉出 report.pdf 與 debate.pdf 存入該 run 資料夾

- Spec A1 入口與停機（五條）。
- Spec A2 規則生效、報告可點、PDF 落地（四條）。
- Spec A3 換套與標的選單（五條）。
- Spec A4 保護區與全站綠燈（三條）。
- Spec A5 全繁體中文（四條，含「資料原值」定義與不在此限的清單）。
- Spec〈測試決策／公開行為〉全部八項。
- Spec〈測試決策／既有測試模式〉：「驗收基準：既有全綠 ＋ 渲染後繁體中文 grep ＋ 對比度實測數字。」「環境無瀏覽器，不得假稱有截圖。」

## 使用者價值

確認整套改版真的合起來能用：台股題與幣題各跑一次都對，辯論框行為沒走鐘，畫面全中文，顏色看得清楚。

## 範圍

### 進入範圍

1. **端到端 fixture 流程**：以既有 fake provider 與注入時鐘，跑一組 `tw_stock` 題與一組 `crypto` 題，從標的選單發問到報告產出。
2. **A1–A5 逐條驗證**：每一條驗收條件對應一個可判定的檢查，缺一不可；逐條記錄通過與否及證據位置。
3. **換套一致性回歸**：同一 run 的 webapp 席位標籤與離線報告席位標籤逐席比對相同；台股 run 的離線報告不含幣圈席名。
4. **保護區行為回歸**：動態聊天室、燈位、正方／反方／無法判斷票數的行為與改版前一致（沿用既有測試並補齊跨票缺口）。
5. **繁中 grep**：對 webapp 各頁渲染後輸出執行 grep，確認枚舉英文原值為零；標的代號、`run_id`、`seat_id`、evidence ID 不在此限。
6. **對比度實測數字**：兩套 palette 全部受測色對的實測數字表，達 WCAG AA。
7. **全站測試全綠**：`python3 -m unittest discover -s tests` 退出碼 0。
8. 產出彙整後的驗收證據，保存於本 Ticket 的「執行與 Review 紀錄」。

### 不進入範圍

- 修改前七票的產品程式碼（本票發現缺陷時回報並由 Coordinator 決定退回哪一票；不得在本票偷改）。
- 新增產品功能。
- 離線報告版面驗收（Spec A5 明列不納入）。

## 已確認實作決策

- 環境無可自動截圖的瀏覽器：沿用 Ticket 13 慣例，以渲染後 HTML 存檔加關鍵元素斷言作為證據，**不得假稱有截圖**。
- 「英文資料原值」定義沿用 Spec A5：權威詞彙表涵蓋的枚舉值必翻；標的代號（`2330`、`AAPL`、`BTC`）、`run_id`、`seat_id`、evidence ID 不在此限。
- 離線報告版面本次不動，不納入 A5 驗收，其回歸由既有測試守住。
- 捷徑與停機屬實機操作，須據實記錄觀察結果。

## 驗收條件

1. A1 五條逐條通過並各有證據（含捷徑無黑框、不重複啟動、`server_stop`、備援捷徑、兩個檔案已不存在）。
2. A2 四條逐條通過並各有證據（含改規則即時生效、兩份報告可點、PDF 落地且既有檔案未變、失敗不留半成品）。
3. A3 五條逐條通過並各有證據（含台股／幣題兩套顯示、離線報告一致、標的選單、roster fail-closed）。
4. A4 三條逐條通過：保護區行為與改版前一致的比對結果、全站測試退出碼 0、對比度實測數字表。
5. A5 四條逐條通過：webapp 各頁繁中 grep 結果為零命中，且報告中明列被排除的資料本體類別。
6. 台股 run 與幣題 run 的 webapp 與離線報告席位標籤逐席比對一致，比對表附於證據。
7. Spec〈測試決策／公開行為〉八項各至少有一個對應測試，並列出測試名稱對照表。
8. 全部驗收項目通過前，本 Ticket 不得標記完成；任何一條不過即回報並指名應退回的 Ticket。

## 測試與證據

- 測試接縫：既有 fake provider、注入時鐘、暫存目錄；前七票新增的各接縫（roster profiles 載入器、合併頁與轉跳、PDF 匯出假轉換器、shutdown handler、標的表單→launcher 參數傳遞、設計 token）。
- 迭代期快速檢查：`python3 -m unittest tests.test_frontend_redesign_acceptance`（WSL，秒級）。
- Ready for Review 完整驗收：`cd <Code Root> && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`（WSL）。若專案仍沿用 Ticket 13 的 T08 攔截器，依該慣例補上對應環境變數，實際值由 Coordinator 提供，不寫入票面。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：A1–A5 逐條對照表（條文、判定方式、結果、證據位置）、兩組 fixture 的渲染後 HTML 存檔、席位標籤逐席比對表、繁中 grep 完整輸出、對比度實測數字表、全站測試輸出與退出碼、公開行為↔測試名稱對照表、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票是整個工作包的使用者介面總驗收（A1–A5 全部使用者可觀察結果），但原生瀏覽器「代驗」不適用——平台無任何 Claude／Codex 原生瀏覽器工具（已於 2026-08-10 如實回報），由使用者本人於 2026-08-10 以本機瀏覽器親自實機操作（開啟捷徑、切換頁面、開啟市場報告）驗收並明示「結案」授權；輔以 A1–A5 逐條矩陣（二十一條全通過）、兩組 fixture 渲染存檔與 34 條跨票驗收測試（acceptance/ 目錄，見執行與 Review 紀錄）。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
  1. 雙擊「開啟辯論室」→ 無黑框、瀏覽器開主頁。
  2. 用標的選單選「台股」＋一檔標的發問 → 一頁式辯論室正常運作，七席顯示股票套名稱。
  3. run 產出後 → 右上角兩份報告可點；離線報告席位名稱與 webapp 一致，且無幣圈席名。
  4. 按「匯出 PDF」→ run 資料夾出現兩份 PDF，既有檔案未變。
  5. 開 `/stats` → 轉跳 `/history`；合併頁上統計卡、下 run 列表帶命中結果。
  6. 進設定頁改規則存檔 → 重新整理即見新值。
  7. 改用幣題重跑一次 → 七席顯示幣圈套名稱。
  8. 逐頁檢視 → 全繁體中文、無外部資源、無 inline script。
  9. 按「關閉伺服器」→ 出現已關閉頁面並優雅停機。
- 操作結果：不適用
- 操作證據：不適用

## 依賴

- Depends on：01、02、03、04、05、06、07
- Blocks：無

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：
  - 新增 `tests/test_frontend_redesign_acceptance.py`
  - 本 Ticket 檔案的「執行與 Review 紀錄」
  - 驗收證據存放位置（執行階段由 Coordinator 指定，不得寫入 run 資料夾）
- Shared resource locks：全專案 unittest 套件；驗收期間不得有其他票同時修改產品程式碼。
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`
- model_reasoning_effort：`high`
- 路由理由：需要跨全部七票判讀行為一致性、辨識回歸來源並指名退回對象，屬跨模組判斷，非局部低風險工作。
- 升級路徑：`claude-opus-5`／`xhigh`；`max` 需使用者明確核准
- Research 證據：無
- 執行時覆寫：無

## Agent 分工

- Developer：負責實作、驗證、修正或以證據反駁 Findings
- Reviewer A：`both` 時只執行 Spec Review；`a_only` 時執行 Spec 與 Standards
- Reviewer B：`both` 時只執行 Standards Review；`b_only` 時執行 Spec 與 Standards
- Reviewer 啟用規則：由執行 Task 最新 `settings_update: reviewers` 決定；預設 `both`，Ticket 不自行固定或搜尋設定
- Reviewer 標準：每位啟用 Reviewer 都載入 `$milktea-skills-code-review`，只執行 Coordinator 指定的 `review_axis`
- CLI 與模型：Developer 初始模型與推理強度以上述配置為準；Reviewer 由執行 Task 的角色設定獨立決定

## 完成規則

- Developer 與各 Finding 的原 Reviewer 已處理所有可重現且有證據的問題。
- 沒有未解決的阻擋或重要正確性、可執行性、可讀性、架構或衍生風險。
- Developer 與各 Finding Owner 對關閉或撤回事由達成共識。

## 執行與 Review 紀錄

### 開始執行（2026-08-10 11:15 +0800）

- Execution environment：沿用 Task 設定（`source: auto_current`）：Windows 宿主＋`wsl.exe -d Ubuntu-24.04 --` 前綴；WSL 路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；python3 3.12.3。
- 並行批次：batch-8（單票；上游 01–07 **全部完成**，全專案測試鎖與驗收期獨占予本票——驗收期間無任何其他票修改產品程式碼）。
- 基準版本：main @ `9b8a451`＋工作樹（含 T01–T07 全部成果），基準樹 `6daa2e45bd17d9389ea6e82f09a4ede5b0f29f82`（已驗證）。基準全套：2520 tests OK (skipped=1)、exit 0（Ticket 07 驗收）。
- 開發角色：Developer＝Claude Code 子 Agent（`milktea-build`），model `claude-opus-5`（票面偏好）；effort＝後端預設（派工工具無 effort 欄位，原因同前）。Reviewer A／B＝Codex（gpt-5.6-sol／high）。
- 驗收證據存放位置（Coordinator 指定）：`docs/work/wp-20260809-125056-b3e957c6/acceptance/`（不寫入任何 run 資料夾）。
- Exclusive write scope：依票面（新增 tests/test_frontend_redesign_acceptance.py、驗收證據目錄；本 Ticket 檔案由 Coordinator 寫）。**不得修改前七票產品程式碼**——發現缺陷即回報，由 Coordinator 決定退回對象。
- 已知範圍外殘留（不要試圖在本票修）：`report["asset_class"]` 值層驗證（T01 移交，修正點在 report_contract、本票禁改）；`report_renderer.py:38` 死別名；CATEGORY_LABELS 幣圈語彙；tracer／manifest 通用 focus；「Four boundaries」docstring 計數。
- 必跑指令（Ready for Review 完整驗收）：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`（WSL，腳本檔取碼）。

### 首輪交付（2026-08-10 12:20 +0800，A1–A5 矩陣完成、一條不過）

- 進入範圍 1–8 全部完成：兩組 fixture（tw_stock `20260314T015926Z-2330-aaa111`／crypto `20260315T015926Z-btc-bbb222`）端到端；新增 `tests/test_frontend_redesign_acceptance.py`（677 行、26 tests、6 類別，零 codex 呼叫 delta=0、零瀏覽器）；驗收證據 7 檔＋rendered/ 16 檔落於 `acceptance/`。前七票 28 個關鍵檔逐檔 hash 比對基準樹全 UNCHANGED（本票零產品碼改動）。
- **A1–A5 矩陣結果**：A1 五條通過（A1-1 帶誠實邊界照引 T07；A1-5 本票新增可程式驗證）；A2 四條通過（PDF 完整 bundle 差集恰兩檔、匯出前後 verify_run VERIFIED）；**A3-3／A3-1 正常路徑通過、退化路徑不通過（D-1）**；A3 其餘通過（席位標籤 7/7×兩市場×三來源一致、附突變證明）；A4 三條通過（**全套 2546 tests OK (skipped=1)、exit 0**；對比度 50/50 全逾門檻，最低 light 5.91／dark 3.75 border）；A5 四條通過（12 份渲染輸出、30 個權威推導枚舉零命中、植入控制有鑑別力、豁免值確實在頁面上）。
- 公開行為八項↔測試對照表齊（本票補第 4、6、8 項的跨票測試）。
- **D-1 已由 Coordinator 裁決退回 Ticket 01**（裁定十：report_workflow 驗證失敗骨架補 asset_class）；本票依授權未修產品碼、未把「加入即紅」的回歸測試放進套件（避免違反 A4-2），該測試已備好待 D-1 修復後加入。
- Review snapshot（首輪）：基準樹 `6daa2e45`＋新檔 blob `7eba8eb9`；acceptance/ 各檔 blob 在案。

### 第二輪交付（2026-08-10 15:10 +0800，D-1 關閉後）

- D-1 修復確認在樹上（三檔 blob 比對），本輪零產品碼改動（15 關鍵檔僅 D-1 三檔變動）。
- 新增 `TheDegradedReportStillKnowsItsMarketTest`（3 tests＋形狀前置斷言防空過）；與 T01 新測試分工明寫於 docstring（renderer 入口 vs 使用者整條路）。測試檔 677→759 行、26→29 tests（blob `b434af03`）。
- 突變驗證：執行期 `mock.patch` 拿掉答案欄位（零改檔）→ 3/3 紅且訊息各對應 D-1 三症狀、還原 3/3 綠。
- D-1 場景重驗：`report.json` 有 `asset_class=tw_stock`、仍在退化路徑（validation_failed）、離線兩頁零幣圈席名、七席全等於股票套且與 webapp 一致。
- **A1–A5 二十一條全部通過**（矩陣更新，A3-1/A3-3 含退化路徑；D-1 段改為已關閉含全程記錄）。acceptance/ 4 檔更新（blob 在案），23 檔 552K。
- 完整驗收：**exit 0，Ran 2551 tests，OK (skipped=1)**（帳目 2548＋3；skipped 既有；codex 攔截 39 次與基準同、本票貢獻 0）。
- Review snapshot（第二輪）：交付 5 檔 blob＝b434af03／36c604b4／0f324d95／9e671a8b／c7f06340；D-1 背景三檔＝bd93f38b／82fe0de5／8e715041。

### Review 紀錄（2026-08-10）

- Reviewer A（spec 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：8/8 blob 吻合＋產品檔抽查全同基準。票面驗收 1–4、6、7 通過；專項全過（兩層退化測試分工**不重複**——T01 走 workflow→renderer、T08 走選單→launcher→run→兩端輸出，能抓跨層接線遺失；A3-1/A3-3 改判成立；A1-1／真 Edge 誠實邊界充分）。**結論：不通過**，Finding（Owner＝Reviewer A）：
  - **A8-1［重要］test_frontend_redesign_acceptance.py:99**：A5 掃描的枚舉集合只合併 `OUTCOME_VERDICTS`，漏掉權威 `OUTCOME_STATES` 的 `pending`、`unreadable`（run_index.py:149 為權威；Spec A5 明列 `pending`）。探針：頁面植入這兩值仍全綠。期望：掃描集合由完整 `OUTCOME_STATES` 推導＋鑑別力斷言＋重新產出 a5-grep 證據。
- Reviewer B（standards 軸、native、codex CLI、隔離 session）：8/8 blob 前後吻合＋12 檔產品抽查全同。突變驗證 🟢（執行期 patch 判定優於改檔還原）；證據時序一致；fixture 陷阱處置合理；掛載說明查證屬實。**結論：不通過**，Findings（Owner＝Reviewer B）：
  - **B8-F1［重要］test_frontend_redesign_acceptance.py:327／665**：新測試綁定 Spec 禁止的 DOM 巢狀與精確 class 字串（article.agent→small、p→code、section/span/strong 相鄰、class="light"）——合法 wrapper／語意標籤調整會假紅。期望：以穩定語意標記／可見文字讀取。
  - **B8-F2［重要］test_frontend_redesign_acceptance.py:629**：三種票數只比總和——偏多 6/偏空 1 錯印成偏多 1/偏空 6 仍綠。期望：以 `package.stance_labels` 建立顯示詞彙→votes.json 數量映射逐項比較。
  - **B8-F3［建議］a1-a5-matrix.md:58**：A5-4 殘留舊計數 2546（應 2551）。

### 第三輪修正與共識（2026-08-10 17:00 +0800）

- **A8-1 fixed→closed**：掃描集合改讀完整 `OUTCOME_STATES`（30→32 值）＋權威覆蓋斷言＋植入探針轉紅；12 頁以 32 值重掃全零命中；a5-grep.txt 重產。Reviewer A 重放探針（planted_detected=['pending','unreadable']）後 closed，「spec 軸最終結論：通過；A1–A5 二十一條全部成立」。
- **B8-F1 fixed→closed**：讀取層重寫為 `_VisibleText(HTMLParser)`（不讀屬性、跳過 style/script、功能性 id 錨點）；四處耦合全消；429→8 class 免疫證明；鑑別力手段全保留＋2 條 FP 測試；`class="light"` 斷言刪除（擁有者為 T03 測試）。B 確認錨點取捨合理（id 未匯出常數屬非阻擋殘餘契約）後 closed。
- **B8-F2 fixed→closed**：逐立場精確比對＋「三數不全同」前置；票數對調突變證明新斷言紅、舊兩種斷言實測都放過；無真實錯印。B closed。
- **B8-F3 closed**：矩陣計數全檔校正 2556。
- Reviewer B：「standards 軸最終結論：通過。」
- 共識成立。最終 Snapshot：test_frontend_redesign_acceptance.py=86933509（962 行／34 tests／7 類別）；acceptance/ 更新檔 blob：matrix=cb92ec99、a5-grep=2c10cd1a、seat-label=72b93332、end-to-end-runs=fd7afd11、full-suite=512fe989。**全套 2556 tests OK (skipped=1)、exit 0**（帳目 2548＋8）。零產品碼改動（雙 Reviewer 抽查確認）。
- 殘留（結案報告列出）：`live-tally`/`live-feed` id 未匯出常數（產品碼、範圍外）；名稱位置斷言由版面測試擁有；T01 契約值層驗證仍未關；A1-1 目視項與真 Edge 一次性證據的邊界。

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

## 阻擋與裁決紀錄
