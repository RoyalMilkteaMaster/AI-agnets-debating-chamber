# WSL-only Runtime、零經驗安裝與七席可靠性規格

- 狀態：已核准
- 日期：2026-08-12
- 需求來源：`../../planning/requirements.md`〈WSL-only Runtime、零經驗安裝與共用可靠性修復〉
- 架構來源：`../../planning/architecture.md` §15
- ADR：`../../adr/0009-wsl-only-provider-runtime.md`
- 實作基準：`e05bf493e2f05dca37e15d6d10721c418c2c37e3`

## 問題

產品同時維護 Windows 原生與 WSL Provider Runtime，導致 CLI 登入環境、PATH、輸出編碼、程序樹回收與 timeout 行為分岔。Windows 真實 run 曾出現七席有派工卻只有部分有效票的情況；WSL2 Ubuntu 的 Linux 原生 Provider 路徑較穩定。繼續修補雙 Runtime 會擴大故障面，且不增加市場研究能力。

新使用者可能完全沒有 WSL 經驗，現有 README 又混入大量內部 preflight、Codex Task 與開發文件，無法作為單一路徑教學。現有 Live 頁也有送出後跳轉、精確 run 綁定、倒數倒退、attempt 終局不透明與 late result 可能覆寫既有結果等共用問題。

## 目標

1. WSL2 Ubuntu 成為 webapp、Python controller 與全部 Provider CLI 的唯一正式 Runtime。
2. 完全沒有 WSL 經驗的使用者能依 README 的可複製指令完成安裝、登入、啟動與關閉。
3. Windows 桌面捷徑、Ubuntu 終端與 MobaXterm 都操作同一套 WSL Runtime。
4. Live 頁在原頁完成 launch、精確 run 綁定、倒數、失敗與完成提示。
5. 修正 WSL／共用的 process group、backup、terminal outcome、late result、lineage 與 independent Opening 契約。
6. 以真實市場題在既有時間規則內完成一次恰好七席 `7/7` 有效最終票與可驗證報告。

## User Stories

1. 身為完全沒有 WSL 經驗的新使用者，我希望 README 從安裝 WSL2 Ubuntu 開始提供可複製指令，以便不理解內部架構也能使用產品。
2. 身為日常使用者，我希望雙擊「開啟辯論室／關閉辯論室」就能安全操作同一個 WSL webapp，以便不必手動輸入 PowerShell。
3. 身為使用 MobaXterm 或 Ubuntu 終端的使用者，我希望執行與捷徑相同的 Bash 入口，以便不維護第二套 Runtime。
4. 身為市場分析操作人，我希望送出問題後留在 Live 頁、看到正確場次與不倒退的辯論倒數，以便確定正在觀看自己剛啟動的 run。
5. 身為除錯者，我希望每席 primary／backup 的 Provider、階段、終局與失敗碼可追溯，以便區分沒有派工、逾時、輸出無效與備援耗盡。
6. 身為發行負責人，我希望真 Provider 問題一出現就停止驗收、修正後重跑，以便最後的 `7/7` 是真實結果而非部分成功或 fake drill。

## 需求與行為

### R-001 — 唯一正式 Runtime

- 正式支援 Windows 10／11 上的 WSL2 Ubuntu。
- webapp、Python controller、Claude、Codex 與 Antigravity CLI 全部在 WSL 執行。
- Windows 只負責 WSL 安裝、桌面捷徑、瀏覽器及以 `wsl.exe` 呼叫 Bash；不得執行 Provider。
- MobaXterm 必須連入同一個 WSL2 Ubuntu，不得建立另一套 Code／Data／Runtime。
- 其他 WSL2 Linux 發行版為盡力相容，不納入 release gate。

### R-002 — 零經驗 README

