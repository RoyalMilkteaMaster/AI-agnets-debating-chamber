# Hoya Bit 賽前操作 Runbook

本文件所有命令都在 **WSL Ubuntu 24.04** 執行。程式只使用 Python 3.12 標準函式庫；
不建立 venv、不安裝套件、不接受 API key。Code Root 與 Data Root 必須分離。

```bash
# WSL
CODE_ROOT='/mnt/d/workstationD/hoya bit/hoya-bit-market-agents'
DATA_ROOT='/mnt/d/workstationD/hoya bit/hoya-bit-market-agents_data'
cd "$CODE_ROOT"
```

## 1. 乾淨 checkout 與版本

```bash
# WSL
git status --short --branch
python3 --version
/home/leslie/.local/bin/codex --version
/home/leslie/.local/bin/claude --version
/home/leslie/.local/bin/agy --version
python3 -m unittest discover -s tests -v
```

預期 Code Root 無未提交變更，Python 為 3.12，三個 CLI 都有非空版本。缺任何 CLI 就停止；
本專案不會自動安裝、更新或改寫登入環境。

## 2. 登入重驗（不輸出 token）

```bash
# WSL
/home/leslie/.local/bin/codex login status
/home/leslie/.local/bin/claude auth status --json
/home/leslie/.local/bin/agy models | grep -Fx 'gemini-3.1-pro-high'
```

Codex 必須顯示已登入；Claude JSON 必須為 `claude.ai`／Max；Antigravity 必須列出精確模型。
不要把完整 session、OAuth、cookie、email 或 token 貼到 issue。

## 3. 不耗訂閱的回歸

```bash
# WSL
python3 -m hoya_market_agents preflight --provider system --seats 7 \
  --mode fixture --preflight-id rehearsal-fixture

python3 -m hoya_market_agents drill --provider-mode fake \
  --question '分析 BTC 過去 14 日市場狀態'
```

fixture 預期 exit `1`、`status=NOT_READY`、`simulation_status=PASS`；這是刻意避免 fake
被誤認成 READY。drill 預期 exit `0` 並印出 `run_id` 與 `verification.status=VERIFIED`。

四個 fail-closed fixture：

```bash
# WSL
python3 -m hoya_market_agents preflight --provider system --seats 7 --mode fixture \
  --fixture-failure login --preflight-id broken-login
python3 -m hoya_market_agents preflight --provider system --seats 7 --mode fixture \
  --fixture-failure model --preflight-id broken-model
python3 -m hoya_market_agents preflight --provider system --seats 7 --mode fixture \
  --fixture-failure write --preflight-id broken-write
python3 -m hoya_market_agents preflight --provider system --seats 7 --mode fixture \
  --fixture-failure renderer --preflight-id broken-renderer
```

四者都必須 exit `1` 且列出對應 blocker。

## 4. Fresh Core preflight

1. 關閉舊 Codex Task。
2. 在 WSL 產生 one-time challenge：

   ```bash
   # WSL
   CODEX_CHALLENGE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
   COMPETITION_RUN_ID="$(python3 -c 'from datetime import datetime, timezone; import secrets; print(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-btc-") + secrets.token_hex(3))')"
   COMPETITION_CHALLENGE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
   printf '%s\n' "$CODEX_CHALLENGE"
   printf '%s\n' "$COMPETITION_RUN_ID"
   printf '%s\n' "$COMPETITION_CHALLENGE"
   ```

3. 從 `$CODE_ROOT` 開啟 fresh Codex Task，把上面的 challenge 原樣交給 Core。
4. 呼叫 `$hoya-market-research --preflight`，並指定該 challenge。
5. Core 必須觀察自身 actual model、三個 persistent thread、runtime dispatch receipt；缺一即停止。
6. 記下 Skill 產生的 `<CODEX_RUN_ID>`，不要自行編造 ID 或 receipt。

接著執行：

```bash
# WSL
python3 -m hoya_market_agents preflight --provider system --seats 7 --mode real \
  --codex-run-id '<CODEX_RUN_ID>' --codex-challenge "$CODEX_CHALLENGE" \
  --competition-run-id "$COMPETITION_RUN_ID" \
  --competition-challenge "$COMPETITION_CHALLENGE"
```

