# 02 WSL Provider Process Group 與穩定終止結果

- Spec：`../spec.md`
- Spec 覆蓋：R-007
- Blocked by：無

## 交付成果

1. 每次 WSL Provider invocation 都以獨立 POSIX session／process group 啟動，registry 保存可回收的 group identity。
2. timeout、cancel、first-valid-wins 與 cutoff 皆依同一 invocation generation，先送 `SIGTERM`、保留既有 grace，再以 `SIGKILL` 回收仍存活的整個 group。
3. ProcessRegistry 以 attempt key 加 generation 分隔 invocation；同 key resume 不得沿用前一代 outcome，不同 key 不得被全域鎖不必要地序列化。
4. 同 key 的 track、settle、terminate 與 poisoned track 回收共用 per-key reclaim lock；取得鎖後重新讀取該 generation 已 settle 的 outcome，不能用已關閉 handle 覆寫較強結果。
5. 每個 invocation 只產生一個穩定 process termination outcome；已成功完成不得被晚到 cancel 改成 `process_tree_termination_failed`。
6. 無法證明整個 group 已回收時 fail closed，回傳穩定 `process_tree_termination_failed`，不得把「root 已退出」當成「所有子孫已回收」。
7. 本票只實作 WSL POSIX 路徑；不新增或修改 Windows Job Object、`taskkill`、CP950 decoding 或 Windows Provider fallback。

## 必要寫入範圍

- Provider process lifecycle／registry 模組
- 對應 adapter／process lifecycle 測試
- 不修改 README、shortcut、webapp ownership、research scheduler、Live UI 或報告格式

## 驗收條件

1. timeout、cancel、first-valid-wins 與 cutoff 測試均證明 root 與仍存活子孫被回收，沒有 orphan process。
2. root 先退出但子孫仍存活的案例仍可回收整個 group；不能只靠 root PID 不存在判定成功。
3. deterministic barrier 測試涵蓋 finish／cancel race：worker 已 settle clean 後，等待 reclaim lock 的 terminate 讀回相同 clean outcome，不覆寫成 failure。
4. same-key resume 測試涵蓋 first clean → second generation expect → cancel-before-track → second track；結果只可採第二代實際 verdict。
5. poisoned track 與 concurrent terminate 的同 key 最大回收併發數為 1；不同 key 以 barrier 證明仍可並行。
6. 同一 `(attempt key, generation)` 的重複查詢得到相同 terminal process outcome。
7. mutation-style 驗證至少能抓到：移除 generation、移除鎖後重讀、把 poisoned reclaim 移出鎖、只檢查 root PID。
8. WSL 定向測試與相關 Provider adapter 完整離線測試全綠；測試不得呼叫真實 Provider。
