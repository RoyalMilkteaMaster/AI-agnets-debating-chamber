# AI agnets debating chamber

## Language

- **Core Agent**：單一 GPT-5.6 Sol 協調者；負責派工、時間關卡、原文分發、計票與報告內容，不得擅自選邊。
- **Research seat／研究席**：七個固定研究與投票角色之一。席位是邏輯身分，不等同某次模型程序。
- **Attempt／嘗試**：某個研究席的一次實際模型或 CLI 呼叫。
- **Replacement／替補**：原 attempt 失敗後，讀取該席公開歷史並接續工作的模型呼叫。
- **Format Repair Agent**：不投票的輔助 Agent；只能整理既有內容格式，不能補造資料或立場。
- **Evidence card／證據卡**：具有來源、時間、方向、原始數值或短摘錄及可信度說明的最小可追溯證據單位。
- **Evidence Store**：單次 run 的 `evidence.jsonl`；不是向量資料庫或 RAG。
- **Evidence snapshot／證據快照**：一般題 `T+6:00`、兩標的比較題 `T+6:30` 產生，之後不得覆寫的正式證據集合。
- **Claim／論點**：Agent 以 evidence ID 支持或反駁的公開主張。
- **Shared debate room／共享原文辯論室**：七席讀取相同、未經 Core 改寫的論點、回應與投票快照。
- **Provisional vote／暫定票**：研究完成後、第一輪開票前的投票（2026-08-10 起不再以反方挑戰定義）。
- **Valid vote／有效票**（2026-08-10 起）：某輪開票當下該席最新公開立場；「完成反方挑戰才有效」的舊定義退役。
- **Consensus／共識**（2026-08-10 起）：在指定投票輪達到絕對 `7／6／5／4` 同立場票數。
- **No consensus／未達共識**（2026-08-10 起）：硬停結算（封存+360 秒）沒有任何立場取得四張有效票。
- **投票輪／Vote round**（2026-08-10 起）：錨定封存時刻的離散開票：封存+60s 門檻 7、+150s 門檻 6、+240s 門檻 5、+330s 門檻 4、+360s 硬停結算。一般題封存 6:00（即 7:00／8:30／10:00／11:30／12:00），兩標的比較題封存 6:30（整體後移 30 秒）。
- **證據可見性閘門**（2026-08-10 起）：第一輪開票前各席只讀得到自己的證據；第一輪未過才分發全席證據，之後可自由互看與辯論。
- **概述／Message brief**（2026-08-10 起）：直播訊息預設只顯示 `public_reason` 第一句（30–60 字核心結論），其餘摺疊展開；伺服器是唯一斷句處，頁面與 SSE 共用同一份概述。
- **READY**：所有主要模型、搜尋、權限、路徑、Skill、資料寫入與報告能力均通過賽前 fail-closed 預檢。
- **Run**：從單一題目啟動至報告完成的一次完整分析，使用唯一 `run_id`。
- **燈號／Light**（2026-08-05 起）：最終採納立場有效票數的直接映射——7 藍、6 綠、5 黃、4 橘、少於 4 紅；僅「獨立網域少於 2」與「引用低可信來源（輿情席豁免）」各降一級。
- **盲投直過／Unanimous blind pass**（2026-08-05 起）：opening 盲投收齊即 7/7 同立場時直接停止產藍燈報告，不進辯論輪；2026-08-10 四輪投票制下保留，不必等第一輪牆。
- **Debate rules／辯論規則檔**：`config/debate_rules.json`，時間門檻、票數階梯與燈號規則的唯一來源；程式與驗證器不得複製字面值。
- **Run index**：`_data/runs/index.db`（SQLite），可重建的衍生查詢索引；run artifact 仍是唯一事實來源。
- **事後驗證／Outcome tracking**：預測到期後由報價 API（或手動輸入）記錄實際結果到該 run 的 `outcome.json`，用於燈號命中率統計；報價 API 永不進研究管線。
- **席位方向套組／Seat profile set**（2026-08-09 起）：七席的顯示名稱與研究方向依 run 的資產類別選套——股票套（台股＋美股共用）、幣圈套（原有語彙）、通用套（開放題）；`seat_id` 與 `output_dir` 永不隨套組改變。
- **基本面研究員**（2026-08-09 起）：第七席的新職能（股票看營收／財報／估值、幣圈看 TVL／解鎖日曆／協議收入、開放題做關鍵數據查核）；其 `seat_id` 仍是歷史名 `counter-evidence`。2026-08-10 起顯示名分流：股票套「基本面分析師」、幣圈套「項目體質分析師」、開放套仍「基本面研究員」。
- **席位名稱定案**（2026-08-10 起）：股票套與幣圈套顯示名稱全面換為使用者自取名（定案表見 `docs/planning/requirements.md`）；`seat_id`、`output_dir`、提供者、`focus` 均不變。
- **席位白話說明**（2026-08-10 起）：即時辯論頁席位卡下方僅供顯示的一句白話（「他看哪方面資訊」）；不是 `focus` 權威，不得回寫入研究方向。
- **開放題下架**（2026-08-10 起）：發問選單僅台股／美股／幣；`open` 資產類別與開放套在後端與 roster 保留（fail-closed 檢查仍須齊全），僅不可從 UI 發問，歷史開放題 run 照舊可回看。
- **導覽注入**（2026-08-10 起）：離線報告的五導覽由伺服器回應時插入（ADR 0007），磁碟檔案不動；直接開檔或分享時維持自足兩分頁導覽。
- **標的選單**：發問時以選單選定資產類別與標的，取代純文字解析；接 T05 的 `assets`／`asset_class` 接縫。
- **歷史與命中率頁**（2026-08-09 起）：原歷史查詢頁與命中率統計頁合併成的單一頁面。
- **WSL Runtime**（2026-08-12 起）：正式執行 webapp、Python controller 與全部 Provider CLI 的唯一產品 Runtime；正式支援環境為 Windows 10／11 上的 WSL2 Ubuntu。
- **Windows 薄入口**（2026-08-12 起）：只負責以桌面捷徑呼叫 `wsl.exe` 啟動或關閉 WSL Runtime 的 Windows 管理入口；不得執行 Provider CLI。
- **Provider canary**（2026-08-12 起）：正式七席驗收前，針對單一 WSL Provider 執行的最小真實呼叫，用來提早暴露安裝、登入、搜尋證據或輸出契約問題。

