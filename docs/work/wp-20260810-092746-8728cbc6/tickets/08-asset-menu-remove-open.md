# 發問選單移除開放題

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：Ticket 05

## 目標

把發問選單的資產類別選項改由 `config/market_scopes.json` 的三市場（台股／美股／幣）產生，選單不再出現「開放題」；launcher 介面、後端 open 路徑與套組選擇規則全部保留；歷史開放題 run 照舊可回看。

## 對應原始需求

- R-006：開放題下架：發問選單只留台股／美股／幣；開放套留舊值當內部填充（fail-closed 檢查仍過）；後端 open 能力不刪；歷史開放題 run 照舊可回看。

## 使用者價值

對應 User Story 7：使用者的發問選單只剩實際會用的三類，不再被用不到的開放題干擾；同時過去的開放題 run 仍然打得開。

## 範圍

包含：

- `pages.py` 發問表單區：資產類別選項由 `config/market_scopes.json` 的三市場產生。
- 選單中移除「開放題」選項。
- 對應測試（選單只列三市場、套組選擇規則不變、歷史開放題 run 可開啟）。

不包含：

- launcher 介面調整（保留）。
- 後端 `open` 資產類別與 open 路徑的移除（保留）。
- 套組選擇規則（`open` 或跨類→open）的修改（保留）。
- 開放套 roster 內容變更（Ticket 05 已補 blurb、名稱留舊）。

## 已確認實作決策

- 資產類別選項的來源是 `config/market_scopes.json` 的三市場，不在 `pages.py` 內硬寫選項清單。
- `open` 資產類別與開放套在後端與 roster 保留，fail-closed 檢查仍須齊全；僅不可從 UI 發問。
- 套組選擇規則（`open` 或跨類→open）完全不動。
- 歷史開放題 run 照舊可回看，顯示沿用現行套組規則。

## 驗收條件

- 發問選單無「開放題」；歷史開放題 run 仍可開啟回看。
- 選單選項與 `config/market_scopes.json` 的三市場一致（台股／美股／幣），無硬寫清單。
- 套組選擇規則測試不變且全綠。
- 後端 `open` 路徑與 roster 開放套仍存在，賽前 fail-closed 預檢通過。
- 既有測試全綠。

## 測試與證據

- 測試接縫：沿用發問選單既有測試（斷言選項只列三市場）；套組選擇規則測試不變；歷史開放題 run 開啟路徑。
- 迭代期快速檢查：WSL 執行針對本票模組的單測 `python3 -m unittest tests.test_webapp_asset_picker -v`（秒級；若本票另建獨立測試模組，改跑該模組）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、發問頁渲染後 HTML 存檔與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：適用
- 判定依據：R-006 直接規範發問選單的可見選項，必須實際開頁確認。
- 操作環境與實際網址：本票階段無瀏覽器互動工具，依環境註記採渲染後 HTML＋關鍵元素斷言；整包瀏覽器走查列入工作包總驗收（T09 後）
- 使用的原生瀏覽器工具：無（渲染管道產出樣本）
- 操作步驟與預期結果：
  1. 開啟發問頁：資產類別選單只有台股、美股、幣三個選項，無「開放題」。
  2. 逐一選取三個選項發問：流程正常、套組選擇結果與改版前一致。
  3. 開啟一個歷史開放題 run 的詳情頁與報告：仍可正常回看。
- 操作結果：渲染樣本選單僅「請選擇資產類別／加密資產／台股／美股」、無 open／asset_open；三市場逐一送出皆 303 且 launcher 收到對應市場與標的；`/history?asset_class=open`、`/run/<id>`、`/run/<id>/report.html` 皆 200 且顯示開放題內容
- 操作證據：session scratchpad `t08\rendered\`、`open-run-view.log`、`full-suite.log`
- 環境註記：環境無瀏覽器時，依 Spec 測試決策以渲染後 HTML 存檔＋關鍵元素斷言為操作證據。

## 依賴

- Depends on：Ticket 05
- Blocks：Ticket 09

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：`hoya_market_agents/webapp/pages.py`（發問表單區）、對應測試
- Shared resource locks：`hoya_market_agents/webapp/pages.py`（熱點鏈鏈尾）
- Can run with：Ticket 06、Ticket 07

## 初始執行配置

- Developer model：`claude-sonnet-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：工作明確、局部、低風險——只把選單選項來源換成既有 `market_scopes.json` 三市場，已有相鄰選單實作可沿用且可秒級驗證，不涉及 Schema、Migration、權限、安全、資料風險或公開介面變更。
- 升級路徑：Claude 偏好為 `claude-opus-5`／`xhigh`；實際使用其他後端時由 Implement 採用該後端已驗證可用的升級設定；`max` 需使用者明確核准
- 執行時覆寫：最新使用者角色設定優先；偏好後端不可用時回退到唯一可用平台並留下紀錄
- Research 證據：無

