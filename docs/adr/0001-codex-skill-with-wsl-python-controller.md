# ADR 0001：Codex Skill 搭配 WSL Python Controller

- 狀態：Accepted
- 日期：2026-08-01

## 背景

產品必須在 15 分鐘內協調 Codex Sub-agents、Claude CLI 與 Antigravity CLI，並嚴格執行研究、辯論、替補與報告時間關卡。只靠 Core Agent 的 Prompt 記憶時間與程序狀態，無法獨立測試，也難以證明 deadline、重試及檔案寫入確實發生。

## 決策

- 使用 repo-local Codex Skill 作為使用入口與 Core Agent 規則。
- GPT-5.6 Sol Core Agent 負責派工、原文分發、計票與報告內容。
- 使用 WSL Python 3.12 標準函式庫控制程式負責單調時鐘、外部 CLI 程序、timeout、重試、格式驗證、合併與報告排版。
- Python 不做市場方向判斷、不投票、不修改 Agent 原文或 Core 結論。
- MVP 不建立網站、後端服務或常駐資料庫。

## 理由

- 時間與程序狀態可用假時鐘及假 provider 自動測試。
- 異質 CLI 的失敗、取消與替補有單一控制點。
- Core 保留自然語言分析責任，控制程式不越權成為第二個決策者。
- 使用既有 WSL Python，避免新增 Node、框架與長期服務。

## 主要後果

- 正式執行依賴 Windows Codex 與 WSL 間的可靠路徑及程序控制。
- Provider CLI flags、登入、搜尋、resume 與並行額度必須在賽前驗證。
- Core 與 Python 之間需要版本化 JSON contract。
- 若 Python controller 失敗，系統必須交付紅燈失敗報告，不得退回未驗證的自由文字流程。
