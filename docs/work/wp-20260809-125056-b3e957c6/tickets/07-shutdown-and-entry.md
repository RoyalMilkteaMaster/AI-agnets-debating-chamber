# 07 關閉伺服器與入口動線

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：06

## 目標

頁面右上角「關閉伺服器」按鈕經 `POST /shutdown` 優雅停機並照常寫 `server_stop`；啟動與關閉邏輯移入 Code Root `scripts/`；工作區根目錄只留「開啟辯論室」與「關閉辯論室」兩個隱藏視窗捷徑，刪除 `辯論室預覽.html` 與 `開啟辯論室.bat`。

## 對應原始需求

- R-004：入口動線改造：刪除辯論室預覽.html 與開啟辯論室.bat、桌面捷徑隱藏啟動、頁面「關閉伺服器」按鈕與備援關閉捷徑

- Spec R4 入口動線改造（全部六條）：刪除 `辯論室預覽.html`；`開啟辯論室.bat` 退役（刪除）；桌面捷徑隱藏視窗啟動、偵測已在跑時只開瀏覽器；頁面右上角「關閉伺服器」按鈕走優雅停機並寫 `server_stop`；備援「關閉辯論室」捷徑；伺服器仍為前景程序模型，關閉一律走優雅路徑。
- Spec〈實作決策／模組責任與公開介面〉：「Code Root `scripts/`：承載啟動與關閉邏輯，工作區根目錄現行的 `start-webapp.ps1` 併入此處。工作區根目錄只留兩個捷徑……」
- Spec〈實作決策／Schema、API contract 與系統互動〉端點契約第 4 條：「`POST /shutdown`：先回覆「已關閉」頁面，再優雅停機，照常寫 `server_stop`。」
- Spec〈實作決策／相容、遷移與技術限制〉：「無殘留執行緒。」
- Spec A1 全部五條。

## 使用者價值

雙擊就開、按一下就關；不再有關不掉的黑框，也不再有誤導人的靜態快照檔。

## 範圍

### 進入範圍

1. 新增端點 `POST /shutdown`：先回覆「已關閉」頁面給瀏覽器，再優雅停機；照常寫 `server_stop`；不留殘留執行緒。
2. 頁面右上角新增「關閉伺服器」按鈕（表單 POST，零 inline script）。
3. Code Root 新增 `scripts/`，承載啟動與關閉邏輯；工作區根目錄現行的 `start-webapp.ps1` 併入此處。
4. 工作區根目錄新增兩個捷徑：
   - 「開啟辯論室」：PowerShell 隱藏視窗啟動；偵測伺服器已在跑時只開瀏覽器，不重複啟動。
   - 「關閉辯論室」：備援，打 `POST /shutdown`。
5. 刪除工作區根目錄的 `辯論室預覽.html` 與 `開啟辯論室.bat`；`start-webapp.ps1` 併入 `scripts/` 後不在工作區根目錄留第二份。
6. 伺服器維持前景程序模型；關閉一律走優雅路徑，不使用強制終止。

### 不進入範圍

- 改為背景服務或常駐程序模型。
- 任何辯論、投票、燈號行為。
- 前面各票已完成的頁面內容（本票只在既有頁首加一顆按鈕）。

## 已確認實作決策

- 停機必須先回應、後停機，讓使用者看得到「已關閉」頁面。
- `server_stop` 的寫入行為與現行一致，不改格式、不改位置。
- 捷徑放工作區根目錄，啟動與關閉的實際邏輯放 Code Root `scripts/`（捷徑只是薄殼）。
- 刪檔為使用者明確要求的結果（Spec R4、A1 第 5 條）。

## 驗收條件

1. `POST /shutdown` 先回傳「已關閉」頁面（HTTP 200 且內容可讀），之後伺服器停止監聽。
2. 停機後 log 中出現 `server_stop`，格式與改版前一致。
3. 停機後無殘留執行緒（以測試斷言驗證）。
4. `GET /shutdown` 不會觸發停機。
5. 頁面右上角存在「關閉伺服器」按鈕，且渲染後 HTML 零 inline script。
6. Code Root 存在 `scripts/`，內含啟動與關閉邏輯；工作區根目錄不再有第二份 `start-webapp.ps1`。
7. 工作區根目錄存在「開啟辯論室」與「關閉辯論室」兩個捷徑。
8. 執行「開啟辯論室」捷徑：不出現主控台黑框，瀏覽器開到主頁。
9. 伺服器已在跑時再次執行捷徑：不產生第二個伺服器程序，只開瀏覽器。
10. 執行「關閉辯論室」捷徑：伺服器停止。
11. 工作區根目錄不存在 `辯論室預覽.html` 與 `開啟辯論室.bat`。
12. 既有測試全綠。

