# Hoya Bit Market Agents 架構紀錄

- 狀態：已核准
- 核准日期：2026-08-01
- 最後更新：2026-08-05（§11 賽後續用整改核准架構；§1 Code Root 更正為實際 repo）
- 來源：`$milktea-skills-grill-me` 架構階段逐項核准決策；2026-08-05 起加入 Brownfield 整改核准（報告：`docs/architecture-reviews/2026-08-05-hoya-bit-refactor.html`）
- 規則：本文件只記錄已核准架構；尚未決定的項目明列為「未確認」。§11 與先前章節衝突時，以 §11 為準（比賽已結束，系統轉為日常續用）

## 1. 專案與根目錄

黑客松產品與通用開發流程插件分離：

```text
D:\workstationD\hoya bit\
├─ milktea-agents-skills-for-codex\   # 通用 Codex 開發流程插件，不放黑客松產品程式
├─ hoya-bit-market-agents-final\ # Code Root
└─ hoya-bit-market-agents_data\ # Data Root
```

- Code Root：`D:\workstationD\hoya bit\hoya-bit-market-agents-final`（2026-08-05 更正：活動 repo 為 `-final`；無 `-final` 的 sibling 為已凍結舊版，處置見 §11.1）
- Data Root：`D:\workstationD\hoya bit\hoya-bit-market-agents_data`
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
hoya-bit-market-agents-final\
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
→ T+4:00 不可變證據快照
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
- `T+4:00` 產生不可修改的證據快照。
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
T+2:35  尚未交出有效研究結果的席位啟動替補
T+3:50  停止接收新的研究結果，開始格式驗證與合併   ← 兩幣比較題為 T+4:20
T+4:00  產生不可變證據快照並進入投票與辯論        ← 兩幣比較題為 T+4:30
```

- 時刻的**唯一權威**是 `research_scheduler.research_deadlines(question_type)`，
  回傳 frozen `ResearchDeadlines{accept_until_ms, seal_ms}`；scheduler、Claude
  研究 timeout、辯論起點、`verify-run` 與直播頁一律查它，不得複製字面值。
- 未宣告或未知題型退回單幣預設（T+3:50／T+4:00），因為晚封存要有明確理由。

- 任何時間發生明確程序或 provider 錯誤，都立即重試，不等待 deadline。
- `T+2:35` 替補時，尚未用過重試的席位先使用相同模型；相同模型已重試失敗時改用其他可用模型。
- 同一席的原程序與替補都成功時，只採用第一份通過格式驗證的結果。
- 未採用的重複結果保存為診斷資料，不加入共享證據、辯論或投票。
- `T+3:50` 後完成的研究輸出不進入該次正式分析，避免越過 `T+4:00` 搜尋硬截止。
- 每席研究期間持續把證據卡寫入自己的目錄；`T+2:00` checkpoint 供同模型重試或替補接續，不直接產生額外票數。
- 原程序失敗時，替補從該席 checkpoint 接續，仍受每席最多 8 張正式證據卡的總上限約束。
- Claude 研究呼叫的 timeout 為 225 秒：必須在 `T+3:50` 收件牆之前分出勝負；
  實測七席最慢一席 217 秒交卷。兩幣比較題的收件牆在 `T+4:20`，同一條規則
  （收件牆前 5 秒）把 timeout 推成 255 秒。

## 5.3 辯論回合與計票時間

> **2026-08-02 使用者核准修訂：研究 4 分鐘、辯論延長。**

辯論固定最多三輪：

```text
T+4:00       公布證據快照，七席開場逐席即時發布（兩幣比較題為 T+4:30）
             某席開場已發布且全場已有兩種以上立場 → 立刻增量派發該席第一輪
