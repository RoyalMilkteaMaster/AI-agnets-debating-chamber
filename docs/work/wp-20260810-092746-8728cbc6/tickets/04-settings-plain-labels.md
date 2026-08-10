# 設定頁白話中文

- 狀態：完成
- Spec：`../spec.md`
- Blocked by：Ticket 03

## 目標

在 `webapp/settings.py` 建立標籤表（key-path → 中文標籤＋一句白話說明）與分組標題中文化；未涵蓋的鍵顯示原鍵名＋「尚未翻譯」標註且照常可編輯（不 fail-closed）；`_about` 註解顯示照舊；`pages.py` 的設定欄位渲染加上白話說明行。

## 對應原始需求

- R-001：設定頁白話中文：每個規則項顯示中文標籤＋一句白話說明，分組標題中文化；規則檔新增而未翻譯的鍵顯示原鍵名＋「尚未翻譯」標註、照常可編輯（不 fail-closed）。

## 使用者價值

對應 User Story 1：使用者打開設定頁時不必再猜英文鍵名在控制什麼，每一項都有中文標籤與一句白話說明；規則檔日後新增鍵也不會讓設定頁壞掉或擋住編輯。

## 範圍

包含：

- `webapp/settings.py` 新增標籤表：key-path 對應 `{中文標籤, 白話說明}`；`light_scale[]` 內欄位以容器語境對應。
- 分組標題（fieldset legend）中文化。
- 未涵蓋鍵的 fallback：顯示原鍵名＋「尚未翻譯」標註，照常可編輯。
- `pages.py` 設定欄位渲染加上說明行。
- 對應測試（含以測試設定檔加入未知鍵驗證 fallback）。

不包含：

- 設定頁版面視覺（Ticket 03 已完成）。
- 規則檔本身的欄位增刪或預設值調整。
- `_about` 註解顯示方式的改動（照舊）。

## 已確認實作決策

- 標籤表放在 `webapp/settings.py`，即既有標籤解析位置；不新建模組。
- 未涵蓋鍵不得 fail-closed：顯示原鍵名＋「尚未翻譯」標註，仍可正常編輯與儲存。
- `_about` 註解顯示照舊。
- 分組標題（fieldset legend）文案：頂層→「基本」；`timeline_ms`→「時間軸（毫秒）」；`vote_thresholds`→「票數門檻」；`confidence`→「燈號規則」；`confidence.light_scale`→「燈號階梯」；`confidence.downgrades.few_independent_domains`→「降級：獨立來源不足」；`confidence.downgrades.low_trust_source`→「降級：低可信來源」。
- 逐鍵文案逐字採用下表（取自 Spec〈R-001 設定頁白話中文〉，Spec 為文案權威）：

| 鍵路徑 | 中文標籤 | 白話說明 |
|---|---|---|
| `schema_version` | 規則檔版本 | 規則檔的格式版本，目前僅支援 1，平常不需改動 |
| `timeline_ms.debate_start` | 證據封存時刻 | 開賽後多久結束研究、封存證據（毫秒），之後進入辯論 |
| `timeline_ms.round_one_window` | 第一輪挑戰時窗 | 證據封存後，留給第一輪反方挑戰的時間長度（毫秒） |
| `timeline_ms.reduced_threshold_from` | 門檻下調時刻 | 從此時間點起，過關票數由「初始」降為「下調後」 |
| `timeline_ms.final_round_start` | 最終輪開始 | 最後一輪辯論的開始時間 |
| `timeline_ms.final_round_end` | 最終輪結束 | 最後一輪辯論的結束時間 |
| `timeline_ms.force_stop` | 強制結算時刻 | 時間到就強制結算：達強停票數採納立場，否則未達共識 |
| `vote_thresholds.unanimous_blind_pass` | 盲投直過票數 | 開場盲投全數同立場達此票數，直接產出藍燈報告、不進辯論 |
| `vote_thresholds.initial` | 初始過關票數 | 辯論開始時，達成共識所需的有效票數 |
| `vote_thresholds.reduced` | 下調後過關票數 | 門檻下調時刻後，達成共識所需的有效票數 |
| `vote_thresholds.forced_stop` | 強停採納票數 | 強制結算時至少要這麼多票才採納立場，否則未達共識 |
| `confidence.light_scale[].min_votes` | 最低票數 | 拿到至少這麼多有效票，燈號落在這一級 |
| `confidence.light_scale[].level` | 燈色 | 這一級對應的燈色（blue／green／yellow／orange／red） |
| `confidence.downgrades.few_independent_domains.levels` | 降幾級 | 獨立來源網站太少時，燈號往下降的級數 |
| `confidence.downgrades.few_independent_domains.min_independent_domains` | 最低獨立網域數 | 採納立場引用的來源至少要來自幾個不同網站 |
| `confidence.downgrades.low_trust_source.levels` | 降幾級 | 引用低可信來源時，燈號往下降的級數 |
| `confidence.downgrades.low_trust_source.trusted_source_tiers` | 可信來源等級 | 視為可信的來源等級清單（逗號分隔） |
| `confidence.downgrades.low_trust_source.exempt_seat_ids` | 豁免席位 | 不受此降級約束的席位（輿情席職責即蒐集輿情） |

