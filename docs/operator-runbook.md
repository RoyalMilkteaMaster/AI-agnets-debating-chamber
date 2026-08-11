# AI agnets debating chamber 操作 Runbook（快速路徑版）

本文件所有命令都在 **WSL Ubuntu 24.04** 執行。程式只使用 Python 3.12 標準函式庫；
不建立 venv、不安裝套件、不接受 API key。Code Root 與 Data Root 必須分離。

```bash
# WSL
CODE_ROOT='/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber'
DATA_ROOT='/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber_data'
cd "$CODE_ROOT"
```

## 0. 總覽：賽前 vs 比賽日

```text
賽前（一次，約 10 分鐘）：§1 環境 → §2 登入 → §3 回歸 → §4 real preflight
  → 成功 = exit 0 + <DATA_ROOT>/preflight/latest-ready.json（READY 憑證）
比賽日（秒級冷啟動）：§5 開 fresh Codex Task → 貼觸發句
  → Core 只跑 launch 一條命令：查憑證 → 建 run → 並行派滿七席
     （3 Codex 經 codex exec、3 Claude、1 Antigravity）→ 背景起直播頁
     → T+5:20 停止新增搜尋 → T+5:50 停止收件 → T+6:00 封存證據
     → 七席辯論與投票 → Core 報告 → 寫 manifest
  → stdout 三行：LAUNCHED（T+0）、SEALED（T+6:00）、FINALIZED（<T+15:00）
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
開始 AI agnets debating chamber 真實七席分析。
題目：分析 BTC 過去 14 日市場狀態
```

Core 會依 `hoya-market-research` skill 快速路徑自動執行，不需要其他輸入。
支援台股、美股、加密資產與開放命題；前端須明確選資產類別並填標的。缺 READY 憑證時 launch 會 exit 2 並停下
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
`stop_reason`、`report_status` 與 `report_html` 路徑。現行時間關卡（2026-08-11
使用者核准修訂）固定為：T+5:20 停止發起新搜尋，所有席位立即整理已取得資料；
T+5:50 停止接收研究結果，即使不足三張證據卡也必須交回；T+6:00 封存並開場。
四輪開票依序在 T+7:00（7 票）、T+8:30（6 票）、T+10:00（5 票）、
T+11:30（4 票），T+12:00 硬停結算；最晚必須在 T+15:00 前完成報告。

**兩標的比較題 +30 秒**：`two_asset_comparison` 的搜尋停止、收件與封存分別為
T+5:50、T+6:20、T+6:30；四輪開票與硬停也全部後移 30 秒，依序為
T+7:30／9:00／10:30／12:00／12:30。各 provider 研究 timeout 由該題型收件牆減
5 秒動態推導（一般題 345 秒、比較題 375 秒）；報告硬截止仍是全題型 T+15:00。

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

### 7.1 從 Run ID 找到 run 目錄（§7、§8 共用）

run 目錄自 Ticket 07 起是 `runs/<台北日期>/<HHMM-題目slug-hash>/`，**目錄名不含
run_id**（日期夾是台北日期，run_id 內的時戳是 UTC，跨日時兩者不同天），所以
`"$DATA_ROOT/runs/$RUN_ID"` 不是有效路徑。手上只有 run_id 時，一律用
`resolve_run_dir` 換算——它是 run_id → 目錄的唯一權威，直接掃磁碟：

```bash
# WSL（必須在 Code Root 執行）
RUN_ID='<EXACT_RUN_ID>'
RUN_DIR="$(python3 - "$DATA_ROOT" "$RUN_ID" <<'PY'
import sys
from pathlib import Path
from hoya_market_agents.run_store import resolve_run_dir
found = resolve_run_dir(Path(sys.argv[1]), sys.argv[2])
if found is None:
    sys.exit(1)
print(found)
PY
)"
test -n "$RUN_DIR" && printf 'RUN_DIR=%s\n' "$RUN_DIR"
```

指令失敗（exit 1、`RUN_DIR` 空）代表該 run_id 不合法或磁碟上找不到唯一對應目錄，
**就停在這裡**，不要自己拼路徑。

`index-query`（§9）**不可**用來做這件事：`index.db` 是可重建的衍生資料，允許落後或
損毀；把它當成找路徑的依據，等於讓一個可以刪掉的檔案決定 run 找不找得到。

### 7.2 驗證與開啟

```bash
# WSL（接續 §7.1 的 RUN_ID／RUN_DIR）
python3 -m hoya_market_agents verify-run --run-id "$RUN_ID" --data-root "$DATA_ROOT"
REPORT="$RUN_DIR/report.html"
test -f "$REPORT"
explorer.exe "$(wslpath -w "$REPORT")"
```

`verify-run` 的 JSON 輸出本身就帶 `run_dir` 欄位，已完成的 run 也可以直接從那裡取。
§7.1 的做法對**尚未完成、驗證本來就會失敗**的 run 一樣有效，所以 §8 用它。

