# 賽後續用整改（post-competition-refit）

- 依據：`docs/planning/architecture.md` §11（2026-08-05 核准）、`docs/architecture-reviews/2026-08-05-hoya-bit-refactor.html`（方案 A）、ADR 0003／0004／0005
- 本 Spec 是本次整改的唯一規格來源；與其他文件衝突時以本 Spec 與 architecture.md §11 為準

## 問題

比賽已結束，系統要轉為日常續用工具，但現況有五類阻礙：

1. 分析標的被五幣白名單（`SUPPORTED_ASSETS`）鎖死，台股／美股／其他幣種一律拒收。
2. 投票規則沒有「第一輪盲投 7/7 直過」層級；燈號被多層證據品質規則降級且存在 elif 級聯 bug（7 票可能被 6 票層級規則誤降），票數與燈號對不上；規則全是硬編常數，使用者無法自行調整。
3. Data Root 堆滿測試殘留（36 個 run、六份 presentation 快照、coordination／adjustment-audit 等孤兒目錄），且 run 目錄扁平命名難以人工瀏覽、無法查詢歷史。
4. 直播頁是比賽用完即棄的 `live_dashboard.py`，沒有常駐入口：不能查歷史、不能在頁面上提問啟動、不能改設定、不能追蹤預測命中率。
5. 舊 repo、worktrees、site 等 sibling 殘留 38MB 廢棄內容。

## 目標

1. 標的三類全開：所有虛擬貨幣＋台股＋美股＋開放命題，自動訂定正反面。
2. 投票新制：R1 盲投 7/7 直過（藍燈）→ 共享辯論 6 票 → 時間降階 5／4；燈號純票數制＋兩條來源降級；全部規則設定檔化、前端可改。
3. Data Root 清空重整：日期分層資料夾＋SQLite 查詢索引。
4. 常駐本機前端：歷史查詢、提問啟動、聊天室直播、設定頁、事後驗證追蹤。
5. 每個 Phase 結束測試基準全綠（681 案例起跳，只增不減）。

## User Stories

1. 身為使用者，我希望輸入「幫我分析 2330 這張股票未來七天會不會漲」就能啟動七席分析，以便不再受限於五種幣。
2. 身為使用者，我希望系統自動辨別任何問題的正方／反方命題，以便不用自己定義立場詞彙。
3. 身為使用者，我希望七席第一輪互不可見地盲投、7/7 一致就直接產藍燈報告，以便最強共識能被最快辨識。
4. 身為使用者，我希望未達全票時七席互看資料、互相質疑說服直到達到門檻，以便結論經得起對抗檢驗。
5. 身為使用者，我希望燈號直接等於票數（7藍/6綠/5黃/4橘/<4紅、來源不可信才降級），以便一眼判讀共識強度。
6. 身為使用者，我希望在一個設定頁（或設定檔）改時間門檻與票數階梯，以便不用改程式碼。
7. 身為使用者，我希望歷史 run 按日期＋題目存放並可用資料庫查詢，以便快速找到過往分析。
8. 身為使用者，我希望隨時打開一個本機網頁就能提問、看直播、查歷史、看命中率，以便日常使用這套系統。
9. 身為使用者，我希望預測到期後系統自動對答案並統計各燈號命中率，以便知道這套系統到底準不準。

## 需求與行為

### Phase 0：清理（一次性受控）