## 驗收條件

- 設定頁渲染後：每個規則項顯示上表中文標籤＋白話說明、分組標題為中文；以測試設定檔加入未知鍵時，顯示原鍵名＋「尚未翻譯」且可編輯。
- 文案與上表逐字一致，無錯字、無自行改寫。
- `_about` 註解顯示與改版前一致。
- 既有測試全綠。

## 測試與證據

- 測試接縫：標籤表 fallback（以測試設定檔加入未知鍵，斷言顯示原鍵名＋「尚未翻譯」且欄位可編輯）。
- 迭代期快速檢查：WSL 執行針對本票模組的單測 `python3 -m unittest tests.test_webapp -v`（秒級；若本票另建獨立測試模組，改跑該模組）。
- Ready for Review 完整驗收：WSL 執行 `python3 -m unittest discover -s tests` 全綠（只在首次準備 Review 前執行一次）。
- Findings 修正後：由 Coordinator 依影響範圍指定要重跑的指令，不預設重跑全套。
- 必交證據：Ready for Review 的完整驗收結果、退出碼、設定頁渲染後 HTML 存檔與變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 前端實際操作驗收

- 適用性：適用
- 判定依據：R-001 規範設定頁畫面上每個規則項的呈現，必須實際開頁核對逐鍵標籤與說明。
- 操作環境與實際網址：執行階段填寫
- 使用的原生瀏覽器工具：執行階段填寫
- 操作步驟與預期結果：
  1. 開啟設定頁：逐項核對每個規則鍵的中文標籤與白話說明與上表逐字一致。
  2. 核對分組標題（fieldset legend）皆為中文，且與上列對應表一致。
  3. 以測試設定檔加入一個未知鍵後重新整理：該項顯示原鍵名＋「尚未翻譯」標註，且欄位仍可輸入與儲存。
  4. 核對 `_about` 註解顯示與改版前相同。
- 操作結果：執行階段填寫
- 操作證據：執行階段填寫
- 環境註記：環境無瀏覽器時，依 Spec 測試決策以渲染後 HTML 存檔＋關鍵元素斷言為操作證據。

## 依賴

- Depends on：Ticket 03
- Blocks：Ticket 05

## 並行與所有權

- Dispatch：serialized
- Exclusive write scope：`hoya_market_agents/webapp/settings.py`、`hoya_market_agents/webapp/pages.py`（設定欄位渲染區）、對應測試
- Shared resource locks：`hoya_market_agents/webapp/pages.py`（熱點鏈）
- Can run with：Ticket 06、Ticket 07

## 初始執行配置

