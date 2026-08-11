# 05 — run_verifier 資料驅動與舊 run 相容

- Spec：`../spec.md`
- Blocked by：02 四輪投票狀態機
- 需求覆蓋：R-007（相容）、R-006（燈號判定沿用）

## 交付成果

`run_verifier` 的合法 stop_reason 與時間表檢查由該 run manifest 規則快照的輪陣列推導，不再寫死枚舉分支；帶 v1 快照的舊 run 與無快照的 legacy run 判定行為完全不變。

## 驗收條件

- 新 v2 run：各輪合法 stop_reason 通過驗證；該 run 規則禁止的 stop_reason（例如超出輪數、票數與門檻不符）被拒。
- 帶 v1 規則快照的舊 run 以其當時規則判定；既有相容測試（`test_verify_run.py` 的 v1 與 legacy 案例，含「舊 run 不被新票階梯評判」）全綠。
- 全套件測試（WSL）全綠。