- 刪除前先把 `hoya-bit-market-agents_data` 整包 zip 備份到 `D:\workstationD\hoya bit\backups\`。
- 刪除 `_data` 下：`runs/` 全部（含比賽三場）、`presentation-v2`~`v7`、`coordination/`、`adjustment-audit/`、`inbox/` 全部歷史、`logs/live-server.log`、`preflight/` 內 `ticket11-*` 與 `final-real-not-ready`。
- 保留：`preflight/latest-ready.json` 與其對應時戳憑證目錄。
- Sibling：程式化驗證 `-final` 含舊 repo 全部 commit（git log 比對）後，刪除 `hoya-bit-market-agents`、`hoya-bit-market-agents_worktrees`（git 連動，必須同組）、`hoya-bit-site`。
- 驗收煙霧測試：清理後 fixture launch 全流程可跑通。

### Phase 1：規則設定檔化（行為不變）

- 新增 `config/debate_rules.json`，內容＝現行常數的等價搬移：辯論起點沿用研究封存時刻、第一輪相對窗（180s）、6→5 切換時刻（T+8:00）、最終輪起訖（T+8:45／9:45）、強停（T+10:00）、門檻階梯（6/5/4）、燈號映射與降級規則。
  - **2026-08-05 使用者裁定修訂**：燈號映射與降級規則的搬移**順延至 Phase 2（Ticket 04）**，Phase 1 只在設定檔內預留有明確鍵名與型別校驗的欄位結構。理由：Ticket 04 隨即會把燈號全面改為純票數新制，在 Phase 1 先搬入舊制規則等於做一次即刻被重寫的工，且會擴大 Phase 1「行為零變化」的回歸風險。Ticket 02 的驗收條件已同步修訂。
- `debate_state_machine` 改為由載入器取得規則；driver、`run_verifier`、測試讀同一來源，不複製字面值。
- 載入 fail-closed：欄位缺漏、時間非遞增、票數非法即拒絕啟動並回報具體錯誤。
- 驗收：預設設定下全部既有測試綠（行為零變化）。

### Phase 2：投票與燈號新制

- 盲投直過：opening（互不可見）收齊後，7/7 同立場即停止，`stop_reason=unanimous_blind_pass`，直接產報告；未全票則進入現行共享辯論流程（6 票停、T+8:00 後 5 票、T+10:00 強停 4 票採納、<4 未達共識）。
- 質疑與說服：沿用 challenge 對立配對、scrutiny 輪替、改票必附理由與 `vote_changes` 全程記錄；辯論 prompt 強化「你的目標是說服對立席位改票，使己方票數達到當前門檻」語意。
- 燈號（ADR 0003）：基準 7藍/6綠/5黃/4橘/<4紅（新增 `blue` 級，移除 `yellow_green`）；降級①採納立場引用來源少於 2 個獨立網域→降一級；降級②引用含非 tier 1/2 來源→降一級（social-macro 席證據豁免）；移除類別數級聯與 30 天時效降級。
- 同步範圍：`confidence_cap`、報告驗證、Core 撰稿上限提示、renderer 樣式（藍色＋圖示＋文字）、`run_verifier`。

### Phase 3：標的三類全開

- 移除 `SUPPORTED_ASSETS` 白名單；題目 intake 改為資產類別偵測：`crypto`（全幣種）／`tw_stock`／`us_stock`／開放命題，皆可分析；無法歸類走 open 模式，不拒收。
- `open_proposition` 命題訂定（Core 以 codex exec 無搜尋產生正方／反方／無法決定詞彙）升為所有題型的主路徑；失敗 fallback 題目原文。
- 新增 `config/market_scopes.json`：各資產類別語意提示（代號解析如 2330→台積電/TSMC/2330.TW、交易時段語意如台股週末休市、美股盤前盤後），經 `build_attempt_prompt` 唯一入口注入七席。
- 研究仍由七席自行搜尋；Python 不抓行情（唯一破例見 Phase 5 事後驗證）。

### Phase 4：run 日期分層＋SQLite 索引（ADR 0005）

- run 目錄：`_data/runs/YYYY-MM-DD/HHMM-題目slug-hash/`（日期夾用 Asia/Taipei；run_id 內部含 UTC 時戳保證唯一）；run 內部檔案契約不變。
- `latest.json` 格式保留、指向新分層路徑；所有讀者同步。
- 新增 `run_index` 模組：`_data/runs/index.db` 唯一寫入者；FINALIZED 後 upsert（run_id、日期、題目原文、slug、資產類別、標的、題型、燈號、採納立場、票數分佈、共識狀態、報告路徑、事後驗證結果）；提供 backfill 命令全量重建；索引寫入失敗不阻擋 run 完成。

### Phase 5：常駐前端＋事後驗證（ADR 0004）

- `webapp` 模組：本機 127.0.0.1 常駐伺服器（stdlib http＋SSE，零外部套件），單一入口吸收直播後移除 `live_dashboard`；launch 不再另開舊直播頁。
- 功能：
  - 歷史查詢：依日期／標的／燈號／關鍵字查 index.db，點入 run 詳情（報告、七席投票、證據卡、辯論過程）。
  - 提問啟動：頁面輸入問題→呼叫 launch 管線→即時進度（七席狀態、輪次、票數變化、最終燈號）。
  - 聊天室直播：沿用 v5-chat 版面語彙（頭像＋氣泡＋輪次分隔）呈現七席發言，SSE 即時推送；繼承唯讀直播邊界（不顯示隱藏思考、直播故障不影響 run）。
  - 設定頁：讀寫 `debate_rules.json`，寫入前 fail-closed 驗證，非法值拒絕並顯示原因。
  - 事後驗證：伺服器運行時掃描到期 run→`quote_api_client`（免費公開報價 API 唯一介接點）取實際價格→寫該 run `outcome.json`（write-once 新 artifact）→更新 index.db；API 失敗提供手動輸入；統計頁顯示各燈號命中率。
- 正式 Log（僅 webapp 範圍）：JSONL 寫 `_data/logs/webapp.jsonl`，欄位 timestamp/level/event/source/message，日期輪替、保存 30 天、啟動時清逾期；記伺服器啟停、請求錯誤、launch 觸發、到期檢查與報價失敗；不記機密。
- UI 品質：重新設計視覺（比賽版只求能用）；基礎 a11y（語意標籤、鍵盤可操作、足夠對比）。
- exe 打包：不在本輪（功能定案後另案）。

## 實作決策

- **資料與所有權**：run 目錄仍由 `run_store` 單一寫入（write-once／append-only 語意不變）；`index.db` 由 `run_index` 唯一寫入、可重建、非事實來源；`debate_rules.json` 由使用者／設定頁寫，程式啟動時載入驗證；`outcome.json` 是 run 目錄新增的 write-once artifact。
- **模組責任與公開介面**：`debate_rules` 載入器（讀檔→驗證→凍結物件，全系統唯一規則來源，比照 `research_deadlines` 模式）；`run_index`（upsert／backfill／query）；`quote_api_client`（依資產類別解析報價來源，僅事後驗證可呼叫）；`webapp`（http 路由＋SSE 推送＋到期檢查，業務不進 route）；`question` intake（白名單改類別偵測）；`market_scopes` 經 `build_attempt_prompt` 注入。
- **Schema 與系統互動**：報告 schema 的 confidence 級別集合改為 red/orange/yellow/green/blue；`votes.json` 新增 `unanimous_blind_pass` 停止原因；index.db 單表起步；`debate_rules.json` 與 `market_scopes.json` 附範例與驗證器。
- **相容、遷移與技術限制**：零外部套件維持（sqlite3／http.server／urllib 皆 stdlib）；Phase 1 行為零變化是硬驗收；舊 run 全刪故無資料遷移；`latest.json` 讀者同步改版；`live_dashboard` 移除屬核准的 userspace 變更（由 webapp 等價功能取代）。

## 驗收條件

1. 問「2330 未來七天會不會漲」：系統接題、自動產生正反面命題、七席完成研究辯論、產出報告（fixture 與 real 各驗一次）。
2. 純 crypto 題（如 DOGE）、美股題（如 NVDA）、開放命題各一題 fixture 驗證可跑通。
3. 七席盲投全同立場的 fixture：不進辯論、`stop_reason=unanimous_blind_pass`、報告藍燈。
4. 6/5/4 票 fixture：燈號分別綠／黃／橘；3 票以下紅且標未達共識。
5. 來源降級 fixture：單網域 7 票→綠（降一級）；含低可信來源→降一級；同樣證據掛在 social-macro 席→不降。
6. 改 `debate_rules.json` 的門檻時刻（如 6→5 切換提前），狀態機與 verifier 行為跟著變；填非法值（時間倒序）系統拒絕啟動並說明原因。
7. Phase 0 後：`_data` 只剩 `preflight/`（含 latest-ready.json）與空的執行期目錄；備份 zip 存在於 `backups/`；fixture launch 煙霧測試通過；sibling 三目錄已刪且 `-final` git log 驗證紀錄留存。
8. 新 run 落在 `runs/YYYY-MM-DD/HHMM-slug-hash/`；index.db 查得到該 run 完整欄位；刪掉 index.db 後 backfill 重建結果一致。
9. 打開 `http://127.0.0.1:<port>`：可查歷史、開 run 詳情、送出問題啟動分析、看到聊天室直播即時更新、在設定頁改規則、看到命中率統計。
10. run 到期後 `outcome.json` 出現實際價格與方向判定，統計頁命中率更新；報價 API 斷線時可手動輸入。
11. 每 Phase 結束 `python3 -m unittest discover -s tests`（WSL）全綠，案例數不少於 681。

