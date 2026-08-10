# 設計 token 權威與白底單套 palette

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：無

## 目標

新建 `hoya_market_agents/design_tokens.py` 作為全站設計 token 的唯一權威：白底單套 palette（深色刪除）、Google 四色裝飾 token（與語意色分開命名）、毛玻璃 token（含合成後實色）、字體堆疊微軟正黑體優先；`webapp/pages.py` 改為從此模組取值；成品樣式表不再輸出 `@media (prefers-color-scheme: dark)`；`ContrastTest` 直接對 token 計算 WCAG AA（文字 4.5:1、線條 3.0:1，毛玻璃面用合成色）。

## 對應原始需求

- R-004：全站重新設計：Google 風白底、紅藍綠黃彩色點綴、極簡留白、半透明毛玻璃；含離線兩頁換新裝（新 run 起生效）；深色模式退場只留白底一套；微軟正黑體優先；語意色保留語意只校準色階。

## 使用者價值

對應 User Story 4：使用者要的是白底極簡、毛玻璃、彩色點綴的一致視覺，告別深淺雙 palette 的拼裝感。本票先立起唯一色值權威，讓後續每個頁面與離線報告都取同一份值，不會再出現第二套色碼。

## 範圍

包含：

- 新增 `hoya_market_agents/design_tokens.py`：白底單套 palette、Google 四色裝飾 token、毛玻璃 token（半透明值＋合成後實色）、字體堆疊（微軟正黑體優先、純系統字型）。
- `webapp/pages.py` 的 token 區改為從 design_tokens 取值，刪除深色 palette 與 `@media (prefers-color-scheme: dark)` 區塊。
- `ContrastTest` 改為直接對 design_tokens 計算 WCAG AA，毛玻璃面用合成色計算。
- 更新既有的 `tests/test_design_tokens.py`：改以 design_tokens 為受測對象做資料驅動的 token 與對比斷言，移除雙 palette 假設。

不包含：

- `report_renderer.py`／`report_audit_renderer.py` 換裝（Ticket 06）。
- webapp 版面與元件重排（Ticket 03）。
- header 導覽結構（Ticket 02）。

## 已確認實作決策

- design_tokens 是唯一權威，不維護第二份色值；`pages.py` 與兩個 renderer 一律取自此模組。
- Google 四色裝飾 token 與語意色 affirm／oppose／abstain 及燈號分開命名，不得混用。
- 毛玻璃以原生 CSS `backdrop-filter` 實作、零外部資源；半透明面同時提供「合成後實色」供對比測試使用。
- 語意色保留語意，只校準色階；不改變語意色的意義對應。
- 深色 palette 與 `@media (prefers-color-scheme: dark)` 區塊刪除，只留白底一套。

## 驗收條件

- 成品樣式表（webapp 產出的樣式表內容）不含 `@media (prefers-color-scheme: dark)` 字串。
- 作業系統設為深色時，頁面仍呈現同一套白底配色。
- 對 design_tokens 的對比實測全數通過 WCAG AA：文字 4.5:1、線條 3.0:1；毛玻璃面以合成後實色計算並通過。
- 語意色 token（affirm／oppose／abstain 與燈號）仍存在，且與 Google 四色裝飾 token 名稱分開、程式中無混用。
- 字體堆疊以微軟正黑體優先且維持純系統字型，無外部字型資源請求。
- `pages.py` 內無寫死色值字面值；色值一律由 design_tokens 提供。
- 既有測試全綠。

## 測試與證據

