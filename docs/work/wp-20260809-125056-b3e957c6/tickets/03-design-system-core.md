# 03 全站設計系統核心

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：01、02

> Blocked by 02 是**串接邊**而非程式相依：本票不改 02 的任何檔案，但兩者共用 roster 讀取路徑與全專案測試套件，串成鏈可避免不相干的紅燈。程式上的真實相依只有 01（席位標籤讀取口）。

## 目標

建立全站唯一的設計系統：設計 token 以資料形式落在 `webapp/pages.py`（沿用既有 `THEMES` 模式），全站收斂為單一樣式表，所有網頁頁面（含 run 詳情頁與保護區外衣）套同一套視覺，`ContrastTest` 直接從 token 表計算 WCAG AA，webapp 席位標籤改讀 Ticket 01 的讀取口。

## 對應原始需求

- R-001：頁面清單重整：即時辯論主頁、歷史與命中率合併頁、設定、run 詳情（功能保留、版面套新系統）、市場報告與完整辯論（內容不碰、右上角必可點）
- R-002：保護區功能凍結：動態聊天室、燈位、正方／反方／無法判斷票數的內容、位置、語意色不動，僅外衣可隨新設計系統統一
- R-003：全部網頁頁面套同一套新設計系統：語意色保留只校準色階、系統字型堆疊、零外部資源
- R-007：全繁體中文：webapp 畫面不得出現英文資料原值，標籤從既有權威帶出

- Spec R3 全站設計系統（全文，含「語意色（bull／bear／五燈）保留語意，只校準色階。系統字型堆疊，不引入任何外部資源」與設計方法段）。
- Spec R2 保護區：「**內容、位置、語意色不動**。可以動的只有字體、間距、卡片樣式——隨新設計系統統一。任何行為、門檻、計票、燈號語意的改動都不在本次授權範圍。」
- Spec R1 表格「run 詳情｜功能保留現況，版面套新設計系統（R3 適用於所有網頁頁面）」。
- Spec R7 全繁體中文中「標籤必須從既有權威帶出」的席位名稱部分（webapp 端）。
- Spec〈實作決策／資料與所有權〉：「設計 token（色彩／字級／間距）以資料形式落在 webapp `pages.py`，沿用既有 `THEMES` 模式；不散進各頁字串。」
- Spec〈實作決策／模組責任與公開介面〉webapp `pages.py` 條。
- Spec〈實作決策／相容、遷移與技術限制〉：「`ContrastTest` 直接從 token 表計算 WCAG AA，不維護第二份色值。」「設計參考 taste-skill 與 ui-ux-pro-max-skill clone 至工作區並記錄 commit SHA（比照 research skill 慣例），不進 Code Root。」
- Spec A4 全部三條。

## 使用者價值

各頁視覺一致、不像拼裝；辯論框的行為原封不動，只換外衣；色彩可讀性由測試而非目視保證。

## 範圍

### 進入範圍

1. **前置步驟（本票內完成，不另開雜務票）**：將 taste-skill 與 ui-ux-pro-max-skill clone 至**工作區**（不進 Code Root），記錄 commit SHA 於本 Ticket 的「執行與 Review 紀錄」，比照 research skill 慣例。以 ui-ux-pro-max-skill 產出金融場景的風格／配色／字體決策，以 taste-skill 做排版與密度紀律的執行審計。
2. **設計 token 表**：色彩、字級、間距以資料形式進 `webapp/pages.py`，沿用 `THEMES` 的兩套 palette 模式；語意色 `--bull`／`--bear`／五燈保留語意，只校準色階。
3. **單一樣式表**：辯論室原本隔離的樣式併入全站樣式表；全站只產生一份樣式來源。
4. **全頁套版**：即時辯論（主頁）、設定、run 詳情、既有歷史／命中率頁（本票只換外衣，合併留給 Ticket 04）全部改用同一套 token 與版面語彙。
5. **保護區外衣重設**：動態聊天室、燈位、正方／反方／無法判斷票數只調字體、間距、卡片樣式；DOM 內容、位置、語意色、行為完全不動。
6. **`ContrastTest` 從 token 計算**：對比度斷言直接讀 token 表，不維護第二份色值；報出實測數字。
7. **webapp 席位標籤改讀 Ticket 01 的讀取口**：`webapp` 端不得保留第二份席位顯示名稱表。
8. 系統字型堆疊；零外部資源（無 Google Fonts、無 CDN）；零 inline script；CSP 逐 directive 釘死沿用。

### 不進入範圍