- Developer model：`claude-sonnet-5`（相容預設偏好）
- model_reasoning_effort：`high`（相容預設偏好）
- 路由理由：工作明確、局部、低風險——固定格式的文案表加上既有 walk 模式的局部修改，已有相鄰實作可沿用且可秒級驗證，不涉及跨模組設計、Schema、Migration、權限、安全、資料風險或公開介面。
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
- 並行批次：批次 4（與 T07 的 Review／修正回合並行；寫入範圍互斥——本票不碰 server.py 與 test_webapp_nav_injection.py）
- 排程判定：依賴 T03 已完成；`pages.py` 熱點鏈第四棒（T03 已釋放）
- 基準版本：branch `main`、HEAD `9b8a451`、基準樹 `1fc2d449ee2f613ca05ebe1eb52d12631918b605`（T01/T02/T03/T06 完成＋T07 凍結送審狀態）
- 開發角色：Developer = Claude（milktea-build）、model `claude-opus-5`；票面偏好 `claude-sonnet-5`/`high` 不採用之原因：平台派發工具無 reasoning effort 欄位，無法保證「Sonnet 不得低於 high」硬性下限，依規則改用後端已驗證預設
- 確切寫入範圍：`hoya_market_agents/webapp/settings.py`、`hoya_market_agents/webapp/pages.py`（設定欄位渲染區）、對應測試、`tests/test_frontend_redesign_acceptance.py`（僅 A5 掃描對設定頁 `note-confidence.light_scale[*].level` 燈色枚舉的窄豁免；2026-08-10 Coordinator 依 R-001/A5 衝突裁決補授權）
- 資源鎖：`pages.py` 熱點鏈（本批次獨占）；全套測試執行權（`.locks/full-suite` 目錄鎖）
- 必跑指令：迭代 `PYTHONPATH=tests python3 -m unittest tests.test_webapp -v`；Ready for Review `python3 -m unittest discover -s tests`（WSL，取鎖後執行）

### R-001／A5 衝突裁決（2026-08-10，Coordinator）

- 矛盾事實：R-001 使用者定案文案 `confidence.light_scale[].level` 說明含「（blue／green／yellow／orange／red）」；驗收條件 7 引用之 A5 標準（wp-20260809 §A5）禁止畫面出現英文資料原值並明舉 `green`。機器執法者 `tests/test_frontend_redesign_acceptance.py::EveryPageIsTraditionalChineseTest`（基準樹既有）由 8/8 綠轉 2 紅（`[] != ['blue','green','orange','red','yellow'] : settings`）。Developer 逐字照做 R-001 並原樣呈報，未自行處置（兩條出路均越權）——處置正確。
- 裁決：採窄豁免（Developer 方案 a）。依據：①Spec 自身衝突解決原則「§13 與先前章節衝突時以 §13 為準」——R-001 為本次使用者明確定案的特定意圖，優先於前工作包的一般性 A5；②該欄位存檔值即英文枚舉，不列合法值使用者無從輸入，正是 R-001 白話說明之目的；③同值已以 `value="blue"` 形式在頁面上且 A5 自身視屬性值為機器值。豁免範圍僅限設定頁 `note-confidence.light_scale[*].level` 說明中的燈色枚舉；A5 其他掃描不變。
- 寫入範圍已補授權 `tests/test_frontend_redesign_acceptance.py`（僅此豁免）。裁決列入結案報告交使用者複核。

### Ready for Review（2026-08-10）

- Snapshot：基準樹 `1fc2d449ee2f613ca05ebe1eb52d12631918b605` → 快照樹 `3394ae7cf1f4965a01cd52c65b8d16f73c73db92`（path-scoped 於本票四檔）
- 變更：`settings.py +140/-11`（FIELD_LABELS 18 鍵、SECTION_LABELS 7 組、UNTRANSLATED_NOTE、`_generic()` 索引正規化、fallback 走原路）、`pages.py +33/-7`（`_settings_title()` 供 label/legend、說明 `<p class="hint" id="note-{path}">` 串 aria-describedby、未動樣式表）、`test_webapp.py +227`（19 條新測試）、`test_frontend_redesign_acceptance.py +68/-2`（窄豁免綁 `note-confidence.light_scale[N].level` id、扣自身文字非手抄字串、附裁決註解＋3 條守衛：豁免內容攤開可見、拿掉豁免設定頁即紅、FP 方向整頁未成免掃區）
- 完整驗收（取鎖）：`Ran 2696, OK (skipped=1), RESULT_EXIT=0`（skip 為基準既有環境條件）；A5 類別 11 tests OK（原 2 紅轉綠）；`tests.test_webapp` 640 OK；compileall exit 0
- 文案驗證：`verify_wording.py` 剖析 spec.md R-001 表格與渲染 HTML 逐字比對——18 鍵全逐字相符、26 控制項、分組標題序列正確、fallback「尚未翻譯」3 處可編輯帶值、`_about` 一字不動、RESULT_PROBLEMS=0；渲染樣本 2 檔存 scratchpad `t04/rendered/`
- Developer 自報風險：五個 legend 同名「燈號階梯」（Spec 只給一個標題，逐字採用的結果；改文案需 Spec 擁有者）；文案表與規則檔漂移窗（R-001 明定 fail-open，有守門測試）；A5 豁免邊界（未來第二個欄位需列英文值時須再裁決，不擴大既有豁免）
- Diff 全文：scratchpad `t04/ticket04.diff`（四檔）