- 測試接縫：design_tokens → `ContrastTest` 資料驅動（對 token 逐項計算對比，不斷言色碼字面值）。
- 迭代期快速檢查：WSL 執行針對本票模組的單測 `python3 -m unittest tests.test_design_tokens -v`（秒級）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、對比實測數字表與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：不適用
- 判定依據：本票只產出資料層設計 token 與樣式表取值來源，不改變任何頁面版面；畫面驗證由 Ticket 03（webapp 換裝）與 Ticket 09（端到端驗收）承接。
- 操作環境與實際網址：不適用
- 使用的原生瀏覽器工具：不適用
- 操作步驟與預期結果：不適用
- 操作結果：不適用
- 操作證據：不適用（判定依據：本票僅資料層 token，畫面驗證由 Ticket 03／09 承接）

## 依賴

- Depends on：無
- Blocks：Ticket 02、Ticket 03、Ticket 06

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：`hoya_market_agents/design_tokens.py`（新增）、`hoya_market_agents/webapp/pages.py`（token 區）、`tests/test_design_tokens.py`、`tests/test_webapp.py`（僅雙 palette 假設斷言：`ContrastTest`、`SingleSiteStylesheetTest`、`LiveContrastTest`、`SettingsColourTest` 四個 class 的 8 處；2026-08-10 Coordinator 依 Spec R-004 補授權）
- Shared resource locks：`hoya_market_agents/webapp/pages.py`（熱點鏈鏈頭）
- Can run with：無

## 初始執行配置

- Developer model：`claude-opus-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：新建模組並成為跨模組唯一權威，牽動 `pages.py` 與後續兩個 renderer 的公開取值介面，且需設計對比測試資料結構；不屬於局部低風險小修。
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

- Execution environment：Windows 宿主＋WSL `Ubuntu-24.04`（python3 3.12.3）；專案命令前綴 `wsl.exe -d Ubuntu-24.04 --`；source: auto_current
- 並行批次：批次 1（僅本票；T02/T06 待本票完成後進入 Ready Queue）
- 排程判定：無依賴；`pages.py` 熱點鏈鏈頭，serialized
- 基準版本：branch `main`、HEAD `9b8a451`、基準樹 `bd6fff456d31bf24bdae07e7a6563e691663fc81`（含 wp-20260809 已驗收之未提交成果）
- 開發角色：Developer = Claude（平台 Agent，milktea-build）、model `claude-opus-5`（Ticket 相容預設偏好）、model_reasoning_effort：平台派發工具無此欄位，採後端預設
- 確切寫入範圍：`hoya_market_agents/design_tokens.py`（新增）、`hoya_market_agents/webapp/pages.py`（token 區）、`tests/test_design_tokens.py`
- 資源鎖：`pages.py` 熱點鏈、全套測試執行權（本批次無競爭）
- 必跑指令：迭代 `python3 -m unittest tests.test_design_tokens -v`；Ready for Review `python3 -m unittest discover -s tests`（WSL）

### 寫入範圍補授權（2026-08-10，Coordinator）

- 事由：Developer 唯讀查核發現 `tests/test_webapp.py` 有 8 處斷言寫死雙 palette 假設（`test_webapp.py:3146-3149` 斷言樣式表必含 `prefers-color-scheme`；`test_webapp.py:1593-1594` 斷言 `pages.THEMES` 恰為兩套；另 6 處同源讀法），與 Spec 驗收條件 4「成品樣式表無 `@media (prefers-color-scheme: dark)`」不可能同時成立。基準全套測試實測全綠（`Ran 2556 tests, OK (skipped=1), RESULT_EXIT=0`），證明衝突源自拆票寫入範圍疏漏，非 Developer 實作選擇。
- 裁決（Coordinator，非使用者阻擋）：此為所有權缺口，Spec R-004 已核准深色退場，更新編碼舊行為的測試是「既有測試全綠」的必要條件，不改變核准公開行為。採 Developer 選項 B：`pages.py` 刪除 `THEMES` 改 `PALETTE = design_tokens.PALETTE`，`tests/test_webapp.py` 四個 class（`ContrastTest`、`SingleSiteStylesheetTest`、`LiveContrastTest`、`SettingsColourTest`）8 處改讀單一 `PALETTE`；不採選項 A（單元素 `THEMES` 殘骸違反 Spec「不維護第二份色值」，會由 T03/T06 繼承）。
- Exclusive write scope 已同步原地更新。
- 追加（同日第二次）：`tests/test_webapp.py:3202`（`SingleSiteStylesheetTest.test_the_font_stacks_are_the_operating_systems_own`）斷言 `--font-sans:system-ui,` 置首，與 R-004「微軟正黑體優先」字面相反，為全站唯一字體堆疊斷言（Developer 已 grep 確認）。同類事由（既有斷言編碼已核准需求淘汰的舊行為），Coordinator 補授權該單行修改為斷言 `"Microsoft JhengHei"` 置首。
- Review diff 取得方式更正：工作樹多數檔案在 index 為 untracked，`git diff <基準樹> -- <paths>` 會誤報整檔刪除；正式 Snapshot 改由 Coordinator 以臨時 index 建立快照樹，Reviewer 使用 `git diff <基準樹> <快照樹> -- <paths>` 純物件比對。

### Ready for Review（2026-08-10）

- Snapshot：基準樹 `bd6fff456d31bf24bdae07e7a6563e691663fc81` → 快照樹 `d65b498b357090b935d59ec87d6a6fbf77e51437`（Coordinator 臨時 index 建立，已驗含 `design_tokens.py` 且異於基準）
- 變更檔案：`hoya_market_agents/design_tokens.py`（新增 +256）、`hoya_market_agents/webapp/pages.py`（+37/-171，僅 docstring、import、token 區、`stylesheet()`；`THEMES` 刪除改 `PALETTE`、深色 media 區塊刪除）、`tests/test_design_tokens.py`（+310/-99）、`tests/test_webapp.py`（+48/-39，授權 4 class 8 處＋`:3202` 單行）
- 完整驗收：WSL `python3 -m unittest discover -s tests` → `Ran 2577 tests, OK (skipped=1), RESULT_EXIT=0`；skipped=1 為基準既有（test_system_preflight 大小寫別名）；受影響模組 `test_webapp*.py` 842 tests OK
- TDD Red 證據：`ImportError: cannot import name 'design_tokens'`，RESULT_EXIT=1
- 對比實測：41 組全過 AA（文字 4.5:1、線條 3.0:1、毛玻璃合成色計算；Google Blue 由 `#1a73e8`（4.51）下調 `#186ede`（4.86））
- Developer 自報風險：border 3.49:1 較 Google 原味深；`google_*`／glass token 待 T03 套用（有守門測試釘死 declared−read 集合）；`link`＝`accent` 同值沿用舊模式；兩 renderer 寫死 palette 屬 T06
- Diff 全文：scratchpad `t01/ticket01.diff`（1286 行）

