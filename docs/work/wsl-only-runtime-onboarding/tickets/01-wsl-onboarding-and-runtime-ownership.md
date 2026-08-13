# 01 WSL 新手安裝、入口與 Runtime Ownership

- Spec：`../spec.md`
- Spec 覆蓋：R-001、R-002、R-003、R-004，以及 R-012 的既有 Data Root 保護
- Blocked by：無

## 交付成果

1. 把 `README.md` 收斂成既有 hero 圖片、零經驗 WSL2 Ubuntu 教學與最小故障排除；移除其餘既有說明。
2. README 依序提供 Windows 安裝 WSL、重開機與建立 Ubuntu 帳號、在 WSL home clone、執行 `bash setup-wsl.sh`、三個 Provider 的 Linux 安裝／登入命令、桌面捷徑、Bash 入口及 MobaXterm 連線方式。
3. Code Root 提供可重複執行的 `setup-wsl.sh`、`START-HERE.sh`、`STOP-HERE.sh`；路徑從腳本位置動態推導，不寫死使用者名稱、磁碟或 `/home/leslie`。
4. setup 不自動安裝或登入 Provider、不讀寫 Provider credential、不建立 `.venv`；只檢查 WSL 執行所需的最小條件並建立兩個桌面捷徑：`開啟辯論室`、`關閉辯論室`。
5. 兩個捷徑共用單一薄入口 `scripts/wsl-shortcut.ps1`，PowerShell 只負責隱藏視窗並呼叫 WSL Bash；所有啟停、ownership 與確認邏輯留在 Bash／Python。
6. 移除 setup 曾建立的 `WSL 開啟辯論室`、`WSL 關閉辯論室` 重複捷徑；不得刪除其他不屬於本專案的桌面檔案。
7. 建立單一 Python runtime-control 模組，統一解析 `/health`、判斷 owned／foreign／unknown、送出有 precondition 的 shutdown；Bash 與 PowerShell 不各自實作 JSON ownership 規則。
8. `/health` 固定回傳 `app`、`runtime_owner=wsl`、非空 per-server `instance` 與 JSON boolean `active_run`。缺欄、錯型別、404、malformed JSON、其他 app 或其他 owner 一律 fail closed。
9. `START-HERE.sh` 只重用 owned WSL listener；foreign／unknown listener 必須顯示簡短原因後退出，不可占用、終止或誤報成功。
10. `STOP-HERE.sh` 只關閉剛確認的 owned WSL instance，送出 `expect_runtime=wsl` 與 `expect_instance`；server 在 POST 當下重新比對，不符回 `409`。`active_run=true` 時必須取得使用者明確確認。
11. 保留與 Code Root 相鄰的 `AI-agnets-debating-chamber_data`；不得刪除、移動、重建或重新格式化既有 run。只允許正常啟動新增必要 log，以及後續正常分析新增 run artifacts。

## 必要寫入範圍

- `README.md`
- Code Root 的三個 Bash 入口
- `scripts/` 內 WSL shortcut／安裝入口及必要的共用薄封裝
- `hoya_market_agents/webapp/` 內 runtime ownership、health、shutdown 的最小接縫
- 對應的 launcher／runtime ownership 測試
- 不修改 Provider process、attempt scheduler、Early Opening、research proof parser 或 Live 同頁流程

## 驗收條件

1. README 只剩 hero 圖片及其引用、零經驗 WSL 教學、Provider 命令、MobaXterm 簡短教學與最小故障排除；沒有本機專用路徑、Anaconda、READY、preflight 或 Codex Task 指示。
2. 在 Code Root 執行兩次 `bash setup-wsl.sh` 都成功，第二次不增加額外捷徑；桌面最後只有兩個本專案捷徑，名稱與 Target／Arguments 符合本票契約。
3. `bash -n` 通過三個 Bash 入口；PowerShell 腳本可解析，且薄入口沒有 Provider CLI 呼叫或 ownership 商業邏輯。
4. 從 WSL Bash、MobaXterm 的 Ubuntu shell、兩個 Windows 捷徑進入時，皆管理同一個 WSL webapp runtime。
5. owned WSL、foreign owner、其他 app、malformed、404、連線失敗與 port free 均有隔離測試；foreign／unknown 情境零啟動、零 shutdown。
6. listener replacement 競態測試證明：health 所見 WSL instance 被替換後，舊 precondition 的 POST 收到 `409`，replacement listener 保持存活。
7. `active_run` 缺失或非 boolean 時拒絕；`active_run=true` 的取消確認零 POST，明確確認才送出 shutdown。
8. 實作前後以相同清單與 SHA-256 稽核既有 Data Root；除允許的新 log／新 run 外，既有檔案內容與路徑不變。