## Relationships

- 一個 Run 固定包含七個 Research seats。
- 一個 Research seat 可因重試或替補包含多個 Attempts，但任何時間只有一張有效票。
- Attempts 產生 Evidence cards；驗證後由單一寫入者形成 Evidence snapshot。
- 七席根據同一 Evidence snapshot 在 Shared debate room 交換 Claims；2026-08-10 起此交換受證據可見性閘門管制，第一輪開票前各席只據自己的證據投票。
- Valid votes 決定是否達成 Consensus；Core Agent 依實際票數與證據撰寫報告。
- Python controller 管理時間、程序、驗證、合併與排版，不產生市場方向。
- Windows 薄入口、WSL／Ubuntu 終端與 MobaXterm 都只能操作同一個 WSL Runtime；不得形成第二套 Windows Provider Runtime。
- 既有 Data Root 持續保存所有歷史 run、報告與設定；新版 Code Root 不得在安裝或更新時搬移、刪除或重新格式化它。

## Flagged ambiguities

- 「Agent」容易同時指研究席與實際模型程序；正式文件分別使用 Research seat 與 Attempt。
- 「Gemini CLI」已不再是個人 Google AI Ultra 的執行工具；本專案使用 Antigravity CLI 承載 Gemini Pro 席。
- 「信心」描述結論可靠程度，不代表價格上漲幅度或方向強度。
- 「中性」是有理由的市場立場，不等同流程失敗；少於四張有效票才是分析失敗。
- `research` 指固定、已記錄 commit 與雜湊的 Skill snapshot，不指執行時從 GitHub 即時下載的內容。
- 「支援 Windows 10／11」只表示 Windows 是 WSL2 Ubuntu 的宿主與桌面捷徑所在處，不表示正式支援 Windows 原生 Provider Runtime。
- 2026-08-05 起「燈號」代表共識強度（票數），不再代表證據廣度；證據品質把關交給七席辯論，僅剩兩條來源降級。舊報告的 `yellow_green` 級已退場。
- 「反證」有兩義：全席共用的「第一輪反方挑戰」機制（2026-08-10 起正式關卡退役，由資料交換＋重新判斷＋自由辯論取代）與第七席的舊研究職能（2026-08-09 起退役，改為基本面研究）。`seat_id: counter-evidence` 是歷史識別碼，不代表現職能。