- 歷史與命中率合併（Ticket 04）。
- 標的選單（Ticket 05）、PDF 匯出按鈕（Ticket 06）、關閉伺服器按鈕（Ticket 07）。
- 離線 HTML 報告版面（Spec〈不在範圍內〉）。
- 行動版專屬佈局（Spec〈不在範圍內〉）。

## 已確認實作決策

- token 是唯一色值來源；樣式表與對比度測試都從 token 推導。
- 保護區「功能凍結、外衣可換」：任何行為、門檻、計票、燈號語意的改動都不在本次授權範圍。
- 設計參考 skill 不進 Code Root。
- 規則值一律走 reload-aware API 讀取，不得在模組層凍結（Ticket 13 十三輪防過的缺陷，改版時不得復發）。

## 驗收條件

1. 全站樣式由單一樣式表產生；辯論室不再有獨立隔離樣式表。
2. 色彩、字級、間距皆可在 token 表一處改到，改 token 後渲染輸出跟著變。
3. `ContrastTest` 從 token 表計算並斷言 WCAG AA 通過，測試輸出含實測對比度數字。
4. 渲染後的 HTML 不含任何外部網域資源引用（字型、CSS、JS 皆無）。
5. 渲染後的 HTML 零 inline script；CSP 逐 directive 設定與改版前同等嚴格或更嚴。
6. 主頁保護區的 DOM 結構、元素順序與語意色 class 與改版前一致；聊天室、燈位、三種票數的行為測試全部沿用且全綠。
7. run 詳情頁、設定頁、歷史／命中率頁渲染後皆使用同一套 token 產生的樣式表。
8. webapp 渲染出的席位名稱來自 Ticket 01 的讀取口：台股 fixture 顯示股票套名稱、幣圈 fixture 顯示幣圈套名稱。
9. 設定頁改規則後重新整理即見新值（規則值未被模組層凍結）。
10. 既有測試全綠。

## 測試與證據

- 測試接縫：設計 token 表（可讀取、可比對）；token → 對比度計算；席位標籤讀取口在 webapp 端的注入點；既有 webapp 渲染測試。
- 迭代期快速檢查：`python3 -m unittest tests.test_webapp`（WSL）與新增的 token 測試模組（秒級）。
- Ready for Review 完整驗收：`cd <Code Root> && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`（WSL）。若專案仍沿用 Ticket 13 的 T08 攔截器，依該慣例補上對應環境變數，實際值由 Coordinator 提供，不寫入票面。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：完整驗收結果與退出碼、各頁渲染後 HTML 存檔、對比度實測數字表、保護區 DOM 前後 diff、外部資源與 inline script 的 grep 結果為零、兩個設計 skill 的 commit SHA、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票變更直接影響使用者介面（Spec R3／R2），但原生瀏覽器「代驗」不適用——平台無任何 Claude／Codex 原生瀏覽器工具（已於 2026-08-10 如實回報），由使用者本人於 2026-08-10 以本機瀏覽器親自實機操作驗收並明示「結案」授權；輔以票面原核准慣例之渲染後 HTML 存檔＋關鍵元素斷言證據（29 項全 PASS、對比度 50 對實測、保護區五區塊 DOM 前後 IDENTICAL，見執行與 Review 紀錄）。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
  1. 啟動 webapp，開主頁 → 頁首、焦點列、四個計時、聊天室、即時票數、七席、三個摺疊面板全部存在且套新樣式。
  2. 開設定頁 → 版面與主頁同一套視覺語彙；改一項規則存檔後重新整理 → 顯示新值。
  3. 開 run 詳情頁 → 功能與改版前相同，版面已套新設計系統。
  4. 開歷史／命中率頁 → 已套新樣式（合併留待 Ticket 04）。
  5. 切換淺色／深色 palette → 兩套皆通過對比度斷言。
  6. 檢視主頁原始碼 → 無外部網域資源、無 inline script。
- 操作結果：不適用
- 操作證據：不適用

## 依賴

- Depends on：01（席位標籤讀取口）、02（串接邊）
- Blocks：04

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：
  - `hoya_market_agents/webapp/pages.py`
  - `hoya_market_agents/webapp/live.py`（僅樣式引用與席位標籤來源）
  - `hoya_market_agents/webapp/settings.py`（僅版面相關，不動規則載入行為）
  - `tests/test_webapp.py`
  - 新增 `tests/test_design_tokens.py`