## 測試與證據

- 測試接縫：shutdown handler（可在測試中驗證「先回應、後停機」的順序與 `server_stop` 寫入）；啟動腳本的「已在跑則只開瀏覽器」判定（可注入探測結果）。
- 迭代期快速檢查：`python3 -m unittest tests.test_webapp_shutdown`（WSL，秒級）。
- Ready for Review 完整驗收：`cd <Code Root> && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`（WSL）。若專案仍沿用 Ticket 13 的 T08 攔截器，依該慣例補上對應環境變數，實際值由 Coordinator 提供，不寫入票面。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：完整驗收結果與退出碼、停機順序與 `server_stop` 的測試輸出、殘留執行緒斷言結果、捷徑實際執行的過程說明與觀察結果（有無黑框、是否重複啟動）、刪檔前後的工作區根目錄清單、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票是純粹的使用者操作動線需求（Spec R4／A1：雙擊捷徑、按按鈕關閉），但原生瀏覽器「代驗」不適用——平台無任何 Claude／Codex 原生瀏覽器工具（已於 2026-08-10 如實回報），由使用者本人於 2026-08-10 親自雙擊捷徑、開啟頁面實機操作驗收並明示「結案」授權；輔以本票 Developer 的實機程序驗證（捷徑六段驗證、零新 PID、優雅停機、server_stop 落 log）與渲染存檔＋斷言證據（見執行與 Review 紀錄）。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
  1. 雙擊「開啟辯論室」捷徑 → 無主控台黑框，瀏覽器自動開到主頁。
  2. 伺服器已在跑時再雙擊一次 → 只開新分頁，不產生第二個伺服器程序。
  3. 頁面右上角按「關閉伺服器」→ 出現「已關閉」頁面，隨後伺服器停止監聽。
  4. 重新啟動後改用「關閉辯論室」捷徑 → 伺服器同樣停止。
  5. 檢視工作區根目錄 → 沒有 `辯論室預覽.html`、沒有 `開啟辯論室.bat`、沒有第二份 `start-webapp.ps1`。
- 操作結果：不適用
- 操作證據：不適用

## 依賴

- Depends on：06
- Blocks：08

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：
  - `hoya_market_agents/webapp/server.py`
  - `hoya_market_agents/webapp/pages.py`（關閉伺服器按鈕）
  - 新增 Code Root `scripts/`（啟動與關閉腳本）
  - 工作區根目錄 `D:\workstationD\hoya bit\`：新增兩個捷徑；刪除 `辯論室預覽.html`、`開啟辯論室.bat`；移除併入後的 `start-webapp.ps1`
  - 新增 `tests/test_webapp_shutdown.py`
- Shared resource locks：`hoya_market_agents/webapp/pages.py` 與 `hoya_market_agents/webapp/server.py`（Tickets 03～07 共用熱點，本票是鏈尾）；工作區根目錄（本票是唯一有權在此刪檔的票）；全專案 unittest 套件。
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`
- model_reasoning_effort：`high`
- 路由理由：涉及程序生命週期（優雅停機、殘留執行緒）、新增公開端點、跨 Code Root 與工作區的檔案結構調整，以及**不可逆的刪檔**，屬高風險。
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

### 開始執行（2026-08-10 08:40 +0800）

