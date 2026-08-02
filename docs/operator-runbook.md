# Hoya Bit 操作 Runbook（快速路徑版）

本文件所有命令都在 **WSL Ubuntu 24.04** 執行。程式只使用 Python 3.12 標準函式庫；
不建立 venv、不安裝套件、不接受 API key。Code Root 與 Data Root 必須分離。

```bash
# WSL
CODE_ROOT='/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final'
DATA_ROOT='/mnt/d/workstationD/hoya bit/hoya-bit-market-agents_data'
cd "$CODE_ROOT"
```

## 0. 總覽：賽前 vs 比賽日

```text
賽前（一次，約 10 分鐘）：§1 環境 → §2 登入 → §3 回歸 → §4 real preflight
  → 成功 = exit 0 + <DATA_ROOT>/preflight/latest-ready.json（READY 憑證）
比賽日（秒級冷啟動）：§5 開 fresh Codex Task → 貼觸發句
  → Core 只跑 launch 一條命令：查憑證 → 建 run → 並行派滿七席
     （3 Codex 經 codex exec、3 Claude、1 Antigravity）→ 背景起直播頁
     → T+4:00 封存證據 → 七席辯論與投票 → Core 報告 → 寫 manifest
  → stdout 三行：LAUNCHED（T+0）、SEALED（T+4:00）、FINALIZED（≤T+13:00）
```

比賽日**不再**執行 prepare-launch、verify-preflight、provider preflight 或 drill；
Core 也**不需要**手動開 Codex threads（`--codex-mode cli` 為預設，七席全由
launch 派出）。唯一前置是 READY 憑證存在。

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

預期 Python 為 3.12，三個 CLI 都有非空版本。缺任何 CLI 就停止；
本專案不會自動安裝、更新或改寫登入環境。

### 1.1 比賽日前：暫時停用 Codex hooks（重要）

`C:\Users\leslie\.codex\hooks.json` 的六個 hook 讓每次工具呼叫都多付一次 node
進程冷啟，會拖慢冷啟動與七席派發。比賽前把
`C:\Users\leslie\.codex\config.toml` 的 `[features]` 段落改為：

```toml
[features]
hooks = false
```

賽後改回 `hooks = true` 即可，其他設定不動。

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

fixture 預期 exit `1`、`status=NOT_READY`、`simulation_status=PASS`（fixture 刻意
永不 READY）。drill 預期 exit `0` 並印出 `run_id` 與 `verification.status=VERIFIED`。

## 4. 賽前 real preflight → READY 憑證（一次性）

這是唯一會消耗訂閱 smoke 的賽前步驟。它仍走 legacy Codex bridge（handoff 需在
300 秒內綁定），但只需成功一次。

1. 從 `$CODE_ROOT` 開 fresh Codex Task，依
   `.agents/skills/hoya-market-research/references/preflight-checklist.md` 執行：
   `prepare-launch` → 開 3 個 Codex threads → 寫 handoff → `verify-preflight`。
2. handoff 驗證通過後**立刻**執行（handoff 建立後 300 秒內）：

   ```bash
   # WSL
   python3 -m hoya_market_agents preflight --provider system --seats 7 --mode real \
     --codex-run-id '<CODEX_RUN_ID>' --codex-challenge "$CODEX_CHALLENGE" \
     --data-root "$DATA_ROOT"
   ```

3. 成功判準（provider 能力全過）：**exit 0**，且產生
   `$DATA_ROOT/preflight/latest-ready.json`（`provider_capabilities_ready: true`）。
   `search`／`seven_seat_timeline`／`report_deadline` 屬 run-scoped 證據、由正式 run
   本身產生，不阻擋憑證。
4. exit 1 時看 manifest 的 blockers（登入、模型、寫入、renderer 等），修好重跑。
   handoff 超過 300 秒或已綁定 → 重開 fresh Task 重做第 1 步。

憑證一經產生即長期有效（launch 只驗存在與 hash 一致；過舊僅印 advisory）。
若中途重新登入任何 provider CLI，建議重跑本節。

## 5. 比賽日：貼題目即開跑

**開跑前 10 秒檢查**：確認 8765 埠沒被舊的 demo／直播 server 佔用，否則 launch
自帶的直播會 bind 失敗（只警告不阻塞派席，但你會在瀏覽器看到舊資料）：

```bash
# WSL：列出殘留的 live server，有就 kill <PID>
pgrep -af 'hoya_market_agents live'
```

開 fresh Codex Task（從 `$CODE_ROOT`），貼上：

```text
開始 Hoya Bit 真實七席分析。
題目：分析 BTC 過去 14 日市場狀態
```

Core 會依 `hoya-market-research` skill 快速路徑自動執行，不需要其他輸入。
支援資產僅 BTC、ETH、SOL、BNB、XRP。缺 READY 憑證時 launch 會 exit 2 並停下
回報——此時回到 §4，不得繞過。

手動除錯時，launch 可直接呼叫：

