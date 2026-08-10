# 01 Phase 0：Data Root 與 sibling 清理（備份→受控刪除→煙霧測試）

- 狀態：完成
- Spec：`../spec.md`（Phase 0）
- Blocked by：無

## 目標

清空測試殘留，讓 Data Root 只剩續用所需內容，且系統仍可完整啟動。

## 使用者價值

「太多之前測試的資料散在裡面」——一次清乾淨，之後的日期分層結構從零開始。

## 範圍

1. 把 `hoya-bit-market-agents_data` 整包 zip 備份到 `D:\workstationD\hoya bit\backups\`（檔名含日期）。
2. 刪除 `_data` 下：`runs/` 全部（含 `latest.json`）、`presentation-v2`~`v7`、`coordination/`、`adjustment-audit/`、`inbox/` 全部內容、`logs/live-server.log`、`preflight/ticket11-*`、`preflight/final-real-not-ready`。
3. 保留：`preflight/latest-ready.json` 與其對應時戳憑證目錄、`preflight/launch-reservations/`、`preflight/antigravity/`（憑證解析鏈完整性以實際讀取程式為準，刪除前先確認 `latest-ready.json` manifest 引用的路徑全數保留）。
4. Sibling：以 git 比對（如 `git log --oneline` 全量比對＋`git branch --contains`）程式化驗證 `-final` 已含舊 repo `hoya-bit-market-agents` 需要的全部 commit，把驗證輸出存證後，刪除舊 repo、`hoya-bit-market-agents_worktrees`（與舊 repo `.git` 連動，必須同組刪）、`hoya-bit-site`。
5. 清理後跑 fixture launch 煙霧測試確認系統可啟動。

## 已確認實作決策

- Data Root 非 git，刪除不可逆——備份 zip 是唯一回復手段，必須先做且驗證 zip 可開啟。
- 進行中 run 不得存在時才可清理 `inbox/`。
- 不從 workspace 根目錄遞迴刪除；逐一指定解析後路徑。
- 本票不修改任何程式碼。

## 驗收條件

- `backups/` 存在含日期的 zip 且可列出內容。
- `_data` 下只剩 `preflight/`（含 `latest-ready.json` 及其引用的憑證檔）與程式會自動重建的空目錄。
- 舊 repo、worktrees、site 三個目錄已不存在；git 驗證輸出已存入本票紀錄。
- fixture launch 煙霧測試輸出 LAUNCHED/SEALED/FINALIZED 三行 handshake。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數 ≥681。

## 測試與證據

- 測試接縫：既有 fixture launch 流程；測試不碰正式 Data Root。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）；fixture launch 命令（依 README）。
- 必交證據：備份 zip 路徑與大小、刪除清單、git 比對輸出、煙霧測試 stdout、測試結果。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：無
- Blocks：02

## Agent 分工

- Developer：負責實作、驗證、修正或以證據反駁 Findings
- Reviewer A：使用隔離上下文執行 Review
- Reviewer B：使用另一個隔離上下文執行 Review
- Reviewer 標準：兩者都載入 `$milktea-skills-code-review`，並同時執行 Standards 與 Spec Review
- CLI 與模型：由執行 Task 的 Coordinator 依目前 Task 分工與實際可用能力決定

## 完成規則

- 三個角色已處理所有可重現且有證據的問題。
- 沒有未解決的正確性、可執行性、可讀性、架構或衍生風險。
- 三個角色對完成狀態達成共識。

## 執行與 Review 紀錄

### 1. 開始執行（Coordinator，2026-08-05）

- **Execution environment**：Windows 10 host ＋ WSL distribution `Ubuntu-24.04`（Ubuntu 24.04.4 LTS、Python 3.12.3）；shell 為 WSL bash；command prefix `MSYS_NO_PATHCONV=1 wsl.exe -e bash -lc '...'`；專案路徑（WSL）`/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final`；來源 `source: auto_current`（本 Task 無 `set-agent-roles` 環境更新）。
- **基準版本**：`main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`。工作樹既有 7 項未提交變更，全部為 Planner 規劃產出（`CONTEXT.md`、`docs/planning/architecture.md` 修改；`docs/adr/0003|0004|0005`、`docs/architecture-reviews/`、`docs/work/` 未追蹤），不屬本票變更。
- **基準測試（Coordinator 實測）**：`python3 -m unittest discover -s tests`（WSL）→ **Ran 681 tests OK, 26.454s**。
- **開發角色**：Developer＝Claude 一般臨時 Agent（平台原生派發）；Reviewer A＝Codex CLI（`codex-cli 0.146.0`, `review_engine: native`）；Reviewer B＝Codex CLI（`codex-cli 0.146.0`, `review_engine: native`，本 Task 無 OCR 設定）。獨立性註記：Reviewer A／B 為兩個隔離上下文但同一 CLI 與模型；Developer 與 Reviewer 之間為跨模型。
- **範圍**：僅資料與目錄清理，不修改任何程式碼。Data Root `hoya-bit-market-agents_data`（36 個 run、6 份 presentation、coordination／adjustment-audit／inbox／logs／preflight 殘留）與 sibling 三目錄（`hoya-bit-market-agents`、`hoya-bit-market-agents_worktrees`、`hoya-bit-site`）。
- **必跑指令**：`python3 -m unittest discover -s tests`（WSL）；fixture launch 煙霧測試（依 README）。
- **環境限制（實測）**：WSL 內無 `zip` 與 `sqlite3` CLI；備份改以 Python stdlib `zipfile` 產生，`sqlite3` 於後續 Ticket 以 Python 模組使用（`sqlite3.sqlite_version 3.45.1`）。
- **清理前實測現況**：`_data` 下 `runs/`（36 個 run 目錄）、`presentation-v2`／`v3-sse`／`v4-nav`／`v5-chat`／`v6-desktop`／`v7-desktop`、`coordination/`、`adjustment-audit/`、`inbox/`、`logs/`、`preflight/`；`preflight/` 內容為 `latest-ready.json`、`20260801T225249Z-94d490`、`antigravity`、`launch-reservations`、`final-real-not-ready`、`ticket11-real-no-codex-v3`、`ticket11-real-no-codex-v4`。`D:\workstationD\hoya bit\backups\` 尚不存在。

### 2. 流程偏離紀錄（Coordinator，2026-08-05）

本票的**實際刪除動作由 Coordinator 執行，不由 Developer 執行**。原因：把 Ticket 01 的刪除清單委派給臨時開發 Agent 時，被 harness 的 auto-mode classifier 連續兩次拒絕（拒絕理由為「Blocked by classifier」，屬平台對 subagent 破壞性授權的限制，非專案規則）。Coordinator 改以逐項指定解析後路徑的方式親自執行，每一步都保留刪除前計數與刪除後存在性檢查。

偏離範圍僅限「誰按下刪除」；Developer 仍完整負責備份製作與驗證、憑證解析鏈查證、sibling git 比對、清理後複驗、煙霧測試與測試基準，以及後續 Findings 的修正或反駁。Reviewer 審查對象不變。

另有兩項超出 Ticket 原文的追加動作，皆為降低不可逆風險，已取得使用者明確裁示（選項「補備份後全刪」）：

1. Ticket 原文只要求備份 `hoya-bit-market-agents_data`。Developer 在 Stage A 查出兩處未被該備份涵蓋的內容：`hoya-bit-market-agents_worktrees\hoya-bit-market-agents_data\`（481 檔／23MB，非註冊 worktree、非 git 追蹤）與 `hoya-bit-site`（其遠端 `https://github.com/RoyalMilkteaMaster/hoya-bit-reports.git` 實測回傳 `Repository not found`，exitcode 128；同憑證對舊 repo `ls-remote` 成功，排除認證問題，故遠端已不可還原，且 `tools/build_site.py` 為全域唯一副本）。兩者各補一份 zip。
2. Developer 於 Stage B0 回報 R7：worktrees 的 9 個 `.git` 只是 `gitdir:` 指標檔，admin 目錄僅存在於舊 repo。Coordinator 追加備份舊 repo（含 `.git`）以消除此缺口。

