# 端到端驗收摘要（Ticket 09）

- 工作識別碼：`wp-20260810-092746-8728cbc6`
- Spec：`../spec.md`（R-001～R-006）
- Ticket：`../tickets/09-end-to-end-acceptance.md`（七條驗收條件為唯一標準）
- 驗收日期：2026-08-10
- 本票性質：**只驗證與彙整證據，不修改任何產品程式碼或測試**。寫入範圍僅 `acceptance/`。

## 結論

七條驗收條件**全部 PASS**，共 11 項判定全綠（`checks.json`），
全套測試 `Ran 2729 tests — OK (skipped=1)`、退出碼 0，與 Coordinator 給的基準完全相符。
未發現任何需要退回其他 Ticket 的不符。

這 11 項是**可重跑**的，不只是留存結果：生成器與重跑入口都在 `tools/` 底下，
一行命令就能從頭再算一次，見〈重跑與快照〉。全套測試的命令與退出碼在 `full-suite.txt`。

## 驗收環境與方法

- 環境：WSL Ubuntu-24.04、Python 3.12.3；Code Root 為本 repo。
- **沒有可自動化的瀏覽器**，因此依 Spec〈測試決策〉以「渲染後 HTML 存檔 ＋ 對關鍵元素的語意斷言」
  作為操作證據；整包實際瀏覽器走查由 Coordinator 於本票後另行安排，不在本票範圍。
- 兩趟真的 run（台股 `2330`、幣圈 `BTC`）都是從**主頁標的選單送出**、跑完整條 launch 流程產生的：
  席位池、Core 撰稿與命題撰寫三個外部邊界是注入的替身，不啟動瀏覽器、不呼叫 codex。
  - 台股：`20260314T015926Z-2330-aaa111`
  - 幣圈：`20260315T015926Z-btc-bbb222`
- 讀法一律只讀**公開可見行為**：頁面的可見文字、`aria-label`／`role`／`aria-disabled`／`aria-current`、
  連結目標、檔案位元組。依 Spec〈不應耦合的實作細節〉，**不**斷言 CSS 類名順序與色碼字面值、
  DOM 巢狀細節、roster JSON 鍵順序、注入導覽的確切 HTML 字串。
- 對比度是本票**獨立實作一次** WCAG 相對亮度公式算出來的，不是呼叫受測程式回報的數字。
- 產生證據的腳本存在 `tools/`（隨證據一起交付），詳見〈重跑與快照〉。

## 重跑與快照

### 怎麼重跑

```
# WSL Ubuntu-24.04，從 repo 內任何目錄（或用絕對路徑）都可以
bash docs/work/wp-20260810-092746-8728cbc6/acceptance/tools/rerun.sh
```

- 生成器：`acceptance/tools/gen_evidence.py`（讀法工具在 `acceptance/tools/evidence_lib.py`）。
- 入口：`acceptance/tools/rerun.sh`。Code Root 與輸出目錄都從腳本自己的位置推得，
  沒有寫死任何暫存目錄路徑；要換位置就設 `HOYA_CODE_ROOT`／`HOYA_ACCEPTANCE_OUT`。
- 退出碼 0＝11 項判定全 PASS，1＝有 FAIL（哪一項看 stdout 與 `checks.json`）。
- **預設會覆寫 `acceptance/` 底下的證據**；複驗時想留著已凍結的一份，就把輸出導到別處：
  `HOYA_ACCEPTANCE_OUT=/tmp/t09-rerun bash …/rerun.sh`。
- 全套測試不在生成器裡，另跑（見 `full-suite.txt` 的命令與退出碼）：
  `python3 -m unittest discover -s tests`。

### 重現性實測

交付的這一份證據就是 `tools/rerun.sh` 跑出來的。之後又從 `acceptance/tools/` 獨立再跑一次
（工作目錄故意設在 `/tmp`、輸出導到別的目錄，證明不依賴呼叫者的當前目錄、也不會覆寫已凍結的一份），
11 項判定同樣全 PASS、退出碼 0；兩份逐檔比對：

- **41 份生成檔中 36 份逐位元組相同**：8 份文字證據（`nav-audit.txt`、`settings-labels.txt`、
  `nav-injection.txt`、`contrast-table.txt`、`seat-roster.txt`、`ask-menu.txt`、
  `protected-zone.txt`、`a5-grep.txt`）與 `checks.json` 全數相同，32 份 HTML 中 27 份相同。