快速路徑 run 的 manifest 使用 `provider_mode: "real-subscription-fast"` 且
`competition_ready: false`（兩旗標必須同真或同假，不可只改其一）：receipt 鏈重檢
自然停用，timeline、七席 lineage、報告同源重渲染與離線 HTML 檢查照常執行。
manifest 的 `provider_lineage_fast` 欄位保存憑證引用與各席 actual model，作為
誠實揭露。verify-run 不在冷啟動路徑上；研究進行中的 run 尚無 manifest，驗證
本來就會失敗，屬預期行為。

verify-run 對 timeline 的要求很硬，下列情況會**如實**驗證失敗，不要試圖修 manifest：

- 任一席沒有在 T+5:50（比較題 T+6:20）前交出有效研究結果（`seat_completion_ms` 缺該席）。
- `evidence_snapshot_sealed_at_ms` 與該 run 題型的封存時刻不符（例如比較題卻寫
  360000），或 manifest 與 `question.json` 的 `question_type` 互相矛盾。
- 任一席沒有出現在 evidence／debate／votes 三份紀錄裡。
- 報告在 T+15:00 或之後完成。

`--phase research` 產生的 run 沒有 manifest，本來就不該送 verify-run。

## 8. 精確 Run ID 清理（只由 operator 執行）

系統不會自動刪除任何 run。先照 **§7.1** 把 run_id 換成 `RUN_DIR`，再跑下面這段。

**這一段必須整段貼上。** 它是一個函式，任何一項驗證不過就 `return 1`；`TARGET`
只有在全部通過時才會被賦值，「確認目標」也只有在那時才會印出來。**不要把裡面的
`test` 拆出來單獨跑**——一串裸的 `test` 後面接 `printf`，最後的結束碼會被 `printf`
蓋成 0，看起來像通過。

```bash
# WSL（接續 §7.1 的 RUN_ID／RUN_DIR）
confirm_run_dir_for_deletion() {
  local candidate="$1" runs_root target
  if [ -z "$candidate" ]; then
    printf '拒絕：RUN_DIR 是空的。\n' >&2; return 1
  fi
  runs_root="$(realpath "$DATA_ROOT/runs")" || {
    printf '拒絕：無法解析 %s/runs。\n' "$DATA_ROOT" >&2; return 1
  }
  target="$(realpath "$candidate")" || {
    printf '拒絕：無法解析 %s。\n' "$candidate" >&2; return 1
  }
  if [ ! -d "$target" ]; then
    printf '拒絕：%s 不是目錄。\n' "$target" >&2; return 1
  fi
  # run 目錄固定在 runs/<台北日期>/<HHMM-slug-hash>/：它的父目錄是日期夾，
  # 日期夾的父目錄才是 runs/。這一項同時排除了 runs/ 本身、Data Root、
  # Code Root 與工作區根目錄——它們都不可能剛好深兩層掛在 runs/ 底下。
  if [ "$(dirname "$(dirname "$target")")" != "$runs_root" ]; then
    printf '拒絕：%s 不在 %s/<日期夾>/ 底下。\n' "$target" "$runs_root" >&2; return 1
  fi
  printf '%s\n' "$target"
}

if TARGET="$(confirm_run_dir_for_deletion "$RUN_DIR")"; then
  printf '確認目標：%s\n' "$TARGET"
else
  unset TARGET
  printf '拒絕刪除，沒有可刪的目標。\n' >&2
  # 這一行讓整段的結束碼保持非零。if／else 回傳的是所屬分支最後一個命令的
  # 結束碼，少了它，一次「拒絕」在腳本裡看起來會和「成功」一模一樣。
  false
fi
```

只有印出「確認目標」時，才可另行執行 `rm -r -- "$TARGET"`。禁止 glob、禁止空變數、
禁止把 `$DATA_ROOT/runs`、Code Root 或工作區根目錄當刪除目標——上面那一項深度比對
就是這四種的統一拒絕條件，不需要各寫一條。`tests/test_run_index.py` 的
`RunbookDeletionGuardTest` 會把這一段**逐字**抽出來，對六種目標各跑一次整段，
確認結束碼與「確認目標」是否出現都符合本節的描述。

刪完之後：

- **`index.db` 會留下孤兒列。** 索引是可重建的衍生資料，不是事實來源，所以不做逐列
  刪除；重掃一次即可，重建後的表就等於磁碟現況（backfill 讀不到某個資料夾時會**失敗
  並保留舊索引**，不會把「讀不到」當成「沒有 run」而清空）：

  ```bash
  # WSL
  python3 -m hoya_market_agents index-backfill --data-root "$DATA_ROOT"
  ```

  在重建之前，`index-query` 仍會列出這個 run，而它的 `report_path` 已經指不到檔案；
  這是刻意的**可見**落後，不是無聲的資料遺失。

- **日期夾旁邊的 claim 檔（`.<HHMM>-<hash>.run-claim`）保留，不要刪。** 它是「這個
  run_id 已被用過」的永久紀錄（Ticket 07 §3）。刪掉它，未來同一個 run_id 就能再被
  建立一次，等於讓一個新 run 頂替掉已刪除 run 的身分；留著只花約 60 bytes，而且
  整個日期夾一起刪時它會跟著消失。