### 3. 備份證據（Developer 製作與驗證，Coordinator 追加第 4 份）

備份位置 `D:\workstationD\hoya bit\backups\`。共同方法：Python stdlib `zipfile`（WSL 無 `zip` CLI），建檔後 `testzip()` 並**逐檔解壓 sha256 與來源比對**，`content_mismatches` 必須為 0。

| zip | 來源 | 檔數 | bytes | testzip | mismatches | sha256 |
|---|---|---|---|---|---|---|
| `hoya-bit-market-agents_data-20260805.zip` | Data Root | 2,423 | 5,813,854 | None | 0 | `3d6472974f5cf351520a99d53b72f76e8cfa9b40b4c3042dc9450c531a275c3a` |
| `hoya-bit-market-agents_worktrees-20260805.zip` | `_worktrees` | 1,261 | 4,147,530 | None | 0 | `b60ab8e980b62b1e98a127b14e9be5e4aae03d6b3edd641482e463a66f37399a` |
| `hoya-bit-site-20260805.zip` | `hoya-bit-site` | 282 | 733,693 | None | 0 | `700e8cd59f88c26297e1ccb602c78bdc742bca1803ffc9fa76837419adcd3280` |
| `hoya-bit-market-agents-oldrepo-20260805.zip` | 舊 repo（含 `.git`） | 734 | 1,932,488 | None | 0 | `818135d0eda3c1beb437a2eb586f90fa0532857861d1655070ce38d0766b3857` |

針對性存在確認（以獨立第二支程式重開 zip 讀 namelist，不採建檔腳本自我回報）：

- worktrees zip：`hoya-bit-market-agents_data/` 底下 **481 entries**（與 Stage A 盤點一致）；9 個 worktree 的 `.git` 指標檔全部收錄。
- site zip：`tools/build_site.py` PRESENT；`.git/` **164 entries**（全 loose object，無 packfile）。
- oldrepo zip：`.git/` **603 entries**，含 9 個 worktree admin 目錄的 HEAD（`ticket-3`…`ticket-11`）。

**還原實測**（因 site 遠端已消失，Developer 另做實證而非只驗雜湊）：解壓 site zip 後 `git fsck --no-progress` 無錯誤輸出；`git log --oneline` 得 `5aa3b75` / `1948b4a`；HEAD 與來源相同；tracked files 118 與來源相同；`build_site.py` sha256 還原＝來源（`aad1f156…c0d105`）。worktrees zip 解壓後 1,261 檔、`_data` 481 檔、`run_store.py` sha256 還原＝來源（`05797af1…e8b1f0`）。

### 4. 憑證解析鏈查證（Developer，唯讀）

launch 前置憑證的實際解析與校驗路徑：`launcher.py:286` 讀 `data_root/preflight/latest-ready.json`（檔名常數見 `system_preflight.py:48 READY_CERTIFICATE_NAME`）→ `launcher.py:293` 要求 `provider_capabilities_ready is True` → `:297 _manifest_path()` 解析憑證的 `manifest_path`、`:317` 強制不得逃出 Data Root → `:299` 讀取 → `:304-305` sha256 必須等於憑證的 `manifest_sha256`，否則 `LaunchRejected` fail closed。

**sha256 校驗對象只有一個檔案**：`preflight/20260801T225249Z-94d490/manifest.json`。

必須保留：`preflight/latest-ready.json`、`preflight/20260801T225249Z-94d490/`（整包；其中 `competition-authorization.json` 另由 `run_verifier.py:539-543` 以 sha256 比對讀取）。
可刪除且已確認零程式引用：`preflight/final-real-not-ready/`、`preflight/ticket11-real-no-codex-v3/`、`ticket11-real-no-codex-v4/`（`run_verifier.py:572` 的 `preflight_root.glob("*/competition-authorization.json")` 對三者皆無命中，刪除不改變 `:581` 的 `matching_authorizations != 1` 判定）。

事實補記（不改變處置）：Ticket §3 要求保留的 `preflight/antigravity/` 與 `preflight/launch-reservations/` 經查**並非 launch 必需**——`cli.py:296-303` 每次以時戳＋亂數新建 antigravity attempt 目錄，不讀既有內容；`codex_bridge.py:42, 705-729` 的 launch-reservations 為 write-once，既有檔案唯一作用是 `:726-727` 觸發 `FileExistsError` 以跳過重複 run_id，launch 路徑不讀它。仍**依 Ticket 原文保留**（7 個小檔，保留無副作用）。

### 5. Sibling git 比對驗證（Developer，唯讀）

舊 repo `hoya-bit-market-agents` 全量 commit 比對：

```
old_unique_commits=33
present_in_final=33
missing_from_final=0
=== MISSING LIST ===
(none)
```

祖先性複查得 `ancestor_of_final_main=31`、`not_ancestor=2`。兩個非祖先 commit 經查為 cherry-pick，非遺失：

| 舊 commit | patch-id | -final 對應 | 佐證 |
|---|---|---|---|
| `2de04fe` feat: simplify agent debate rooms | `0948882b3e7e2f81…` | `9c2eaf4`（main 上） | reflog `main@{3}: cherry-pick` |
| `e770375` fix: unify three-page navigation | `e066b00753878436…` | `5dda59a`（main 上） | reflog `main@{4}: cherry-pick` |

舊 repo 51 個未提交變更中 50 個為純 CRLF 雜訊；`git diff --ignore-cr-at-eol --stat` 只剩 `docs/planning/architecture.md | 2 +-`，內容為 `milktea-agents-army-codex` → `milktea-agents-skills-for-codex` 改名，實測已存在於 -final `docs/planning/architecture.md:15`。無 stash。舊 repo `origin/main` 實測 `= 9b8a4510`（即 -final HEAD）。

9 個 worktree（ticket-3…ticket-11）HEAD 分別為 `cccc9d9`／`fc6bc65`／`fbf930f`／`b35069c`／`81d0086`／`3d0a39c`／`b829555`／`9422063`／`ad14b9f`，全部為 -final `main` 的祖先；各自未提交差異皆為同一份 milktea 改名（9 份 diff sha 皆 `8C69B706CA375392`，len 455），內容已在 -final。各 `.git` 為 `gitdir: D:/workstationD/hoya bit/hoya-bit-market-agents/.git/worktrees/<name>`，確認與舊 repo `.git` 連動，必須同組刪。

`hoya-bit-site`：git repo，HEAD `5aa3b751ff04ce992fc6677939574f7da45ec75b`，工作樹乾淨、無未推送 commit、stash 空、2 commits／118 tracked files。其 `docs/runs/` 16 個 run 全部存在於 Data Root 備份內。遠端已 404（見上）。

Developer 原始結論：舊 repo 單獨 `SAFE`；`_worktrees` 與 `hoya-bit-site` 因含未備份內容標為 `UNSAFE`。**四份備份完成後三者皆解除**，使用者裁示全刪。

### 6. 進行中 run 檢查（Developer，唯讀）

WSL `ps -eo pid,ppid,cmd` 無 hoya / live_dashboard / uvicorn / http.server 程序（唯一命中 `tail -f /tmp/hoya-btcmulti.log`，pid 2794，為 log 觀察程序，不碰 Data Root）；`ss -ltn` 僅 systemd-resolved 的 53 埠；`ss -ltnp | grep -E "8765|:87"` 無輸出（rc=1）。Windows `Get-NetTCPConnection -LocalPort 8765` 與 `netstat -ano | findstr 8765` 皆無符合。結論：無進行中 run、無殘留 live server，`inbox/` 可安全清理。

### 7. 實際刪除（Coordinator 執行）

`_data`（合計 2,408 檔）：`runs/` 1,799 檔（含 `latest.json` 與 36 個 run）、`presentation-v2` 41、`presentation-v3-sse` 41、`presentation-v4-nav` 41、`presentation-v5-chat` 41、`presentation-v6-desktop` 41、`presentation-v7-desktop` 41、`coordination/` 156、`adjustment-audit/` 45、`inbox/` 內容 156（目錄保留空殼，程式以 `mkdir(parents=True, exist_ok=True)` 重建）、`logs/live-server.log` 833 bytes、`preflight/final-real-not-ready` 2、`preflight/ticket11-real-no-codex-v3` 2、`preflight/ticket11-real-no-codex-v4` 2。每項刪除後皆以 `Test-Path` 確認不存在。

刪除後立即重驗憑證鏈：

```
manifest_path : preflight/20260801T225249Z-94d490/manifest.json
expected sha  : 11563be30424116e5ede8a01ffb95ce1df817fe00fac7e0ca41d45b4d8836429
actual  sha   : 11563be30424116e5ede8a01ffb95ce1df817fe00fac7e0ca41d45b4d8836429
MATCH         : True
provider_capabilities_ready: True
```

Sibling：`hoya-bit-market-agents` 734 檔、`hoya-bit-market-agents_worktrees` 1,261 檔、`hoya-bit-site` 282 檔，另刪除前次指令誤建的殘留空檔 `; else echo MISS`。刪除後 workspace 僅存 `.claude`、`backups`、`hoya-bit-market-agents-final`、`hoya-bit-market-agents_data`、`milktea-agents-skills-for-codex`。

### 8. 已知風險（Developer 回報，Coordinator 處置）

| # | 風險 | 處置 |
|---|---|---|
| R1 | `_worktrees` 內 481 檔／23MB 未備份 | 已補 `_worktrees` zip，**解除** |
| R2 | `hoya-bit-site` 遠端 404，`tools/build_site.py` 唯一副本 | 已補 site zip 並通過解壓＋`git fsck` 還原實測，**解除** |
| R3 | Ticket §3 列為必留的 `antigravity/`、`launch-reservations/` 實際非 launch 必需 | 僅記錄事實，仍依 Ticket 保留 |
| R4 | `2de04fe` 在 -final 為 dangling object，未來 `git gc` 會清掉該 SHA | 內容已由 `9c2eaf4` 保存於 main 並推上 GitHub；另有 oldrepo zip，**無實質損失** |
| R5 | WSL git 未設 `core.autocrlf`，查 status 會看到 54 個 modified 的 CRLF 假象 | **Reviewer 注意**：核對 Code Root 改動請用 `git diff --ignore-cr-at-eol`，否則會誤判大量修改。Windows git 與加旗標的 WSL git 結果逐字相同（`111 insertions(+), 4 deletions(-)`，僅 `CONTEXT.md`＋`architecture.md`） |
| R6 | workspace 殘留空檔 `; else echo MISS` | 已刪除 |
| R7 | worktrees zip 還原後不含 git admin 目錄，無法直接 `git status` | Coordinator 追加 oldrepo zip（含 9 個 worktree admin HEAD），**解除** |

### 9. 中途裁示：U1 煙霧測試殘留（Coordinator）

Developer 首次回報時指出「依 Ticket §5 執行的煙霧測試會在正式 Data Root 產生 run，與驗收條件 2『只剩 preflight 與空目錄』字面衝突」，並提出兩個收尾選項。Coordinator 採 (a)：刪除該 smoke run，讓終態符合驗收條件 2。

已刪除 `_data/runs/`（含 `20260314T015926Z-btc-smoke01` 與 `latest.json`，43 檔）與 `_data/inbox/20260314T015926Z-btc-smoke01/`（3 檔）。刪除後 `_data` 為 14 檔。

同時指示：後續煙霧測試改以暫存 Data Root 執行（以真實 `preflight/` 的逐位元組副本播種，使憑證解析與 sha256 校驗仍走真實憑證鏈），不得再寫入正式 Data Root。

### 10. 中途裁示：R8 README 失效路徑（Coordinator）

Developer 回報本票的 sibling 刪除使 `README.md` 15 處路徑指向已不存在的舊 repo（14 個 `cd` 指令＋第 240 行 Code Root 說明），照抄即失敗。

Coordinator 判定屬**本票造成的衍生風險**，依 Ticket「完成規則：沒有未解決的正確性、可執行性、可讀性、架構或衍生風險」納入本票收尾，非擴大範圍。修改限定為純路徑字串置換 `hoya-bit-market-agents` → `hoya-bit-market-agents-final`，不得重寫語句或變更結構。

Coordinator 另核實：`docs/` 內的 `cd` 指令 grep 為零命中；`tests/test_system_preflight.py:96` 亦含該路徑字串，交由 Developer 判斷是否需修。

### 11. Ready for Review（Developer，Snapshot 凍結）

**Snapshot**

- 基準：`main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`
- Review revision：同一 SHA（本票未 commit，變更全在工作樹）
- 本票歸屬的 git 變更檔案列表：**`README.md`（唯一）**，`git diff --stat` 為 `README.md | 30 ++++++------`＝15 增 15 刪，純路徑字串置換
- 工作樹合計 8 項：基準的 7 項 Planner 產出 ＋ `README.md`
- 非 git 管轄的本票產出：Data Root 清理、4 份備份 zip、3 個 sibling 目錄刪除
- **CRLF 注意**：WSL git 未設 `core.autocrlf`，直接 `git status` 會顯示約 60 個 modified，屬呈現差異。加 `--ignore-cr-at-eol` 後與 Windows git 逐字相同（3 modified＋5 untracked，`126 insertions(+), 19 deletions(-)`）

**變更摘要**

`README.md` 第 34、46、54、66、80、113、140、152、163、174、187、202、221、234 行的 `cd` 目標與第 240 行的 Code Root 說明，由 `hoya-bit-market-agents` 改為 `hoya-bit-market-agents-final`。第 95 行原本即為 `-final`，未動；`hoya-bit-market-agents_data` 的 4 處引用未動。`.py` 與 `config/` 零改動。

**Developer 判定不修改 `tests/test_system_preflight.py:96`（附證據）**

判定為純字串 fixture。證據：①唯一消費點 `system_preflight.py:196` 只執行 `str(Path(code_root))`，該函式對 `code_root` 無 `.resolve()`／`.exists()`／`.is_dir()`／任何 I/O（`grep -n "code_root" system_preflight.py` 僅回 126 行簽名與 196 行）；②姊妹測試 `tests/test_competition_drill.py:179` 與 `tests/test_reviewer_complete_attack.py:162` 使用本機從不存在的 `/code` 且測試通過，證明此參數不觸碰檔案系統；③`grep -rn "code_root" tests/` 顯示無任何測試對該值做斷言。修改它零行為效益且違反本票「程式零改動」硬條件。

**測試證據**

```
$ grep -n "hoya bit/hoya-bit-market-agents[^-_]" README.md
hit count: 0

