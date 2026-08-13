# 03 Attempt Recovery、Terminal Outcome 與 Lineage

- Spec：`../spec.md`
- Spec 覆蓋：R-008、R-009，以及 R-012 的報告期限與離線測試隔離
- Blocked by：02

## 交付成果

1. 保持固定七席 primary roster：3 Codex、2 Claude、2 Antigravity；每席最多啟動一次不同 Provider 的 backup，候選順序確定且不重複。
2. research worker capacity 能容納七個 primary 與最多七個 backup；Provider CLI 不在 WSL `PATH` 時立即記錄 `provider_cli_missing` 並進入既有 recovery，不等待無意義 timeout，也不使整個 webapp 崩潰。
3. `ResearchScheduler.attempt_outcomes` 對每個 attempt 只允許一個 terminal outcome：`adopted`、`superseded`、`failed`、`cancelled` 或 `late_discarded`。
4. attempt 一旦 timeout、cancel、failure 或其他 terminal finish，晚到 result 只能成為 diagnostic，不得被 adopted，也不得覆寫 terminal outcome、failure code 或已採用 attempt。
5. primary／backup event 與 `research-summary.json` 提供 additive lineage：seat、attempt、requested／actual provider、requested／actual model、attempt kind、phase、terminal outcome、failure、adopted／exhausted；舊 run 缺欄位時顯示「未記錄」，不得重寫舊 artifact。
6. Live 七席投影使用同一 additive summary；一席若有多次 attempt，畫面仍以單席呈現，並可辨認目前採用來源與簡短失敗／重試狀態，不以錯誤文字大量占用版面。
7. 每次 research invocation 都從 generic research schema deep copy，並以單值 `enum` 綁定 envelope `seat_id`，以及每張 card 的 `run_id`、`seat_id`、`attempt_id`；Claude、Codex、Antigravity 正式 research callsite 全部使用該 schema。
8. `RealEvidenceGateway` 保持 lineage／run binding 的 fail-closed 權威；schema 是較早的輸出限制，不取代 gateway validation。
9. WSL Provider executable 只由當前 WSL `PATH` 動態解析，不寫死個人 home；offline full-phase 測試注入假的 proposition／Provider adapter，禁止暗中呼叫真實 CLI。
10. 保留既有 report completion deadline 修正、17 分鐘總時程、階段 offset、投票門檻與七席語意，不在本票改規則。

## 交付邊界

- 本票處理 research attempt lifecycle、recovery、summary／Live attempt projection、per-attempt schema 與 Provider executable discovery。
- 不處理 process-group 內部實作、Early Opening、Codex 真實 proof canary、同頁 launch 或倒數 UI。

## 驗收條件

1. 七 primary 同時開始且最多各有一個不同 Provider backup；總 worker 容量測試可同時容納 14 attempts，不因 pool 飢餓讓 backup 排到 research window 之外。
2. missing CLI 測試立即產生 `provider_cli_missing`，啟動正確 backup；backup 成功可 adopted，雙方失敗則該席 exhausted，兩者都不使其他席中止。
3. primary timeout 後、接收窗仍開啟時再送入有效 primary result，必須只留 diagnostic；原 `failed/provider_timeout` 與 adopted_attempt 不變。
4. cancel、superseded、failure、backup first-valid-wins、seal 後 late result 均有 deterministic 測試，證明每 attempt 僅一個 terminal outcome。
5. summary、events 與 Live projection 的 provider／model／attempt lineage 一致；舊 fixture 無新欄位仍可讀並顯示「未記錄」。
6. 三個 Provider 的正式 research callsite 都收到獨立 deep-copied schema；並行 14 invocation 不互相污染 enum。
7. 對任一 Provider 移除 per-attempt schema、錯綁 run／seat／attempt，或移除 finished-attempt guard，定向測試必須失敗。
8. offline suite 不啟動真實 Codex、Claude 或 Antigravity；相關 scheduler、gateway、real-provider 與 Live projection 測試全綠。