- **`latest.json` 若仍指向該 run，保留它**作為已刪除 run 的可見診斷，不由 Agent
  自動改寫。

## 9. 歷史查詢索引（index.db）

`_data/runs/index.db` 每個完成的 run 一列（run_id、台北日期、題目原文、slug、
資產類別、標的、題型、燈號、採納立場、票數分佈、共識狀態、報告路徑、事後驗證結果）。
run 走到 `FINALIZED` 之後自動寫入一列；**寫入失敗只在 stderr 留警告，不影響那次 run**
（該 run 之後靠 backfill 補回來）。

```bash
# WSL
# 全量重建（刪掉 index.db、backfill 失敗、或剛手動刪過 run 之後都用這個）
python3 -m hoya_market_agents index-backfill --data-root "$DATA_ROOT"

# 查詢：日期範圍／類別／燈號／關鍵字四類條件可任意組合，全部省略就是列出全部（新到舊）
python3 -m hoya_market_agents index-query --data-root "$DATA_ROOT" \
  --date-from 2026-08-01 --date-to 2026-08-07 \
  --asset-class crypto --confidence green --keyword '台積電' --limit 20
```

- `--keyword` 是**題目原文的字面子字串**：`%` 與 `_` 就是它們自己，不是萬用字元。
- `--asset-class` 與 `--confidence` 不檢查值：合法值的權威分別在
  `config/market_scopes.json` 與 `report_contract.CONFIDENCE_LEVELS`，改設定檔不必動索引。
- `--limit` 不接受負數（exit 2）。要全部列出就不要給這個參數。
- `index.db` **不備份**。它隨時可以刪掉重建，也不參與任何 run 的正確性；`drill`
  產生的演練 run 不會即時進索引，跑一次 backfill 就會列進來。
- 還沒 backfill 過就查詢會 exit 1 並指明要先跑 `index-backfill`，不會假裝「沒有資料」。

**`index.db` 損毀時直接跑 `index-backfill` 就好，不必手動刪檔。** backfill **不會去讀、
去判斷、去刪除**現有的索引：它在 `runs/` 底下先把完整的新索引建在一個暫存檔
（`.index.db.<亂數>.tmp`，是檔案所以不會被任何 run 列舉者當成 run），完全寫好並關閉之後，
用一次 `os.replace` 原子換上去。所以：

- 損毀、空檔、開不起來、被別的程序鎖住——處理方式全都一樣，就是被取代，不必分類。
- **這個名字上永遠有一份完整可用的索引**，沒有「刪掉了、還沒建好」的空窗。
- backfill 中途失敗或被中斷，暫存檔會清掉，**原本的索引原封不動**。
- 已經開著舊索引連線的讀取者會繼續讀舊的那一份（它連上去那一刻的一致快照），
  下次重新開啟就看到新的。

backfill 輸出三個欄位：`indexed`（收錄幾個 run）、`skipped`（讀得到但沒有完成標記
`manifest.json` 的目錄，多半是 Ticket 07 刻意留下的半成品）、`unexpected_date_folders`
（`runs/` 底下名稱不是台北日期的資料夾；裡面的 run 照樣收錄，但它們的日期欄會是那個
資料夾名）。

**backfill 可以在比賽進行中跑。** 索引的兩條寫入路徑——run 完成時的即時寫入、與 backfill
的整趟重建（掃描＋安裝）——共用 `runs/.index.lock` 上的一把 `flock`，所以兩者不會交錯：

- backfill 進行中剛好有 run 完成 → 那次寫入會**等**，等到 backfill 換檔完成後寫進新索引。
  實測 1000 場 run 的 backfill 約 0.05 秒，等待時間同量級；**上限 10 秒是「這次寫入的總等待
  預算」**（等鎖與等 SQLite 合計，不是各自 10 秒），超過就放棄並在 stderr 留警告
  （**不會安靜地掉一列**），該 run 本身完全不受影響，下次 backfill 也會補回。
- 等鎖期間那場 run 被刪掉 → 拿到鎖之後才讀磁碟，會發現已經沒有這個 run，於是**不寫**並留下
  警告。索引不會出現指向已刪除 run 的孤兒列。
- 反過來，run 剛寫完索引時 backfill 才開始 → 該 run 的 `manifest.json` 早就在磁碟上，
  掃描一定看得到。
- **查詢不參與這把鎖，永遠不會被寫入者擋住。**
- 鎖檔 `runs/.index.lock` 建了就永久留著，**不要刪它**。它的存在不代表任何狀態，內容也沒有
  意義；鎖的生死由核心綁在檔案描述子上，程序結束或被 kill 都會自動釋放，不會留下卡住的鎖。

在 `/mnt/d` 這類 DrvFs 掛載上，`os.replace` **無法覆蓋權限被設成唯讀（0400／0000）的
目標**，會回報 `PermissionError` 並保留原索引；ext4 上則會直接覆蓋。兩種結果都安全
（不是原索引留著、就是被一份剛從磁碟重建的完整索引取代），但如果你在 Windows 磁碟上
手動把 `index.db` 設成唯讀，要先把權限改回來 backfill 才會成功。