- 只有 5 份 HTML 不同，差的**只有印出當次暫存 Data Root 路徑的那幾行**
  （`rendered/tw_stock-history.html`、`crypto-history.html`、`empty-history.html`、
  `open-history-filtered.html`、`settings-unknown-key.html`）。每跑一次就是一個新的暫存目錄，
  這是必然的，不是判定不穩：那幾頁的 header 註記印的就是這一趟用的 Data Root。

### 兩棵樹

| 項目 | SHA（git **tree** object，不是 commit） |
|---|---|
| 動工前基準樹 | `1ac0192e024320a913e3211c9c72c668b7c25432` |
| 雙 Reviewer 審查的快照樹 | `1fdce30f030fec18d5d0c449db3c6daced484f20` |

本檔修訂後的交付快照樹 SHA 在交付回報中給出並由 Coordinator 記錄：
一個檔案沒有辦法記載「包含這個檔案的那棵樹」的雜湊，寫進去就會把它算成另一棵樹。
`checks.json` 同理，只記 `baseline_tree`。

## 七條驗收條件逐條對應

### 1. 設定頁白話中文 — PASS

證據：`settings-labels.txt`、`rendered/tw_stock-settings.html`、`rendered/crypto-settings.html`、
`rendered/settings-unknown-key.html`

- Spec R-001 逐鍵表 18 個鍵路徑逐一比對：中文標籤與白話說明**逐字相符**，且都出現在渲染後的頁面上。
- 頁面上沒有 Spec 逐鍵表未涵蓋的鍵（不存在「漏翻的既有鍵」）。
- 分組標題 10 個（含 `light_scale` 五個階梯）全部是 Spec 指定的中文。
- 未知鍵 fallback（測試設定檔，`config/debate_rules.json` 未被更動）：新增 `brand_new_knob`
  與未知分組 `experimental_group` 後，頁面顯示**原鍵名＋「尚未翻譯」**、控制仍是帶原值的
  text input、未被 disabled；已翻譯的鍵不受影響。
- 說明：載入器本來就會拒絕未知欄位，所以那份測試設定檔的頁面上另有一段載入器的紅字。
  R-001 要求的「不 fail-closed」指的是**設定頁照樣把鍵畫出來且可編輯**，這一點成立；
  載入器對未知欄位的態度不屬本工作包範圍。

### 2. 五導覽全站常駐與設定分離 — PASS

證據：`nav-audit.txt`、`rendered/` 下兩個市場各 9 頁 ＋ `rendered/empty-*.html`

- 逐頁（每個市場各 8 頁 ＋ 無報告 Data Root 3 頁，共 19 頁）確認 header 五導覽依序為
  即時辯論｜歷史與命中率｜市場報告｜完整辯論｜設定。
- header 控制的文件順序一律是 `… → 設定 → 關閉伺服器`，即「設定」緊鄰關閉鈕左邊（R-003）。
- 報告導覽指向：
  - 主頁初始（`/?run=` 指到不存在的 run，即「還沒問任何題目」的同一頁）、歷史、設定、404、
    無法啟動頁 → **最新有報告的 run**。
  - 辯論室與 run 詳情頁 → **該 run 自身**。
  - 500 頁 → 兩個報告分頁為**停用樣式**（`role="link" aria-disabled="true"`、無 `href`）。
    這是 T02 已核准的裁決：請求邊界那一頁不讀任何東西就要送得出去。
  - 無報告的 Data Root（`empty-*`）→ 停用樣式。
- 「伺服器已關閉」頁：header 內**零導覽、零按鈕**。

### 3. 離線兩頁經伺服器瀏覽的導覽注入 — PASS

證據：`nav-injection.txt`、`rendered/*-served-report.html`、`rendered/*-served-debate.html`、
`rendered/*-offline-*.html`

- 兩個市場 × 兩個檔案共 4 份：回應 200、站內導覽五個分頁齊全、四個站內連結（`/`、`/history`、
  `/settings` 與兩個報告分頁）都可回站內，兩個報告分頁指向**這一趟 run 自己的檔案**。
