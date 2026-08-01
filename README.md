# Hoya Bit Market Agents

七席多模型加密市場研究流程的 WSL Python 控制程式。

本版本提供兩條明確分離的路徑：`drill --provider-mode fake` 以假時鐘打通七席並行、
T+5 evidence seal、6/5/4 辯論與 T+13 報告；`preflight --provider system --mode real`
則只接受真實訂閱與 fresh Codex Task 的可觀察證據。fake 演練永遠標記
`competition_ready=false`，不得作為市場資料或真實 READY 證據。

三個 Codex 研究席只允許 `web_search`，禁止 filesystem 與 secret access；七席都必須
遵循 repo-local 固定版本的 `research` skill。真正的搜尋成功仍由每席 run-scoped receipt、
原始輸出與正式 evidence lineage 驗證，不會把 Claude/Gemini 的成功或 fake drill 冒充
七席 READY。

Ticket #3 另外提供版本化 Question Package 與 Research Prompt Builder：可正規化單幣、
兩幣比較、整體市場、事件影響四種題型，並把 repo-local 固定 research 規則、來源時間政策、
EvidenceCard contract 與操作邊界等價注入七席。這不代表 Ticket #2 的 fake run controller
已經執行兩幣或事件題；正式 controller 支援會依後續 tickets 接上。

## 執行環境

- WSL Ubuntu 24.04、Python 3.12（只使用標準函式庫）。
- fake run 與單元測試不需要 API key、登入或網路。
- Claude preflight 使用既有 claude.ai Max 登入；Antigravity preflight 使用既有 Google OAuth 登入。
- 不需要安裝套件、不需要 venv。
- Code Root 必須從乾淨 checkout 使用；所有 run/preflight 證據只寫到相鄰 Data Root。

所有指令都在 **WSL** 中、從 Code Root 執行。

確認 Python 版本（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 --version
```

> 本專案在 WSL 直接使用 `python3`，不需要安裝 `python-is-python3`。

## 執行測試

執行完整測試（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m unittest discover -s tests -v
```

只執行 Ticket #3 必跑測試（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m unittest tests.test_question_package tests.test_prompt_builder -v
```

測試全部離線：使用可注入的 fake clock 與 fake provider，不呼叫真實模型、不讀網路。

## 七席 competition drill（離線）

以下指令以固定假資料驗證整合時序；成功不代表真實訂閱 READY：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents drill --provider-mode fake \
  --question "分析 BTC 過去 14 日市場狀態"
```

輸出包含 `run_id`、七席 completion timeline、T+5 snapshot hash、辯論停止原因、
報告完成時間與 `verify-run` 結果。

## 整體 system preflight

先跑不耗訂閱的 fail-closed fixture matrix：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents preflight --provider system --seats 7 \
  --mode fixture --preflight-id rehearsal-fixture
```

fixture 即使全數通過仍輸出 `NOT_READY`；`simulation_status=PASS` 只表示回歸邏輯正常。
故障注入可使用 `--fixture-failure login|model|write|renderer`，每種都必須輸出
`NOT_READY`。

真實 preflight 必須從 fresh Codex Task 取得 `<CODEX_RUN_ID>` 與該次 one-time
`<CODEX_CHALLENGE>`。Operator 也必須在 provider preflight 前預先指定尚未使用的
`<COMPETITION_RUN_ID>` 與 `<COMPETITION_CHALLENGE>`：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents preflight --provider system --seats 7 \
  --mode real --codex-run-id <CODEX_RUN_ID> \
  --codex-challenge <CODEX_CHALLENGE> \
  --competition-run-id <COMPETITION_RUN_ID> \
  --competition-challenge <COMPETITION_CHALLENGE>
```

缺少任何登入、actual model、七席搜尋 receipt、權限、T+4:45 contract、T+5 seal 或
T+13 report 證據時都輸出機器可讀 `NOT_READY` manifest。
目前 Codex、Claude、Antigravity 的訂閱 CLI 都不提供可由第三方獨立驗證的
provider/runtime attestation。manifest 會把 `provider_runtime_attestation` 列為
`advisories`，但它不再阻止 operational READY 或 competition authorization。
本地 SHA-256 保留用來檢查 artifact integrity；它不會被描述成 provider 官方簽章。
預授權階段允許 `search`、`seven_seat_timeline`、`report_deadline` 三項 run-scoped
blocker；其他登入、actual model、權限、roster 與 Codex runtime receipt 通過時即可有
`provider_capabilities_ready=true` 與 write-once authorization。最終 operational
competition READY 仍只由該授權 run 的 `verify-run` 結果決定。
real run 還必須逐席保存綁定預授權 run/challenge 的 dispatch、completion、search、
public transcript 與 structured output receipts。receipt attempt 必須等於正式採納的
evidence attempt，出現在該席 debate messages 與 votes attempt_ids；解析後的 structured
output 經 canonical normalize 必須精確等於該席正式 evidence records。缺任一項即拒絕。

只執行 Claude Adapter 單元測試（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m unittest tests.test_claude_adapter -v
```

## Claude Opus 三席 preflight

此指令會使用既有 claude.ai Max 訂閱登入，並行驗證三個固定席位的 Opus、WebSearch、
structured output 與固定 session resume。它不接受 `ANTHROPIC_API_KEY`，也不輸出憑證或完整
session UUID：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents preflight --provider claude --seats 3
```

任一 CLI、登入、模型、搜尋、schema、resume 或路徑隔離檢查失敗時，輸出 `NOT READY`
並以非零 exit code 結束。

只執行 Antigravity Adapter 單元測試（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m unittest tests.test_antigravity_adapter -v
```

## Antigravity 單席 preflight

以下指令會使用 WSL 既有的 Google OAuth 登入，驗證 `agy` 版本、
`gemini-3.1-pro-high`、high effort、實際完成一次低風險 `search_web` 與 structured output：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents preflight --provider antigravity --seats 1
```