### Review（2026-08-10）

- Reviewer A｜軸 spec｜引擎 native｜backend Codex CLI 0.146.0｜model `gpt-5.6-sol`／effort `xhigh`（後端預設，啟動 banner 佐證）｜Snapshot `bd6fff45`→`d65b498b`
  - 結論：通過，Findings：0。逐項驗收確認：單一白底 palette、THEMES 與深色 media 移除、pages.py 無寫死色值、四色與語意色分離、毛玻璃含合成實色、微軟正黑體置首、41 組對比達標（最低線條 border on page 3.49、最低填色 google_red 4.77）。
  - 驗證：四個受審檔工作樹 blob hash 全等於快照樹；`git diff --check` exit 0；沙箱無法呼叫 WSL（`Wsl/Service/E_ACCESSDENIED`，非測試失敗），採信 Developer 全套證據，另以 Windows fallback（記憶體注入 fcntl 介面）重跑 `test_design_tokens` 44 tests OK 與受影響 4 個 test_webapp class 20 tests OK。
  - 報告存檔：scratchpad `t01/reviewer-a-final.md`
- Reviewer B｜軸 standards｜引擎 native｜backend Codex CLI 0.146.0｜model `gpt-5.6-sol`／effort `xhigh`（後端預設，啟動 banner 佐證；報告自述 GPT-5 未揭露內部值）｜Snapshot `bd6fff45`→`d65b498b`
  - 結論：待修正。Findings：
    - F-B1【重要｜Owner: Reviewer B｜未關閉】對比測試先 `round(ratio, 2)` 再比門檻（`tests/test_design_tokens.py:310`、`tests/test_webapp.py:1594`）；`#006ffb` 對白實際 4.499888 會被誤判通過。現有 41 組色值本身均合格，缺陷在守門測試。建議改為 `ratio >= minimum`，格式化只用於顯示。
    - F-B2【建議｜Owner: Reviewer B｜未關閉】`design_tokens.py:3` docstring 把 renderer 遷移寫成既成事實，T06 完成前與實際資料流不符；建議改為分階段現況描述。
  - 驗證：4/4 受審檔 blob 與快照樹一致；獨立重算 41 組對比 0 不合格（最低線條 3.49466）；沙箱無法呼叫 WSL（E_ACCESSDENIED），全套綠證據採信 Developer 實測。
  - 報告存檔：scratchpad `t01/reviewer-b-final.md`（報告內 Ponytail 一節為 Reviewer 自述工具，不影響結論，未採信為事實）