```bash
# WSL
nohup python3 -m hoya_market_agents launch \
  --question '分析 BTC 過去 14 日市場狀態' --data-root "$DATA_ROOT" \
  --handshake-file /tmp/hoya-launch.json >/tmp/hoya-launch.log 2>&1 &
cat /tmp/hoya-launch.json   # LAUNCHED 握手：run_id、codex_mode、inbox、prompt 路徑
tail -f /tmp/hoya-launch.log   # 依序印出 LAUNCHED → SEALED → FINALIZED
```

預設 `--codex-mode cli`：三個 Codex 席由 launch 經 `codex exec` 直接派出，
**不要**再對它們呼叫 submit-seat（重複提交會被 write-once 拒絕，只留警告）。

預設 `--phase full`：同一條命令接著跑辯論、投票、Core 報告與 finalize，
最後一行 `FINALIZED` 帶 `consensus_status`、`adopted_stance`、`tally`、
`stop_reason`、`report_status` 與 `report_html` 路徑。辯論時間關卡（2026-08-02
使用者核准修訂）固定為 T+4:00（封存並開場，逐席即時發布；某席開場已發且全場
已有兩種以上立場即立刻派發該席第一輪）、T+7:30（第一輪與 6 票）、T+8:00
（門檻降 5）、T+8:45 與 T+9:45（改票輪）、T+10:00（強制停止，4 票採用否則
未達共識）。

**兩幣比較題 +30 秒**（Ticket R7，2026-08-02 使用者核准）：`two_asset_comparison`
的收件牆是 T+4:20、封存是 T+4:30（其餘題型維持 T+3:50／T+4:00），Claude 研究
呼叫的 timeout 也跟著變成 255 秒。T+7:30 之後的每一道辯論牆與 T+13:00 報告期限
四型共用，不隨題型移動。直播頁的規則時間線會依該 run 的題型顯示 4:00 或 4:30。

`report_status: "red_audit"` 是誠實結果不是崩潰：Core 報告兩次都沒通過客觀驗證，
系統改出紅燈「報告驗證失敗」版本並列出精確原因，不得手動改寫成看起來完整的報告。

`--phase research` 只跑到 `SEALED` 就結束，保留舊的人工主持辯論路徑；
比賽日不需要它。

後備路徑 `--codex-mode inbox`（僅在 codex exec 通道故障時使用）：Core 手動開
3 個 Codex threads，第一則訊息貼 `inbox/<run_id>/prompts/<seat>.txt` 的完整內容，
席位回覆後逐字回填：

```bash
# WSL：raw JSON 從 stdin 讀
python3 -m hoya_market_agents submit-seat --run-id '<RUN_ID>' \
  --seat-id spot-technical --attempt-id spot-technical-a1 --data-root "$DATA_ROOT"
```

## 6. 即時觀看七席辯論

launch 會自動在背景啟動唯讀直播（失敗不阻塞派席），瀏覽器開
`http://127.0.0.1:8765/live.html`。需要人工重啟時：

```bash
# WSL
python3 -m hoya_market_agents live --data-root "$DATA_ROOT" \
  --host 127.0.0.1 --port 8765
```

賽前可在已完成的 drill 上用 `http://127.0.0.1:8765/?replay=1&speed=20` 驗證票數與
規則時間線。只允許 loopback；直播頁是唯讀觀察介面，正式稽核依據仍是 run artifacts。

## 7. 驗證與開啟報告（賽後 advisory）

```bash
# WSL
RUN_ID='<EXACT_RUN_ID>'
python3 -m hoya_market_agents verify-run --run-id "$RUN_ID" --data-root "$DATA_ROOT"
REPORT="$DATA_ROOT/runs/$RUN_ID/report.html"
test -f "$REPORT"
explorer.exe "$(wslpath -w "$REPORT")"
```

快速路徑 run 的 manifest 使用 `provider_mode: "real-subscription-fast"` 且
`competition_ready: false`（兩旗標必須同真或同假，不可只改其一）：receipt 鏈重檢
自然停用，timeline、七席 lineage、報告同源重渲染與離線 HTML 檢查照常執行。
manifest 的 `provider_lineage_fast` 欄位保存憑證引用與各席 actual model，作為
誠實揭露。verify-run 不在冷啟動路徑上；研究進行中的 run 尚無 manifest，驗證
本來就會失敗，屬預期行為。

verify-run 對 timeline 的要求很硬，下列情況會**如實**驗證失敗，不要試圖修 manifest：

- 任一席沒有在 T+3:50（比較題 T+4:20）前交出有效研究結果（`seat_completion_ms` 缺該席）。
- `evidence_snapshot_sealed_at_ms` 與該 run 題型的封存時刻不符（例如比較題卻寫
  240000），或 manifest 與 `question.json` 的 `question_type` 互相矛盾。
- 任一席沒有出現在 evidence／debate／votes 三份紀錄裡。
- 報告晚於 T+13:00，或距離辯論停止超過 3 分鐘。

`--phase research` 產生的 run 沒有 manifest，本來就不該送 verify-run。

## 8. 精確 Run ID 清理（只由 operator 執行）

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
`$DATA_ROOT/runs`、Code Root 或工作區根目錄作為刪除目標；`latest.json` 若仍指向該
run，保留它作為已刪除 run 的可見診斷，不由 Agent 自動改寫。