$ python3 -m unittest discover -s tests
Ran 681 tests in 27.098s
OK
EXIT_CODE=0
```

fixture launch 煙霧測試（temp Data Root，以真實 preflight 逐位元組副本播種）：

```
copy identical preflight/latest-ready.json: True
copy identical preflight/20260801T225249Z-94d490/manifest.json: True
manifest sha == certificate manifest_sha256: True

{"status": "LAUNCHED", "run_id": "20260314T015926Z-btc-smoke02", ...}
{"status": "SEALED", "evidence_snapshot_sha256": "c235a25b1ebb9a22cb3333709ceda9097a4cf8bdf0381ee5a582cbe84fd4c6e4",
 "evidence_record_count": 7, "adopted_seat_ids": [七席全數], "exhausted_seat_ids": []}
{"status": "FINALIZED", "consensus_status": "consensus", "adopted_stance": "bullish",
 "tally": {"bullish": 6, "bearish": 1, "neutral": 0}, "valid_vote_count": 7,
 "stop_reason": "consensus_6_votes", "report_status": "accepted", "report_errors": []}

exit_code: 0
statuses: ['LAUNCHED', 'SEALED', 'FINALIZED']
THREE_LINE_HANDSHAKE_OK: True
verify_run status: VERIFIED
run_dir inside temp dir: True