- Shared resource locks：`hoya_market_agents/webapp/pages.py`（本案最大共用熱點，Tickets 03～07 全部經過此檔，一律以 Blocked by 串成單鏈，任何時刻只有一張票持有寫入權）；全專案 unittest 套件。
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`
- model_reasoning_effort：`high`
- 路由理由：跨模組設計（token 抽象、樣式表收斂、對比度計算來源改變）、影響全部頁面的公開輸出，且必須在不改動保護區行為的前提下完成，屬高風險大範圍改動。
- 升級路徑：`claude-opus-5`／`xhigh`；`max` 需使用者明確核准
- Research 證據：無（設計 skill clone 與 SHA 記錄屬本票範圍內步驟）
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

### 開始執行（2026-08-09 18:15 +0800）

- Execution environment：沿用 Task 設定（`source: auto_current`）：Windows 宿主＋`wsl.exe -d Ubuntu-24.04 --` 前綴；WSL 路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；python3 3.12.3。
- 並行批次：batch-3（單票；上游 01、02 已完成並釋放 `pages.py` 寫入鏈與全專案測試鎖）。
- 基準版本：main @ `9b8a451`＋工作樹（含 T01、T02 成果），基準樹 `60f4e47c0b68a7842af38cd5b28e84e328e26bc2`（已驗證 ≠ HEAD^{tree}、含標記檔）。基準全套：2223 tests OK (skipped=1)、exit 0（Ticket 02 結案驗收）。
- 開發角色：Developer＝Claude Code 子 Agent（`milktea-build`），model `claude-opus-5`（票面偏好）；`model_reasoning_effort`：派工工具無 effort 欄位，採後端預設並記錄原因（同前兩票）。Reviewer A／B＝Codex（gpt-5.6-sol／high）。
- 設計參考 skill 來源解析（Coordinator 查證，票面前置步驟用）：taste-skill＝`https://github.com/Leonxlnx/taste-skill`、ui-ux-pro-max-skill＝`https://github.com/nextlevelbuilder/ui-ux-pro-max-skill`（兩者名稱與 Spec 用詞逐字吻合；clone 至工作區 `D:\workstationD\hoya bit\`，不進 Code Root，commit SHA 由 Developer 記錄於本節）。
- Exclusive write scope：依票面（webapp/pages.py、webapp/live.py 僅樣式引用與席位標籤來源、webapp/settings.py 僅版面、tests/test_webapp.py、新增 tests/test_design_tokens.py）。注意：webapp 檔案與 test_webapp.py 為未追蹤檔，diff 須用 `git show <基準樹>:<path>`＋`git diff --no-index` 產出。
- Shared resource locks：`webapp/pages.py`（03–07 鏈上熱點，本票持鏈）；全專案 unittest 套件（本票持有）。
- 必跑指令（Ready for Review 完整驗收）：`cd /mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`（WSL，腳本檔取退出碼）。

### Ready for Review（2026-08-09 19:05 +0800）

