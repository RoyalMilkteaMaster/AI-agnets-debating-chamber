# AI agnets debating chamber 架構紀錄

- 狀態：已核准
- 核准日期：2026-08-01
- 最後更新：2026-08-05（§11 賽後續用整改核准架構；§1 Code Root 更正為實際 repo）
- 來源：`$milktea-skills-grill-me` 架構階段逐項核准決策；2026-08-05 起加入 Brownfield 整改核准（報告：`docs/architecture-reviews/2026-08-05-hoya-bit-refactor.html`）
- 規則：本文件只記錄已核准架構；尚未決定的項目明列為「未確認」。§11 與先前章節衝突時，以 §11 為準（比賽已結束，系統轉為日常續用）

## 1. 專案與根目錄

黑客松產品與通用開發流程插件分離：

```text
D:\workstationD\AI agnets debating chamber\
├─ milktea-agents-skills-for-codex\   # 通用 Codex 開發流程插件，不放黑客松產品程式
├─ AI-agnets-debating-chamber\ # Code Root
└─ AI-agnets-debating-chamber_data\ # Data Root
```

- Code Root：`D:\workstationD\AI agnets debating chamber\AI-agnets-debating-chamber`
- Data Root：`D:\workstationD\AI agnets debating chamber\AI-agnets-debating-chamber_data`
- Runtime Root：第一版不建立。
- Claude、Codex 與 Antigravity CLI 使用系統或 WSL 已安裝位置，由賽前預檢驗證，不複製到專案內。
- 程式碼、設定範例、Skills、測試與長期文件放入 Code Root。
- 現場題目、Agent 輸出、證據、辯論、票數、報告與 Log 放入 Data Root，不提交 Git。

## 2. 執行架構

採用「Codex 專用 Skill＋WSL Python 控制程式」：

```text
Codex 專用市場研究 Skill
└─ GPT-5.6 Sol Core Agent
   ├─ 直接啟動 3 個 GPT-5.6 Sol Sub-agents
   └─ 呼叫 WSL Python 控制程式
      ├─ 啟動 3 個 Claude Opus
      └─ 透過 Antigravity CLI 啟動 1 個 Gemini Pro
```

已確認環境事實：WSL Ubuntu 24.04 有 Python 3.12.3；第一版 Python 控制程式只使用標準函式庫。

Core Agent 責任：

- 驗證題目是否在核准範圍。
- 派發七個研究席位。
- 將相同且未改寫的證據與辯論快照交給七席。
- 依時間關卡收集投票、判定是否達到絕對門檻。
- 依核准資料產生報告，不得擅自選邊或修改少數意見。

WSL Python 控制程式責任：

- 使用單調時鐘記錄正式時間關卡。
- 啟動、監控、終止與重試外部 CLI 程序。
- 驗證檔案格式與必要欄位。
- 合併各席輸出、產生不可變快照並記錄內容雜湊。
- 保存實際執行狀態與錯誤。
- 不做市場方向判斷、不代替 Agent 投票、不修改 Agent 原文。

Gemini provider 修正：

- 個人 Google AI Pro／Ultra 帳戶自 2026-06-18 起不能再用 Google 登入 Gemini CLI。
- 使用者已確認持有 Google AI Ultra；第七席改用 Antigravity CLI 的 Gemini Pro。
- 賽前必須以實際登入及 `agy models` 驗證可用模型、非互動呼叫與額度，不只依方案名稱判定。
- 正式執行紀錄必須保存實際解析到的模型名稱；不得只寫泛稱 `Gemini Pro`。

## 2.1 Code Root 最小結構

專用市場研究流程採 repo-local Skill，不封裝為全域 Codex Plugin。Codex 必須從 Code Root 開啟新的 Task，才能載入專案 Skill。

```text
AI-agnets-debating-chamber\
├─ AGENTS.md
├─ CONTEXT.md
├─ .agents\
│  └─ skills\
│     ├─ hoya-market-research\
│     │  └─ SKILL.md
│     └─ research\
│        └─ SKILL.md
├─ execution\
│  └─ hoya_market_agents\
│     ├─ run_controller.py
│     ├─ run_store.py
│     ├─ claude_cli.py
│     ├─ antigravity_cli.py
│     └─ report_renderer.py
├─ config\
│  ├─ agent_roster.json
│  ├─ source_policy.json
│  └─ schemas\
│     ├─ evidence-card.schema.json
│     ├─ agent-position.schema.json
│     └─ report.schema.json
├─ tests\
└─ docs\
   └─ planning\
```

- `AGENTS.md`：長期有效的專案 Agent 規則。
- `CONTEXT.md`：只保存專有名詞、關係與歧義，不保存完整開發紀錄。
- `docs/planning/`：需求與架構規劃紀錄。
- 不建立前端、後端 API、資料庫、模糊 `utils`／`manager` 或未使用的空目錄。

## 3. MVP 資料處理

第一版不加入：

- 向量資料庫。
- 向量 RAG。
- FinGPT。
- 會自行計算市場方向的複雜評分引擎。

第一版資料流：

```text
七席分面研究
→ 結構化證據卡
→ evidence.jsonl
→ 來源、時間、格式與重複檢查
→ 一般題 T+6:00（兩標的比較題 T+6:30）不可變證據快照
→ 七席共同閱讀完整快照
→ 共享原文辯論與投票
→ Core Agent 報告
```

`evidence.jsonl` 是 MVP 的 Evidence Store。只有在實測證據量超過模型上下文、且簡單去重與排序無法解決時，才重新評估 RAG。

規則程式只處理可客觀驗證的規則：

- 時間關卡。
- 來源等級。
- 必要欄位與格式。
- 重複證據。
- 絕對票數門檻。
- 信心燈號上限。
- 失敗與替補紀錄。

## 3.1 資料蒐集邊界

- MVP 不建立集中式市場 API 收集器。
- Python 不爬網站，也不預先抓取價格、成交量、OI 或 Funding baseline。
- 七席依共同 `research` 規則自行蒐集、引用與交叉檢查資料。
- Python 只驗證提交證據的格式、時間、來源欄位與重複關係。
- 若端到端實測證明 Agent 經常抄錯同一類數值，才針對該單一缺口評估最小公開 API 呼叫。

## 4. 寫入所有權與不可變快照

七席不得同時直接寫入同一個共享檔案。每席只寫自己的目錄，由單一合併者產生共享檔案。

核准的邏輯結構：

```text
<Data Root>\runs\<YYYY-MM-DD>\<HHMM-題目slug-hash>\   # 目錄命名見 §8.4
├─ run_manifest.json
├─ agents\
│  ├─ spot-technical\
│  ├─ derivatives\
│  ├─ onchain\
│  ├─ official-events\
│  ├─ news\
│  ├─ social-macro\
│  └─ counter-evidence\
├─ evidence.jsonl
├─ debate.jsonl
├─ events.jsonl
├─ votes.jsonl
├─ report.html
├─ debate.html
└─ report.md
```

規則：

- 每席只擁有自己的輸出目錄。
- Python 驗證各席輸出後，由單一寫入者合併共享紀錄。
- 一般題在 `T+6:00`、兩標的比較題在 `T+6:30` 產生不可修改的證據快照。
- 已被引用的證據不得覆寫；修正必須新增更正紀錄並指向原證據 ID。
- 共享快照與 Agent 發言保存內容雜湊，以證明 Core 未改寫原文。

### 4.0.1 即時可視化邊界

- `events.jsonl` 只追加七席已發布的公開事件，直播頁不得顯示模型隱藏思考過程。
- Python 標準函式庫 HTTP server 只監聽 `127.0.0.1`；前端以 SSE 長連線非同步接收公開事件。
- 新發言、改票與規則切換才由伺服器推送；倒數由瀏覽器本地更新，不使用固定間隔 HTTP 輪詢。
- 直播頁顯示票數變化、倒數、當前門檻、Agent 公開理由與 evidence ID，不參與投票或報告判斷。
- `report.html` 與 `debate.html` 是正式稽核 artifact，維持無 JavaScript、無 CDN、可離線列印。
- 直播故障不得破壞研究流程；run artifact 仍是唯一事實來源。

檔案名稱與子目錄仍可在 Schema 與 CLI contract 核准時微調；寫入所有權及不可變原則不可自行更改。

## 4.1 研究席權限邊界

七個研究席使用最小權限：

允許：

- 搜尋與讀取公開網路資料。
- 讀取題目、固定版本 research skill、證據快照與辯論快照。
- 寫入自己的 Data Root 席位目錄。

禁止：

- 修改 Code Root。
- 修改其他 Agent 的檔案。
- 直接修改 `evidence.jsonl`、`debate.jsonl` 或正式票數紀錄。
- 安裝套件。
- 把來源網頁內容當成系統指令並執行。
- 讀取其他專案或使用者私人檔案。

只有 Python 單一寫入者能合併共享紀錄；只有 Core Agent 能產生報告內容。實際 Claude、Codex 與 Antigravity tool policy 必須在預檢與測試中證明符合此邊界。