### Findings 修正（2026-08-10，Developer fixed）

- F-B1：重現屬實（`#006ffb` 對白 4.499888 → round 4.50 誤過）。修正：`test_design_tokens.py` 抽具名述詞 `meets(ratio, minimum)`＝未捨入比較、訊息 `{:.4f}`、新增反例回歸測試（第 45 項，內含 `assertEqual(4.5, round(near_miss,2))` 證明舊守門確實放行）；`test_webapp.py` `ContrastTest` 同步改行內 `ratio >= minimum`＋`{:.4f}`。
- F-B2：重現屬實（兩 renderer grep `design_tokens` 零命中）。修正：docstring 改分階段現況，明寫 T06 關閉前「唯一一份色值」尚未成立；renderer 未動、未擴大範圍。
- 修正後快照樹：`3caf0dc2f6f08ff355d871d0b7741f4bf9af0bd9`（修正差分：design_tokens.py +18/-5、test_design_tokens.py +34/-4、test_webapp.py +17/-5；pages.py 未動）
- Coordinator 指定重驗（無產品行為變更，不重跑全套）：`tests.test_design_tokens -v` → Ran 45 OK, RESULT_EXIT=0；`discover -p "test_webapp*.py"` → Ran 842 OK, RESULT_EXIT=0
- Developer 附帶說明：守門邏輯兩份寫法（`meets()` vs 行內），統一需動 test_webapp.py 授權外匯入結構，交 Reviewer B 表態。

### 定向複驗與完成（2026-08-10）

- Reviewer B 定向複驗（resume 原 session）：F-B1 `closed`（`test_design_tokens.py:67` 直接 `ratio >= minimum`、`:330` 反例回歸測試、`test_webapp.py:1600` 同步；`meets()` 與行內兩份寫法判定為不構成新 Finding——單行比較避免跨測試模組耦合）；F-B2 `closed`（docstring 分階段現況屬實、renderer 未越權）。無新 Finding。standards 軸結論：通過。報告：scratchpad `t01/reviewer-b-reverify-final.md`
- 最終 Snapshot：`3caf0dc2f6f08ff355d871d0b7741f4bf9af0bd9`；首次完整驗收 2577 OK（skipped=1 基準既有）；Findings 後指定重驗 45 OK＋842 OK；共識：Developer 與兩位 Reviewer 均通過，無未解風險遺留（border 色階、renderer 遷移屬 T03/T06 既定範圍）
- 前端實際操作驗收：本票不適用（判定依據見該節），操作欄位以不適用結案

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