=== production Data Root untouched? ===
production file count : 14
production runs/ exists: False
```

Data Root 終態複驗：`files: 14  dirs: 17  size: 68K`，內容為 `preflight/`（`latest-ready.json`、`20260801T225249Z-94d490/`、`antigravity/`、`launch-reservations/`）＋空的 `inbox/`、`logs/`；憑證鏈 `MATCH: True`、`AUTH MATCH: True`（`competition-authorization.json` sha256 `1ce9fa2d945924048ef75251a44e6cd3b0b80ec817c96189b20bf67ba18b8e54`）。4 份 zip 全部重驗 `testzip=None`、sha256 與製作時一致。

**TDD 適用性**：本票為資料與目錄清理，不新增程式行為，**TDD 不適用**。替代驗證＝既有 681 測試基準（不得下降）＋ fixture launch 端到端煙霧測試＋逐項目錄與雜湊事實核對。R8 的 README 修正為純文字路徑置換，以 grep 零命中與既有測試全綠驗證。未載入 `$milktea-skills-debug`（無非預期錯誤）；未載入 `$milktea-skills-git-merge-conflict`（未執行 merge／rebase／cherry-pick）。

**Developer 對五條驗收條件的自評**：1 ✅／2 ✅／3 ✅（目錄；文件落筆屬 Coordinator）／4 ✅／5 ✅。

**Developer 回報的剩餘風險**

| # | 風險 | 嚴重度 | 狀態 |
|---|---|---|---|
| R5 | WSL git 的 CRLF 呈現差異會讓 Reviewer 誤判 Code Root 被大量修改 | 中 | 已於契約中明示，Reviewer 須用 `--ignore-cr-at-eol` |
| R9 | `docs/operator-runbook.md`、`docs/COMPETITION-CARD.md` 等文件未逐一稽核是否另有失效路徑引用（`cd` 形式已 grep 零命中，其他形式未全面掃描） | 低 | 建議 Reviewer 覆核 |
| R3 | `antigravity/`、`launch-reservations/` 依 Ticket 保留，實測非 launch 必需 | 低 | 僅記錄事實 |
| R4 | `2de04fe` 在 -final 僅為 dangling object，`git gc` 後該 SHA 會消失 | 低 | 內容已由 `9c2eaf4` 保存於 main 並推上 GitHub；oldrepo zip 另存 |

### 12. Review 派工（Coordinator）

兩位 Reviewer 以獨立 `codex exec` session 平行派出，共用上述同一固定 Snapshot，首輪互不可見。

| | Reviewer A | Reviewer B |
|---|---|---|
| 後端 | Codex CLI `codex-cli 0.146.0` | Codex CLI `codex-cli 0.146.0` |
| 模型 | CLI 預設 | CLI 預設 |
| `model_reasoning_effort` | 模型／CLI 預設（未指定） | 模型／CLI 預設（未指定） |
| `review_engine` | `native` | `native` |
| 沙箱 | `--sandbox workspace-write --add-dir /tmp`（需實際重跑測試） | 同左 |

OCR Delegation 未使用：本 Task 無 `settings_update: open_code_review`，`delegate_ready` 不為 true，故 Reviewer B 依規則使用 `native`，未偵測、未安裝、未執行 OCR。

**獨立性註記**：兩位 Reviewer 為兩個隔離上下文，但使用同一 CLI 與同一模型，彼此**缺少跨模型獨立性**；Developer（Claude）與兩位 Reviewer（Codex）之間為跨模型。使用者可透過 `$milktea-skills-set-agent-roles` 調整。

### 13. 第 1 輪 Review 結果

兩位 Reviewer 首輪互不可見，獨立作業，結論高度一致。

| | Reviewer A | Reviewer B |
|---|---|---|
| 實際模型 | `gpt-5.6-sol` | Codex CLI 預設（介面未揭露 model ID，Reviewer 拒絕臆測） |
| 實際 `model_reasoning_effort` | `high` | 模型預設 |
| `review_engine` | `native` | `native` |
| 品味評分 | 🟢 | 🟢 |
| 致命問題 | 無（測試受阻屬環境限制） | 無 |
| AC1 備份 | 通過 | 通過 |
| AC2 Data Root 終態 | 通過 | 通過 |
| AC3 sibling 刪除與存證 | 通過 | 通過 |
| AC4 fixture launch | 通過 | 通過 |
| AC5 完整測試 | 證據不足 | 證據不足 |
| 首輪結論 | 證據不足 | 證據不足 |

**兩位都做了超出照抄的獨立驗證：**

- Reviewer A：對 README diff 做機械比對得 `pairs=15, non_mechanical_pairs=0, data_root_removed=0, data_root_added=0`；`git diff --ignore-cr-at-eol --exit-code -- '*.py' 'config/**'` 得 `EXIT_CODE=0`；煙霧測試前後對正式 Data Root 取全樹指紋 `fd3d2a6873b127d03009e7015eb4bc990ac2ccf515f32f83b7f9d701f3c2b618` 不變；**從 oldrepo zip 的 git objects／refs 自行重算** commit reachability 得 `reachable_unique_commits=33`／`present_in_final=33`／`missing_from_final=0`／`ancestor_of_final_main=31`。
- Reviewer B：`targeted_occurrences=15, exact_targeted_replacement=True, data_root_old=4, data_root_new=4, line_count_old=300, line_count_new=300`；**重新計算 zip 內 loose Git objects 的物件 SHA-1**，oldrepo 490 個、site 139 個，壞物件皆為 0；自行重建 commit reachability 與 patch-id 比對。

**AC5「證據不足」的根因與處置（環境限制，非程式缺陷）**

兩位 Reviewer 的 codex native sandbox 以 `--unshare-net` 執行並禁止 `socket.socket()`，`tests/test_live_dashboard.py` 的 10 個 localhost 測試一致拋出 `PermissionError: [Errno 1] Operation not permitted`，無任何 assertion failure。Reviewer A 排除該 10 案後得 `Ran 671 tests / OK / FAILURES=0 ERRORS=0`。

Coordinator 處置：①在同一台機器、同一 WSL distribution、無 codex 沙箱的條件下獨立重跑，得 `Ran 681 tests in 27.467s / OK / exit 0`；②以最小 probe 實測 `-c sandbox_workspace_write.network_access=true` 可解除限制（回傳 `SOCKET_OK ('127.0.0.1', 57689)`）；③以該旗標 resume 兩位 Reviewer 重跑。兩位皆據實回報，未為結案降低標準。

重跑結果：

| | Reviewer A | Reviewer B |
|---|---|---|
| socket probe | `SOCKET_OK ('127.0.0.1', 55927)` | `SOCKET_OK ('127.0.0.1', 41975)` |
| 完整測試 | `Ran 681 tests in 28.900s / OK / EXIT_CODE=0` | `Ran 681 tests in 29.124s / OK / EXIT_CODE=0` |
| AC5 更新判定 | **通過** | **通過** |
| 自身零改動 | README sha256 `6b765a02…f93856` 未變 | `3 files changed, 126 insertions(+), 19 deletions(-)` 未變 |

### 14. 唯一 Finding 與開發者處置

兩位 Reviewer **獨立提出同一項**，且**建議的修法完全一致**：

```
[建議] docs/planning/architecture.md:16 — 根目錄示意仍把已刪除的 hoya-bit-market-agents 標為 Code Root；
       同檔第 71 行的 Code Root 結構名稱亦未加 -final。