- README 只保留既有 hero 圖片及本節教學；其餘既有 README 文字全部移除。
- 教學順序固定為：
  1. `[Windows]` 執行 `wsl --install -d Ubuntu`。
  2. 重新開機、首次啟動 Ubuntu 並建立帳號。
  3. `[WSL／Ubuntu]` 安裝 `git`／`python3`、在家目錄 clone 正式 Git remote。
  4. 執行 `bash setup-wsl.sh`。
  5. 依第一手官方命令安裝並互動登入 Linux 原生 Codex、Claude 與 Antigravity CLI。
  6. 使用兩個 Windows 桌面捷徑，或執行 `./START-HERE.sh`／`./STOP-HERE.sh`。
  7. MobaXterm 進入同一 WSL2 Ubuntu 的最短操作方式。
  8. 極短故障排查：命令不存在、未登入、8765 被占用、Log 位置。
- README 不得出現開發者帳號、`D:\workstationD`、Anaconda、READY、preflight、Codex Task 或內部驗收步驟。
- setup 不下載、更新或登入 Provider，不讀寫或顯示 credential。

### R-003 — 三個 Bash 入口與兩個捷徑

- Code Root 提供 `setup-wsl.sh`、`START-HERE.sh`、`STOP-HERE.sh`，均由自身位置解析 Code Root 與同層 Data Root。
- 專案不建立 `.venv`；沒有第三方 Python dependency 時不得假裝安裝套件。
- `setup-wsl.sh` 確認 WSL2 Ubuntu 與 `python3` 後，呼叫 Windows 內建 PowerShell 安裝捷徑。
- Windows Desktop 最終只存在：
  - `開啟辯論室.lnk`
  - `關閉辯論室.lnk`
- 兩個 `.lnk` 共用一支 `scripts/wsl-shortcut.ps1`，只以 `wsl.exe` 和 login Bash 呼叫根目錄入口。
- setup 重跑結果相同；精確移除舊的 `WSL 開啟辯論室.lnk`、`WSL 關閉辯論室.lnk` 與可確認的舊同名入口，不得掃描或刪除其他捷徑。
- Windows 原生 `start-webapp.ps1`、`stop-webapp.ps1`、`webapp-common.ps1` 在確認無呼叫者後退役移除。

### R-004 — Runtime ownership 與安全關閉

- `GET /health` 成功時回傳且只把下列必要欄位作為 ownership contract：

```json
{
  "app": "hoya-market-agents-webapp",
  "runtime_owner": "wsl",
  "instance": "non-empty-per-server-value",
  "active_run": false
}
```

- `active_run` 必須是 JSON boolean；缺欄位、錯型別、404、malformed JSON 或不同 `app/runtime_owner` 都視為 foreign／unknown。
- `START-HERE.sh` 遇 owned WSL listener 時只開啟瀏覽器；遇 foreign listener 時顯示一行衝突原因並以非零碼退出，不換 port、不終止 listener。
- `STOP-HERE.sh` 先取得 owned WSL 的 `instance`，再以 form body 傳入 `expect_runtime=wsl` 與 `expect_instance=<value>` 呼叫 `POST /shutdown`。
- server 必須在 POST 當下重新核對。listener 被替換或 claim 不符時回 `409` 且保持運行。
- `active_run=true` 時預設不關閉；只有互動 Bash 或捷徑確認框明確同意才可送出 shutdown。
- ownership consumer 集中於 Python runtime-control 模組；Bash／PowerShell 不複製 JSON 判斷。

### R-005 — 同頁 launch 與精確 run

- `live.js` 攔截 Live form submit，以 `fetch` 呼叫既有 `POST /launch`；送出後停用重複提交並顯示小型動畫。
- 合法請求回 `202`：

```json
{"status":"pending","launch_token":"..."}
```

- 輸入拒絕回穩定問題碼與單行繁體中文 `reason`；busy 使用 `409`。頁面只顯示 `啟動失敗：<reason>　[重試]`。
- `GET /launch/status?token=...` 只回：
  - `pending`
  - `launched`＋精確 `run_id`
  - `failed`＋單行 `reason`
  - 未知 token 的 `404 unknown`