## Agent 分工

- Developer：負責實作、驗證、修正或以證據反駁 Findings
- Reviewer A：`both` 時只執行 Spec Review；`a_only` 時執行 Spec 與 Standards
- Reviewer B：`both` 時只執行 Standards Review；`b_only` 時執行 Spec 與 Standards
- Reviewer 啟用規則：由執行 Task 最新 `settings_update: reviewers` 決定；預設 `both`，Ticket 不自行固定或搜尋設定
- Reviewer 標準：每位啟用 Reviewer 都載入 `$milktea-skills-code-review`，只執行 Coordinator 指定的 `review_axis`
- CLI 與模型：Developer 的上述配置只是相容預設偏好；實際配置依最新使用者角色設定與後端可用性決定，Reviewer 仍獨立決定

## 完成規則

- Developer 與各 Finding 的原 Reviewer 已處理所有可重現且有證據的問題。
- 沒有未解決的阻擋或重要正確性、可執行性、可讀性、架構或衍生風險。
- Developer 與各 Finding Owner 對關閉或撤回事由達成共識。

## 執行與 Review 紀錄

- Developer 結論：Claude（milktea-build／opus；票面 Sonnet 偏好因平台無法保證 ≥high 推理下限，依既定規則改 opus 並記錄）TDD 交付。快照 `f1f6c9535e174b162d329264acad7415ec589132`（對基準樹 `39fd39b2bdfee684e44336d2682227ba1233f4c5` diff 451 行、僅 pages.py 與 tests/test_webapp_asset_picker.py 兩檔）。新增 `ask_bar_markets()` 單一來源，發問表單三處改源；測試 75→87。全套 `Ran 2729 / OK (skipped=1) / RESULT_EXIT=0`。證據：session scratchpad `t08\`。
- Reviewer 模式：both，皆 Codex CLI 0.146.0 native（A session `019feb4a-d3c6-7e41-8adc-c38f92e8a9ac`、B session `019feb4a-dcf6-72f2-97e2-955e5418c220`）。
- Reviewer A 結論（Spec 軸）：零 Finding、通過——R-006 各項成立，open 保留路徑與歷史回看入口核實。
- Reviewer B 結論（Standards 軸）：零 Finding、通過、🟢——三處統一來源、測試鑑別力足、無新注入或 I/O 風險；`_NON_MARKET_TARGET_HINTS` 保留無可證危害。
- 未關閉阻擋或重要 Findings：無。
- Ticket 最終驗收：完成。最終快照樹 `f1f6c9535e174b162d329264acad7415ec589132`；首輪零 Finding，三方共識成立。
- Coordinator 裁決（本票，列入結案報告複核）：
  1. `_asset_picker_rules()`（樣式表段落、僅服務發問表單）一併改為市場來源——授權；不改會殘留選取已不存在 option 的死規則。
  2. `target_format_hint()` open 分支與 `_NON_MARKET_TARGET_HINTS` 保留——授權；票面明示保留後端 open 能力，是否清理列結案報告備考交使用者決定。

## 阻擋與裁決紀錄

只有真正需要方向裁決時才追加下列欄位；一般 Bug 修正、測試失敗、Review Finding 或同一方案內的迭代不得寫成使用者阻擋：

- 原始需求：
- 目前理解：
- 實際卡住的原因：
- 已嘗試方案與證據：
- 為什麼不能繼續盲修：
- 簡單可行方案：
- Agent 建議：
- 需要使用者決定：