## 測試決策

- **公開行為**：測狀態機停止原因與票數→燈號映射、intake 類別偵測、規則載入器 fail-closed、index upsert/backfill 等價性、webapp 各路由回應與 SSE 事件、quote 解析與 outcome 寫入。
- **測試接縫**：沿用注入時鐘與 fake provider；新增：暫存目錄的 index.db、假 HTTP 回應的 quote client、直接呼叫 handler 的 webapp 測試、暫存 `debate_rules.json` 變體。
- **既有測試模式**：unittest、分鐘壓縮成秒驗狀態轉換、測試不碰正式 Data Root。
- **不應耦合**：不斷言 HTML 樣式細節與文案、不斷言 SQLite 內部儲存格式、不斷言報價 API 原始回應結構（只測解析結果）。

## 不在範圍內

- exe 打包與發布給他人（功能定案後另案）。
- 向量資料庫／RAG／自動交易／付費 API。
- 研究管線抓行情（事後驗證的報價 API 是唯一破例）。
- 舊 run 資料遷移（已核准全刪）。
- 多人同時使用與遠端部署（僅 127.0.0.1 本機）。

## 補充

- 測試基準現況：2026-08-05 實測 681 案例全綠（26.8s）。
- 比賽遺留的 GitHub Issues 流程停用，Spec／Tickets 以本目錄為唯一真相。
- 輿情席豁免的席位識別以 `config/agent_roster.json` 的 social-macro 席位 ID 為準。