- status 只能讀 `LaunchLock`、child process 與 token-bound atomic handshake；不得使用 newest run、`latest.json` 或 run index 推測。
- handshake 必須同時符合 token、合法 `run_id` 且精確 run directory 已存在。
- 取得 `run_id` 後，同頁關閉舊 EventSource、清除舊 run-local state、連接 `/live/events?run=<run_id>`，並以 `history.replaceState` 保存可刷新 URL。
- 不建立或使用 `/launch/wait` HTML 頁，不整頁跳轉。
- 分析完成且 manifest 與 `report.html` 都有效時只顯示 `分析完成　[查看市場報告]`；不自動跳頁、不播放聲音、不發瀏覽器通知。
- 啟動路徑不再要求 READY certificate；題目／標的格式驗證仍保留。

### R-006 — 權威時間與開始辯論倒數

- run 未完成時，elapsed 由注入 clock 減 `question.json.created_at_utc`；完成後使用有效 manifest 的 `elapsed_ms`。
- 不得以最後一則訊息的 `elapsed_ms` 代表 run clock。
- `ChatRoom` 遇 `debate_opened` 時設為 sticky，後到舊事件不得取消。
- `debate_start_remaining_ms` 是唯一欄位：`research_deadlines(question_type).seal_ms - authoritative_elapsed_ms`；已 opened 或剩餘小於等於零時為 `null`。
- 初始 HTML、SSE snapshot／append／done、reconnect 與 refresh 使用相同 projection。
- UI 第二格標題為「開始辯論剩餘時間」；正值以向上取整秒顯示，最後 1～999ms 顯示 `00:01`，不得顯示 `00:00`；`null` 顯示「辯論已開始」。
- 同一 run 的 JS elapsed 只能單調不減；stale frame 不得增加 remaining 或解除 started latch。換 run 才重設。
- 17 分鐘總窗、報告 deadline、四輪 offset 與 `config/debate_rules.json` 不改。

### R-007 — WSL Provider process group

- 每個 Provider invocation 在 WSL 以獨立 POSIX session／process group 啟動並保存 group id。
- timeout、cancel、first-valid-wins 與 cutoff 先使該 generation 不可採用，再以有界 `SIGTERM`、grace、`SIGKILL` 回收整個 group。
- registry 以 attempt key＋generation 區分同 key resume；同 key reclaim 序列化，不同 key 可並行。
- terminate 等待 reclaim lock 後必須重讀 settled outcome；poisoned track 也必須在相同 lock 內回收。
- outcome 只表示：已回收、已確認無需回收、或 `process_tree_termination_failed`。最後一種永久禁止該 attempt 採用。
- 不實作或移植 Windows Job Object、`taskkill`、CP950 decoding 或 Windows fallback。

### R-008 — 主備、terminal outcome 與可見 lineage

- primary roster 與模型配額維持既有 3 Codex／3 Claude／1 Antigravity。
- 每席最多建立一個不同 Provider backup，沿用已核准 Provider candidate order 與固定模型；最多十四個 research attempts 可進入 worker。
- Provider CLI 找不到時，該 attempt 以 `provider_cli_missing` 終止，recovery 繼續，不阻擋 webapp 或整場 run。
- `ResearchScheduler.attempt_outcomes` 是唯一 terminal outcome 權威，允許值為 `adopted`、`superseded`、`failed`、`cancelled`、`late_discarded`。
- attempt 一旦 finished 或 terminal non-adoptable，任何後到 result 只寫 diagnostic，不得進 `adopted_records` 或覆寫既有 outcome／failure。
- 失敗至少包含 `seat_id`、`attempt_id`、`provider`、`attempt_kind`、`phase`、`failure_code`、`failure_message`。
- 穩定 failure code 至少涵蓋：`provider_cli_missing`、`provider_start_failed`、`provider_timeout`、`provider_empty_output`、`provider_malformed_output`、`research_proof_missing`、`research_result_window_closed`、`process_tree_termination_failed`。
- events 與既有 `research-summary.json` 以加法欄位保存 primary／backup、requested／started、provider、requested／actual model、terminal outcome、failure、adopted／exhausted。
- Live 席位卡讀同一 projection；已 adopted 優先於另一 attempt 的 late failure。舊 run 缺欄位時顯示「未記錄」，不得猜測。

### R-009 — per-attempt research schema

