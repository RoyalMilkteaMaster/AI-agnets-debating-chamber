# 全站 Google 風極簡毛玻璃換裝

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：Ticket 02

## 目標

把 webapp 全部頁面（即時辯論、歷史與命中率、設定、run 詳情、錯誤頁、關閉頁）換成 Google 風白底極簡版面：大量留白、半透明毛玻璃面、紅藍綠黃四色點綴，色值一律取自 design_tokens；保護區（聊天室、燈位、三種票數）只換外衣——內容、位置、語意色與行為不動。

## 對應原始需求

- R-004：全站重新設計：Google 風白底、紅藍綠黃彩色點綴、極簡留白、半透明毛玻璃；含離線兩頁換新裝（新 run 起生效）；深色模式退場只留白底一套；微軟正黑體優先；語意色保留語意只校準色階。

## 使用者價值

對應 User Story 4：使用者要的是整站一致的白底極簡新視覺，而不是深淺兩套拼裝的舊介面；同時最在意的聊天室、燈位與票數必須維持原本的判讀方式，不能因為換皮而改變含意。

## 範圍

包含：

- `webapp/pages.py` 樣式與版面區：全部頁面套白底極簡＋留白＋毛玻璃＋四色點綴。
- 涵蓋頁面：即時辯論、歷史與命中率、設定、run 詳情、錯誤頁、關閉頁。
- 保護區（聊天室、燈位、三種票數）只換視覺外衣。
- 對應測試（版面與樣式斷言、保護區行為回歸）。

不包含：

- 離線兩頁 renderer 換裝（Ticket 06）。
- 設定頁的中文標籤與白話說明（Ticket 04）。
- 席位卡 blurb（Ticket 05）。
- 行動版佈局（Spec 明列不在範圍內）。

## 已確認實作決策

- 色值一律取自 design_tokens，不在 `pages.py` 內另寫色碼。
- 保護區只換外衣：內容、位置、語意色與行為一律不動；語意色保留語意，只校準色階。
- 毛玻璃使用原生 CSS `backdrop-filter`，零外部資源。
- 成品樣式表不得含 `@media (prefers-color-scheme: dark)`；作業系統深色時仍是同一套白底。
- 不耦合 CSS 類名順序與色碼字面值：測試斷言對比結果與關鍵元素，不斷言色碼。

## 驗收條件

- 全站每個 webapp 頁面呈現白底新設計；作業系統設深色時仍為同一套白底；成品樣式表無 `@media (prefers-color-scheme: dark)`。
- 對比實測達 WCAG AA（含毛玻璃合成色），毛玻璃面以合成後實色計算並通過。
- 保護區（聊天室、燈位、三種票數）行為與改版前一致，既有保護區測試全綠。
- 上列六類頁面（即時辯論、歷史與命中率、設定、run 詳情、錯誤頁、關閉頁）皆已套用新版面，無殘留舊樣式頁面。
- 既有測試全綠。

## 測試與證據

- 測試接縫：沿用既有保護區行為測試；design_tokens → 對比實測（含毛玻璃合成面）；渲染後 HTML 的關鍵元素斷言。
- 迭代期快速檢查：WSL 執行針對本票模組的單測 `python3 -m unittest tests.test_webapp -v`（秒級；若本票另建獨立測試模組，改跑該模組）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、對比實測數字（含毛玻璃合成色）、渲染後 HTML 存檔與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：適用
- 判定依據：R-004 是純使用者介面需求，必須實際檢視每一類頁面的版面與保護區呈現。
- 操作環境與實際網址：執行階段填寫
- 使用的原生瀏覽器工具：執行階段填寫
- 操作步驟與預期結果：
  1. 逐一開啟即時辯論、歷史與命中率、設定、run 詳情、錯誤頁、關閉頁：皆為白底極簡版面，含留白、毛玻璃面與四色點綴。
  2. 將作業系統切為深色後重新整理：頁面仍為同一套白底。
  3. 檢視即時辯論頁保護區：聊天室、燈位、三種票數的內容、位置與語意色與改版前一致。
  4. 檢查頁面無外部資源請求（字型與毛玻璃皆為原生）。
- 操作結果：執行階段填寫
- 操作證據：執行階段填寫
- 環境註記：環境無瀏覽器時，依 Spec 測試決策以渲染後 HTML 存檔＋關鍵元素斷言為操作證據。

## 依賴

- Depends on：Ticket 02
- Blocks：Ticket 04

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：`hoya_market_agents/webapp/pages.py`（樣式與版面區）、對應測試
- Shared resource locks：`hoya_market_agents/webapp/pages.py`（熱點鏈）
- Can run with：Ticket 06、Ticket 07

## 初始執行配置

