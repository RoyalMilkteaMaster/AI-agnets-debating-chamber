# 04 獨立 Early Opening Provider Invocation

- Spec：`../spec.md`
- Spec 覆蓋：R-010
- Blocked by：03

## 交付成果

1. research 結果在 evidence seal 前 adopted 後，系統必須另行發出一次 Opening provider invocation；不得把 research output、research envelope 或摘要改名後當成 Opening。
2. Opening 使用該席 adopted attempt 的實際 Provider 與 model lineage，並保留可稽核的 phase／attempt 關聯。
3. Opening prompt／schema 只產生辯論開場需要的內容；不得要求再次完成 research evidence envelope，也不得把 Opening 計入 research proof。
4. Opening 成功或失敗皆不能改寫先前 research attempt 的 terminal outcome、evidence lineage 或 adopted 決定。
5. 保持既有 evidence seal、Opening deadline 與 DebateStateMachine 時序；不得為等待 Opening 無限制延後 seal 或後續投票。
6. artifact／events 能區分 research 與 Opening 是兩次不同 invocation，且不保存 credential 或不必要的完整 prompt／stderr。

## 必要寫入範圍

- Early Opening dispatch、DebateStateMachine／launcher 的必要接縫
- Real Provider 的 Opening phase 呼叫與對應測試
- 不修改 Codex research proof parser、webapp ownership、shortcut 或 Live launch UI

## 驗收條件

1. fake adapter 明確記錄同一席至少兩次獨立 invocation：research 與 Opening；兩者輸入、phase 與輸出物件不可相同或共用。
2. Opening 的 actual Provider／model 與 adopted research attempt lineage 一致。
3. research 成功但 Opening 失敗時，research evidence 仍保持 adopted；失敗依既有 phase 規則可見且不冒充 Opening 成功。
4. late／failed／cancelled／未 adopted 的 research attempt 不得觸發 Opening。
5. seal 到達時尚未完成的 Opening 依既有 deadline 收斂，不延長 17 分鐘總時程，也不讓晚到 Opening 改寫已進入的辯論狀態。
6. mutation-style 驗證能抓到「直接重用 research output 作 Opening」與「Opening 回寫 research outcome」。
7. 相關 launcher、state machine、real-provider 測試全綠，且全部使用 fake adapter，不呼叫真實 Provider。