- 通用 `RESEARCH_ENVELOPE_SCHEMA` 保留為模板，不得在 invocation 間原地修改。
- 每次正式 research 呼叫建立 deep copy，並以單值 JSON Schema `enum` 鎖定：
  - envelope `seat_id`
  - 每張 card 的 `run_id`
  - 每張 card 的 `seat_id`
  - 每張 card 的 `attempt_id`
- Claude、Codex、Antigravity 三個正式 research callsite 都使用 per-attempt schema。
- `RealEvidenceGateway` 繼續驗證相同 lineage 與 asset contract；schema 是提早拒絕，不取代 gateway。
- 平行建立不同 schema 不得共享或污染巢狀物件。

### R-010 — 獨立 Early Opening

- 某席第一個合法 research result adopted 後，可在 evidence seal 前派出一次獨立 Opening provider call。
- Opening 使用 adopted attempt 的 Provider／model lineage，只能讀該席自己的 evidence 與題目，不得讀其他席證據。
- Opening 是新的 provider output 與 phase，不得把 research envelope、研究摘要或 Core 文字直接轉成投票。
- seal 前完成的 Opening 暫存；seal 後按既有 DebateStateMachine contract 發布。未完成仍走既有 opening deadline／缺席規則。

### R-011 — Codex proof 條件式修復

- 實作前先以一個真實 WSL Codex research canary 保存命令、版本、退出碼及清理後 machine-readable events。
- 未重現 `research_proof_missing`：不得修改 parser，只把 canary 證據寫入完成報告。
- 可重現：parser 只計入 matching search tool invocation 與其非錯誤 result；URL、引用、模型自述、一般 final text 或 stderr 不算 proof。
- 無事件、malformed event、tool-use-only 或沒有 matching result 時 fail closed，回 `research_proof_missing` 或更精確格式錯誤。
- artifact 只保存清理後的 invocation count、parse status、malformed count；不得保存 credential、完整 prompt 或未清理 stderr。

### R-012 — 資料、報告期限與真實驗收

- setup、啟停與本次 schema 變更不得刪除、搬移、重新格式化或回溯改寫 Data Root／舊 run。
- 基準版 report completion deadline 修正必須保持通過：到 deadline 使用期限內已取得證據完成誠實報告，不無限等待。
- 正式驗收順序為 Codex、Claude、Antigravity WSL canary，再跑真實市場題。已知失敗立即中止該次驗收，修正、Review 後重跑。
- 最終 run 必須：
  - 七個固定席位恰有 `7/7` 有效最終票。
  - `report.html`、`debate.html`、`report.md`、`evidence.jsonl`、`debate.jsonl`、`votes.json`、manifest 存在且相符。
  - `verify-run` 成功。
  - 遵守既有研究、seal、四輪、硬停、報告與總時間規則。
  - 沒有 Windows Provider process。
- 單次成功只代表固定版本與當下環境通過 release gate，不宣稱外部 Provider 永久可用。

## 實作決策

### 資料與所有權

- `RunStore` 與 Data Root artifacts 仍是 run 事實來源；SQLite index 可重建。
- `ResearchScheduler` 擁有 attempt terminal outcome；`ProcessRegistry` 擁有 invocation process verdict；Live 只讀 events／summary projection。
- webapp server 產生 runtime ownership；runtime-control client 消費並提交 shutdown precondition。
- `LaunchLock` 擁有短生命期 launch token 與 handshake path；token 不持久化、不作登入認證。

### 模組責任與公開介面

- `webapp/runtime_control.py`：health parsing、owned/foreign 判定、conditional shutdown。
- `webapp/server.py`：`/health`、`/shutdown` precondition、launch/status JSON、run-pinned SSE。
- `webapp/launch.py`：token-bound child／handshake state；不解析 newest run。
- `webapp/live.py`：authoritative elapsed、debate-start projection、attempt projection、completion link。
- `webapp/static/live.js`：同頁 submit／status polling、EventSource lifecycle、run-local monotonic state。
- `provider_cli.py`：WSL `PATH` 解析與穩定 missing failure，不作登入管理。
- `claude_adapter.ProcessRegistry` 與各 adapter：POSIX process group lifecycle。
- `research_scheduler.py`：first-valid-wins、backup、terminal outcome 與 late diagnostic。
- `real_provider.py`：Provider/model mapping、per-attempt schema、lineage 與 independent Opening dispatch。