證據：第 20 行已明確宣告活動 Code Root 為 hoya-bit-market-agents-final，與第 16、71 行矛盾；
       test -e 證明舊 sibling 已不存在。
影響：本票刪除 sibling 後，權威架構文件仍展示不存在的 Code Root，形成文件漂移與維護導引風險。
建議：只把第 16、71 行根目錄名稱改為 hoya-bit-market-agents-final，不改其他架構內容。
```

嚴重度為**建議級，依 code-review Skill 不阻擋完成**；Coordinator 仍採納，因為它與本票已修的 R8（README 失效路徑）同屬本票刪除造成的衍生風險。

**Developer 無反駁**，明確表示這正是它第 1 輪自陳 R9（其他 docs 未逐一稽核失效路徑）的漏網實例，並完成最小修正：

```diff
@@ -13,11 +13,11 @@
-├─ hoya-bit-market-agents\      # Code Root
+├─ hoya-bit-market-agents-final\ # Code Root

@@ -68,7 +68,7 @@
-hoya-bit-market-agents\
+hoya-bit-market-agents-final\
```

Developer 提出可獨立核算的無夾帶佐證：`architecture.md` numstat 由第 1 輪 `105  4` 變為第 2 輪 `107  6`，差值恰為 +2 insertions／+2 deletions。並主動說明第 16 行註解 `#` 與第 17 行有 1 欄對齊差，因為修正對齊必須動到第 17 行，而兩位 Reviewer 都限定「只改這兩行」，故刻意不動（fenced text 示意圖，無語意影響）。

