# Spec A1–A5 逐條對照表（Ticket 08 端到端驗收）

- 產生時間：2026-08-10（+0800）；**第三輪**（Reviewer A／B 首輪四項 Finding 修正後）
- 基準：main `9b8a4510`＋工作樹。第一輪基準樹 `6daa2e45bd17d9389ea6e82f09a4ede5b0f29f82`（T01–T07）；
  現行基準＝同樹＋D-1 修正三檔（`report_workflow.py` `bd93f38b`、`debate_driver.py` `82fe0de5`、
  `tests/test_report_renderer.py` `8e715041`），全套 2548 tests OK (skipped=1)
- 完整驗收：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/t08-intercept python3 -m unittest discover -s tests`
  → **Ran 2556 tests、OK (skipped=1)、exit 0**、122.3 秒（見 `full-suite.txt`；基準 2548＋本票 34−26＝8）
- 本票測試檔合計 34 個測試（第一輪 26＋D-1 回歸 3＋Finding 修正新增 5）
- **A1–A5 二十一條：全部通過**（A5-4 依 Spec 明文為不適用）。第一輪不過項 A3-3（退化路徑）已隨 D-1 修復關閉；
  Reviewer 首輪四項 Finding 的處理見本檔末〈Review Finding 處理〉
- 三輪的變更：第一輪建立、第二輪加 D-1 回歸、第三輪修 A8-1（掃描集合）、B8-F1（讀取層改語意錨點＋可見文字）、
  B8-F2（票數逐立場比對）、B8-F3（本檔計數）
- 兩組 fixture：台股 `20260314T015926Z-2330-aaa111`、幣圈 `20260315T015926Z-btc-bbb222`（暫存 Data Root，見 `end-to-end-runs.md`）
- **誠實邊界**：本執行環境沒有可自動截圖的瀏覽器。所有「畫面」證據都是渲染後的 HTML 存檔加關鍵元素斷言；**本票沒有任何截圖**，也沒有任何測試啟動瀏覽器或呼叫 codex。捷徑相關條目屬 Windows 宿主實機操作，引用 Ticket 07 的紀錄並標明其誠實邊界。

## A1 入口與停機（5 條）

| # | 條文 | 判定方式 | 結果 | 證據位置 |
|---|---|---|---|---|
| A1-1 | 雙擊「開啟辯論室」後沒有主控台黑框，瀏覽器直接開到主頁 | 實機操作（Windows 宿主），引用 Ticket 07 | 通過（帶誠實邊界） | Ticket 07 執行紀錄「實機捷徑驗證」：啟動→聽、瀏覽器開頁。**邊界（原文照引）**：「無黑框」為目視項，留使用者最終驗收；佐證為 `.lnk` 屬性＋`MainWindowHandle=0`＋啟動用的 powershell 立即結束。本票無法在無頭環境重現目視，**不代為宣稱** |
| A1-2 | 伺服器已在跑時再次雙擊不產生第二個伺服器程序，只開瀏覽器 | 實機操作，引用 Ticket 07 | 通過 | Ticket 07：「重複啟動→零新 PID 只開瀏覽器」（`scripts/webapp-common.ps1` 埠偵測） |
| A1-3 | 按「關閉伺服器」後伺服器停止，且該次記錄到 `server_stop` | 本票重跑既有測試（含真 loopback socket 層） | 通過 | `tests/test_webapp_shutdown.py::ShutdownOverASocketTest`（真 socket、真 `serve_forever`）：`test_a_submitted_shutdown_is_answered_and_then_ends_the_serving_loop`、`test_the_port_stops_accepting_connections`、`test_server_stop_is_written_exactly_as_it_was_before`、`test_server_stop_is_the_last_word_in_the_log`；順序由注入接縫量測：`ShutdownEndpointTest::test_the_closed_page_reaches_the_client_before_the_loop_is_asked_to_stop`。渲染後的「已關閉」頁：`rendered/tw_stock-closed.html`、`rendered/crypto-closed.html` |
| A1-4 | 「關閉辯論室」備援捷徑同樣能停掉伺服器 | 實機操作，引用 Ticket 07 | 通過 | Ticket 07：「關閉腳本與關閉捷徑→停」；`scripts/stop-webapp.ps1` 打 `POST /shutdown`，無任何強制終止路徑（Reviewer A 覆核） |
| A1-5 | 工作區根目錄不存在 `辯論室預覽.html` 與 `開啟辯論室.bat` | 本票新增可程式驗證 | 通過 | `tests/test_frontend_redesign_acceptance.py::TheRetiredEntryFilesAreGoneTest::test_neither_retired_entry_file_is_in_the_workspace_root`；同類別 `test_the_check_looks_at_a_directory_that_is_really_there` 排除「對著不存在的目錄問」的假通過。舊檔備份在工作區 `backups/wp-b3e957c6-t07-entry-files-20260810/` |

## A2 規則生效、報告可點、PDF 落地（4 條）

| # | 條文 | 判定方式 | 結果 | 證據位置 |
|---|---|---|---|---|
| A2-1 | 設定頁改規則存檔後，重新整理即顯示新值（規則值不得在模組層凍結） | 本票重跑既有測試 | 通過 | `tests/test_webapp.py::SettingsValueSurvivesAReloadTest::test_a_saved_value_is_the_one_a_fresh_render_shows`、`test_a_second_handler_built_after_the_save_shows_it_too`；`RulesTakeEffectOnTheNextRunTest::test_the_next_run_reads_the_new_numbers`、`test_the_two_answers_come_from_two_different_frozen_objects`。渲染後設定頁：`rendered/*-settings.html` |
| A2-2 | run 產出後右上角「市場報告」與「完整辯論」皆可點；尚未產生時為停用狀態 | 本票新增（完整 launch 產生的 run）＋既有測試 | 通過 | 可點：`…::TheReportsAreReachableAndExportableTest::test_both_report_links_on_the_run_page_point_at_files_that_exist`（連結指向的檔案真的存在）；渲染後 run 詳情頁 `rendered/*-run_detail.html`。停用態：`tests/test_webapp.py::RunDetailTest::test_a_run_without_a_report_says_so_instead_of_framing_nothing`、`test_a_run_without_a_transcript_offers_no_link_to_one`；匯出鈕停用態 `tests/test_webapp_pdf_export.py::ExportButtonTest::test_the_button_is_disabled_before_the_run_produces_its_report` |
| A2-3 | 按「匯出 PDF」後該 run 資料夾出現 `report.pdf` 與 `debate.pdf`，且既有檔案全部未被修改 | 本票新增（完整 bundle、逐檔 sha256＋mtime）＋Ticket 06 真 Edge 實測引用 | 通過 | 本票：`…::TheReportsAreReachableAndExportableTest::test_an_export_adds_two_pdfs_and_changes_nothing_else`（差集恰為兩個 `.pdf`，既有檔案雜湊與 mtime 全同）、`test_the_bundle_still_verifies_after_an_export`（匯出前後 `verify_run` 皆 VERIFIED）。本票用注入的假轉換器，**沒有啟動瀏覽器**。真 Edge 由 Ticket 06 在拋棄式 Data Root 實測（引用原文）：真報告 168KB→`report.pdf` 887KB、`debate.pdf` 1.2MB、`%PDF-1.4`、修正 `--user-data-dir` 後 6.4 秒退出碼 0；使用者真實 Data Root 零 `.pdf`／`.part` |
| A2-4 | 匯出失敗時顯示誠實錯誤頁，run 資料夾不留半成品 | 本票重跑既有測試 | 通過 | `tests/test_webapp_pdf_export.py::ExportEndpointTest::test_a_failing_conversion_is_an_honest_error_page_with_no_pdf_left`、`test_a_conversion_that_produced_nothing_says_that_and_not_success`、`test_a_promotion_failure_through_the_endpoint_leaves_no_pdf`、`test_no_failure_puts_a_traceback_on_the_page`；模組層 `ExportFailureTest`（含回滾與暫存檔清理） |

## A3 換套與標的選單（5 條）

| # | 條文 | 判定方式 | 結果 | 證據位置 |
|---|---|---|---|---|
| A3-1 | 台股題七席在 webapp 與離線報告顯示股票套名稱與研究方向，兩處一致 | 本票新增：逐席比對兩份**渲染後**輸出（正常路徑＋報告驗證失敗的退化路徑各一組） | 通過（7/7，兩條路徑） | `seat-label-comparison.md` 台股表；`…::TheSameSeatIsNamedTheSameEverywhereTest::test_a_taiwan_stock_run_is_named_from_the_stock_set_in_both_places`；退化路徑 `…::TheDegradedReportStillKnowsItsMarketTest::test_a_refused_report_is_named_the_same_as_the_room_seat_by_seat`（D-1 回歸）。方向（focus）的權威一致性由 `tests/test_seat_profiles.py::RosterProfileLoadTest::test_the_stock_focus_covers_both_stock_markets` 與 `test_the_shown_names_are_the_approved_division_of_labour` 釘住 |
| A3-2 | 幣題七席顯示幣圈套名稱與研究方向 | 同上 | 通過（7/7） | `seat-label-comparison.md` 幣圈表；`…::test_a_crypto_run_is_named_from_the_crypto_set_in_both_places` |
| A3-3 | 台股題的離線報告中不出現任何幣圈席名 | 本票新增：兩份離線頁面對「只屬幣圈套的名稱」零命中（正常路徑＋退化路徑） | **通過（含退化路徑）** | 正常路徑：`…::TheSameSeatIsNamedTheSameEverywhereTest::test_a_taiwan_stock_runs_offline_pages_hold_no_crypto_seat_name`＋反向控制 `…_do_hold_the_stock_seat_names`；存檔 `rendered/tw_stock-offline-report.html`、`rendered/tw_stock-offline-debate.html`。退化路徑（原缺陷 D-1，**已修復關閉**）：`…::TheDegradedReportStillKnowsItsMarketTest::test_a_refused_reports_offline_pages_hold_no_crypto_seat_name`、`…::test_a_refused_report_still_records_the_market_the_menu_stated`；見本檔末〈缺陷 D-1（已關閉）〉 |
| A3-4 | 發問流程可用選單選定資產類別與標的，不依賴純文字解析 | 本票新增：選單送出的那一份參數走到完成的 run | 通過 | `…::TheMenusAnswerReachesTheFinishedRunTest`（6 條）：`question.json` 的 `asset_class`／`assets`、`report.json` 的 `asset_class`、`verify_run` VERIFIED、兩份離線頁面產出、run 出現在歷史頁。「文字與選單衝突時以選單為準」由 Ticket 05 `MenuDecidesTheRunTest::test_the_launcher_is_told_the_market_the_menu_says` 釘住（本票的兩題文字與選單一致，不重複該情境）；webapp 不再做純文字解析由 `TheWebAppNeverReadsAWordingForATargetTest` 掃描 |
| A3-5 | roster 缺席位或缺任一套組時載入即失敗並給出可讀錯誤，不靜默降級 | 本票重跑既有測試 | 通過 | `tests/test_seat_profiles.py::RosterFailsClosedTest`（13 條，含 7 席×3 套的逐一刪除 subTest、空白欄位、重複 seat_id、非 JSON、檔案不存在） |

## A4 保護區與全站綠燈（3 條）

| # | 條文 | 判定方式 | 結果 | 證據位置 |
|---|---|---|---|---|
| A4-1 | 動態聊天室、燈位、正方／反方／無法判斷票數的行為與改版前一致 | 本票新增（真的跑完的 run，**逐立場**精確比對）＋既有測試＋Ticket 03 的前後 DOM 比對引用 | 通過 | 本票（第三輪已依 Reviewer B 的 F1／F2 重寫讀取層與斷言）：`…::TheProtectedZoneStillBehavesTest::test_each_of_the_three_tallies_is_the_public_records_own_number`（畫面上「立場詞→數字」逐項等於 `votes.json` 該 stance 的票數，不是比總和；另先斷言這一趟的三個票數不全相等，否則逐項比對沒有鑑別力）、`test_every_tally_is_labelled_in_this_question_types_own_vocabulary`、`test_the_tally_reading_pairs_each_word_with_its_own_number`（FP 方向：能分辨「總和一樣但每項都不對」）、`test_the_tally_reading_survives_a_rewrapped_region`（FP 方向：換標籤／改 class／多包兩層讀出來一樣）、`test_the_chat_room_still_carries_every_seat_that_spoke`（聊天室每一席恰好一個名稱且屬本 run 套組）、`test_the_light_this_run_earned_is_shown_as_the_authoritys_own_word`（燈號為權威中文詞、英文原值零命中）。**保護區的 markup／class／語意色由擁有者測試斷言**，本票不再重複：`tests/test_webapp.py::ProtectedZoneOuterwearTest`（三區塊 class 詞彙集合相等、燈號只帶 `light` 類）、`ChatRoomTest`、`BallotVocabularyTest`、`LiveSnapshotTest`；`tests/test_design_tokens.py::SingleStylesheetTest::test_only_the_stance_class_r2_leaves_free_is_painted`、`LightSemanticsTest`。Ticket 03 引用：保護區五區塊 DOM 前後 `cmp` 全 0（並記錄該票 A-1 Finding 已修正：不得讓保護區立場文字取得改版前沒有的語意著色） |
| A4-2 | 全站測試全綠 | 本票完整驗收（第三輪，退出碼在腳本內取） | 通過 | `full-suite.txt`：**Ran 2556 tests、OK (skipped=1)、EXIT=0**、122.3 秒（基準 2548＋本票 34−26＝8）。skip 1 為既有條件式（與基準相同）。攔截器記錄本次 39 次 codex 呼叫嘗試全部被攔下（與基準相同筆數；本票新增測試注入命題替身，貢獻 0 次） |
| A4-3 | 對比度以實測數字呈報，達 WCAG AA | 本票重跑並保存整張表 | 通過（50/50） | `contrast-table.txt`：兩套 palette × 25 對＝50 個實測比值全部逾門檻。最低值：文字級 light `oppose`／`danger` on page = 5.91:1（門檻 4.5）、dark `muted` on surface = 6.96:1（門檻 4.5）；非文字級 light `border` on page = 4.34:1、dark `border` on surface = 3.75:1（門檻 3.0）。與 Ticket 03 記錄的「light 5.91／dark 6.96」相符。門檻表由 token 角色產生，比值由測試端獨立實作 WCAG 2.1 公式計算（不引用被測模組的算式） |

## A5 全繁體中文（4 條）

| # | 條文 | 判定方式 | 結果 | 證據位置 |
|---|---|---|---|---|
| A5-1 | webapp 頁面實際渲染後 grep，畫面上沒有英文資料原值 | 本票新增：2 組 fixture × 6 頁 = 12 份渲染後輸出，以 `html.parser` 取可見文字（不讀屬性、跳過 `<style>`／`<script>`） | 通過（12/12 零命中，32 個枚舉值） | `a5-grep.txt`（第三輪重產）；渲染存檔 `rendered/{tw_stock,crypto}-{room,history,settings,run_detail,not_found,closed}.html`。掃描頁面包含辯論室主頁、歷史與命中率合併頁、設定頁、run 詳情頁、404 頁與「已關閉」頁 |
| A5-2 | 「資料原值」＝權威詞彙表已涵蓋的狀態／枚舉值（如 `tw_stock`、`open`、`green`、`hit`、`pending`）不得以英文原樣出現 | 枚舉值集合**由權威推導**而非打字列出 | 通過 | `a5-grep.txt` 列出受掃描的 **32** 個值，來源：`question.ASSET_CLASSES`、`report_contract.CONFIDENCE_LEVELS`、**`run_index.OUTCOME_STATES`**、`report_renderer.CONSENSUS_LABELS`、`debate_state_machine.STANCES_BY_QUESTION_TYPE`（五種題型的立場詞彙全收）。**命中狀態讀 `OUTCOME_STATES` 而非 `OUTCOME_VERDICTS`**（Reviewer A 的 Finding A8-1）：`pending` 與 `unreadable` 是從紀錄推導、不寫進紀錄的兩個狀態，因此不在 verdicts 裡，但畫面照樣顯示，且 Spec A5 明文點名 `pending`；第一輪只掃 30 個值，這兩個植入頁面也抓不到。鑑別力三條：植入 `<p>資產類別：tw_stock｜燈號：green</p>` 命中 `green`、`tw_stock`（`…::EveryPageIsTraditionalChineseTest::test_the_scan_would_notice_an_english_value_on_a_page`）；植入 `<p>命中狀態：pending，另一筆 unreadable</p>` 命中 `pending`、`unreadable`（`…::test_the_scan_would_notice_a_derived_outcome_state_too`）；權威新增狀態自動納入（`…::test_the_scan_covers_every_state_the_authority_declares`）。屬性中的機器值（`value="tw_stock"`）不算畫面上的字，與 Ticket 04 合併頁同一讀法（`test_a_machine_value_in_an_attribute_is_not_a_word_on_screen`） |
| A5-3 | 不在此限（屬資料本體）：標的代號（`2330`、`AAPL`、`BTC`）、`run_id`、`seat_id`、evidence ID | 明列豁免清單，並確認它們**確實出現**在頁面上（否則零命中是空的） | 通過 | `a5-grep.txt` 每頁列出豁免類別的實際出現數：辯論室 標的代號 2、`seat_id` 7、evidence ID 7；run 詳情頁 標的代號 1、`seat_id` 7、evidence ID 7。反向控制：`…::test_the_target_code_stays_as_the_reader_typed_it`（`2330` 必須留在畫面上）、`test_the_run_detail_page_shows_this_runs_market_as_a_chinese_word`（不是空白頁通過） |
| A5-4 | 離線報告版面本次不動，不納入本條驗收；其回歸由既有測試守住 | 依 Spec 明文排除 | 不適用（明示排除） | 排除範圍＝離線報告的**版面**；本票仍驗其席位標籤（A3-1～A3-3）。版面回歸由 `tests/test_report_renderer.py`、`tests/test_report_audit_renderer.py`、`tests/test_verify_run.py` 守住，皆在本次 **2556** 全綠內 |

## 缺陷 D-1（已關閉）：A3-3／A3-1 在報告驗證失敗的退化路徑

- 狀態：**第一輪由本票發現並定位、退回 Ticket 01；Ticket 01 原 Developer 修復，經其兩位原 Reviewer
  delta review 通過關閉；本票第二輪加入跨票回歸測試並重驗通過。**

**現象**：台股 run 的 Core 報告在一次 correction 後仍未通過客觀驗證時，`report_workflow` 產生的
`validation_failed` 報告骨架**沒有 `asset_class` 欄位**。兩個離線 renderer 都以
`report.get("asset_class")` 選套（Ticket 01 的唯一開口），欄位缺席即落到 open 套，
而 open 套沿用幣圈套名稱——於是台股 run 的 `report.html` 與 `debate.html` 印出
「槓桿雷達」「鏈上獵人」，同時 webapp 因為讀 `question.json`（`asset_class = tw_stock`）
仍顯示「籌碼雷達」「資金流獵人」。**A3 第 3 條與 A3 第 1 條的「兩處一致」在這條路徑上同時不成立。**

**重現**（暫存 Data Root，唯讀於 repo）：以本票的端到端 fixture 發一題台股，Core 撰稿替身
回覆 `confidence_level` 不在核准燈號的初稿（`validate_core_narrative` 拒絕，兩次後走
red_audit）。實測輸出：

```
question.json asset_class: 'tw_stock'
report.json has asset_class key: False
report.json consensus_status: validation_failed  process_failure: True
  report.html crypto-only names present: ['槓桿雷達', '鏈上獵人']
  debate.html crypto-only names present: ['槓桿雷達', '鏈上獵人']
report seat labels: {... 'derivatives': '槓桿雷達', 'onchain': '鏈上獵人' ...}
room   seat labels: {... 'derivatives': '籌碼雷達', 'onchain': '資金流獵人' ...}
```

該 run 的 launch 仍以 exit 0／`FINALIZED` 結束，所以這是使用者到得了的狀態。

**根因**：報告有兩個生產端。Core 稿件通過驗證時由 `debate_driver.assemble_market_report` 產生
（Ticket 01 裁定三已補 `asset_class`）；一次修正後仍不通過時改由 `report_workflow` 的紅字稽核
骨架產生，那一個沒補。契約沒有要求這個欄位，所以第二個生產端漏掉它不會被任何人擋下。

### 修復與關閉（Ticket 01 原 Developer，經其兩位原 Reviewer delta review 通過）

- `hoya_market_agents/report_workflow.py`（純追加）：`run_report_workflow`／`build_red_audit_report`／
  `_red_outcome` 增 `asset_class` 接縫，10 個失敗分支呼叫點補欄位。現況 blob `bd93f38b`。
- `hoya_market_agents/debate_driver.py`：`run_core_report` 與 `run_after_seal` 呼叫點接上
  `package.asset_class`。現況 blob `82fe0de5`。
- `tests/test_report_renderer.py`：新增 2 條，含本重現腳本的等價測試
  `test_a_refused_core_report_still_says_which_market_it_was`。現況 blob `8e715041`。
- 修復後全套基準：2548 tests OK (skipped=1)、exit 0。

### 本票的跨票回歸測試（Ticket 08 第二輪新增，3 條）

`tests/test_frontend_redesign_acceptance.py::TheDegradedReportStillKnowsItsMarketTest`。與
`test_report_renderer.py` 那條**刻意不重複**：那邊從 renderer 入口證明骨架帶得出市場；這邊走
使用者的整條路——標的選單發問、跑完一趟真的 run、比對兩份**渲染後**的輸出。契約欄位掉了那邊紅，
任何一端接線掉了這邊紅。退化路徑的形狀（`process_failure`、`validation_failed`、
`validation_errors` 非空）在 fixture 內先斷言，所以三條都不可能在「其實走了正常路徑」的 run 上空過。

### 修復後同一 fixture 重驗（實測輸出）

```
run_id: 20260314T015926Z-2330-aaa111
question.json asset_class: 'tw_stock'
report.json has asset_class key: True  value: 'tw_stock'
report.json consensus_status: validation_failed  process_failure: True  confidence: red
validation_errors: 1 筆
  report.html crypto-only names present: （零命中）
  debate.html crypto-only names present: （零命中）
逐席比對（webapp vs 離線報告）：七席全部 OK，且七席全部等於股票套
```

### 突變驗證（一次性、未修改 repo 任何檔案）

以 `unittest.mock.patch` 在執行期把 `report_workflow.build_red_audit_report` 的答案裡的
`asset_class` 拿掉（產品程式碼完全未動），對這三條測試各跑一次：

```
as shipped: ran=3 failures=0 errors=0
mutated   : ran=3 failures=3 errors=0
   test_a_refused_report_still_records_the_market_the_menu_stated  -> AssertionError: 'tw_stock' != None
   test_a_refused_reports_offline_pages_hold_no_crypto_seat_name   -> AssertionError: '槓桿雷達' unexpectedly found in ...
   test_a_refused_report_is_named_the_same_as_the_room_seat_by_seat -> AssertionError: '籌碼雷達' != '槓桿雷達'
restored  : ran=3 failures=0 errors=0   （build_red_audit_report is the real one again: True）
```

三條各自獨立有效，且三種紅的訊息正好對應 D-1 的三種症狀。

## 突變證明（本票斷言不是恆真的）

| 斷言 | 突變 | 結果 |
|---|---|---|
| webapp ↔ 離線報告逐席一致（正常路徑） | 拿掉台股 run `report.json` 的 `asset_class` 後以 `render_market_html` 重新渲染 | 由全等變成 derivatives（籌碼雷達→槓桿雷達）、onchain（資金流獵人→鏈上獵人）兩席不一致 |
| 退化路徑三條（D-1 回歸） | 執行期 patch `report_workflow.build_red_audit_report`，把答案裡的 `asset_class` 拿掉（未動 repo） | 三條全紅，訊息分別為缺市場、幣圈席名出現、逐席不一致；還原後三條全綠 |
| 三種票數逐立場相等（B8-F2） | 執行期 patch `live.live_snapshot`，把三個票數前後對調（**總和不變**，仍是 7） | 逐立場斷言紅：`{'bullish': 6, 'bearish': 1, 'neutral': 0} != {'bullish': 0, 'bearish': 1, 'neutral': 6}`；同一情境下「只比總和」為 `7 == 7 → True`、「只核對詞彙集合」也為 True——第一輪的兩條會放過它。還原後全綠 |
| 讀取層不綁 markup（B8-F1 反方向） | 把真實辯論室頁面的 **429 個 `class` 屬性拿掉剩 8 個**，並把 `span`／`strong`／`small`／`article`／`p` 五種標籤換名，再重讀 | 票數讀取、逐席名稱讀取、聊天室讀取三者結果與原頁**完全相同**（免疫）；同時上一列證明它對印錯數字仍敏感 |
| A5 掃描含推導狀態（A8-1） | 對 `<p>命中狀態：pending，另一筆 unreadable</p>` 套同一段讀法 | 命中 `pending`、`unreadable`（第一輪的 30 值集合對同一段植入為零命中，即 Reviewer A 的探針結果 `planted_detected=[]`） |
| A5 零命中 | 對 `<p>資產類別：tw_stock｜燈號：green</p>` 套同一段讀法 | 命中 `green`、`tw_stock` |
| 兩套名稱有差別 | `crypto - stock` 名稱集合為空時 | `test_a_taiwan_stock_runs_offline_pages_hold_no_crypto_seat_name` 先以「roster 的兩套名稱已無差別」失敗 |
| 工作區沒有退役檔 | 對著不存在的目錄問「檔案不存在」 | `test_the_check_looks_at_a_directory_that_is_really_there` 擋掉這種假通過 |
| 兩趟 run 各自獨立 | fixture 未讓上一趟假程序收尾時 | 發問鎖使第二次送出拿到「正在進行」頁、無第二個程序，`child_launch` 會讀到第一趟的參數——實際撞到並在 fixture 內修正（`finish_any_earlier_launch`），該情境即是這條的紅 |

## Review Finding 處理（Reviewer A／B 首輪四項）

| Finding | 級別 | 處理 | 證據 |
|---|---|---|---|
| **A8-1** A5 掃描集合漏 `OUTCOME_STATES` 的 `pending`／`unreadable` | 重要 | `enumerated_data_values()` 改讀 `run_index.OUTCOME_STATES`（32 值），並加兩條鑑別力測試：植入這兩個值必須被抓到、權威新增狀態自動納入。`a5-grep.txt` 全部重產、12 頁重驗零命中 | `a5-grep.txt`（32 值清單＋兩組鑑別力）；`…::test_the_scan_would_notice_a_derived_outcome_state_too`、`…::test_the_scan_covers_every_state_the_authority_declares`；矩陣 A5-1／A5-2 已改 |
| **B8-F1** 測試綁 DOM 巢狀／相鄰標籤／精確 class | 重要 | 讀取層整層換掉：以 `html.parser` 取可見文字（不讀屬性、跳過 `<style>`／`<script>`）；區塊用 `id` 這個功能性錨點定位（即時頁腳本綁的同一個 id），不用 class 也不用巢狀；席位逐席判定改為「這一席三套候選名稱裡畫面上出現哪一個」；票數改為「立場詞之後第一個數字」。**刪掉**重複的 `class="light"` 精確斷言（R2 凍結的擁有者是 `test_webapp.ProtectedZoneOuterwearTest`），改為「燈號是權威中文詞、英文原值零命中」 | 突變表第 4 列（429 個 class 拿掉＋五種標籤換名，三種讀取結果完全相同）；`…::test_the_reading_is_not_tied_to_the_markup_that_carries_the_name`、`…::test_the_tally_reading_survives_a_rewrapped_region`；鑑別力未被拔牙——植入控制、反向控制、退化路徑形狀前置斷言全部保留 |
| **B8-F2** 三種票數只比總和 | 重要 | 以 `package.stance_labels` 建「顯示詞彙→`votes.json` 該 stance 票數」映射，逐項精確比較；另加「三個票數不全相等」的前置斷言（否則逐項比對沒有鑑別力）與一條 FP 方向單元測試（總和相同、每項不同要能分辨） | 突變表第 3 列（真實頁面票數對調，總和仍 7：新斷言紅、舊斷言綠）；`…::test_each_of_the_three_tallies_is_the_public_records_own_number`、`…::test_the_tally_reading_pairs_each_word_with_its_own_number`。**沒有發現真的票數錯印**：未突變時畫面逐立場＝`{偏多: 6, 偏空: 1, 方向不明: 0}`＝`votes.json` |
| **B8-F3** 本檔 A5-4 殘留舊計數 2546 | 建議 | 已更新為 2556（第三輪實測），同時全檔計數與輪次一併校正 | 本檔表頭與 A4-2、A5-4 |