## 5. 共享原文辯論室

隔離寫入不限制互相閱讀。跨模型交流採回合式共享快照：

```text
每席寫入自己的論點／回應
→ 單一合併者原文合併
→ 產生相同的 debate round snapshot
→ 七席讀取同一份內容
→ 以 Agent ID、claim ID 或 evidence ID 直接回應
```

- Core Agent 不摘要、不篩選、不改寫發言。
- 每則發言保存 Agent 身分、時間、目前立場、公開理由、證據 ID、回應目標與改票原因。
- 所有模型收到完全相同的已合併辯論內容。
- Agent 不交換或要求隱藏思考過程；只交換可稽核的公開理由、證據與反駁。

## 5.1 Agent Session 連續性

採用 provider-aware 的混合 session 策略：

- 3 個 GPT-5.6 Sol 席維持各自的 Codex Sub-agent thread。
- 3 個 Claude Opus 席各自使用固定 Claude session UUID，後續回合以 `--resume` 延續。
- 1 個 Gemini Pro 席透過 Antigravity CLI 非互動呼叫；每輪建立新的模型呼叫，並完整載入該席先前公開立場、證據與共享辯論。
- Gemini 席是相同的邏輯席位，但不得宣稱為同一個持續中的 CLI session；每次實際 invocation 都要記錄。
- 所有可稽核狀態以 Data Root 的不可變檔案為準，不依賴模型未公開或無法驗證的隱藏記憶。
- Provider session 中斷時，依替補規則處理並在報告揭露；不得把新模型程序偽裝成原 session。

## 5.2 研究 Timeout 與替補

> **2026-08-11 使用者核准修訂：研究再延長兩分鐘，並強制收尾交卷。**
> 一般題可發起新搜尋至 T+5:20，T+5:50 停止收件，T+6:00 封存；兩標的比較題
> 三道牆全部後移 30 秒。停止搜尋後不得再找新資料，必須在收件牆前提交已取得內容；
> 不足三張卡仍須交回。此修訂取代本節較早的 T+3:50／4:00 時刻。

> **2026-08-02 使用者核准修訂：研究 4 分鐘、辯論延長。**
> 理由：真實 run `20260802T022316Z-btc-dc3d33` 證明原本 T+5:00 之後的 90 秒
> 塞不下兩次真實 CLI 呼叫（三席 Claude timeout、三席 codex `deadline_missed`、
> 七席零有效票）；而且展示的主秀是辯論，不是蒐證。

> **2026-08-02 使用者核准修訂（Ticket R7）：兩幣比較題 +30 秒。**
> 理由：真實 run `20260802T040230Z-btc-eth-4448e8` 證明比較題的研究負擔是單幣
> 題的兩倍，T+4:00 封存下只有 3/7 席交卷。只有收件牆與封存兩個時刻依題型分流，
> 其餘里程碑四型共用，辯論的絕對牆一律不動。

```text
T+0:00  七席全部啟動
T+0:30  未成功啟動的席位立即以相同模型重試
T+1:30  開放可信二手來源
T+2:00  每席必須留下第一份研究 checkpoint
T+2:00  checkpoint 存檔後，尚未交出有效研究結果的席位立即啟動替補（2026-08-13 起，原 T+2:35）
T+5:20  停止發起新搜尋，立即整理既有資料           ← 兩標的比較題為 T+5:50
T+5:50  停止接收研究結果，開始最終格式驗證與合併   ← 兩標的比較題為 T+6:20
T+6:00  產生不可變證據快照並進入投票與辯論        ← 兩標的比較題為 T+6:30
```

- 時刻的**唯一權威**是 `research_scheduler.research_deadlines(question_type)`，
  回傳 frozen `ResearchDeadlines{accept_until_ms, seal_ms}`；scheduler、Claude
  研究 timeout、辯論起點、`verify-run` 與直播頁一律查它，不得複製字面值。
- 未宣告或未知題型退回一般題預設（T+5:20／T+5:50／T+6:00），因為晚封存要有明確理由。

- 任何時間發生明確程序或 provider 錯誤，都立即重試，不等待 deadline。
- `T+2:00` 替補時（checkpoint 存檔之後、同一里程碑內），尚未用過重試的席位先使用相同模型；相同模型已重試失敗時改用其他可用模型。
- 同一席的原程序與替補都成功時，只採用第一份通過格式驗證的結果。
- 未採用的重複結果保存為診斷資料，不加入共享證據、辯論或投票。
- `T+5:20` 後不得發起新搜尋；在 `T+5:50` 後完成的研究輸出不進入該次正式分析。
- 每席研究期間持續把證據卡寫入自己的目錄；`T+2:00` checkpoint 供同模型重試或替補接續，不直接產生額外票數。
- 原程序失敗時，替補從該席 checkpoint 接續，仍受每席最多 8 張正式證據卡的總上限約束。
- Claude、Codex 與 Antigravity 研究呼叫的 timeout 一律由該題型收件牆前 5 秒推導：
  一般題 345 秒，兩標的比較題 375 秒。這是程序回收上限；提示詞另要求在搜尋停止牆
  主動停止新增搜尋並整理交卷。

## 5.3 辯論回合與計票時間

2026-08-10 起使用離散四輪投票；2026-08-11 只平移封存絕對時刻，規則檔內的
封存相對 offset 與門檻維持不變：

```text
T+6:00       公布證據快照並收集 opening（兩標的比較題為 T+6:30）
T+7:00       第一輪開票，門檻 7
T+8:30       第二輪開票，門檻 6
T+10:00      第三輪開票，門檻 5
T+11:30      第四輪開票，門檻 4
T+12:00      強制停止；4 票採用，無 4 票則輸出未達共識
```

- 七席 opening 全到且 7/7 同立場時可盲投直過；否則在離散牆開票。
- 每輪每席提交：接受或反駁的 claim ID、目前立場、公開理由、evidence ID，以及改票原因。
- 達到當下有效的絕對門檻時立即停止後續辯論，開始報告，不為了填滿時間增加回合。
- Opening 可使用到第一輪開票牆，牆內回覆都必須採用；只有後續自由辯論 turn 停在下一道牆前 5 秒，保留 relay 與計票時間。
- 辯論起點與所有輪牆都跟著該 run 實際封存時刻走，所以兩標的比較題整體後移 30 秒。

## 5.4 有效票生命週期

- 封存後的初始票是暫定票；除 7/7 盲投直過外，要到離散開票牆才計入正式共識。
- 有效票是開票牆當下該席最新公開立場，不再要求完成反方挑戰。
- 第一輪後不要求每輪重新投票；最後一次有效票持續有效，直到該席明確改票。
- Agent 中途斷線時，Core 優先恢復原 session；無法恢復時依替補規則派出替補。
- 替補必須讀取完整證據、該席公開歷史與共享辯論後，才能確認或改變該席的票。
- 報告必須標示每席最後參與回合、是否中途離線、是否由替補確認或改票。
- 未在開票牆前留下有效公開立場的席位不產生有效票。

## 6. Research Skill 供應方式

- 使用 `mattpocock/skills` 的 `research` skill 作為七席共同研究紀律。
- 比賽前在 Code Root 固定一份已驗證版本。
- 記錄上游網址、commit SHA 與檔案雜湊。
- 執行時由 Prompt Builder 將同一份規則加入七席任務，再附加各席專責研究範圍。
- `run_manifest.json` 記錄該次使用的 research skill 版本與雜湊。
- 正式 15 分鐘內不從 GitHub 下載或更新 Skill。

上游來源：<https://github.com/mattpocock/skills/blob/main/skills/engineering/research/SKILL.md>

## 7. 證據卡

每席目標提交 3 至 8 張有效證據卡，最多 8 張；找不到三張時必須標示資料不足，不得虛構。

每張證據卡至少包含：

- 證據 ID。
- 事實、事件或原始數值。
- 支持、反對或中性分類。
- 來源網址或 API 來源。
- 發布時間與取得時間。
- 來源等級。
- 必要短原文或原始數值。
- 蒐集 Agent。
- Agent 對可信度與限制的公開說明。

其他規則：

- 每席至少嘗試尋找一項反駁自己初步立場的證據。
- 七席在合併前最多產生 56 張共享證據卡。
- 合併時檢查同源轉載與內容重複，不把轉載鏈誤算成獨立證據。
- 完整搜尋紀錄保存在各席目錄；共享辯論只載入驗證後的證據卡。
- 不保存整篇受版權保護文章，只保存判斷所需的短摘錄、數值與網址。

## 7.1 報告產生責任

報告的分析、判斷與文字全部由 Core Agent 產生。Python 不撰寫市場結論，只負責驗證與排版。

```text
Core Agent 撰寫結構化 report.json
→ Python 檢查票數、Agent 身分及 evidence ID
→ 從同一份內容產生 report.md
→ 從同一份內容產生 report.html
```