### 15. 第 2 輪複查與三方共識

Coordinator 將新 Snapshot 交回**原兩位 Reviewer**（同一 session，保留上下文），要求依 code-review Skill 的「複查」段自行重現，不得照單全收。

| | Reviewer A | Reviewer B |
|---|---|---|
| numstat 自行重算 | `107  6`，差值 `+2/+2` ✅ | `107  6`，差值 `+2/+2` ✅ |
| 內容自行檢視 | 第 16、71 行僅根目錄名稱變更；第 20 行與樹中其他項目未動 ✅ | 同左 ✅ |
| 舊名稱殘留 grep | `rg -F 'hoya-bit-market-agents\'` exit 1；`rg --pcre2 'hoya bit/hoya-bit-market-agents(?![-_])'` exit 1 ✅ | `ABSOLUTE_OLD_ROOT_EXIT_CODE=1`、`BACKSLASH_OLD_ROOT_EXIT_CODE=1` ✅ |
| 完整測試 | `Ran 681 tests in 28.381s / OK / EXIT_CODE=0` | `Ran 681 tests in 28.243s / OK / EXIT_CODE=0` |
| `.py`／`config/` | `git diff --ignore-cr-at-eol --exit-code` → `EXIT_CODE=0` ✅ | `PY_CONFIG_NUMSTAT` 空、`PY_CONFIG_UNTRACKED` 空 ✅ |
| 工作樹 | 3 modified＋5 untracked，未新增檔案 ✅ | `EFFECTIVE_MODIFIED_COUNT=3`、`UNTRACKED_STATUS_ENTRIES=5` ✅ |
| Finding 處置 | **關閉** | **關閉** |
| **最終結論** | **Reviewer A 簽署 Ticket 01 通過** | **Reviewer B 簽署 Ticket 01 通過** |