### Review（2026-08-10）

- Reviewer A｜軸 spec｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot `1fc2d449`→`3394ae7c`
  - 結論：不通過。八條檢核 7 過；文案 18/18、標題 7/7 逐字比對通過；「燈號規則」未渲染判定為規則檔結構的正確反映；五 legend 同名不列 Finding。
    - F-A04-1【重要｜Owner: Reviewer A｜未關閉】A5 豁免以全域 `str.replace()` 刪文字，範圍不限於核准 note id：在核准 note 與頁面他處各放同句後，他處文字也被刪（VISIBLE 5 色→SCANNABLE 0）；現有「另放 green」守衛抓不到。建議 parser 層僅跳過指定 id 子樹＋「同句在他處仍被抓」回歸測試。
  - 報告：scratchpad `t04/reviewer-a-final.md`（Ponytail 為 Reviewer 自述工具，未採信為事實）
- Reviewer B｜軸 standards｜引擎 native｜backend Codex CLI 0.146.0｜model/effort 後端預設｜Snapshot 同上
  - 結論：待修正。
    - F-B04-1【重要｜Owner: Reviewer B｜未關閉】同一缺陷之獨立重現（SCANNABLE=' '、DETECTED=[]、REPRO_EXIT=7）；違反窄豁免裁決邊界。其餘自報事項（_generic、文案表、_settings_title、docstring）均無 Finding。
  - 報告：scratchpad `t04/reviewer-b-final.md`

### Findings 修正（2026-08-10，Developer fixed）

- F-A04-1／F-B04-1：重現屬實（含確認原 docstring 宣稱錯誤並刪除）。修正：略過改在剖析當下、範圍等於節點子樹——`_VisibleText` 增 `skip` 判斷式與獨立深度計數，`scannable_text(body)`＝`visible_text(body, skip=is_exempt_note)`；`_EXEMPT_NOTE_ID` 改比對 id 值、單一判斷式服務掃描與列舉（無兩份規則）；新回歸測試「豁免節點外的同一句仍被抓」（植入自豁免節點讀出的原句）；豁免由四條斷言夾住。修正後重現哨兵 7→0。
- 修正後快照樹：`d818ea7b35d4eb7daa8b0d56d7ef3e57d10f52ca`（僅 test_frontend_redesign_acceptance.py +103/-31；產品程式一字未動）
- 指定重驗：`tests.test_frontend_redesign_acceptance` 38 OK（A5 類別 12 條全綠）、RESULT_EXIT=0；未重跑全套（產品碼與 2696 綠證據同一份）。

### 定向複驗（2026-08-10）

- Reviewer A：F-A04-1 `closed`（重放原反例 DETECTED 五色齊、REPRO_EXIT=0；確認 scannable_text 於解析期僅略過完整 id 符合之子樹；新同句異位置回歸測試有效；38 tests OK；blob 與新快照一致）。spec 軸：通過，無新問題。報告：scratchpad `t04/reviewer-a-reverify-final.md`
- Reviewer B：F-B04-1 `closed`（節點式 skip 取代全域 replace；巢狀略過與近似 id 邊界探針通過：NESTED_FOUND=['green']、NEAR_ID_FOUND=['blue']；38 tests OK）。standards 軸：通過，無新 Finding。程序備考：B 曾嘗試 OCR revision preview（因兩端為 tree 物件 exit 1）後回退 native——與契約 native 引擎「不得偵測 OCR」有偏差，實質驗證全基於 native 證據，不影響關閉效力。報告：scratchpad `t04/reviewer-b-reverify-final.md`
- 最終 Snapshot：`d818ea7b35d4eb7daa8b0d56d7ef3e57d10f52ca`；首次完整驗收 2696 OK（skipped=1 基準既有）；Findings 後指定重驗 38 OK；共識：Developer 與兩位 Reviewer 均通過。

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