- `report.json` 保存 Core Agent 撰寫的市場狀態、分析期間、信心燈號、票數、正反證據、各席理由、改票原因、限制與失效條件。
- Python 不得修改 Core Agent 的方向判斷或少數意見。
- Markdown 與 HTML 必須從同一份 Core Agent 內容產生，避免兩份報告不一致。
- 報告引用的 evidence ID、票數與 Agent 身分必須能在原始稽核檔案中找到。

報告時間與驗證失敗處理：

```text
前 90 秒  Core Agent 完成 report.json 初稿
接著 60 秒 Python 驗證；有錯時 Core 只修正一次
最後 30 秒 產生 report.md 與 report.html
```

- Core 修正後仍有不存在的 evidence ID、錯誤票數或缺少必要內容時，不允許 Python 偽造或補寫市場結論。
- 系統產生紅燈「報告驗證失敗」版本，保留已驗證的題目、票數、各席公開立場與來源。
- 無法追溯的結論或引用不進入最終報告，並明列驗證失敗原因。
- 最差情況仍交付誠實的稽核結果，不輸出表面完整但無法驗證的報告。

HTML 呈現邊界：

- `report.html` 是單一、自包含檔案；CSS 內嵌，不依賴 CDN、外部字型、JavaScript 或前端框架。
- `debate.html` 完整保留七席公開發言、改票與 evidence ID，並與 `report.html` 雙向連結。
- 第一個畫面只顯示市場狀態、分析期間、信心燈號與文字、票數、判斷及失效條件。
- 支持／反方證據、每席理由、改票、替補與限制放在下方詳細區段。
- 報告斷網時仍可完整開啟；來源網址維持可點擊。
- 信心不得只以顏色表示，必須同時提供圖示與文字。
- HTML 支援瀏覽器列印成 PDF；`evidence.jsonl` 與 `debate.jsonl` 使用相對連結。

## 7.2 跨模型 JSON Contract 與格式修復

Claude、GPT 與 Gemini 共用三份版本化 JSON Schema：

- `config/schemas/evidence-card.schema.json`
- `config/schemas/agent-position.schema.json`
- `config/schemas/report.schema.json`

各研究席只寫自己的 attempt 目錄；Python 驗證通過後才合併到正式 JSONL 與報告資料。

格式錯誤處理：

```text
Python Schema 驗證失敗
→ 原 Agent 依精確錯誤修正一次
→ 仍失敗時由 Core 派出非投票 Format Repair Agent
→ 再次驗證
→ 缺少實質內容時標記 unrepairable 並啟動席位替補
```

- Format Repair Agent 是額外的非投票輔助角色，不研究市場、不加入辯論、不產生立場。
- 它只能整理括號、欄位名稱、陣列與可直接對應的既有內容。
- 它不得補造來源、猜測未寫立場、改變證據方向或把含糊內容改成確定事實。
- 修復前後內容、formatter 模型、時間與雜湊都要保存。
- Schema 驗證在研究期間增量執行，不等到收件牆才開始。
- 無法在該題型收件牆前完成修復的內容不得加入正式證據快照。

## 8. 已確認但尚待具體化的架構限制

- 七席目標模型組成固定為 3 個 Claude Opus、3 個 GPT-5.6 Sol、1 個 Gemini Pro。
- Gemini Pro 席使用 Google AI Ultra 權限與 Antigravity CLI，不再使用 Gemini CLI。
- Agent 失敗時，先以相同模型重試一次；再次失敗不以 Sonnet 或其他未核准模型跨模型替補。
- 投票門檻永遠使用絕對 `7／6／5／4`，不得按剩餘席位比例降低。
- 所有登入、模型權限、網路與資料源檢查必須在正式計時前完成。
- 只使用既有模型會員權限及免費／公開市場資料，不新增付費 API。

## 8.1 Fail-closed 賽前預檢

只有下列檢查全部通過才顯示綠色 `READY`：

- Core Agent 實際解析為 GPT-5.6 Sol。
- 能同時建立 3 個 GPT-5.6 Sol Sub-agents。
- Claude CLI 已登入，3 個 Opus 席能回傳結構化測試結果。
- Antigravity CLI 已登入，`agy models` 顯示 Gemini Pro，且能完成非互動測試。
- 七席的研究搜尋能力均已通過 smoke test。
- 固定版 `research` skill 的來源 commit 與檔案雜湊正確。
- 研究席無法修改 Code Root，只能寫入自己的 Data Root 席位目錄。
- Data Root 可建立執行目錄、寫入 JSONL、產生快照與報告。
- Windows／WSL 路徑轉換與命令參數傳遞正常。
- 測試用 Markdown 與 HTML 報告能成功產生並開啟。
- 至少完成一次七席同時執行的端到端壓力測試，七席均須在該題型收件牆前交出可驗證研究結果；只測單一 Agent 不算通過。

任何主要席位失敗時顯示 `NOT READY`，不得用備援模型偽裝完整預檢通過。備援 roster 仍需另行通過測試，只供正式執行期間的突發故障替補。

## 8.2 Agent Roster 固定方式

- 七個研究角色固定，provider／模型映射在開發演練期間輪換測試。
- 以實際搜尋能力、結構化輸出穩定性與完成速度選定正式映射，不先假設特定模型最適合某角色。
- 正式比賽版本把主要模型、精確模型名稱、第一替補、第二替補、允許工具及席位輸出目錄寫入 `config/agent_roster.json`。
- 正式執行不隨機分配角色，以保留可重現性。
- `run_manifest.json` 記錄該次實際解析模型、替補與替補原因。

## 8.3 Run ID 與時間基準

- 每次正式分析建立唯一 `run_id`，格式包含 UTC 開始時間、題目資產 slug 與短隨機識別，例如 `20260801T073000Z-btc-8f3a2c`。
- 每筆正式紀錄至少包含 `run_id`、`seat_id`、`attempt_id`、`phase`、`created_at_utc` 與 `elapsed_ms`。
- 所有 deadline 使用 WSL Python 單調時鐘判定，不受系統校時或時區變動影響。
- 原始紀錄時間使用 UTC；人類報告顯示 `Asia/Taipei` 與 `T+` 經過時間。
- Windows、WSL 與模型自行回報的時間不作為 deadline 依據；由控制程式統一換算與記錄。

## 8.4 執行歷史與保留

```text
<Data Root>\runs\
├─ <YYYY-MM-DD>\                 # 台北日期
│  ├─ <HHMM-題目slug-hash>\
│  └─ <HHMM-題目slug-hash>\
└─ latest.json
```

- 每次執行使用新的 run 目錄，永遠不覆蓋先前報告、證據、辯論或診斷資料。
- 目錄依台北日期分層，資料夾名是人眼可讀的標籤：台北 `HHMM`＋題目 slug＋整串
  `run_id` 的 16 碼 SHA-256 前綴。run 的身分仍是含 UTC 時戳的 `run_id`；因為尾碼
  取自完整 `run_id`，差一秒或換一個標的都會落到別處，由
  `run_store.resolve_run_dir` 從 run_id 精確找回目錄（ADR 0005）。
- 同一個 `run_id` 只能被建立一次：`create_run` 以 `O_EXCL` 在該日期夾內建立一個
  只由 `run_id` 決定名稱的 claim 檔（`.<HHMM>-<hash>.run-claim`）完成原子占用，
  再建人類可讀目錄；claim 不在 run 目錄內，run 內部檔案契約不變。
- `latest.json` 只指向最近完成的執行，方便使用者立即開啟報告。
- 失敗與未達共識的執行仍完整保留，並明確標記 `failed` 或 `no_consensus`。
- Agent 與自動程式沒有刪除歷史的權限。
- MVP 不做自動清理；只有使用者明確指定 run ID 時，才能另行執行受控刪除。

## 8.5 測試接縫

- 使用 Python 標準函式庫 `unittest`，不引入額外測試框架。
- `run_controller` 接受可注入的時鐘與 provider 介面；自動化測試使用假時鐘與假 Agent，不呼叫真實模型。
- 測試時可用假時鐘驗證完整 `T+5:20／5:50／6／7／8:30／10／11:30／12／15` 狀態轉換。
- 必測：四輪 `7／6／5／4` 門檻、`3／2／2` 未達共識、Agent timeout／同模型重試、Format Repair、少於四票紅燈失敗、權限隔離，以及 Markdown／HTML 同源內容。
- 真實七席並行壓力測試屬賽前預檢，不放進一般自動化測試，避免每次測試消耗模型額度。

## 8.6 文件與 Issue Tracker

- `CONTEXT.md`：只保存專有名詞、關係與歧義。
- `docs/planning/`：核准需求與架構訪談紀錄。
- `docs/adr/`：重大且難逆轉的架構決策。
- `docs/feasibility/`：可行性評估。
- GitHub Issues：Spec 與 Tickets 的唯一正式來源；不建立本機重複副本。
- Ticket 標籤使用 `ready-for-agent`；Spec 標籤使用 `spec`。
- GitHub repository：`RoyalMilkteaMaster/AI-agnets-debating-chamber`。
- Repository 可見性：private。
- 建立 repository、Issue、標籤、留言及更新狀態的實際權限仍須在發布前預檢。