### Schema、API contract 與系統互動

- 所有新增 artifact 欄位均為加法；舊 reader 缺值時使用 `None／未記錄`。
- `/health` 必要欄位及型別依 R-004；shortcut stop 使用 form-encoded shutdown precondition。
- `/launch` 與 `/launch/status` 依 R-005；頁面和 SSE 一律使用精確 `run_id`。
- research schema 與 failure／terminal outcome 依 R-008、R-009；不得從文字訊息反推機器值。

### 相容、遷移與技術限制

- 不新增第三方 Python、Node runtime dependency、daemon、資料庫或 logging framework。
- Node 只允許作 release-time production JavaScript harness，不是使用者執行產品的前置條件。
- Windows 原生 Provider 路徑明確退役，不提供隱藏 fallback。
- 保留 dirty worktree供唯讀比對；只在新 branch 重建或選擇性移植經定向測試證明必要的 WSL／共用修正。
- 新增 ADR 0009；不新增其他 ADR。

## 驗收條件

1. README 只含 hero 圖與完整 WSL 教學；全新使用者流程無個人硬編碼路徑。
2. setup 連續執行兩次後，Windows Desktop 仍精確只有兩個正確捷徑；Data Root hash／歷史 run 可讀性不變。
3. shortcut、WSL 與 MobaXterm 都啟動同一 WSL server；Windows process tree 沒有 Provider。
4. owned、foreign、malformed、404、active run 與 listener replacement 案例符合 R-004，沒有誤關。
5. Live 同頁取得精確 run；錯誤與完成提示各只有一行；無 waiting page、聲音、通知或自動報告跳轉。
6. 一般題／比較題倒數、stale SSE、reconnect、refresh、done 與換 run 測試皆不倒退或顯示 `00:00`。
7. POSIX 父子孫回收、finish/cancel race、same-key generation、different-key parallel 均有確定性測試。
8. 七席 primary 與 backup、terminal outcome、late diagnostic、summary／Live projection、per-attempt schema 與 independent Opening 測試通過。
9. offline suite 不呼叫真 Provider；完整 WSL Python suite、Bash syntax、PowerShell 靜態／隔離測試與 production JS harness 通過。
10. 真實 WSL run 達成 R-012 的 `7/7`、artifact、verify 與時間門檻；任何失敗都有先中止再修正重跑的證據。

## 測試決策

- 公開行為優先：HTTP status/body、檔案 artifact、process 存活、桌面捷徑欄位、DOM 可見文字與 exit code。
- 使用 `FixedClock`、暫存 Data Root、fake `Popen`／fake listener、barrier 與可控 handshake；不得以長 `sleep` 猜競態。
- mutation-style 證據必須能證明移除 finished guard、generation、per-attempt enum、POST-time precondition或 JS monotonic guard 時定向測試轉紅。
- JavaScript harness 以 Node VM 執行 production `live.js`，不以搜尋原始碼字串自證行為。
- 真 canary／final run 與單元測試分開；只有明確標示的 acceptance 命令可呼叫 Provider。
- Review 使用固定 Snapshot，由 Spec 與 Standards 兩位獨立 Reviewer 分別核對；Developer 未修正 finding 前不得進下一張 Ticket。

## 不在範圍內

- Windows 原生 Provider Runtime、Windows Job Object、`taskkill`、CP950 decoding、Windows PATH refresh 或 Windows 真實七席驗收。
- 自動安裝／更新／登入 Provider、credential 管理、模型狀態檢查或全功能環境診斷平台。
- 新 port、自動換 port、遠端存取、多使用者登入或雲端部署。
- 一般回答格式重構、行動版專屬佈局、舊 run 回溯重製。
- 聲音、瀏覽器通知、大面積錯誤面板或自動跳轉報告。

## 補充

- 原 dirty worktree 路徑維持封存，不作 Implement Code Root。
- Spec 與後續 Tickets 的唯一工作目錄是 `docs/work/wsl-only-runtime-onboarding/`。
- 本 Spec 核准後才可拆 Ticket；本階段不實作、不執行真 Provider。
