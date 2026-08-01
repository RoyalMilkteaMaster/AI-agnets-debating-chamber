# Hoya Bit Market Agents

七席多模型加密市場研究流程的 WSL Python 控制程式。

本版本是 **Ticket #2 tracer bullet**：使用離線 fake provider 打通「題目 → 七席研究 →
共享辯論 → 投票 → 報告」的完整骨架。**尚未接上任何真實模型**，輸出內容一律為示範資料，
不得作為市場依據。

## 執行環境

- WSL Ubuntu 24.04、Python 3.12（只使用標準函式庫）。
- 不需要任何 API key、登入或網路連線。
- 不需要安裝套件、不需要 venv。

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

測試全部離線：使用可注入的 fake clock 與 fake provider，不呼叫真實模型、不讀網路。

## 執行一次分析

執行一次 fake run（WSL）：

```bash
# WSL
cd "/mnt/d/workstationD/hoya bit/hoya-bit-market-agents"
python3 -m hoya_market_agents run --provider-mode fake --question "分析 BTC 過去 14 日市場狀態"
```

指令會印出唯一 `Run ID`、Data Root、Run 目錄、Markdown 與 HTML 報告的絕對路徑，
以及票數與七席立場。

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
│  ├─ votes.json         # 七席有效立場與票數
│  ├─ report.json        # 報告 contract（Markdown 與 HTML 的唯一來源）
│  ├─ report.md
│  ├─ report.html        # 單一自包含檔案，離線可開啟
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

`report.html` 內嵌 CSS，不依賴 CDN、外部字型、JavaScript 或圖片，斷網仍可完整開啟，
來源網址維持可點擊，並以相對連結指向 `evidence.jsonl` 與 `debate.jsonl`。

## 支援範圍與 fail closed

- 只支援 `BTC`、`ETH`、`SOL`、`BNB`、`XRP`。
- 題目中出現任何無法辨識為已核准資產的大寫代號（例如 `DOGE`、`ETF`）時，
  CLI 以 exit code `2` 拒絕，**不建立任何 run 目錄**。此檢查刻意從嚴：寧可誤拒，不可誤放。
- 未指定分析期間時預設過去 14 日。
- 本版本只支援單一資產題目；兩幣比較題目同樣 fail closed。

## 本版本尚未實作

以下項目不在 Ticket #2 範圍，並記錄在每次執行的 `manifest.json` 與報告的「限制與失效條件」中：

- 真實 provider（Claude CLI、Codex、Antigravity CLI）。
- 時間關卡（T+5／7／9／10／13）與強制停止。
- 重試、替補與 Format Repair Agent。
- 絕對 6／5／4 共識門檻與信心燈號分級。
- 兩幣比較與事件影響的投票詞彙。
- 向量資料庫、RAG、FinGPT、crawler 與 web service（依 ADR 0002 不在 MVP 範圍）。

## 相關文件

- `CONTEXT.md`：專有名詞、關係與歧義
- `docs/planning/requirements.md`：已核准需求
- `docs/planning/architecture.md`：已核准架構
- `docs/adr/0001-codex-skill-with-wsl-python-controller.md`
- `docs/adr/0002-immutable-file-based-run-store.md`
