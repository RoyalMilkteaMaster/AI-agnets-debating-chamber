# 離線報告換新裝

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：Ticket 01

## 目標

把 `report_renderer.py` 與 `report_audit_renderer.py` 的 CSS 改為由 design_tokens 產生，刪除 renderer 內寫死的 palette 與燈色 hex；DOM 章節結構維持不變；新 run 起生效、舊 run 檔案與 PDF 不回溯；`run_verifier.py` 不動，既有相對連結檢查不得破壞。

## 對應原始需求

- R-004：全站重新設計：Google 風白底、紅藍綠黃彩色點綴、極簡留白、半透明毛玻璃；含離線兩頁換新裝（新 run 起生效）；深色模式退場只留白底一套；微軟正黑體優先；語意色保留語意只校準色階。

## 使用者價值

對應 User Story 4：分享出去的離線報告與完整辯論頁也要是同一套白底新視覺，不能站內換了新裝、離線頁還停在舊 palette。

## 範圍

包含：

- `report_renderer.py`：`_MARKET_CSS` 等處寫死的 `:root` 色值與燈色 hex 刪除，改由 design_tokens 產生 CSS。
- `report_audit_renderer.py`：同上（`_CSS` 等處）。
- 對應測試（渲染樣本的樣式來源與無深色 media query 斷言）。

不包含：

- DOM 章節結構調整（維持不變）。
- 舊 run 檔案與 PDF 回溯重製（Spec 明列不在範圍內）。
- `run_verifier.py` 任何修改（Spec 明列不在範圍內）。
- 伺服器側五導覽注入（Ticket 07）。

## 已確認實作決策

- 兩個 renderer 的 CSS 一律取自 design_tokens；不維護第二份色值。
- DOM 章節結構維持，避免破壞既有相對連結檢查與報告契約。
- 新 run 起生效；舊 run 檔案與 PDF 不回溯。
- `run_verifier.py` 不動，既有相對連結檢查必須維持通過。
- 不耦合 CSS 類名順序與色碼字面值：斷言對比結果與結構，不斷言色碼。

## 驗收條件

- 新 run 產出的離線兩頁（`report.html`／`debate.html`）呈現白底新設計；作業系統設深色時仍為同一套白底；產出的樣式表無 `@media (prefers-color-scheme: dark)`；對比實測達 WCAG AA（含毛玻璃合成色）。
- 兩個 renderer 原始碼中不再有寫死的 palette 色值與燈色 hex。
- 離線頁 DOM 章節結構與改版前一致。
- `run_verifier.py` 未被修改，且其既有檢查（含相對連結檢查）全部通過。
- 渲染樣本已存檔作為證據。
- 既有測試全綠。

## 測試與證據

- 測試接縫：兩個 renderer 的渲染樣本（斷言色值來源為 design_tokens、無深色 media query、章節結構不變）；沿用 `run_verifier` 既有檢查。
- 迭代期快速檢查：WSL 執行針對本票模組的單測 `python3 -m unittest tests.test_report_renderer -v` 與 `python3 -m unittest tests.test_report_audit_renderer -v`（秒級）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、`run_verifier` 檢查輸出、渲染樣本存檔與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：適用
- 判定依據：R-004 涵蓋離線兩頁的視覺，必須實際開啟新產出的離線頁核對新裝。
- 操作環境與實際網址：執行階段填寫
- 使用的原生瀏覽器工具：執行階段填寫
- 操作步驟與預期結果：
  1. 產生一個新 run，直接開啟其 `report.html`：呈現白底新設計，含留白、毛玻璃面與四色點綴。
  2. 直接開啟同一 run 的 `debate.html`：同樣為新裝，且維持離線自足兩分頁導覽。
  3. 將作業系統切為深色後重新開啟：仍為同一套白底。
  4. 開啟舊 run 的離線頁：確認未被回溯改動。
- 操作結果：執行階段填寫
- 操作證據：執行階段填寫
- 環境註記：環境無瀏覽器時，依 Spec 測試決策以渲染後 HTML 存檔＋關鍵元素斷言為操作證據。

## 依賴

- Depends on：Ticket 01
- Blocks：Ticket 09

## 並行與所有權

