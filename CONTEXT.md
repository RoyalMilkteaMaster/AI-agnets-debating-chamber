# Hoya Bit Market Agents

## Language

- **Core Agent**：單一 GPT-5.6 Sol 協調者；負責派工、時間關卡、原文分發、計票與報告內容，不得擅自選邊。
- **Research seat／研究席**：七個固定研究與投票角色之一。席位是邏輯身分，不等同某次模型程序。
- **Attempt／嘗試**：某個研究席的一次實際模型或 CLI 呼叫。
- **Replacement／替補**：原 attempt 失敗後，讀取該席公開歷史並接續工作的模型呼叫。
- **Format Repair Agent**：不投票的輔助 Agent；只能整理既有內容格式，不能補造資料或立場。
- **Evidence card／證據卡**：具有來源、時間、方向、原始數值或短摘錄及可信度說明的最小可追溯證據單位。
- **Evidence Store**：單次 run 的 `evidence.jsonl`；不是向量資料庫或 RAG。
- **Evidence snapshot／證據快照**：`T+4:00` 產生、之後不得覆寫的正式證據集合。
- **Claim／論點**：Agent 以 evidence ID 支持或反駁的公開主張。
- **Shared debate room／共享原文辯論室**：七席讀取相同、未經 Core 改寫的論點、回應與投票快照。
- **Provisional vote／暫定票**：研究完成後、尚未完成第一輪反方挑戰的投票。
- **Valid vote／有效票**：研究席完成第一輪反方挑戰後的最後有效立場。
- **Consensus／共識**：在指定時間關卡達到絕對 `6／5／4` 同立場票數。
- **No consensus／未達共識**：`T+10:00` 沒有任何立場取得四張有效票。
- **READY**：所有主要模型、搜尋、權限、路徑、Skill、資料寫入與報告能力均通過賽前 fail-closed 預檢。
- **Run**：從單一題目啟動至報告完成的一次完整分析，使用唯一 `run_id`。

## Relationships

- 一個 Run 固定包含七個 Research seats。
- 一個 Research seat 可因重試或替補包含多個 Attempts，但任何時間只有一張有效票。
- Attempts 產生 Evidence cards；驗證後由單一寫入者形成 Evidence snapshot。
- 七席根據同一 Evidence snapshot 在 Shared debate room 交換 Claims。
- Valid votes 決定是否達成 Consensus；Core Agent 依實際票數與證據撰寫報告。
- Python controller 管理時間、程序、驗證、合併與排版，不產生市場方向。

## Flagged ambiguities

- 「Agent」容易同時指研究席與實際模型程序；正式文件分別使用 Research seat 與 Attempt。
- 「Gemini CLI」已不再是個人 Google AI Ultra 的執行工具；本專案使用 Antigravity CLI 承載 Gemini Pro 席。
- 「信心」描述結論可靠程度，不代表價格上漲幅度或方向強度。
- 「中性」是有理由的市場立場，不等同流程失敗；少於四張有效票才是分析失敗。
- `research` 指固定、已記錄 commit 與雜湊的 Skill snapshot，不指執行時從 GitHub 即時下載的內容。