- 設計參考 skill（clone 至工作區，未進 Code Root）：taste-skill＝`D:\workstationD\hoya bit\taste-skill` @ `e988add20dab0fa97d7a76781c48961c8184288e`；ui-ux-pro-max-skill＝`D:\workstationD\hoya bit\ui-ux-pro-max-skill` @ `abb7f2fd5a083fa1ff55c326a963ff0d95c33f99`。設計方向：ui-ux-pro-max 的 Data-Dense Dashboard＋Financial Dashboard＋Real-Time Monitoring 條目（字型建議因需 Google Fonts 違反零外部資源而改系統堆疊）；taste-skill 的排版／密度紅線（單一 accent、單一圓角尺度、hairline 分隔、密度 8–10）。
- 實作：`THEMES`（兩套 palette）＋`SCALE`（字型／字級／間距／圓角）為唯一值來源；`CONTRAST_REQUIREMENTS` 由角色集合生成非手寫；`.stance-*`／`.outcome-*`／provider 條紋由 token 表生成；單一樣式表（`LIVE_ROOM_CSS` 與 `_document(style=)` 刪除）；席位標籤改讀 Ticket 01 讀取口；死 CSS 六組清除。語意色命名沿用既有 ballot 語彙 `affirm/oppose/abstain`（一角色一名，event 題第一立場非「偏多」），角色一對一保留只校準色階。
- 完整驗收：**exit 0，Ran 2259 tests，OK (skipped=1)**（基準 2223＋36；腳本檔取碼）。迭代期另過 693 項。
- 必交證據在案：前後渲染 HTML 存檔（/tmp/t03-before、/tmp/t03-after）；29 項前端關鍵元素斷言全 PASS；對比度實測數字表（11 個 token×4 底色全逾門檻，accent 門檻自 3.0 收緊為 4.5）；保護區五區塊 DOM 前後 IDENTICAL＋class 詞彙集合相等測試；外部資源與 inline script grep 全零；CSP 逐位元未變（server.py 未修改）；禁改清單 14 檔 cmp 全 UNCHANGED。
- `settings.py` 經查全檔無版面字串（標記全在 pages.py），未修改，票面範圍該項無可改之處。
- 變更檔案 4＋1 新增：pages.py（+474/−222）、live.py（+54/−21）、test_webapp.py（+358/−45）、新增 test_design_tokens.py（+243）；`live._seat_fields` 改名公開 `live.seat_fields(asset_class)`（原私有無外部使用者）。
- Review snapshot：基準樹 `60f4e47c`＋blob 雜湊 pages.py=225b6100、live.py=c0171e16、test_webapp.py=d31ecec9、test_design_tokens.py=8712fe07；未動鄰檔 settings.py=b5a20915、views.py=9e2e3293、server.py=514748d5。
- Developer 移交事項（Coordinator 已受理）：**views.py:413 席位標籤仍讀 open 套視圖**，同一 tw_stock run 辯論室與 run 詳情頁席名不一致；views.py 屬 Ticket 04 檔案，該行修正已列入 Ticket 04 派工契約。過渡 view `SEAT_IDENTITIES`／`SEAT_DISPLAY_NAMES` 因 report_renderer 與 views.py 仍引用而保留。
- Developer 提請 Review 特別檢視：保護區內立場文字由墨色轉語意色（死 CSS 復活，DOM 零變更）是否在 R2「僅外衣」授權內；辯論室改為跟隨深色模式；語意色 token 命名未用票面字面 `--bull/--bear`。

### Review 紀錄（2026-08-09）

- Reviewer A（spec 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：7 檔 blob 雜湊逐行吻合（含 3 個未動鄰檔）。驗收 1–10 逐條通過（對比度實測最低 light 5.91／dark 6.96 對 4.5 門檻；保護區五區塊 cmp 全 0）；裁決項二（辯論室跟隨深色模式）判定符合票面與 R3；裁決項三（affirm/oppose/abstain 命名）判定滿足 R3——「R3 要求的是 bull／bear 語意保留，而非強制 CSS identifier」；settings.py 未修改判定合理（版面所有權在 pages.py）。**結論：不通過**，Findings（Owner＝Reviewer A）：
  - **A-1［重要］pages.py:1918**：保護區立場文字的實際語意色由墨色變為綠／紅／琥珀。改版前隔離 CSS 的 selector（stance-positive/negative/neutral）與保護區既有 class（stance-affirm/oppose/abstain）不匹配、實際繼承墨色；合併後 `_semantic_rules()` 產生可匹配規則。DOM 逐位元相同只證明結構未變；「死規則原有意圖」不能取代改版前實際呈現，也不在 R2 可動清單（字體、間距、卡片樣式）內。期望：不得讓保護區既有立場文字取得改版前沒有的語意著色，或先取得 R2 範圍變更授權。
- Reviewer B（standards 軸、native、codex CLI 0.146.0／gpt-5.6-sol／high、隔離 session）：7 檔 blob 前後雙重驗證吻合。六項全過（品味總評 🟡、致命問題無）：token 架構的三個 mutation probe 各自產生 failure（雙向可失敗）；單一樣式表無旁路（離線 renderer 的 .evidence-card 自帶樣式、不讀 webapp 表，非殘留）；公開介面衛生通過；測試品質通過（無 CSS 順序／色碼字面／DOM 巢狀耦合）。**結論：通過**，2 個建議級 Findings（Owner＝Reviewer B，不阻擋）：
  - **B-F01［建議］test_design_tokens.py:164**：色彩唯一來源守衛可被「剛好等於既有 palette 值」的字面色碼繞過；建議另斷言規則區色彩字面值集合為空。
  - **B-F02［建議］pages.py:142/1981**：停用的 artifact tab 保留 `href="/"`，`pointer-events:none` 只擋滑鼠、鍵盤 Enter 仍可導覽；建議停用時渲染無 href 的非連結元素。
- Coordinator 依影響範圍指定修正後重驗：A-1 修正屬 pages.py 樣式生成層 → `tests.test_webapp`＋`tests.test_design_tokens` 迭代檢查、重出保護區前後呈現證據（含樣式解析層，不只 DOM）、完成後全套一次（帶攔截器、腳本檔取碼）。A-1 交回 Reviewer A 定向複驗；B-F01／B-F02 由 Developer 修復或附理由不修，交 Reviewer B 表態。