這會在 Codex handoff 有效後才消耗 Claude Max／Google Ultra smoke。現在 Ticket #8 的 Codex
receipt 是 no-tool policy，不能證明三個 GPT 席各自搜尋，因此目前正確結果預期為
`NOT_READY`，blocker 為 `search`。禁止改用 fake、降低模型或移除 receipt 來湊 READY。
handoff 必須在 300 秒內驗證且只能綁定一次；過期、challenge 不符或 replay 都要重開
fresh Task 並產生新 challenge。
只有 `provider_capabilities_ready=true` 才會以 write-once artifact 簽發該 competition
run ID/challenge；預授權前先決定 run ID，因此不需要在 run 完成後回頭偽造 lineage。
`search`、`seven_seat_timeline`、`report_deadline` 是 run-scoped 證據；預授權 manifest
可誠實維持 `NOT_READY`，但只有這三項可作為 blocker。正式 run 必須使用已授權的
run ID/challenge，並由最終 `verify-run` 驗證七席 receipts，不能再跑第二份 preflight
補造證據。

## 5. 正式題目

只有最新 real manifest 的 `provider_capabilities_ready=true`、competition authorization
為 `AUTHORIZED`，且 blocker 僅包含三項 run-scoped 證據時，才可在 fresh Task 呼叫：

```text
$hoya-market-research 分析 BTC 過去 14 日市場狀態
```

若仍為 `NOT_READY`，不得啟動正式計時 run；先展示 manifest 的 blocker。支援資產僅
BTC、ETH、SOL、BNB、XRP。

## 6. 驗證與開啟報告

```bash
# WSL
RUN_ID='<EXACT_RUN_ID>'
python3 -m hoya_market_agents verify-run --run-id "$RUN_ID" --data-root "$DATA_ROOT"
REPORT="$DATA_ROOT/runs/$RUN_ID/report.html"
test -f "$REPORT"
explorer.exe "$(wslpath -w "$REPORT")"
```

`verify-run` 必須 exit `0` 並輸出 `VERIFIED`。它會檢查六個必要 artifact、manifest hash
index、七席 lineage、T+4:45、T+5、辯論停止、T+13 與離線 HTML。
任何宣稱 `real-subscription`／`competition_ready=true` 的 run 還必須包含完整 timeline、
report.json 同源驗證、七席 target/actual provider/model，以及可回查且 hash 相符的 real
provider-preflight manifest；只改 manifest flag 或移除 timeline 必定拒絕。

每個 real 研究席還必須有 `provider-receipts/<seat_id>.json`，逐席綁定：

- 預授權 preflight ID、competition run ID 與 competition challenge；
- seat/attempt/provider/target model/actual model；
- 唯一 dispatch/completion receipt 與 UTC/monotonic elapsed；
- 成功的 provider-specific search receipt artifact；
- 該席 public transcript 與 structured output 的 Data Root 路徑及 SHA-256。

七份 receipt、search、transcript、output 都必須在 run artifact index 內且 hash 相符。
任何 shipped fake marker 會另外拒絕，但主要信任根是上述 run-scoped receipt lineage。

## 7. 精確 Run ID 清理（只由 operator 執行）

系統不會自動刪除任何 run。先指定完整 Run ID 並確認路徑仍位於 Data Root：

```bash
# WSL
RUN_ID='<EXACT_RUN_ID>'
TARGET="$DATA_ROOT/runs/$RUN_ID"
test -n "$RUN_ID" && test "$RUN_ID" != '.' && test "$RUN_ID" != '..'
test -d "$TARGET"
test "$(dirname "$(realpath "$TARGET")")" = "$(realpath "$DATA_ROOT/runs")"
printf '確認目標：%s\n' "$TARGET"
```

人工確認上面唯一目標後，才可另行執行 `rm -r -- "$TARGET"`。禁止 glob、空變數、
`$DATA_ROOT/runs`、Code Root 或工作區根目錄作為刪除目標；`latest.json` 若仍指向該 run，
保留它作為已刪除 run 的可見診斷，不由 Agent 自動改寫。