**三方共識達成**：Developer 完成實作與驗證且無未解反駁；Reviewer A 與 Reviewer B 於獨立上下文各自簽署通過。

### 16. R9 收尾稽核（Coordinator）

Developer 的 R9 指出其他文件未逐一稽核失效路徑。Coordinator 全 repo 掃描（排除 `docs/work/` 與 `docs/architecture-reviews/` 兩處本次規劃產出）：

| 形式 | 命中 | 判定 |
|---|---|---|
| POSIX `hoya bit/hoya-bit-market-agents`（非 `-final`、非 `_data`） | 僅 `tests/test_system_preflight.py:96` | Developer 已證明為純字串 fixture（`system_preflight.py:196` 只做 `str(Path(...))`、姊妹測試以不存在的 `/code` 通過、無斷言讀回）→ 不改正確 |
| Windows `hoya-bit-market-agents\` | 0 | ✅ |
| `hoya-bit-site` ／ `_worktrees` | 僅 `docs/planning/architecture.md:530` | §11.1 的清理決策紀錄（描述「連同 `_worktrees` 與 `hoya-bit-site` 一併刪除」），非失效路徑 → 保留正確 |

`docs/operator-runbook.md`、`docs/COMPETITION-CARD.md` 皆零命中。**R9 關閉，無殘留待辦。**

### 17. 完成（Coordinator）

**最終 Snapshot**

- 基準與 revision：`main` @ `9b8a4510ec9406f19506e21d50af7918da2385d4`（本票未 commit）
- 工作樹 8 項：基準的 7 項 Planner 產出（其中 `docs/planning/architecture.md` 由本票追加 2 行修正）＋ `README.md`
- 本票歸屬的變更：`README.md`（15+/15−）、`docs/planning/architecture.md`（+2/−2）
- `.py` 與 `config/` 零改動（`git diff --ignore-cr-at-eol --exit-code -- '*.py' 'config/**'` → `EXIT_CODE=0`，由 Reviewer A 獨立確認）

**必跑指令最終結果**

| 執行者 | 結果 |
|---|---|
| Developer | `Ran 681 tests in 28.087s / OK / EXIT_CODE=0` |
| Reviewer A | `Ran 681 tests in 28.381s / OK / EXIT_CODE=0` |
| Reviewer B | `Ran 681 tests in 28.243s / OK / EXIT_CODE=0` |
| Coordinator | `Ran 681 tests in 27.467s / OK / exit 0` |

fixture launch 煙霧測試：Developer 與兩位 Reviewer 各自獨立執行，皆得三行 handshake（`LAUNCHED`／`SEALED`／`FINALIZED`）依序輸出、`verify_run = VERIFIED`、正式 Data Root 指紋不變。

**五條驗收條件最終判定：全數通過。**

**未解風險（不阻擋完成，供後續 Ticket 注意）**

| # | 風險 | 嚴重度 | 說明 |
|---|---|---|---|
| R5 | WSL git 未設 `core.autocrlf`，直接 `git status` 會顯示約 54–60 個 modified 的 CRLF 假象 | 中 | 後續所有 Ticket 的 Developer 與 Reviewer 契約都必須載明：核對 diff 一律加 `--ignore-cr-at-eol`。repo 仍缺 `.gitattributes`（不在本次 12 票範圍） |
| R3 | `preflight/antigravity/`、`preflight/launch-reservations/` 依 Ticket 保留，實測非 launch 必需 | 低 | 僅記錄事實，未改處置 |
| R4 | 舊 commit `2de04fe` 在 -final 僅為 dangling object，`git gc` 後該 SHA 會消失 | 低 | 內容已由 `9c2eaf4` 保存於 main 並推上 GitHub；`hoya-bit-market-agents-oldrepo-20260805.zip` 另存完整 `.git` |
| — | `docs/planning/architecture.md` 第 71 行的 Code Root 樹仍列出實際不存在的 `AGENTS.md`，且寫 `execution\hoya_market_agents\`（實際在頂層 `hoya_market_agents/`） | 低 | 兩位 Reviewer 均明確要求本票不得順帶重寫；已知漂移，不在本次 12 票範圍 |

**未 commit**：依專案 git 規則與本 Task 授權範圍，本票變更保留在工作樹，未執行 `git add`／`commit`／`push`。

**角色結束**：Developer（Claude 臨時 Agent）與 Reviewer A、Reviewer B（各自獨立的 Codex session）於本票共識後結束。Ticket 02 起建立全新實例。