### 修正輪（2026-08-09 20:30 +0800，Developer fixed，待定向複驗）

- **A-1 fixed（路徑 1，Developer 接受 Finding 未反駁）**：`STANCE_COLOUR_TOKENS`（色彩所有權，仍四筆、仍受量測）與新表 `PAINTED_STANCE_CLASSES`（實際被畫者）切分；後者只含 `stance-unknown`（改版前唯一真的被畫的 class）。Developer 實測發現影響面比 Finding 更大（`span.tally-label` 也連帶變色），一併修正。樣式解析層證據：自製 CSS 串接解析器對前後頁面計算保護區 computed colour——修正前「角色改變元素」18 個，修正後 **0 個**（其餘差異均為同角色校準色階，R3 允許）；`live.py` 實際產出的三個 stance class 前後皆 `(no rule)`。三條新守衛測試紅→綠，並自行抓掉一個「讀自己要守的表」的弱測試（改從 `live.STANCE_CLASSES` 取凍結集合）。`--affirm` 成為「宣告但無規則讀取」的預期狀態，由測試與註解釘死。
- **B-F01 fixed**：新增規則區零色彩字面值守衛；B 的探針實測由綠轉紅。
- **B-F02 fixed**：停用 tab 改 `<span role="link" aria-disabled>` 無 href 無 tabindex；連帶改寫兩個寫死「停用時也是 `<a>`」前提的既有測試（理由記於 docstring），新增無 href／無 tabindex 釘樁測試。
- 指定重驗：迭代 609 tests OK；**全套 2264 tests OK (skipped=1)、exit 0**（基準 2223；腳本檔取碼）。禁改 14 檔 cmp 重驗 any-change=0；保護區五區塊 DOM 仍 IDENTICAL；`home` 整頁 markup 唯一差異＝B-F02 的頁首 tab（保護區外）；CSP 未變；外部資源與 inline script 全零。
- 修正輪 Snapshot：pages.py=92c3b29a、live.py=c0171e16（本輪未動）、test_webapp.py=62e71827、test_design_tokens.py=17760f72。

### 定向複驗（2026-08-09）

- Reviewer B 複驗（resume 原 session，Owner 連續性保留）：4 檔雜湊吻合。**B-F01 closed**（重放探針：舊集合守衛 failures=0、新規則區守衛 failures=1，旁路已封）；**B-F02 closed**（停用態無 href／tabindex 驗證通過；兩個既有測試改寫判定「只移除錯誤前提、未弱化」，可用測試反而加強了必為 `<a>` 的斷言）。Reviewer B 明確回報：「standards 軸最終結論：通過。」
- Reviewer A 複驗 A-1（resume 原 session）：4 檔雜湊吻合。**A-1 closed**——`stylesheet()` 實際掃描：stance-affirm／oppose／abstain 各 0 條 selector、stance-unknown 1 條 muted 規則；`span.tally-label` 只剩字級規則、恢復繼承本文色；五保護區 cmp 全 0；守衛測試從 `live.STANCE_CLASSES` 獨立取凍結集合，不會因 `PAINTED_STANCE_CLASSES` 擴充而失效；殘餘差異全屬同角色色階校準（ink→text、muted→muted、bear→oppose），符合 R3。Reviewer A 自行重跑 test_design_tokens（23 OK）與 A-1 相關三類別（17 OK）；test_webapp 的 7 個 socket 生命週期測試因 codex 沙箱禁 socket 報 EPERM，正確歸因於沙箱並採信 Developer 的 2264 全套證據。Reviewer A 明確回報：「spec 軸最終結論：通過。」

### 共識（2026-08-09 21:10 +0800）

A-1（重要）由 Owner closed；B-F01、B-F02（建議）由 Owner closed。Developer 與兩位 Finding Owner 無未解事項。最終 Snapshot：pages.py=92c3b29a、live.py=c0171e16、test_webapp.py=62e71827、test_design_tokens.py=17760f72（settings.py／views.py／server.py 未動）。首次完整驗收 2259 OK；Findings 後指定重驗 2264 OK (skipped=1)、exit 0。未解風險（移交）：views.py 席位標籤讀 open 套（已列入 Ticket 04 派工契約）；`--affirm` 為 R2 凍結下宣告未用的 token（測試釘死）。

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

## 阻擋與裁決紀錄