- Execution environment：沿用 Task 設定（`source: auto_current`）：Windows 宿主＋`wsl.exe -d Ubuntu-24.04 --` 前綴（專案測試）；本票捷徑與腳本屬 Windows 端，PowerShell 操作在宿主執行。WSL 路徑 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`。
- 並行批次：batch-7（單票；上游 01–06 完成，鏈尾）。
- 基準版本：main @ `9b8a451`＋工作樹（含 T01–T06 成果），基準樹 `d0df5fb9fa7fb98378a2ff86a4d10aec8c2998e5`（已驗證）。基準全套：2497 tests OK (skipped=1)、exit 0（Ticket 06 驗收）。
- 開發角色：Developer＝Claude Code 子 Agent（`milktea-build`），model `claude-opus-5`（票面偏好）；effort＝後端預設（派工工具無 effort 欄位，原因同前）。Reviewer A／B＝Codex（gpt-5.6-sol／high）。
- **裁定九（刪檔保全）**：工作區根目錄的刪除目標（`辯論室預覽.html`、`開啟辯論室.bat`、併入後的 `start-webapp.ps1`）不在 git 管理內、刪除不可逆——刪除前必須先完整複製到 `D:\workstationD\hoya bit\backups\wp-b3e957c6-t07-entry-files-20260810\`，備份完成並驗證位元一致後才准刪除。刪檔本身為 Spec R4／A1 第 5 條的使用者明文要求。
- Exclusive write scope：依票面（server.py、pages.py 關閉按鈕、新增 Code Root scripts/、工作區根目錄 `D:\workstationD\hoya bit\` 的兩個新捷徑與三個刪除目標、新增 tests/test_webapp_shutdown.py；tests/test_webapp.py 依慣例僅限牽動斷言）。
- Shared resource locks：`pages.py`／`server.py`（本票鏈尾）；工作區根目錄（本票唯一有權刪檔者）；全專案 unittest 套件。
- 必跑指令（Ready for Review 完整驗收）：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`（WSL，腳本檔取碼）。

### Ready for Review（2026-08-10 09:30 +0800）

- `POST /shutdown`：先回 200「已關閉」頁、後停迴圈（順序是量測的——注入接縫錄下「呼叫當刻客戶端已收到的位元組」）；`server_stop` 一字未動（仍由 serve_webapp finally 寫、Ctrl+C 與按鈕同路徑）；新增 `shutdown_requested` INFO；`GET /shutdown` 404 不停機。`ServerStop` 解決 handler／server 建立順序問題，bind 失敗不 attach、無靜默 no-op。
- `pages.py`：右上角 `.top-actions` 內共用 `_stop_form()`（表單 POST 零 inline script）；已關閉頁無按鈕無導覽（連結即將全部失效）；樣式僅 2 條規則全 `var()` 零色值字面。
- `scripts/`：webapp-common（跨語言三值各一份、註解指名 Python 權威）、start（已在聽→只開瀏覽器 exit 0；隱藏視窗啟動；40 秒逾時印可重跑命令）、stop（只打端點、任何情況不強制終止）、install-shortcuts（.lnk 可重建）。兩個 .lnk 就位（WindowStyle=7＋`-WindowStyle Hidden` 參數）。
- **裁定九執行並經 Coordinator 獨立驗證**：備份於 `backups/wp-b3e957c6-t07-entry-files-20260810/`，三檔 SHA-256（b72f8dad…／1f26c354…／0ec683e3…）與來源一致（Developer 雙雜湊器＋Coordinator sha256sum 三方吻合）後才刪除。子 Agent 平台安全警示（不可逆刪除）以此授權鏈解除：刪除目標為 Spec R4／A1-5／票面範圍 5 逐字點名、備份可還原。
- 完整驗收：**exit 0，Ran 2517 tests，OK (skipped=1)**（基準 2497＋20）。殘留執行緒斷言拆兩條有依據（stdlib daemon_threads 設計；無非 daemon 殘留＋有界歸零），實機兩次停機後 wsl PID 全消、埠零 listener。
- 實機捷徑驗證（隔離埠＋正式埠）：啟動→聽；重複啟動→零新 PID 只開瀏覽器；關閉腳本與關閉捷徑→停；誠實邊界聲明在案（「無黑框」目視與瀏覽器分頁留待使用者最終驗收，佐證為 .lnk 屬性＋MainWindowHandle=0＋啟動 powershell 立即結束）。
- 變更：server.py(+113/−3)、pages.py(+116/−11)、新增 test_webapp_shutdown.py(+365，20 tests)、scripts/ 四檔（+390）；test_webapp.py 未牽動未修改。
- Review snapshot：基準樹 `d0df5fb9`＋blob：server=789ed3d7、pages=dfff215c、test_webapp_shutdown=7ffb03f2、webapp-common=c20e0dc3、start=9b091c0e、stop=8d5ed328、install-shortcuts=0acdd1cf；未動鄰檔 test_webapp=6666033c、launch=098f8ec6、pdf_export=fafd8b67。
- Developer 判斷點供 Review：「已在跑」＝埠有人聽（與伺服器同一判斷）；跨語言三值雙份（註解指權威）；stop 腳本預設 8765；`.lnk` 不入 git（install-shortcuts 可重建）；daemon 執行緒形狀。

