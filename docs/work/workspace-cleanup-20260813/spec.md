# 工作區殘留檔清理（workspace-cleanup-20260813）

來源：`docs/architecture-reviews/2026-08-13-workspace-cleanup.html`（2026-08-13 架構健檢報告）。
使用者已於報告決策關卡核准「方案一：最小清理（直接刪，不先壓 zip）」。

## 問題

wsl-only-runtime-onboarding 等工作包結案後，工作區根目錄留下派工／審查交接稿與殘留紀錄檔，
加上兩個工作樹內的測試與直譯器快取，讓工作區看起來雜亂，且容易與正式檔案混淆。

## 目標

刪除查證為零引用的殘留檔與可自動再生的快取，使工作區根目錄只剩五個正式資料夾；
全程零程式改動，系統（WSL 正式運行環境與主 repo 進行中工作）完全不受影響。

## User Stories

1. 身為專案擁有者，我希望工作區根目錄只留正式資料夾與檔案，以便一眼分辨哪些東西是系統的一部分。
2. 身為專案擁有者，我希望清理前後用同一套命令驗證，以便確信清理沒有波及任何會影響系統運行的內容。

## 需求與行為

刪除下列項目，除此之外一律不動：

1. 工作區根目錄 6 個交接稿臨時檔：
   - `ticket01-a2-targetpath.tmp`
   - `ticket01-claude-prompt.tmp`
   - `ticket01-review-a-fixes.tmp`
   - `ticket01-review-a-ui-seam.tmp`
   - `ticket01-review-b-fixes.tmp`
   - `ticket01-review-b1-final.tmp`
2. 工作區根目錄 `old-folder-cleanup.log`（46 位元組，單行 TIMEOUT 殘留）。
3. `AI-agnets-debating-chamber\.pytest_cache\` 整個資料夾（pytest 殘留；專案測試用 stdlib unittest）。
4. 兩個工作樹內全部 `__pycache__\` 資料夾。位置以刪除當下
   `git status --ignored --porcelain=v1` 在各工作樹的輸出為準（盤點時主 repo 5 處、wsl-runtime 5 處），
   不得用寫死清單，以免執行間隙新產生的快取漏刪或誤刪。

## 實作決策

- 資料與所有權
  - 刪除對象全部位於 git 管理之外（工作區根散檔＋git ignored 快取），不觸碰任何 git 追蹤內容。
  - `_data`（含 `preflight/latest-ready.json`，CONTEXT.md 明訂不主動刪除）、`backups/`（architecture.md 明訂的還原點）、
    `.claude/` 全數原樣保留。
  - 主 repo 57 筆未提交修改為使用者進行中工作，一個位元組都不能動。
- 模組責任與公開介面
  - 無任何模組、介面、Schema 或設定變更；純檔案系統刪除。
- 相容、遷移與技術限制
  - `AI-agnets-debating-chamber-wsl-runtime\.git` 是指向主 repo 的 worktree 指標檔，屬 git 管理機制，不在刪除範圍。
  - 刪除在 Windows 側執行即可，不需進 WSL。
  - `.tmp` 與 `.log` 刪除後不可回復（使用者已核准不先備份）；快取類刪後由 pytest／Python 自動再生。

## 驗收條件

修改前基準（2026-08-13 盤點時記錄）與修改後必須用同一命令比對：

1. `git -C AI-agnets-debating-chamber status --porcelain=v1` 輸出 57 行，內容與清理前一致；
   `git -C AI-agnets-debating-chamber rev-parse HEAD` 仍為 `85b0553bbbea77afb335d458b0a8399574b2f36a`。
2. `git -C AI-agnets-debating-chamber worktree list` 仍列出兩行（主 repo `85b0553`、wsl-runtime `c99a0b1`），無 prunable 警告。
3. `git -C AI-agnets-debating-chamber-wsl-runtime status --porcelain=v1` 輸出為空（工作樹仍乾淨）。
4. 工作區根目錄不存在任何 `ticket01-*.tmp` 與 `old-folder-cleanup.log`；
   只剩 `.claude`、`AI-agnets-debating-chamber`、`AI-agnets-debating-chamber-wsl-runtime`、
   `AI-agnets-debating-chamber_data`、`backups` 五個項目。
5. 兩個工作樹 `git status --ignored --porcelain=v1` 不再列出 `.pytest_cache/` 與 `__pycache__/`。
6. `AI-agnets-debating-chamber_data` 與 `backups` 的檔案數與總位元組數與清理前一致（清理不得寫入或刪除）。
7. WSL 內 `bash docs/work/wsl-only-runtime-onboarding/acceptance/run.sh offline` 通過
   （或替代煙霧測試：`./START-HERE.sh` 啟動 webapp 後首頁正常開啟再正常停機）。

## 測試決策

- 公開行為：驗收條件 1–7 全部以命令輸出判定，不依賴人工目測。
- 測試接縫：本工作不寫新測試；沿用既有 acceptance offline 閘門作為系統仍能跑的證據。
- 既有測試模式：stdlib unittest／acceptance run.sh，不引入 pytest。
- 不應耦合的實作細節：刪除工具（PowerShell 或檔案總管）不限定；只驗收結果狀態。

## 不在範圍內

- `backups/` 的任何處置（維持原樣，使用者已決定）。
- `_data` 的任何清理（含已退役的 `latest-ready.json`）。
- docs/work 歷史工作包、git 分支或遠端的整理。
- 任何 git add／commit／push。
- 主 repo 進行中 Windows 可靠性工作的任何檔案。

## 補充

- 全工作區引用查證（ripgrep：`ticket01-`、`old-folder-cleanup`、`TIMEOUT time=`）確認刪除清單零程式引用，
  證據記錄於架構健檢報告第 3 節。
- 主 repo 已提交部分與 GitHub 同步（`origin/agent/pre-windows-reliability-backup-20260811` = `85b0553`）；
  本次清理與遠端無涉。