T+7:30       第一輪最晚回覆；達 6 票立即結束
T+8:00       共識門檻切換為 5 票
T+7:30～8:45 未達門檻時進行第二輪；T+8:00 後達 5 票立即結束
T+8:45～9:45 仍未達 5 票時進行最後一輪
T+10:00      強制停止；4 票採用，無 4 票則輸出未達共識
```

- 第一輪**不是一次整波**：快席不等慢席。一席只要自己的開場已進公開紀錄、
  而且場上已有第二種立場，它就有反方可挑戰，該席的第一輪呼叫立刻派出去。
- 即使初始投票已有 6 張同立場票，仍必須完成至少一輪反方挑戰才能接受共識。
- 每輪每席提交：接受或反駁的 claim ID、目前立場、公開理由、evidence ID，以及改票原因。
- 達到當下有效的絕對門檻時立即停止後續辯論，開始報告，不為了填滿時間增加回合。
- 每一輪的收集預算都停在自己那道牆前 5 秒，剩下的 5 秒留給 relay 七席與計票。
- 辯論起點跟著該 run 實際封存的那一刻走：開場沒有自己的牆，收集預算是「封存
  +120 秒」，所以比較題整段開場跟著平移；`T+7:30` 之後的每一道牆都是絕對時刻，
  不隨題型移動（比較題第一輪仍有 180 秒）。

## 5.4 有效票生命週期

- `T+4:00` 的初始票是暫定票，不直接計入正式共識。
- Agent 必須完成第一輪反方挑戰，該席的票才成為有效票。
- 第一輪後不要求每輪重新投票；最後一次有效票持續有效，直到該席明確改票。
- Agent 中途斷線時，Core 優先恢復原 session；無法恢復時依替補規則派出替補。
- 替補必須讀取完整證據、該席公開歷史與共享辯論後，才能確認或改變該席的票。
- 報告必須標示每席最後參與回合、是否中途離線、是否由替補確認或改票。
- 未完成第一輪反方挑戰且沒有合格替補確認的席位，不產生有效票。

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
- Schema 驗證在研究期間增量執行，不等到 `T+3:50` 才開始。
- 無法在 `T+4:00` 前完成修復的內容不得加入正式證據快照。

## 8. 已確認但尚待具體化的架構限制

- 七席目標模型組成固定為 3 個 Claude Opus、3 個 GPT-5.6 Sol、1 個 Gemini Pro。
- Gemini Pro 席使用 Google AI Ultra 權限與 Antigravity CLI，不再使用 Gemini CLI。
- Agent 失敗時，先以相同模型重試一次；再次失敗才允許跨模型替補並完整揭露。
- 投票門檻永遠使用絕對 `6／5／4`，不得按剩餘席位比例降低。
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
- 至少完成一次七席同時執行的端到端壓力測試，七席均須在 `T+3:50` 前交出可驗證研究結果；只測單一 Agent 不算通過。

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
- 測試時可把正式分鐘壓縮成秒，驗證完整 `T+4／7:30／8／8:45／9:45／10／13` 狀態轉換。
- 必測：第一輪後 6 票、`T+8～9:45` 的 5 票、`T+10` 的 4 票、`3／2／2` 未達共識、Agent timeout／重試／替補、Format Repair、少於四票紅燈失敗、權限隔離，以及 Markdown／HTML 同源內容。
- 真實七席並行壓力測試屬賽前預檢，不放進一般自動化測試，避免每次測試消耗模型額度。

## 8.6 文件與 Issue Tracker

- `CONTEXT.md`：只保存專有名詞、關係與歧義。
- `docs/planning/`：核准需求與架構訪談紀錄。
- `docs/adr/`：重大且難逆轉的架構決策。
- `docs/feasibility/`：可行性評估。
- GitHub Issues：Spec 與 Tickets 的唯一正式來源；不建立本機重複副本。
- Ticket 標籤使用 `ready-for-agent`；Spec 標籤使用 `spec`。
- GitHub repository：`RoyalMilkteaMaster/hoya-bit-market-agents`。
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
- Sibling 處置：先程式化驗證 `-final` 已包含舊 repo `hoya-bit-market-agents` 全部需要的 commit，通過後連同 `_worktrees`（git 連動，必須同組）與 `hoya-bit-site` 一併刪除。
- 刪除前先 zip 整包備份到 `D:\workstationD\hoya bit\backups\`（工作區層級，不進三個 Root）。
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
→ 否則進入共享辯論：6 票停 → T+8:00 後 5 票 → T+10:00 強停（4 票採納，<4 未達共識）
```

- 7/7 直過取代 §5.4「即使 6 票仍須完成一輪反方挑戰」中對全票情境的處理；6 票以下情境維持原規則。
- challenge 對立配對、scrutiny 輪替、改票必附理由與 vote_changes 全程記錄等機制沿用；prompt 層強化「說服對方拉票」語意。

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