Schema、遮罩後的 CLI log 與 raw envelope 只會寫到 Data Root；未遮罩 temp log 會在
success、timeout 或 error 後清除，輸出不包含 OAuth token 或帳號資料。

## 執行一次分析

執行一次 fake run（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents run --provider-mode fake --question "分析 BTC 過去 14 日市場狀態"
```

指令會印出唯一 `Run ID`、Data Root、Run 目錄、Markdown 與 HTML 報告的絕對路徑，
以及票數與七席立場。

## 即時觀看辯論

執行中的 run 會把七席公開發言逐筆追加到 `events.jsonl`。以下唯讀本機頁面以 SSE
長連線非同步接收新發言、改票與規則切換；倒數由瀏覽器本地即時更新，不會每隔固定時間
重送 HTTP 請求，也不會改寫任何 run artifact：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents live \
  --data-root "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents_data" \
  --host 127.0.0.1 --port 8765
```

在 Windows 瀏覽器開啟 `http://127.0.0.1:8765/`。如要固定觀看某次執行，加上
`--run-id <RUN_ID>`。賽前可用 `http://127.0.0.1:8765/?replay=1&speed=20` 快速重播。
三個頁面頂部固定提供「即時辯論／市場報告／完整辯論」頁籤；報告尚未產生時，後兩個
頁籤會顯示為不可用，完成後即可在三頁間自由切換。

直播頁需要 JavaScript 才能非同步更新，只監聽 loopback；正式 `report.html` 與
`debate.html` 仍是無 JavaScript、無 CDN、可離線開啟的稽核成品。直播只呈現 Agent
主動發布的公開理由與來源，不顯示或宣稱保存模型隱藏思考過程。

指定另一個 Data Root（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents run --provider-mode fake \
  --question "分析 ETH 過去 7 日市場狀態" \
  --data-root /tmp/hoya-run-sandbox
```

`--provider-mode` 目前只接受 `fake`。傳入其他值會被 CLI 直接拒絕，
不會退回到尚未實作的 provider。

驗證既有 run（不啟動 Agent、不修改檔案）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents verify-run --run-id <RUN_ID>
```

## 輸出位置

- Code Root：`/mnt/d/workstationD/hoya bit/hoya-bit-market-agents`（程式、設定、測試、文件）
- Data Root：`/mnt/d/workstationD/hoya bit/hoya-bit-market-agents_data`（執行結果，不進 Git）

每次執行建立一個新的 `run_id` 目錄，**永不覆寫**先前的執行：

```text
<Data Root>/runs/
├─ <run-id>/
│  ├─ manifest.json      # 題目、資產、期間、七席、attempt、artifact SHA-256、範圍限制
│  ├─ evidence.jsonl     # 合併後的不可變證據快照
│  ├─ debate.jsonl       # 共享原文辯論紀錄
│  ├─ events.jsonl       # 執行中逐筆追加的公開事件，供 SSE 直播推送
│  ├─ votes.json         # 七席有效立場與票數
│  ├─ report.json        # 報告 contract（Markdown 與 HTML 的唯一來源）
│  ├─ report.md
│  ├─ report.html        # 單一自包含檔案，離線可開啟
│  ├─ debate.html        # 完整公開辯論與證據頁，離線可開啟
│  └─ agents/<seat-id>/  # 每席只寫自己的目錄：prompt、研究、辯論、投票
└─ latest.json           # 唯一可變檔案，指向最近一次執行
```

七個固定席位 ID：`spot-technical`、`derivatives`、`onchain`、`official-events`、
`news`、`social-macro`、`counter-evidence`。同一組 ID 出現在 `evidence.jsonl`、
`debate.jsonl`、`votes.json`、`manifest.json` 與兩份報告中，可跨 artifact 追溯。

開啟報告（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents_data/runs"
cat latest.json
```

`report.html` 與 `debate.html` 內嵌 CSS，不依賴 CDN、外部字型、JavaScript 或圖片，
斷網仍可完整開啟；兩頁互相連結，來源網址維持可點擊，並保留 evidence ID 供追溯。

## 支援範圍與 fail closed

- 只支援 `BTC`、`ETH`、`SOL`、`BNB`、`XRP`。
- 題目中出現任何無法辨識為已核准資產的大寫代號（例如 `DOGE`、`ETF`）時，
  CLI 以 exit code `2` 拒絕，**不建立任何 run 目錄**。此檢查刻意從嚴：寧可誤拒，不可誤放。
- 未指定分析期間時預設過去 14 日。
- 本版本只支援單一資產題目；兩幣比較題目同樣 fail closed。

## 仍未宣稱可用的真實能力

以下項目沒有可重現 live 證據，因此 system preflight 不會宣稱 READY：

- 三個 GPT-5.6 Sol persistent threads 的 live `web_search`、actual model 與 runtime receipt。
- 七個真實訂閱席在同一 run 於 T+4:45 前交付、T+5 seal，並於 T+13 前完成報告。
- 在上述 blocker 關閉前，正式 `$hoya-market-research` 必須停止並交付 NOT READY。
- 向量資料庫、RAG、FinGPT、crawler 與 web service（依 ADR 0002 不在 MVP 範圍）。

## 相關文件

- `CONTEXT.md`：專有名詞、關係與歧義
- `docs/planning/requirements.md`：已核准需求
- `docs/planning/architecture.md`：已核准架構
- `docs/adr/0001-codex-skill-with-wsl-python-controller.md`
- `docs/adr/0002-immutable-file-based-run-store.md`
- `docs/operator-runbook.md`：登入、preflight、演練、驗證、開報告與精確 Run ID 清理
