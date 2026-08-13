# ADR 0009：WSL 是唯一正式 Provider Runtime

- 狀態：Accepted
- 日期：2026-08-12
- 取代範圍：ADR 0001「正式執行依賴 Windows Codex 與 WSL 間的可靠路徑及程序控制」之舊後果

## 背景

產品曾同時維護 Windows 原生與 WSL Provider Runtime。Windows 真實執行反覆遇到 CLI 登入環境、PATH、文字編碼、程序樹回收、逾時與 proof 輸出差異；同一產品因此出現兩套啟停、終止與驗收行為。WSL2 Ubuntu 的 Linux 原生 Claude、Codex 與 Antigravity CLI 已能完成真實七席 run，且原始 Python controller 架構本來就以 WSL 為主要執行環境。

使用者需要把產品交給沒有 WSL 經驗的人，也需要由 MobaXterm 操作。繼續維護兩套 Provider Runtime 會擴大教學、測試與故障面，卻不增加產品能力。

## 決策

- WSL2 Ubuntu 是 webapp、Python controller 與全部 Provider CLI 的唯一正式 Runtime。
- Windows 10／11 只作 WSL 宿主、瀏覽器與兩個桌面捷徑所在環境；PowerShell wrapper 只能呼叫 `wsl.exe`，不得執行 Provider。
- Ubuntu 終端、MobaXterm 與 Windows 捷徑都操作同一個 WSL Runtime、同一個 Code Root 與同一個 Data Root。
- Windows 原生 Provider 啟停、Job Object、PATH／編碼修補與真實七席驗收退役，不提供 fallback。
- 產品不建立第三套 CLI 管理或登入平台；README 提供 Linux 安裝／登入指令，Provider failure 由既有 attempt contract 誠實處理。

## 理由

- 單一 Runtime 消除 Windows／WSL 行為分岔，縮小安裝、程序回收與驗收範圍。
- 沿用 WSL Python 標準函式庫、POSIX process group 與既有 Data Root，不需要新依賴或資料遷移。
- Windows 桌面捷徑仍保留新手的一鍵入口；進階使用者可直接使用相同 Bash，因此不犧牲操作便利。
- 已知 Windows-only 修補留在封存 worktree 供追查，比把未完成修補帶入正式產品更可回復。

## 主要後果

- 正式支援環境固定為 Windows 10／11＋WSL2 Ubuntu；其他 WSL 發行版只盡力相容。
- 新使用者必須先安裝 WSL2 Ubuntu 與 Linux 原生 Provider CLI。
- Windows 原生 Provider 即使已登入也不屬於正式產品路徑。
- Release acceptance 只在 WSL 執行真 Provider canary 與七席 run，並確認 Windows 沒有 Provider process。
- 若未來要重新支援 Windows 原生 Provider，必須另立 ADR 與獨立工作包，不得在 WSL 腳本加入隱藏 fallback。
