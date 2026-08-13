# 05 WSL Codex Research Proof Canary 與條件式修復

- Spec：`../spec.md`
- Spec 覆蓋：R-011
- Blocked by：03

## 交付成果

1. 先在 WSL 執行一個最小、單一 attempt 的真實 Codex research canary，確認目前 CLI 輸出是否包含可機器判定的搜尋 invocation 與對應成功 result。
2. 若 canary 沒有重現 `research_proof_missing`，不得修改 production parser；只保存足以重跑與稽核的安全證據後結案。
3. 只有 canary 確實重現 `research_proof_missing` 時，才在 Codex research proof 的既有 parser 接縫做最小修正，不改 prompt 規則、gateway 契約或其他 Provider parser。
4. 有修正時，proof 只計入相符的 search tool invocation 與對應的非錯誤 result；URL、final prose、stderr 文字、只有 tool-use 沒有 result，均不得單獨算 proof。
5. malformed event、缺 result、error result、tool-use-only 或無搜尋的輸出一律 fail closed，維持穩定 `research_proof_missing`。
6. canary artifact 只記錄命令形狀、CLI／model 識別、invocation count、parse status、malformed count、failure code 與必要 hash；不得保存 credential、完整 prompt、完整 Provider 回覆或原始敏感 stderr。

## 必要寫入範圍

- WSL Codex research adapter／proof parser 及其專屬測試（僅在 canary 重現時）
- 本工作包的安全 canary 證據文件
- 不修改 Early Opening、research scheduler、其他 Provider parser、Live UI、shortcut 或 runtime ownership

## 驗收條件

1. canary 在啟動任何完整 debate 前先執行；若已知失敗出現，立即停止，不等待完整 run。
2. 保存的證據能回答：實際呼叫幾次、是否成功 parse、是否出現 malformed event、最終 failure code；且不含 credential、完整 prompt／response／stderr。
3. canary 通過時，production parser 的審前／審後 SHA 相同，證明沒有為了理論風險修改程式。
4. canary 重現時，先有能失敗的 fixture／回歸測試，再完成最小 parser 修正；matching invocation＋success result 通過，其餘 malformed／missing／error 情境 fail closed。
5. mutation-style 驗證能抓到「URL 或 prose 直接算 proof」、「tool-use-only 算 proof」、「error result 算 proof」。
6. Codex adapter 與 real-provider 的相關離線測試全綠；除核准的單一 canary 外，不得在測試中呼叫真實 Provider。