## 8.7 ADR

完整架構核准後建立兩份 ADR：

- `docs/adr/0001-codex-skill-with-wsl-python-controller.md`
- `docs/adr/0002-immutable-file-based-run-store.md`

Antigravity 取代 Gemini CLI 是外部產品變更，保留在架構與可行性報告；private repository、報告樣式與 roster 容易調整，不另建 ADR。

## 9. 後續驗證與實作細節

下列事項不再改變已核准架構，但必須在可行性或 Ticket 階段查證、具體化：

- 可行性：Codex、Claude 與 Antigravity 的實際非互動 flags、輸出、resume、auth、搜尋能力與並行額度。
- 可行性：精確模型別名、演練 roster 映射、免費資料來源、Windows／WSL 路徑與 GitHub 權限。
- Ticket：外部程序 heartbeat、取消、程序樹清理與錯誤碼 contract；已核准 deadline 不得改變。
- Ticket：依三份已核准 contract 補齊 `run_manifest.json`、證據、立場、辯論與報告 Schema 的完整欄位。
- Ticket：依已核准離線報告邊界完成 CSS 細節與瀏覽器列印驗證。
- Issue tracker：Spec 標籤使用已核准的 `spec`。

## 10. 下一步

進入 `$milktea-skills-check-feasibility`，查證 provider、模型、工具、資料來源、GitHub 權限、時間與成功機率區間；不得在可行性報告核准前開始實作。

---

## 11. 2026-08-05 賽後續用整改核准架構

- 狀態：已核准（2026-08-05，Brownfield Refactor Planner 流程）
- 依據：`docs/architecture-reviews/2026-08-05-hoya-bit-refactor.html`（方案 A：局部整理＋定向新增）
- 本章與先前章節衝突時以本章為準；未提及的規則（write-once、Core 不越權、封存後禁搜尋、單一寫入者、測試接縫等）全部沿用。

### 11.1 範圍與清理（Phase 0）

