# 07 WSL-only Release Acceptance 與七席 7/7 真實驗收

- Spec：`../spec.md`
- Spec 覆蓋：R-012，以及 R-001～R-011 的整合驗收
- Blocked by：01、02、03、04、05、06

## 交付成果

1. 對 R-001～R-012 建立一份可重跑的 release acceptance 記錄，分開保存離線測試、三個 Provider canary、README／shortcut smoke、Data Root 稽核與真實市場 run 的證據。
2. 嚴格採 fail-fast：先跑離線測試與單一 Provider canary；已知 failure code 或可重現缺陷出現時立即停止，不啟動完整 run。修正必須回到所屬 Ticket 完成 Developer 與雙 Reviewer 複驗後，再從失敗 gate 重跑。
3. 依序確認 Codex、Claude、Antigravity 在 WSL 的最小真實 canary，再執行一題真實市場問題的完整 WSL run。
4. 最終 run 必須是固定七席 `7/7` 有效 final votes，並在既有時間規則內完成；不得以 fake drill、預約、人工補票或模擬結論替代。
5. 最終 run 的 `report.html`、`debate.html`、`report.md`、`evidence.jsonl`、`debate.jsonl`、`votes.json` 與 manifest 全部存在、hash 相符且可由 `verify-run` 成功驗證。
6. 驗收期間 Windows 只允許薄 shortcut／`wsl.exe`／一般瀏覽器與必要管理程序；不得出現從 Windows 啟動的 Codex、Claude、Antigravity Provider process。
7. 證明 README 零經驗路徑、Bash／MobaXterm 路徑與桌面捷徑都進入同一 WSL runtime，並能安全關閉；驗收結束後 port ownership 與 listener 狀態有明確記錄。
8. 實作前後稽核既有 Data Root；只允許本次正常 log 與新 run artifacts，不得刪除、移動、重寫或重新格式化歷史 run。
9. 文件誠實區分「本次 run 通過」與「Provider 永久可靠」；單次成功不得被描述為未來 Provider 的永久保證。

## 必要寫入範圍

- 本工作包 acceptance 證據、重跑工具與結案摘要
- 不新增產品功能；發現產品缺陷時停止並退回對應 Ticket 修正，不在本票做未經 Review 的臨時補丁

## 驗收條件

1. WSL 離線完整測試、Bash syntax、必要的 PowerShell thin-entry 檢查、Python compile、production JavaScript harness、JavaScript syntax 與 `git diff --check` 全部 exit 0。
2. runtime ownership acceptance 涵蓋 owned、foreign、unknown、malformed、404、active run 與 listener replacement；跨 owner 不誤殺。
3. 三個真實 Provider canary 各自成功且證據不含 credential、完整 prompt／response 或敏感 stderr；任一失敗都必須停止，不能進入完整 run。
4. 真實 run 的 primary／backup lineage、attempt terminal outcomes、Opening invocation、七張 final votes、tally、report status 與 manifest 能彼此對帳。
5. `votes.json` 恰有固定七席的七張有效 final votes；不存在缺席、重複席位、人工補票、無 adopted stance 或把 research output 當 vote 的情況。
6. `verify-run` exit 0 且回報 VERIFIED；manifest 列出的 artifact 全存在並逐一 SHA-256 相符。
7. run 完成時間不超過現有總時程，research seal、Opening、vote、report completion deadline 與各 offset 均符合 `config/debate_rules.json`。
8. Windows process 稽核確認沒有 Windows Provider process；WSL process 稽核確認 timeout／cancel 後沒有殘留 Provider group。
9. README 可由未假設專案專用知識的驗收步驟逐段執行；setup 兩次結果一致且桌面只有兩個本專案捷徑。
10. Data Root 前後稽核通過；任何差異都能對應到本次允許的新 log／run artifact。
11. Spec Reviewer 與 Standards Reviewer 對固定 final snapshot 都無未解決的阻擋／重要 finding，才可宣告本工作包完成。