- 磁碟檔案位元組**完全不變**：sha256 與位元組數前後相同（逐檔列出）。
- 送出的位元組＝原檔在 `<body>` 之後插入一段，**原檔位元組一個不動**（前綴與後綴逐位元組比對）。
- 離線頁**自己的兩分頁導覽仍在**（分享出去的檔案維持自足）。
- 無插入點的 HTML 原樣送出：沒有 `<body>`、只有註解裡的 `<body>`、註解沒關、屬性沒收尾——四種
  都原樣送出；有插入點時才插入（鑑別力）。
- 預覽面板（`Sec-Fetch-Dest: iframe`）不加站內導覽。
- 已核准代價（T07 裁決）：注入的導覽自帶 scoped `<style>`（`.hoya-site-nav`、值取自 design_tokens、
  `aria-label="站內導覽"`），因此舊 run 經伺服器瀏覽時那一條會呈現新設計風。

### 4. 白底新設計、深色退場、WCAG AA — PASS

證據：`contrast-table.txt`

- `prefers-color-scheme` 在 webapp 樣式表、離線兩頁、注入導覽的 `<style>` 與**兩個市場各 9 頁共 18 份
  已渲染頁面**裡命中 0 次 → 作業系統設深色時仍是同一套白底。
- 單一白底 palette：畫布 `#f8f9fa`、卡片 `#ffffff`、毛玻璃 `rgba(255,255,255,0.72)`，
  合成後實色 `#fdfdfe`；畫布相對亮度 0.9461。
- webapp 樣式表與離線兩頁用的是**同一組 16 個色值**，且每一個都由 `design_tokens` 擁有（零例外）。
- 字體堆疊微軟正黑體優先，三處同一份；零外部資源（樣式裡沒有 `@import`／`url()`）；
  毛玻璃走原生 `backdrop-filter`（12px）。
- 對比度實測 41 組配對全部達標（文字 4.5:1、線條 3.0:1），含毛玻璃**合成後**實色；
  餘裕最小的一組 `accent_text on google_red = 4.77`（門檻 4.5）。

### 5. 席位名稱、白話說明與 roster fail-closed — PASS

證據：`seat-roster.txt`、`rendered/tw_stock-room.html`、`rendered/crypto-room.html`、
`rendered/*-offline-report.html`

- 台股題七席顯示股票套定案名，幣題七席顯示幣圈套定案名，**逐席**與 Spec R-005 表相符，
  每一席的 blurb 也逐字出現在席位卡上。
- 每一席「恰好只出現這一套的名稱」——同時擋掉印成別套與三套全印。
- 離線報告席名與 webapp **逐席一致**（兩邊讀的是不同的檔案：`question.json` 與 `report.json`）。
- 美股與台股共用同一套（stock 套）。
- roster fail-closed：缺 `crypto` 套組、`stock` 套缺 `blurb`、`crypto` 套 `blurb` 空白——三種都在載入
  當下失敗並給出指名到席位與欄位的中文訊息；現行 roster 照樣載得進來（鑑別力）。`roster_version` 3.0.0。

### 6. 發問選單無開放題、歷史開放題 run 可回看 — PASS

證據：`ask-menu.txt`、`rendered/open-run_detail.html`、`rendered/open-history-filtered.html`

- 發問區資產類別選單的可選項恰為台股／美股／加密資產三個市場（另有一個「請選擇資產類別」的
  未選擇提示項，不是市場）；開放題的**值與字都不在**；標的輸入框與選單同一組市場。
- 歷史頁的資產類別篩選**仍列開放題**（T08 已核准的回看入口）。
- 一筆歷史開放題 run：詳情頁回應 200、頁面上寫「開放題」（中文）、讀得到它的題目，
  用開放題篩選在歷史頁找得到它。

### 7. 保護區行為、既有測試全綠、A5 繁中 grep — PASS

證據：`protected-zone.txt`、`a5-grep.txt`、`full-suite.txt`

- 保護區（兩個市場各驗一次）：
  - 三種票數逐立場精確比對公開紀錄 `votes.json` 與畫面上的數字（偏多 6／偏空 1／方向不明 0），
    三個數字不全等所以逐項比對有鑑別力；票數區沒有英文立場原值。
  - 聊天室：`events.jsonl` 裡七席都發過言，七席都在聊天室裡且用這個市場那一套名字。
  - 燈位：`report.json` 的燈號在合併頁與 run 詳情頁都是權威的中文詞（黃燈），英文原值 0 命中。