- Dispatch：parallel-safe
- Exclusive write scope：`hoya_market_agents/report_renderer.py`、`hoya_market_agents/report_audit_renderer.py`、對應測試
- Shared resource locks：全套測試執行權（Ready for Review 執行 `python3 -m unittest discover -s tests` 時與其他 Ticket 互斥）
- Can run with：Ticket 02、Ticket 03、Ticket 04、Ticket 05、Ticket 07

## 初始執行配置

- Developer model：`claude-opus-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：跨兩個 renderer 的換裝且對報告契約與相對連結檢查回歸敏感，需同時保住 DOM 結構與驗證器行為，不屬於局部低風險小修。
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

- Developer 結論：通過
- Reviewer 模式：both
- Reviewer A 結論：通過
- Reviewer B 結論：通過
- 未關閉阻擋或重要 Findings：0
- Ticket 最終驗收：通過

### 開始執行（2026-08-10）

- Execution environment：Windows 宿主＋WSL `Ubuntu-24.04`（python3 3.12.3）；前綴 `wsl.exe -d Ubuntu-24.04 --`；source: auto_current
- 並行批次：批次 2（與 Ticket 02 並行；寫入範圍互斥）
- 排程判定：依賴 T01 已完成；Dispatch parallel-safe，寫入僅兩個 renderer 檔與對應測試，與 T02（pages.py/views.py）無重疊
- 基準版本：branch `main`、HEAD `9b8a451`、基準樹 `3caf0dc2f6f08ff355d871d0b7741f4bf9af0bd9`（T01 最終快照）
- 開發角色：Developer = Claude（milktea-build）、model `claude-opus-5`（Ticket 相容預設偏好）、effort：平台工具無此欄位採後端預設
- 確切寫入範圍：`hoya_market_agents/report_renderer.py`、`hoya_market_agents/report_audit_renderer.py`、`tests/test_report_renderer.py`、`tests/test_report_audit_renderer.py`
- 資源鎖：全套測試執行權（與 T02 以 `.locks/full-suite` 目錄鎖互斥）
- 必跑指令：迭代 `python3 -m unittest tests.test_report_renderer -v`／`tests.test_report_audit_renderer -v`；Ready for Review `python3 -m unittest discover -s tests`（WSL，取鎖後執行）

### Ready for Review（2026-08-10）

- Snapshot：基準樹 `3caf0dc2f6f08ff355d871d0b7741f4bf9af0bd9` → 快照樹 `76a8977e726b73932fc92794cbaab049458595e1`（path-scoped 於本票四檔；工作樹另含 T02 進行中變更，不在本票快照語意內）
- 變更：`report_renderer.py +227/-66`、`report_audit_renderer.py +131/-66`、`test_report_renderer.py +219/-15`、`test_report_audit_renderer.py +155/-1`；共用接縫 `report_renderer.stylesheet(rules)` 由 `PALETTE`＋`SCALE` 展開 `:root`，audit renderer 直接 import；`decorative_hairline()`／`_tone_rules()` 由 `DECOR_TOKENS` 生成；燈色 hex 刪除（顏色回到 `CONFIDENCE_ICONS` emoji）；DOM 抽掉 `<style>` 後逐位元不變（4 個 .domdiff 均 0 bytes）
- 完整驗收（取 `.locks/full-suite` 鎖）：`Ran 2621, OK (skipped=1), RESULT_EXIT=0`；模組級 115 OK（新增 22，Red 證據 failures=68）；消費端 497 OK；`run_verifier.py` 與基準 IDENTICAL 且 `test_verify_run`＋bundle 驗證 113 OK；渲染樣本 baseline/after 各 4 檔存 scratchpad `t06/samples/`
- 對比實測：text on glass 15.84、muted 5.95、link/accent 5.28、border 3.62、accent_text on google_red 4.77、text on google_yellow 9.43——全達標
- Developer 自報風險：信心燈改由 emoji 承載色彩（既有測試語意翻轉一處，請 Reviewer A 覆核）；改版前封存的舊 run 若跑 `verify_run` 重渲染比對會不一致（Spec「舊 run 不回溯」的必然後果，交 Coordinator 記錄）；`backdrop-filter` 舊瀏覽器 fallback 安全；七席四色輪替；辯論頁版心 76rem→`var(--shell)` 86rem；CRLF 已正規化回 LF
- Diff 全文：scratchpad `t06/ticket06.diff`（1016 行）

### Review（2026-08-10）

- Reviewer B｜軸 standards｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot `3caf0dc2`→`76a8977e`
  - 結論：通過，Findings：0。八條檢核全過；Developer 自報六項均判不構成 Finding（import 方向合理、燈號 emoji＋文字＋ARIA 保留、四色輪替非唯一訊號、86rem 為共用 shell token、四檔純 LF、blur fallback 安全）。Windows fallback 重跑 115 tests OK。報告：scratchpad `t06/reviewer-b-final.md`
- Reviewer A｜軸 spec｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot `3caf0dc2`→`76a8977e`
  - 結論：不通過。檢核 8 條中 7 過、第 4 條不過：
    - F-A06-1【阻擋｜Owner: Reviewer A｜未關閉】新 renderer 使改版前封存 run 無法通過現行 `verify_run`（`run_verifier.py:1167` 以目前 renderer 重繪舊 artifact 逐位元比對）。實驗證據：baseline 建 competition run → verify VERIFIED exit 0；同一 run 以 review revision 驗證 → exit 1 `RunVerificationError: report.html 不是由正式 report.json 產生`。Reviewer 認定違反驗收「既有檢查不得破壞」。建議方向：跨版本回歸測試＋presentation-version 相容渲染或等效舊 run 驗證路徑；不得改寫舊 artifact。
  - 報告：scratchpad `t06/reviewer-a-final.md`

### F-A06-1 Developer 反證（2026-08-10）

- 立場：反證（程式一行未動，送審 diff 不變）。
- 查證 1（呼叫面）：`verify_run` 全 repo 僅 `cli.py:896`（人工 verify-run）、`cli.py:911`（drill 當場驗）、`cli.py:865`（preflight 驗同 session 演練 run）；webapp 零命中；無跨版本回歸測試。
- 查證 2（決定性實驗，`cross-version-experiment2.sh`，唯讀 git archive 展開）：wp-20260809 基準樹 `5c74fd8b` 封存 bundle → SEALER exit 0；BASE（基準樹 `3caf0dc`，不含 T06 修改）exit 1 `report.html 不是由正式 report.json 產生`；MINE exit 1 同句。另 HEAD `9b8a451` 封存的 bundle 在 BASE/MINE 連 run 都找不到（目錄佈局已在更早工作包改變）。
- 查證 3（根因隔離）：舊 bundle 以 BASE 重繪僅差 2 行提供者署名（wp-20260809 provider swap 所致），與 CSS 無關；renderer 讀活權威（seats/roster）為結構性原因。
- 結論主張：「A build 封存、B build 驗證」在本票前已不成立，且非被維護的公開行為；已交原 Owner（Reviewer A）定向裁決 withdrawn 或列缺口。

### 定向裁決與完成（2026-08-10）

- Reviewer A 裁決：F-A06-1 `withdrawn`。依據：獨立 Git 核對 SEALER→BASE 的 `report_renderer.py`／`run_verifier.py` diff exit 0（僅 roster/provider 對調）；BASE 不含 T06 修改即以相同錯誤拒絕舊 bundle；`verify_run` 呼叫面限 CLI/drill/preflight、webapp 零命中；原實驗只證明「跨 build 逐位元重繪不穩定」，未證明 T06 破壞受維護的公開行為。spec 軸檢核第 4 條改判通過；spec 軸最終結論：通過、未關閉 Findings 無。報告：scratchpad `t06/reviewer-a-reverify-final.md`
- 工作包已知風險（帶入結案報告，交使用者決定是否另開票）：封存 run 的跨版本 `verify_run` 驗證在多個工作包前即已不成立（run 目錄佈局變更、roster/provider 變更、本次視覺變更皆會使重繪偏離封存位元組）；如需「跨版本可驗」需 presentation-version 相容渲染或等效驗證路徑，屬規格層級決策。
- 最終 Snapshot：`76a8977e726b73932fc92794cbaab049458595e1`；共識：Developer 與兩位 Reviewer 均通過。

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