- 比賽已結束，系統轉為日常續用工具；比賽專屬的 GitHub Issues 流程（§8.6）停用，Spec 與 Tickets 改用本機 `docs/work/`（單一真相）。
- 刪除（使用者已核准，Data Root 非 git、不可逆）：`_data/runs/` 全部 36 個 run（含比賽三場）、`presentation-v2`~`v7`、`coordination/`、`adjustment-audit/`、`inbox/` 歷史、`logs/live-server.log`、`preflight/` 內測試殘留（`ticket11-*`、`final-real-not-ready`）。
- 保留：`preflight/latest-ready.json` 與其對應時戳憑證目錄（launch 唯一前置）。
- Sibling 處置：先程式化驗證現行 Code Root 已包含舊 repo 全部需要的 commit，通過後連同 `_worktrees`（git 連動，必須同組）與舊網站目錄一併刪除。
- 刪除前先 zip 整包備份到 `D:\workstationD\AI agnets debating chamber\backups\`（工作區層級，不進三個 Root）。
- §8.4「MVP 不做自動清理」維持；本次刪除屬使用者明確指定的一次性受控清理。

### 11.2 規則設定檔化（Phase 1）

- 新增 `config/debate_rules.json`：辯論時間門檻、票數階梯、燈號映射與降級規則的唯一來源。
- `debate_state_machine.py:36-53` 的硬編常數改為載入此設定；`required_votes_at`、driver、`run_verifier`、測試一律讀同一來源，不得複製字面值（比照 `research_deadlines` 的唯一權威模式）。
- 載入時 fail-closed 驗證：欄位缺漏、時間非遞增、票數非法即拒絕啟動。
- 行為不變是本 Phase 的驗收標準：預設值＝現行常數，681 測試基準全綠。

### 11.3 投票與燈號新制（Phase 2）

投票（時間值以 `debate_rules.json` 為準，下列為核准預設）：

```text
R1 opening 盲投（互不可見）收齊
→ 7/7 同立場 → 直接停止（stop_reason=unanimous_blind_pass）→ 藍燈報告
→ 否則依封存+60／150／240／330 秒開四輪票（門檻 7／6／5／4）
→ 封存+360 秒強停（4 票採納，<4 未達共識）
```

- 7/7 直過保留；其餘有效票定義與自由辯論以 §14.2 為準，舊的 challenge 必經規則已退役。
- 改票必附理由與 `vote_changes` 全程記錄機制沿用；prompt 層強化「用證據說服對方」語意。

燈號（取代 §3「信心燈號上限」的證據品質級聯）：

- 基準純票數：7藍（blue，新增級）／6綠／5黃／4橘／<4紅。
- 降級①：採納立場引用來源少於 2 個獨立網域 → 降一級。
- 降級②：引用含低可信（非 tier 1/2）來源 → 降一級；**輿情席（social-macro）證據豁免**（該席職責即蒐集三手輿情）。
- 其餘現行降級（類別數 elif 級聯、30 天時效）移除；`report_contract.confidence_cap`、`_validate_confidence`、`debate_driver.confidence_ceiling`、renderer 樣式、`run_verifier` 五處同步。
- ADR：`docs/adr/0003-vote-count-light-scale.md`。

### 11.4 標的三類全開（Phase 3）

- 移除 `question.py:18 SUPPORTED_ASSETS` 白名單；§2「驗證題目是否在核准範圍」改為資產類別偵測：crypto（全幣種）／台股／美股／開放命題，四類全收，無法歸類仍走 open 模式，不 fail-closed 拒絕。
- `open_proposition` 命題訂定（Core 以 codex exec 無搜尋把題目轉成正方／反方／無法決定詞彙）升為主路徑；失敗 fallback 題目原文（現制沿用）。
- 新增 `config/market_scopes.json`：各資產類別的語意提示（代號解析如 2330→台積電/TSMC、交易時段語意如台股週末休市），由 `build_attempt_prompt` 唯一入口注入。
- §3.1 資料蒐集邊界維持：研究仍由七席自行搜尋，Python 不抓行情（唯一破例見 §11.6）。

### 11.5 run 日期分層與 SQLite 索引（Phase 4）

- run 目錄改為 `_data/runs/YYYY-MM-DD/HHMM-題目slug-hash/`（本地時區 Asia/Taipei 決定日期夾；run_id 內部仍用 UTC 時戳保證唯一）。
- run 內部檔案契約完全不變（§4）；`latest.json` 指標格式保留、指向新分層路徑；讀者（`live_dashboard` 繼承者、`codex_bridge`、`run_verifier`）同步。
- 新增 `hoya_market_agents/run_index.py`：`_data/runs/index.db`（SQLite，stdlib sqlite3）唯一寫入者。索引欄位：run_id、日期、題目原文、題目 slug、資產類別、標的、題型、燈號、採納立場、票數分佈、共識狀態、報告路徑、事後驗證結果。
- index.db 是**可重建的衍生資料**：提供 backfill 命令從 runs/ 掃描重建；損毀即刪除重建，不備份。
- 與偏好結構的差異說明：不建 `_data/databases/`，index.db 與被索引資料同居 `runs/`（單一功能索引、可重建、就近原則）。
- ADR：`docs/adr/0005-dated-run-layout-sqlite-index.md`。

### 11.6 事後驗證（對答案）

- 新增 `hoya_market_agents/quote_api_client.py`：免費公開報價 API 唯一介接點，僅供事後驗證讀取到期標的價格，永不進研究管線。此為 §3.1／§8「不新增付費 API、Python 不抓行情」的唯一核准破例（仍限免費公開來源）。
- 常駐前端伺服器內建到期檢查（伺服器運行時掃描到期 run），自動記錄實際漲跌到該 run 目錄的 `outcome.json`（write-once 新 artifact，不違反不可變原則）並更新 index.db；API 失敗時前端提供手動輸入。
- 準確率統計（各燈號命中率）由前端從 index.db 聚合，不另建資料表以外的狀態。
- ADR：`docs/adr/0004-quote-api-exception.md`。

### 11.7 常駐前端（Phase 5）

- 新增 `hoya_market_agents/webapp/`：本機常駐網頁（127.0.0.1，stdlib http.server＋SSE，延續 §4.0.1 模式，零外部套件）。
- 功能：歷史查詢＋run 詳情（查 index.db）、提問啟動（呼叫 launch 管線）＋即時進度、聊天室直播視圖（沿用 v5-chat 版面語彙重製）、設定頁（讀寫 `debate_rules.json`，寫入前 fail-closed 驗證）、事後驗證追蹤與準確率統計。
- `live_dashboard.py` 由新前端吸收後退役移除；launch 不再另開舊直播頁。§4.0.1 的唯讀直播邊界（不顯示隱藏思考、直播故障不破壞研究流程、run artifact 是唯一事實來源）由新前端繼承。
- 打包 exe 延後到功能定案後（另案），本輪先以本機網頁交付。

### 11.8 錯誤處理與 Log

- 正式 Log：需要。
- 需要原因：常駐前端伺服器屬長時間無人看守的 Web 服務；到期檢查屬背景工作。
- 共用 Logger：webapp 內單一共用 logger（`hoya_market_agents/webapp/log.py`，只用 stdlib，**不要求是 `logging.Logger`**），僅前端伺服器範圍；研究管線已有 `events.jsonl` 稽核流，不另建。
- 不用 `logging`／`TimedRotatingFileHandler` 的理由：本模組要注入時鐘、要用「檔案內最後一筆 record 的 timestamp」判斷該檔屬於哪一天（而不是看 mtime），還要與全域 logging registry 隔離，避免污染研究管線。這三件事在 `TimedRotatingFileHandler` 上都只能靠等真實時間或改全域狀態來測。
- Log 存放位置：`_data/logs/`。
- Log 檔案：`webapp.jsonl`（輪替後 `webapp-YYYY-MM-DD.jsonl`）。
- 格式與必要欄位：JSONL；timestamp／level（INFO|WARNING|ERROR）／event／source／message。
- 記錄範圍：伺服器啟停、請求錯誤、launch 觸發、到期檢查與報價 API 失敗；不記機密與完整請求內容。
- 預估每日容量：<1MB；磁碟預算：100MB。
- 輪替條件：日期變更（UTC）；保存期限：**保留 30 個日期檔，含今天在內**（即今天與前 29 日；第 30 日前的檔案就是第一個被刪的）；清理方式：伺服器啟動時刪除逾期檔。
- 輪替的併發保證：兩個 webapp 共用同一個 Data Root 時，當日的 active log 由「原子 rename 認領」決定歸誰搬，恰好搬一次；沒搶到的那一方安靜結束，不視為錯誤。
- 敏感資料處理：不記 Token／API key／完整 prompt。
- 驗證方式：webapp 測試斷言事件寫入與輪替行為（暫存目錄）。

### 11.9 測試接縫與驗證邊界

- 沿用：注入時鐘、fake provider、unittest、測試不碰正式 Data Root。
- 新增接縫：`debate_rules.json` 載入器（非法設定 fail-closed 案例）、`run_index`（暫存目錄建庫／backfill／查詢）、`webapp` handler（http 層單元測試）、`quote_api_client`（假 HTTP 回應）。
- 驗證邊界＝Phase：每 Phase 結束跑 `python3 -m unittest discover -s tests`（WSL）全綠才進下一 Phase；Phase 0 另以 fixture launch 煙霧測試確認清理後系統可啟動。

### 11.10 ADR 清單（本次新增）

- `docs/adr/0003-vote-count-light-scale.md`：燈號純票數制＋兩條降級＋輿情席豁免。
- `docs/adr/0004-quote-api-exception.md`：事後驗證報價 API 破例。
- `docs/adr/0005-dated-run-layout-sqlite-index.md`：run 日期分層＋SQLite 衍生索引。

---

## 12. 2026-08-09 前端重設計與七席換套核准架構

- 狀態：已核准（2026-08-09，grill-me 架構階段）
- 需求依據：`docs/planning/requirements.md`〈前端重設計與七席換套〉
- 本章與先前章節衝突時以本章為準；未提及的規則全部沿用（含 §11.8 Log——本次沿用，不新建）。

### 12.1 七席套組（單一權威）

- `agent_roster.json` schema 升版：每席新增 `profiles`，含 `stock`／`crypto`／`open` 三套 `{display_name, focus}`；同批完成提供者對調（onchain→claude、news→codex，維持 3／3／1）。
- 刪除散落的席位標籤寫死表（`seats.py` 顯示常數、`report_renderer.py` 標籤 dict），全部改走同一個 roster 讀取口；載入 fail-closed 驗證七席齊、三套齊。
- `seat_id` 與 `output_dir` 永不改（含 `counter-evidence`，職能改為基本面研究）。
- 套組選擇：`tw_stock`／`us_stock`→stock；`crypto`→crypto；`open` 或跨類→open。
- 顯示規則：所有 run（含歷史）一律以現行套組顯示；使用者已核准接受舊逐字稿自稱與標籤不一致的代價。renderer 僅開放「席位標籤來源」一處修改，版面不動。

### 12.2 webapp 頁面與端點

- 合併頁：「歷史與命中率」在 `/history`（上統計卡、下 run 列表帶結果）；`GET /stats` 302 轉跳 `/history`；手動記錄結果表單併入合併頁。
- 標的選單：主頁發問區＝資產類別選單＋標的輸入框（依類別格式提示；datalist 建議來自 index.db 過往 run 標的）；launcher 走 T05 `assets`／`asset_class` 接縫，不再做純文字資產解析。
- PDF 匯出：`POST /run/<id>/export-pdf` → webapp 新模組以 Edge 無頭模式（WSL 經 `wslpath` 轉路徑）把該 run 現成 HTML 轉 `report.pdf`／`debate.pdf` 寫入 run 目錄（run 目錄第三個核准寫入路徑，只新增 `.pdf`）；轉換器可注入；失敗回誠實錯誤頁。
- 關閉伺服器：`POST /shutdown` → 回覆已關閉頁後優雅停機，照常寫 `server_stop`。
- CSP／零 inline script／零 SQL／artifact 唯讀等 §11.7 邊界全部沿用。
- run 詳情頁功能維持現況，版面納入全站設計系統（需求「全部網頁頁面」適用）。
- 標的建議清單：webapp 以既有 `run_index.query_runs` 結果在 Python 端去重取得，不新增 SQL、不擴充 `run_index`。

### 12.3 設計系統

- 設計 token（色彩／字級／間距）以資料形式放 `pages.py`（沿用 `THEMES` 模式）；全站單一樣式表（辯論室隔離樣式併入）；`ContrastTest` 直接從 token 表計算 WCAG AA。
- 語意色（bull／bear／五燈）保留語意、只校準色階；系統字型堆疊；零外部資源。
- 設計參考：taste-skill 與 ui-ux-pro-max-skill clone 至工作區（記錄 commit SHA，比照 research skill 慣例），不進 Code Root。

### 12.4 入口

- 啟動／關閉邏輯放 Code Root `scripts/`（`start-webapp.ps1` 併入）；工作區根目錄放捷徑「開啟辯論室」（隱藏視窗；偵測已在跑→只開瀏覽器）與「關閉辯論室」（POST /shutdown 備援）。
- 刪除工作區根目錄 `辯論室預覽.html` 與 `開啟辯論室.bat`。

### 12.5 測試接縫與驗證邊界

- 新接縫：roster profiles 載入器（缺套／缺席 fail-closed）、合併頁與 `/stats` 轉跳、PDF 匯出 handler（假轉換器）、shutdown handler、標的表單→launcher 參數傳遞。
- 沿用 unittest、暫存目錄、注入時鐘／fake provider；驗收以既有全綠基準＋渲染後繁中 grep＋對比度實測數字。

### 12.6 ADR

- `docs/adr/0006-seat-profile-sets.md`：席位方向套組——seat_id 永不改、依資產類別選套、第七席轉職基本面。

---

## 13. 2026-08-10 前端白話化、五導覽常駐與 Google 風重設計核准架構

- 狀態：已核准（2026-08-10，grill-me 架構階段）
- 需求依據：`docs/planning/requirements.md`〈前端白話化、五導覽常駐與 Google 風重設計（2026-08-10 核准）〉
- 本章與先前章節衝突時以本章為準；未提及的規則全部沿用（含 §11.8 Log——本次沿用，不新建）。

### 13.1 設計 token 單一權威

- 新增 `hoya_market_agents/design_tokens.py`：白底單套 palette（深色刪除，不再輸出 `@media (prefers-color-scheme: dark)` 區塊）、Google 四色裝飾 token（與語意色 affirm／oppose／abstain 及燈號分開命名，不得混用）、毛玻璃 token（半透明面以「合成後實色」一併提供，供對比測試）。字體堆疊微軟正黑體優先，仍為純系統字型。
- `webapp/pages.py` 與 `report_renderer.py`／`report_audit_renderer.py` 的 CSS 一律從此模組取值；renderer 內寫死的 palette（`_MARKET_CSS`／`_CSS` 的 `:root` 色值與燈色 hex）刪除改引 token。
- `ContrastTest` 直接對 design_tokens 計算 WCAG AA（4.5:1 文字、3.0:1 線條），毛玻璃面用合成色計算；不維護第二份色值。

### 13.2 五導覽與設定分離

- header 結構：左起「即時辯論｜歷史與命中率｜市場報告｜完整辯論」，右側「設定」獨立緊鄰「關閉伺服器」按鈕左邊；所有 webapp 頁面一致，「伺服器已關閉」頁維持無導覽例外。
- 「市場報告／完整辯論」指向：有 run 脈絡的頁面（辯論室、run 詳情）指該 run；無脈絡頁面（主頁初始、歷史、設定）由 `views.py` 以既有 `run_index.query_runs` 在 Python 端解析「最新有報告的 run」（零 SQL、不擴充 run_index）；全站無報告時沿用現行停用樣式（span、不進 tab order）。

### 13.3 離線報告：伺服器側導覽注入

- `server.py` 新增回應注入器：僅對 run artifact 中 `report.html`／`debate.html` 的 text/html 回應，在 `<body>` 標籤後插入五導覽列（純 HTML＋連結，零 script；樣式由站內樣式表 route 供應）；找不到插入點時原樣送出（fail-open 到原內容）。
- 磁碟檔案一字不動（artifact 唯讀）；直接開檔／分享（含 PDF）維持離線自足兩分頁導覽；舊 run 同樣受惠。
- ADR：`docs/adr/0007-offline-report-nav-injection.md`。

### 13.4 離線 renderer 換新裝

- 兩個 renderer 版面換新裝：CSS 改由 design_tokens 產生，DOM 章節結構維持；新 run 起生效，舊 run 檔案與 PDF 不回溯。`run_verifier.py` 不動，既有相對連結檢查不得破壞。

### 13.5 設定頁白話中文

- 標籤表放 `webapp/settings.py`（標籤解析既有位置）：key-path → {中文標籤、一句白話說明}，另含分組標題表（時間軸／票數門檻／燈號規則）。
- 未涵蓋鍵：顯示原鍵名＋「尚未翻譯」標註，照常可編輯（不 fail-closed）；`_about` 註解顯示照舊。

### 13.6 席位名稱與 blurb

- roster schema 升版：每套 profile 新增必填 `blurb`（白話說明，僅顯示用，不進研究 prompt）；載入 fail-closed 驗證擴充為「七席齊、三套齊、每套具 display_name／focus／blurb」。
- 兩套 `display_name` 改為 2026-08-10 定案名（見 requirements 定案表）；open 套名稱留舊值、補 blurb；`focus`／提供者／`seat_id`／`output_dir` 不動。
- 即時辯論頁席位卡經 `seats.py` 既有讀取口顯示 blurb；預檢與測試 fixtures 同步升版。

### 13.7 發問選單移除開放題

- 資產類別選項改由 `market_scopes.json` 的三市場產生（台股／美股／幣）；「開放題」不再出現於選單。
- launcher 介面、後端 open 路徑、套組選擇規則（`open` 或跨類→open）全部保留；歷史開放題 run 照舊可回看。

### 13.8 測試接縫與驗證邊界

- 新接縫：artifact 導覽注入器（插入／略過兩路徑）、latest-report 解析、規則標籤 fallback（未譯鍵）、roster blurb fail-closed、對比測試含毛玻璃合成色。
- 沿用：unittest、暫存目錄、注入時鐘、fake provider、渲染後繁中 grep、對比度實測數字；既有測試全綠為基準。

### 13.9 ADR（本次新增）

- `docs/adr/0007-offline-report-nav-injection.md`：離線報告五導覽採伺服器回應注入，不寫進檔案。

---

## 14. 2026-08-10 四輪投票制與前端分層核准架構

- 狀態：已核准（2026-08-10，grill-me 架構階段）
- 需求依據：`docs/planning/requirements.md`〈四輪投票制、概述摺疊與前端分層（2026-08-10 核准）〉
- 本章與先前章節衝突時以本章為準（§5.3 辯論回合、§5.4 有效票生命週期、§11.3 投票流程的階梯制敘述由本章取代；燈號規則不變）。未提及的規則全部沿用（含 §11.8 Log——本次沿用，不新建；本次無新常駐元件）。

### 14.1 規則檔 schema v2（輪陣列＋封存錨定）

- `config/debate_rules.json` 升版 `schema_version: 2`：

```json
{
  "schema_version": 2,
  "timeline": {
    "vote_rounds": [
      { "open_offset_ms": 60000,  "threshold": 7 },
      { "open_offset_ms": 150000, "threshold": 6 },
      { "open_offset_ms": 240000, "threshold": 5 },
      { "open_offset_ms": 330000, "threshold": 4 }
    ],
    "final_settle_offset_ms": 360000
  },
  "confidence": { ...原樣保留（燈號映射與兩條降級不動）... }
}
```

- 所有 offset 錨定該 run 的證據封存時刻（`research_deadlines(question_type).seal_ms` 仍是封存唯一權威；一般題 6:00、兩標的比較題 6:30）。一般題即字面 7:00／8:30／10:00／11:30／12:00；比較題整體後移 30 秒。
- 載入 fail-closed 驗證：offset 嚴格遞增、threshold 嚴格遞減、`final_settle_offset_ms` 大於末輪 offset；輪數由陣列長度決定，程式不得寫死。
- `initial`／`reduced`／`forced_stop`／`unanimous_blind_pass` 具名欄位與五個絕對牆時刻全部退役；盲投直過語意併入第一輪（見 14.2）。
- `debate_rules.py`：`DebateRules` 改輪陣列結構；`required_votes_at`／`phase_at` 由輪陣列推導（迴圈，不寫 if/elif 階梯）。
- `contract_validator.py`：`_rules_document` 序列化 v2；**保留 v1 讀取分支**——舊 run manifest 內的 v1 規則快照照舊可讀可驗，`run_verifier` 對該 run 用它當時的規則（`test_verify_run.py` 相容測試必須維持全綠）。
- `run_verifier.py`：合法 stop_reason 不再寫死枚舉，改由該 run 規則快照的輪陣列推導。
- `webapp/settings.py`：新鍵中文標籤＋白話說明入 `FIELD_LABELS`／`SECTION_LABELS`（逐鍵文案於 Spec 過目）；`webapp/pages` 設定頁時間軸視覺化改輪陣列版；`webapp/live.py` `rule_timeline`／門檻標籤同步。

### 14.2 四輪投票狀態機

- 封存後資料流：

```text
封存（快照照舊產生）
→ 整理段：每席提示詞只含自席證據卡（見 14.3）；各席交開場票
→ R1 開票（threshold=rounds[0]=7）：七席開場票全到即提前開票（盲投直過語意保留，不必等牆）
→ 未過 → 解鎖完整證據快照（＝JSON 資料交換；不新增 artifact）→ 自由辯論 turn
→ R2 開票（6）→ 辯論 → R3 開票（5）→ 辯論 → R4 開票（4）
→ 硬停結算（final_settle）：當下最新有效立場任一 ≥ 末輪 threshold（4）採納，否則未達共識（紅燈）
```

- `debate_state_machine.py`：回合驗證與停止判定迴圈化（`_accept` 不再寫死 `round in (1,2,3)`）；`_blind_pass` 併入「R1 全到提前開票」；`_force_stop` 改為最終結算。
- `debate_driver.py`：`build_turns` 由規則輪陣列推導；`assign_challenges`／`rotation_pairs` 挑戰配對機制移除；輪間 turn 的提示詞改「用證據說服對方、不盲從也不死守」語意；opening 使用完整第一輪視窗，只有後續自由辯論 turn 在牆前 5 秒停止收集。
- 有效票＝開票當下該席最新公開立場；`SeatRecord` 的 provisional／valid 不再以挑戰完成與否定義。
- 改票必附理由、`vote_changes` 全程記錄、Core 不改寫原文——全部沿用。

### 14.3 證據可見性閘門（提示詞層）

- 封存與快照檔案完全不動（`evidence.jsonl` 仍是單一合併快照、write-once）。
- 閘門在 `debate_driver` 組提示詞處：R1 開票前的提示詞只塞入該席 `seat_id` 的證據卡（卡上既有欄位過濾）；R1 未過後恢復完整快照注入。
- `prompt_builder.py` 模組契約由「七席共用區塊逐位元相同」改為「**同階段內**七席共用區塊逐位元相同」，新增 per-seat 證據視圖參數；debate／vote 不再注入 Research Snapshot、搜尋時線或 EvidenceCard contract，只保留封存後安全規則與當前快照。
- run 檔案契約、稽核紀錄、`run_verifier` 零改動。

### 14.4 webapp 分層（伺服器渲染＋模板＋靜態資產）

```text
hoya_market_agents/webapp/
├─ templates/            # 頁面骨架 HTML 檔（document 殼、各頁佈局）
├─ static/
│  ├─ site.css           # 全站唯一樣式表（var() 引 token）
│  └─ live.js            # 原 pages.LIVE_SCRIPT
├─ pages/                # 原 pages.py 拆成頁面組裝模組
│  ├─ live_page.py …     # 各頁一模組：讀模板、填已跳脫資料、組動態區塊
│  └─ components.py      # 訊息卡、席位卡等重複元件（Python 產生）
└─ server.py             # 新增 /static/* 路由（只服務 webapp/static 白名單）
```

- 頁面仍由伺服器組好 HTML 送出（不做 SPA）；SSE `/live/events` 與其 payload 不變；不新增 JSON API。
- `design_tokens.py` 仍是唯一色彩權威：token 產生的 `:root` 區塊由樣式路由與 `site.css` 組合供應；`pages._tokens` 與 `report_renderer._custom_properties` 兩份重複實作合併為單一實作。
- **CSP 收緊**：webapp 頁面 `style-src` 由 `'unsafe-inline'` 改 `'self'`（CSS 外部化的紅利）；`script-src` 維持現制（live 頁 `'self'`，其餘 `'none'`）。
- 概述摺疊基線（工作區既有實作）隨拆解搬家，行為與測試不變；Gemini 建議之視覺升級落在 `site.css` 與元件結構，於既有設計系統與保護區約束內。
- 離線 `report.html`／`debate.html` 維持自足單檔（CSS 內嵌），不納入分層；renderer 與 `run_verifier` 不動。

### 14.5 測試接縫與驗證邊界

- 沿用：`FixedClock` 注入時鐘、fake provider、unittest、暫存目錄、渲染後繁中 grep、對比度實測。
- 新增：v2 載入器 fail-closed 案例（含 v1 檔被拒）、v1 manifest 快照相容案例、per-seat 提示詞過濾斷言、四輪牆時刻／提前開票／結算斷言、`/static/*` 路由（白名單外 404）與收緊後 CSP 斷言；`LiveScriptTest` 改讀 `static/live.js`；樣式測試改讀樣式路由輸出。
- 驗證邊界分四批，各批 `python3 -m unittest discover -s tests`（WSL）全綠才進下一批：①規則 v2＋狀態機四輪 ②隔離閘門＋辯論 turn ③前端分層搬家（行為不變） ④視覺升級。

### 14.6 ADR（本次新增）

- `docs/adr/0008-discrete-vote-rounds.md`：離散四輪投票＋提示詞層證據閘門＋挑戰機制退役。

---

## 15. 2026-08-12 WSL-only Runtime、零經驗安裝與共用可靠性修復

- 本節來源：2026-08-12 `$milktea-skills-grill-me` 架構訪談核准內容。
- 需求權威：`docs/planning/requirements.md`〈WSL-only Runtime、零經驗安裝與共用可靠性修復〉。
- 實作基準：乾淨 commit `e05bf493e2f05dca37e15d6d10721c418c2c37e3`；原 dirty worktree 只作唯讀比對，不整批移植。

### 15.1 Code／Data／Runtime 根目錄

- **Code Root**：Git clone 或 worktree 的實際位置；所有入口由自身位置解析，不寫死 Windows 磁碟、Windows 使用者、Linux 使用者或開發者路徑。
- **Data Root**：沿用 Code Root 同層的 `AI-agnets-debating-chamber_data`。現有使用者繼續讀取既有 Data Root；新使用者在 WSL 家目錄 clone 時自然取得同層的新 Data Root。
- **Runtime Root**：不建立專案專用 Runtime Root。產品使用 WSL2 Ubuntu 的系統 `python3` 與使用者安裝在 Linux `PATH` 的 Claude／Codex／Antigravity CLI。
- 專案目前使用 Python 標準函式庫，故不建立 `.venv` 或無實際內容的相依安裝層。未來若新增第三方 Python dependency，另開工作包決定環境管理。
- 多個 Code worktree 可指向同一 Data Root；既有 run artifact、SQLite 衍生索引與 write-once 規則維持原權威。測試只能使用暫存 Data Root。

### 15.2 正式 Log

- **正式 Log：沿用既有，不新增系統。** 專案確有長時間 webapp 與背景 Provider，但基準版已由 `hoya_market_agents/webapp/log.py` 寫入 `<Data Root>/logs/webapp.jsonl`，每個 run 另有 `events.jsonl`、diagnostics 與 manifest。
- 本工作不新增 Logger、Log 檔、輪替器、清理器或集中式收集器；短 UI 錯誤只投影既有紀錄的摘要。
- Provider stderr、launch failure 與 attempt lineage 仍由既有 launch log／run artifact 接縫保存；不得記錄 Token、credential 或完整未清理 prompt。

### 15.3 公開入口與腳本責任

Code Root 新增三個使用者入口：

```text
setup-wsl.sh       # 一次性設定與捷徑安裝
START-HERE.sh      # WSL／MobaXterm 正式啟動入口
STOP-HERE.sh       # WSL／MobaXterm 正式關閉入口
```

- `setup-wsl.sh` 驗證自己正在 WSL2 Ubuntu、確認 `python3` 可呼叫，然後以 Windows 內建 PowerShell 執行 `scripts/install-shortcuts.ps1`。沒有第三方 Python 套件可安裝，所以 setup 不建立 venv；缺少 Python 或 Agent CLI 時只印 README 對應的可複製指令，不自動安裝或登入。
- `START-HERE.sh` 與 `STOP-HERE.sh` 是唯一正式啟停行為入口。兩者只負責解析 Code／Data Root、呼叫 Python runtime-control 接縫及開啟 Windows 瀏覽器；ownership 規則不寫在 shell。
- `scripts/install-shortcuts.ps1` 使用 Windows Desktop 的系統 API 建立 `開啟辯論室.lnk`／`關閉辯論室.lnk`，並精確移除同位置及可確認舊工作區中的四個舊名稱捷徑；只處理這些固定檔名，不掃描或刪除其他 `.lnk`。
- `scripts/wsl-shortcut.ps1` 是兩個 `.lnk` 共用的薄 wrapper，以 `-Action start|stop`、setup 時記錄的 distro 與 Linux Code Root 呼叫 `wsl.exe` 的 login Bash；不得執行 Python controller 或 Provider CLI。
- 原 `scripts/start-webapp.ps1`、`scripts/stop-webapp.ps1`、`scripts/webapp-common.ps1` 的 Windows 原生 Runtime 責任退役。確認無呼叫者後刪除，避免留下第二套公開入口。
- MobaXterm 只連入同一個 WSL2 Ubuntu，直接執行根目錄 Bash；不安裝另一份 Code／Data／Runtime。

### 15.4 Runtime ownership 與關閉 precondition

- `hoya_market_agents/webapp/server.py` 的 `GET /health` 是 server ownership producer，精確回傳：

```json
{
  "app": "hoya-market-agents-webapp",
  "runtime_owner": "wsl",
  "instance": "<每次 server 啟動的隨機非空字串>",
  "active_run": false
}
```

- `instance` 只用作短生命期 stale-listener precondition，不是秘密、認證或持久狀態。
- 新的 `hoya_market_agents/webapp/runtime_control.py` 是唯一 ownership consumer：解析 `/health`、區分 free／owned／foreign／malformed，並執行條件式 shutdown。Bash 與 PowerShell 不各自解析 JSON。
- `POST /shutdown` 接受可選的 `expect_runtime` 與 `expect_instance`。捷徑與 Bash stop 必須傳入兩者；server 在處理 POST 當下重新核對，不符即回 `409` 且不停止。
- 網頁內既有 shutdown 可保留無 claim 的同源操作；公開腳本一律走 owner-gated contract。
- `active_run=true` 時，互動 Bash 預設詢問且預設否；Windows shortcut 以最小確認對話框取得明確同意。未確認不得 POST。
- `127.0.0.1:8765` 固定不變；foreign listener 時拒絕啟動／關閉，不換 port、不終止程序。

### 15.5 同頁 launch 與精確 run handshake

沿用 `hoya_market_agents/webapp/launch.py`、`LaunchLock` 與 `launcher.run_launch(..., handshake_path=...)`，不另建 waiting page：

```text
live.js 攔截 form submit
  → fetch POST /launch
  → server 驗證題目並建立一次性 launch token
  → child 收到 token-bound handshake path
  → launcher 建立 run 後原子寫出既有 LAUNCHED handshake + token
  → GET /launch/status?token=... 回 pending／launched／failed
  → launched 回精確 run_id
  → live.js 以該 run_id 重新取得 snapshot 並重連 SSE
```

- `POST /launch` 對 JavaScript 回 `202` JSON；輸入錯誤回穩定問題碼與一行繁體中文摘要。非 JavaScript fallback 可回 Live 頁錯誤，但不得導向獨立 waiting page。
- `GET /launch/status` 只讀目前 server 內 `LaunchLock` 的 token、child 狀態與 atomic handshake；不得查 newest run、`latest.json` 或 run index 猜測。
- handshake 必須同時符合 token、合法 `run_id` 且該精確 run directory 已存在才回 `launched`。暫存 handshake 在採用、失敗或下一次 launch 前清理。
- `live.js` 把 SSE URL 固定為 `/live/events?run=<exact run_id>`，換 run 時關閉舊 EventSource、重設 run-local UI state，並以 `history.replaceState` 保存可刷新 URL；不整頁跳轉。
- form 在送出後 disabled 並顯示小型既有 CSS 動畫；失敗只顯示 `啟動失敗：<短原因>　[重試]`。詳細 stderr 留在既有 launch log。
- 完成時 `completion` 只有在有效 manifest 與 `report.html` 都存在才提供；UI 只顯示 `分析完成　[查看市場報告]`。
- 啟動路徑移除 READY certificate gate。題目／標的驗證保留；Provider 不存在、未登入或輸出失敗由各 attempt 的正式 failure contract 處理，不由 webapp 預先假裝 READY。

### 15.6 WSL Provider process ownership

- `claude_adapter.ProcessRegistry` 繼續作所有外部 Provider `Popen` 的單一追蹤入口；Codex 與 Antigravity adapter 經同一 registry contract 註冊與終止。
- WSL／POSIX spawn 一律 `start_new_session=True`，並在 spawn 後保存 process group id。timeout、cancel、first-valid-wins 與 cutoff 皆先 poison 該 invocation，使後到結果不可採用，再以 `SIGTERM`／有界 grace／`SIGKILL` 回收 group。
- registry 以 `(attempt key, invocation generation)` 分隔同 key 的 resume；每 key 使用一個 reclaim lock。取得 lock 後必須重讀 settled outcome，禁止 late terminate 覆寫較早的肯定結果。
- poisoned process 在 track 時也必須進入相同 reclaim lock；同 key 不得同時回收，不同 key 仍可並行。
- terminal outcome 只有三種證據語意：已回收、已確認沒有東西需回收、回收失敗。回收失敗時 attempt 永久不可採用並帶穩定 failure code。
- 不移植 Windows Job Object、`taskkill`、CP950 reader 或 Windows-specific fallback。

### 15.7 Provider 路徑、主備與獨立 Opening

- 新增或重建 `hoya_market_agents/provider_cli.py`，只以 WSL process 的 `PATH` 和 `shutil.which` 解析 `codex`、`claude`、`agy`；不得寫死 `/home/leslie`。依使用者決策，不額外阻擋解析到 Windows mount 的 CLI；README 的正式路徑只教 Linux 安裝，release acceptance 另檢查沒有 Windows Provider process。
- primary roster、seat identity、focus 與 3 Codex／3 Claude／1 Antigravity 配額不變。
- 每席最多一個不同 Provider backup，沿用已核准的 candidate order 與該 Provider 固定模型；backup 是 attempt lineage，不改 roster primary 身分。
- worker capacity 至少容納七個 primary 與七個 backup 同時進入合法平行窗口；缺少一個 Provider 不阻擋整場，該 attempt 記 `provider_cli_missing` 後由 recovery 繼續。
- 每席第一個合法 research result 採用後，可開始一次獨立 Opening provider call；Opening prompt 只含該席自身 research evidence，不讀他席資料。Opening output 暫存到 evidence seal 後交給既有 DebateStateMachine，不能直接把 research envelope 當票。
- fake／offline launch test 必須明確注入 fake proposition adapter，避免測試意外呼叫真 Codex。

### 15.8 attempt terminal outcome、late result 與 lineage schema

- `ResearchScheduler` 是 attempt terminal outcome 唯一寫入者。`attempt_outcomes[attempt_id]` 至少包含 `terminal_outcome`、`failure_code`、`failure_message`；同一 attempt 的終局不可被較晚訊息覆寫。
- `submit_result` 首先檢查 finished／non-adoptable outcome；逾時、取消、失敗、接收窗關閉或已有 adopted result 後到的有效內容只寫 diagnostic，不進 `adopted_records`。
- events 與既有 `research-summary.json` 以加法欄位投影 primary／backup、provider、requested／actual model、phase、started、terminal outcome、failure 與 adopted。不得建立第二份競爭性 summary。
- 舊 run 缺少新欄位時，Live 顯示中性「未記錄」，不推測 attempt kind、actual model 或 failure。
- `real_provider.research_envelope_schema(run_id, attempt)` 每次從通用模板 `deepcopy`，再以單值 JSON Schema `enum` 鎖住 envelope／card 的 `run_id`、`seat_id`、`attempt_id`；Claude、Codex、Antigravity 三個正式 research callsite 都必須使用該 schema。gateway 保持第二層 fail-closed lineage 驗證。
- `research_proof_missing` 先以單一 WSL Codex 真實 canary 驗證。若可重現，parser 只接受 CLI 機器可讀事件中的 matching search tool invocation/result；URL、模型自述與一般文字不算 proof。若未重現，此工作只保存證據，不修改 parser。

### 15.9 Live 權威時間與 projection

- `webapp/live.py` 提供唯一 `authoritative_elapsed_ms`：run 未完成時以注入 clock 減 `question.json.created_at_utc`；完成後讀合法 manifest 的 `elapsed_ms` 凍結。不得以最後一則聊天室訊息時間當 run clock。
- `ChatRoom` ingest 同一份 `events.jsonl` 時 sticky 保存 `debate_opened`；它不是 seat message。
- `debate_start_remaining_ms` 只以 `research_deadlines(question_type).seal_ms - authoritative_elapsed` 計算。結果為正整數或 `None`；到時／已 opened 回 `None`，因此不產生 `00:00`。
- 初始 HTML、snapshot、append、done、reconnect 與 refresh 都使用相同欄位 `debate_start_remaining_ms`。
- `live.js` 對同一 run 取 `max(current_elapsed, incoming_elapsed)`，且 debate-start target 不得因 stale frame 放大；`debate_started` latch 不可降級。不同 run 才完整重設。
- 報告期限、17 分鐘總窗、四輪 offset 與規則檔不修改；只把第二格顯示改為「開始辯論剩餘時間」。

### 15.10 README 與 onboarding

- `README.md` 重寫為單一路徑教學，只保留現有 `![AI agnets debating chamber](docs/assets/readme-hero.png)` 與下列內容：
  1. Windows 10／11 安裝 WSL2 Ubuntu。
  2. 重新開機與建立 Ubuntu 帳號。
  3. 在 WSL 家目錄 clone 正式 Git remote。
  4. 執行 `bash setup-wsl.sh`。
  5. Python、Codex、Claude、Antigravity 的 Linux 安裝及互動登入命令。
  6. 桌面捷徑、`START-HERE.sh`／`STOP-HERE.sh` 與 MobaXterm 操作。
  7. 只列缺少命令、未登入、8765 被占用與 Log 路徑的極短排查。
- 命令區塊逐段標示 `[Windows]` 或 `[WSL／Ubuntu]`；不假設 Codex Task、不要求使用者理解 READY／preflight／run manifest。
- 既有其餘 README 文字全部刪除；`docs/assets/readme-hero.png` 檔案與引用保留。
- Provider 安裝命令在實作時以各產品第一手官方來源核對；setup 不下載或更新 Provider。

### 15.11 相容、遷移與回復

- Data Root 不遷移、不刪除、不改寫舊 run。所有 runtime／summary 新欄位採加法，相容讀取缺欄位舊資料。
- setup 只重建固定名稱捷徑，不修改 Data Root；重跑結果必須冪等。
- Windows native Provider 路徑是明確退役，不提供 fallback。需要追查時使用封存 dirty worktree，不把它合併回新 branch。
- 回復策略是切回保留的 `e05bf49` 基準或封存 branch；不得以 reset 清除 dirty worktree。

### 15.12 測試接縫與驗證邊界

- **腳本／ownership**：PowerShell installer 靜態 `.lnk` 精確值、Bash syntax、隔離 fake listener、foreign/malformed/404、active-run default-no、instance replacement POST-time `409`、setup 重跑與舊捷徑精確清理。
- **launch／UI**：fake child、token-bound handshake、unknown／invalid／failed token、精確 run SSE、同頁 retry、無 waiting route；Node VM 執行 production `live.js`，覆蓋 snapshot／append／done／reconnect、stale frame、started latch 與換 run reset。Node 不成為產品 runtime dependency，但 release acceptance 必須直接執行該 harness。
- **研究流程**：FixedClock、fake process group、barrier-based registry race、same-key generation、different-key parallel、14-attempt worker、backup adoption、late result diagnostic、attempt summary、per-attempt schema mutation test、Early Opening 資料隔離。
- **回歸**：WSL 執行完整 Python test suite；Windows 可跑純單元測試但不作正式 Runtime acceptance。所有 offline tests 禁止真 Provider。
- **真實驗收**：依序執行 WSL Codex／Claude／Antigravity 最小 canary；任一已知失敗立即停止、修正、雙 Reviewer 複驗後重跑。最後真實市場題須在既有時間內產生恰好七席 `7/7` 有效最終票、完整 report／debate／evidence／votes／manifest，`verify-run` 通過，且 Windows 無 Provider process。

### 15.13 ADR

- 新增 `docs/adr/0009-wsl-only-provider-runtime.md`，記錄 Windows 原生 Provider Runtime 退役、Windows 只保留 WSL 管理入口，以及此決策取代 ADR 0001 中「正式執行依賴 Windows Codex 與 WSL 間橋接」的舊後果。

### 15.14 未決事項

無。