- A5 標準：兩個市場各 9 頁、共 18 份渲染後頁面，32 個由權威推導的英文枚舉值**全部 0 命中**。
  唯一豁免是 T04 裁決的設定頁 `note-confidence.light_scale[*].level` 那五個節點，
  六個方向的鑑別力檢查都通過（豁免前有那五個燈色詞、豁免後 0 命中、豁免掉的正是 Spec R-001 逐字
  指定的那一句、同一句話出現在別的節點照樣被抓到、同一頁別處植入 `green` 照樣被抓到、
  植入三個值都抓得到）。離線兩頁依 A5 原文不納入本條，另列供參（實測亦為 0 命中）。
- 全套測試：`Ran 2729 tests — OK (skipped=1)`，退出碼 0，與基準相符。
  唯一的 skip 是 `tests/test_debate_rules.py` 的大小寫別名測試（環境條件式 skip，
  WSL 的 ext4 大小寫敏感），**如實記為 skip 而非 PASS**。

## 已核准行為（驗收時視為正確，未記為缺陷）

1. 500 頁的報告導覽保留停用樣式；404 與無法啟動頁指向最新有報告 run（T02 裁決）。
2. A5 對 `note-confidence.light_scale[*].level` 子樹的窄豁免（T04 裁決）。
3. 注入導覽自帶 scoped `<style>`，舊 run 經伺服器瀏覽亦呈新設計風（T07／Spec 已核准代價）。
4. 舊 run 逐字稿沿用舊席名（T05 已核准代價）；`roster_version` 已升 3.0.0。
5. 歷史頁篩選選單仍列開放題，只有發問選單移除（T08 裁決）。
6. 全套測試基準 2729 tests、skipped=1。

## 檔案索引

| 檔案 | 對應驗收條件 |
|---|---|
| `checks.json` | 全部 11 項判定的結果，加上重跑入口、命令與基準樹 |
| `tools/gen_evidence.py`、`tools/evidence_lib.py`、`tools/rerun.sh` | 重跑用的生成器與入口（見〈重跑與快照〉） |
| `settings-labels.txt` | 條件 1 |
| `nav-audit.txt` | 條件 2 |
| `nav-injection.txt` | 條件 3 |
| `contrast-table.txt` | 條件 4 |
| `seat-roster.txt` | 條件 5 |
| `ask-menu.txt` | 條件 6 |
| `protected-zone.txt` | 條件 7（保護區） |
| `a5-grep.txt` | 條件 7（A5 繁中 grep） |
| `full-suite.txt` | 條件 7（既有測試全綠） |
| `rendered/*.html` | 條件 1～6 的渲染後頁面本體 |

### `rendered/` 內容

- `tw_stock-*` / `crypto-*`：`home-waiting`、`room`、`history`、`settings`、`run_detail`、
  `not_found`、`launch_problem`、`error_500`、`closed` 九頁；
  `offline-report.html`／`offline-debate.html`（磁碟原檔）；
  `served-report.html`／`served-debate.html`（經伺服器瀏覽、含注入導覽）。
- `empty-home.html`／`empty-history.html`／`empty-settings.html`：沒有任何 run 的 Data Root，
  兩個報告分頁為停用樣式。
- `settings-unknown-key.html`：未知鍵 fallback 的設定頁。
- `open-run_detail.html`／`open-history-filtered.html`：歷史開放題 run 的回看。

## 已知風險

1. **沒有真的用瀏覽器看過。** 本票所有視覺結論都是從渲染後的 HTML、CSS token 與實測對比度推出來的，
   毛玻璃在真實瀏覽器的合成、字體實際落點與行動版佈局都不在此驗證範圍。
2. **深色模式只驗到「沒有第二套」。** 依據是成品樣式裡沒有 `prefers-color-scheme` 且只有一套 palette；
   真的把作業系統切成深色再看一次仍應由實機走查補上。
3. **兩趟 run 的外部邊界是替身。** 席位池、Core 撰稿與命題撰寫都是注入的假物件，所以本票證明的是
   「接線與呈現對」，不是「真的 provider 跑得起來」。
