# 賽前一次性系統預檢（pre-game only）

**本清單屬賽前作業。比賽冷啟動時一步都不執行**——比賽日 Core 只跑
`launch` 單一命令，唯一前置是本清單成功後產出的
`<DATA_ROOT>/preflight/latest-ready.json` 憑證。

在賽前（非比賽計時內）從 Code Root 依序完成：

1. **登入檢查** — Codex、Claude CLI 與 Antigravity CLI 均已登入，且訂閱權限
   有效。Claude 三席能回傳結構化測試結果；`agy models` 顯示
   `gemini-3.1-pro-high`，且能完成非互動測試呼叫。
2. **模型檢查** — Core 與三個 Codex 席的實際模型均為 `gpt-5.6-sol`
   （**GPT-5.6 Sol**）。Core 無法讀取自身 runtime 模型時，可接受操作者明確
   確認 UI 顯示目標模型，並記錄
   `model_confirmation_source="operator_ui"`；不得稱之為 runtime attestation。
3. **Roster 檢查** — `config/agent_roster.json` 的七席 provider／模型映射與
   凍結 roster 一致：Codex 三席 `spot-technical`／`derivatives`／`news`、
   Claude 三席 `onchain`／`official-events`／`social-macro`、Antigravity 一席
   `counter-evidence`。不得替換模型、席位或席數。
4. **環境檢查** — Data Root 與 Code Root 是不同目錄；Data Root 可建立執行
   目錄並寫入檔案；Windows／WSL 路徑轉換與命令參數傳遞正常。
5. **Codex bridge（legacy 儀式，賽前僅此一次）** — real 預檢仍要求已驗證的
   Codex handoff，否則 `codex_runtime_receipts` 失敗且不會消耗
   Claude／Antigravity smoke。在 fresh Codex Task（從 Code Root 開啟）內：

   1. `python3 -m hoya_market_agents prepare-launch --question '<任一核准題目>' --data-root DATA_ROOT`
      取得 `CODEX_RUN_ID` 與 `CODEX_CHALLENGE`。
   2. 開 3 個 persistent Codex threads（`gpt-5.6-sol`），依
      `codex-bridge-contract.md` 寫入 handoff artifact。run 目錄自 ADR 0005
      起按台北日期分層且以題目命名，**不要自己拼路徑**——照 contract 用
      `resolve_run_dir` 問出來。
   3. `verify-preflight --provider codex --run-id ... --challenge ...` 須 READY。

   handoff 建立後 **300 秒內**執行第 6 步；過期或已綁定 → 重開 fresh Task 重做。

6. **系統預檢** — 執行唯一的聚合就緒閘門：

   ```bash
   # WSL
   python3 -m hoya_market_agents preflight --provider system --seats 7 --mode real \
     --codex-run-id '<CODEX_RUN_ID>' --codex-challenge '<CODEX_CHALLENGE>' \
     --data-root DATA_ROOT
   ```

   `--mode fixture` 只驗 schema 與失敗處理，永遠 `NOT_READY`，不能授權
   真實比賽；只有 `--mode real` 消耗真實 provider 能力證據並產出憑證。
   成功判準是 **exit 0**（provider 能力全過；`search` 等三項 run-scoped 證據
   由正式 run 產生，不阻擋憑證）。任一主要席位失敗即 `NOT_READY`，
   不得用備援模型偽裝通過。

成功後產出 `<DATA_ROOT>/preflight/latest-ready.json`
（`status:"READY"`、`provider_capabilities_ready:true`）。比賽日直接貼題目，
Core 依 SKILL.md 快速路徑執行 `launch`，不再重跑本清單任何一步。