- Developer model：`claude-opus-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：全站版面重構範圍大且對保護區回歸敏感，需同時顧及對比可及性與既有行為不變，不屬於局部低風險小修。
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
- 並行批次：批次 3（與 Ticket 07 並行；寫入範圍互斥——本票不碰 `server.py`，T07 不碰 `pages.py`）
- 排程判定：依賴 T02 已完成；`pages.py` 熱點鏈第三棒；測試檔分工：本票用 `tests/test_webapp.py` 等既有模組，T07 另建獨立測試模組避免同檔並行寫入
- 基準版本：branch `main`、HEAD `9b8a451`、基準樹 `8b9c5df12f15617e56b557edfcb3c6872e1e4f11`（T01＋T02＋T06 完成後）
- 開發角色：Developer = Claude（milktea-build）、model `claude-opus-5`（Ticket 相容預設偏好）、effort：平台工具無此欄位採後端預設
- 確切寫入範圍：`hoya_market_agents/webapp/pages.py`（樣式與版面區）、對應測試（`tests/test_webapp.py` 等既有 webapp 測試模組）、`tests/test_design_tokens.py`（僅 T01 守門測試「declared−read」紀錄行——該測試設計上要求 T03 套用 token 時同步更新，派工時已授權）
- 資源鎖：`pages.py` 熱點鏈（本批次獨占）；全套測試執行權（與 T07 以 `.locks/full-suite` 目錄鎖互斥）
- 必跑指令：迭代 `PYTHONPATH=tests python3 -m unittest tests.test_webapp -v`；Ready for Review `python3 -m unittest discover -s tests`（WSL，取鎖後執行）

### Ready for Review（2026-08-10）

- Snapshot：基準樹 `8b9c5df12f15617e56b557edfcb3c6872e1e4f11` → 快照樹 `6a0d4539470fcc3a37f2bf183932cf0f33965c0e`（path-scoped 於本票三檔）
- 變更：`pages.py +134/-69`（`_frosted()` 毛玻璃四面、`decorative_hairline` 重用 T06 共用函式產四色帶、留白與圓角升級、控件膠囊化、保護區零 class 變動、一處 markup 微調 submit 加 class="primary"）、`test_webapp.py +206`（GoogleStyleShellTest 8 條）、`test_design_tokens.py +11/-12`（T01 守門紀錄更新）
- 完整驗收（取 `.locks/full-suite` 鎖）：`Ran 2651, OK (skipped=1), RESULT_EXIT=0`（skip 為套件既有環境條件、不在本票三檔內；本票兩測試模組單跑零 skip）；模組級 621 OK（+8，Red 證據 failures=4）；相鄰 9 模組 430 OK；compileall exit 0
- 對比實測：合成色 `rgba(255,255,255,0.72)` over `#f8f9fa`→`#fdfdfe`；41 組全 PASS，最小餘裕 accent_text on google_red 4.7729；證據 scratchpad `t03/contrast-evidence.txt`
- 渲染樣本：六類頁面存 scratchpad `t03/rendered/`（沿用 tests fixture 產生，非第二套 harness）
- Developer 自報風險：毛玻璃配方三份（renderer×2 屬 T06 已關閉範圍，列後續整併候選）；blur 為視覺 no-op（刻意不做 sticky header）；h1 加大；markup 微調一處交 Reviewer 裁決；`pages` 新增 import `report_renderer`（views/live 已有先例）；行動版不在範圍；全套 1 skip 未定位（鎖被 T07 持有）
- Diff 全文：scratchpad `t03/ticket03.diff`（710 行）

### Review（2026-08-10）

- Reviewer B｜軸 standards｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot `8b9c5df1`→`6a0d4539`
  - 結論：通過，Findings：0。八條檢核全過；Developer 自報六項均判不構成 Finding（毛玻璃三份配方暫可接受不立整併票、pages→report_renderer 同向依賴無循環、blur no-op 非錯誤、primary class 不改行為、守門集合正確縮為 --affirm、skip 定位為 `test_webapp.py:1449` port 1 環境條件非本票）。Windows fallback 重跑本票相關 60 tests OK。報告：scratchpad `t03/reviewer-b-final.md`
- Reviewer A｜軸 spec｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot `8b9c5df1`→`6a0d4539`
  - 結論：不通過。九條檢核 8 過 1 不過（第 7 條）；自報四項（blur no-op、primary class、h1 放大、行動版）均判不越權不列 Finding。
    - F-A03-1【重要｜Owner: Reviewer A｜未關閉】`tests/test_webapp.py:3810` `test_every_frosted_surface_sits_directly_on_the_canvas` 於 3829–3836 要求祖先僅 html/body/main 且不得帶 class，直接耦合 DOM 巢狀結構，違反 Spec 測試決策（spec.md:120）。無害 wrapper 會誤判失敗。最小修正：保留毛玻璃存在與合成色對比斷言，移除祖先標籤／class 精確限制，改驗有效背景／合成行為。
  - 報告：scratchpad `t03/reviewer-a-final.md`

### Findings 修正（2026-08-10，Developer fixed）

- F-A03-1：重現屬實（純 div wrapper 使原測試誤判失敗，重現腳本 `t03/repro_f_a03_1.py`），接受裁定未反證。修正：測試更名 `test_every_frosted_surface_is_composited_over_the_colour_it_was_measured_on`，改由樣式表推導 glass_surfaces／background_fills 兩表，`backdrop_of` 依合成語意找第一個有底色的祖先，斷言有效底色等於 design_tokens 量測假設色；祖先標籤與 class 不再受限；三態驗證（plain 過、無害 wrapper 過、真變底色仍敗）；保護區測試共用 glass_surfaces 消除重複推導。
- 修正後快照樹：`1a88fec6d480e952d85d780f4c7c683bd029e8ce`（僅 test_webapp.py +108/-21；pages.py 未動，產品行為與首輪全套 2651 OK 證據同一份）
- 指定重驗：`tests.test_webapp` 621 OK 零 skip、RESULT_EXIT=0；compileall exit 0。B 首輪零 Finding 且本輪無產品變更，Coordinator 判定無需 B 重審。

### 定向複驗與完成（2026-08-10）

- Reviewer A：F-A03-1 `closed`（獨立以 Windows fallback 重放三態：PLAIN_EXIT=0、WRAPPER_EXIT=0、PAINTED_EXIT=1，painted 精準失敗於合成底色錯誤）。spec 軸：通過、未關閉 Findings 0。報告：scratchpad `t03/reviewer-a-reverify-final.md`
- 最終 Snapshot：`1a88fec6d480e952d85d780f4c7c683bd029e8ce`；共識：Developer 與兩位 Reviewer 均通過。

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