### Review 紀錄（2026-08-10）

- Reviewer A（spec 軸、native、codex CLI／gpt-5.6-sol／high、隔離 session）：10 檔雜湊吻合。驗收 1–12 逐條通過（含：停機順序有量測證據；殘留執行緒兩條斷言判定符合 Spec 本意——前者守程序不卡住、後者防 daemon 掩蓋永久殘留；「無黑框」證據形態判定符合票面據實記錄慣例，目視留使用者；另自行執行無 socket 子集 12 tests OK）；裁定九合規（唯讀核對備份三檔雜湊）；停機語意通過（stop 腳本無任何強制終止路徑）；Never break userspace 通過（唯一行為變更為驗收 9 授權項）；五個判斷點全過。**Findings：無。結論：通過。**
- Reviewer B（standards 軸、native、codex CLI、隔離 session）：10 檔雜湊吻合。停機順序測試 🟢（量測法可信、執行緒斷言拆法有據）、頁首重構 🟢（自行重跑 design tokens 23 OK）、衍生風險 🟢（事件詞彙無白名單解析、log 邊界合規）。**結論：不通過**，Findings（Owner＝Reviewer B，皆重要級）：
  - **F-01［重要］server.py:754**：stop callable 拋例外落入通用 `_guarded`，在已送出的 200 後再送 500——fake socket 中出現兩個 HTTP status line，違反 `_stop_serving` 自己宣告的「不能送第二份回應」。期望：200 送出後 stop 失敗只記錄明確失敗事件，不得再寫第二組 headers。
  - **F-02［重要］webapp-common.ps1:142**：WSL 命令以裸單引號包路徑、未 escape 內含 `'`——合法路徑（如 `/mnt/d/Leslie's data`）使 `bash -n` unmatched quote、exit 2。期望：所有合法 Windows 路徑安全轉成 shell argument（至少正確處理單引號），以行為測試驗證、不鎖死完整 argv 字串。
  - 測試品質部分不通過：缺 stop 例外與 shell-safe 路徑兩類行為型回歸。

### 修正輪與共識（2026-08-10 11:00 +0800）

- Developer 修正（兩項全接受，並自我點名根因：docstring 宣告了程式沒守住的保證）：F-01——`stop()` 拿到自己的例外邊界（比照 `_live_events` 慣例），例外只記 ERROR `server_stop_failed`（訊息指引讀者看有無 `server_stop`），不再落入 `_guarded` 回應路徑；三事件分工明確。F-02——`ConvertTo-ShellQuoted`（單引號自接規則，docstring 論證對每個合法 Windows 路徑成立），三層行為驗證（bash -n 新舊對照、stub argv 完整性、含 `'` 路徑真實啟停含 log 落地）。紅→綠：F-01 三條先重現 B 證據後轉綠。
- **F-02 測試缺口的裁定**：專案無 PowerShell 測試框架，套件內測試會 skip 並污染 `skipped=1` 閘門基準；Coordinator 裁定本票不引入新框架（跨票決策），以文件化可重跑行為驗證代替——Owner B 複驗時明確接受此處置。
- Reviewer B 定向複驗（resume 原 session）：3 檔雜湊吻合。**F-01 closed**（原 probe 現為 status_line_count=1、事件序列 shutdown_requested→server_stop_failed、無 request_failed）；**F-02 closed**（bash -n exit 0、Leslie's data 單一 argv 到達）。「standards 軸最終結論：通過。」
- 共識成立：A 首輪通過零 Finding、B 兩項重要級全 closed。最終 Snapshot：server=33f7be8e、pages=dfff215c、test_webapp_shutdown=0446d13b、webapp-common=4e8994c9、start=9b091c0e、stop=8d5ed328、install-shortcuts=0acdd1cf（基準樹 `d0df5fb9`）。完整驗收 2517→2520 tests OK (skipped=1)、exit 0。
- 未解風險（記錄）：埠占用誤判已在跑（明文揭露）；跨語言三值雙份（註解指權威）；stop 腳本預設 8765；.lnk 不入 git（install-shortcuts 可重建）；daemon 執行緒形狀（兩條斷言釘住）；PowerShell 自動化測試層屬跨票框架決策（移交事項）。

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

## 阻擋與裁決紀錄
