# Hoya Bit Market Agents 可行性覆核紀錄

![可行性未知](../assets/feasibility/unknown.svg)

**⚪ 尚無法評估｜成功機率區間：無法估計**

- 日期：2026-08-01
- 原因：Antigravity CLI 已完成登入／模型／結構化 headless 驗證，但尚未執行完整 7 席、15 分鐘端到端壓測。
- 使用者決定：使用者明確表示相信方案可完成，接受上述風險，要求跳過可行性閘門並進入 `to-spec`。
- 此覆核不等於系統已通過賽前 `READY` gate。

## 已驗證

- WSL Ubuntu 24.04 可執行 Python 3.12.3、Node 18.19.1 與 Claude Code 2.1.220。
- Codex CLI 已透過 ChatGPT 登入；官方文件支援專案級 skills、自訂 subagents、模型指定與並行 thread 上限設定。
- Claude Code 已透過 `claude.ai` Max 訂閱登入。
- 三個 `--model opus` 的唯讀請求已同時成功，分別在 1.6 至 2.1 秒完成，實際模型皆回報 `claude-opus-5`。
- Claude Code 官方提供 `WebSearch`、`WebFetch`、結構化輸出、固定 session ID 與 resume。
- Google 官方文件支援 Antigravity CLI 的 Google OAuth、`agy -p`、`agy models`、`--model` 與 WebSearch。
- Antigravity CLI 1.1.9 已在 WSL 完成 Google OAuth 登入；`agy models` 實際列出 `gemini-3.1-pro-high` 與 `gemini-3.1-pro-low`。
- 指定 `gemini-3.1-pro-high`、high effort 的 headless 測試成功；以 schema 檔路徑呼叫時，JSON envelope 內含符合 contract 的 `structured_output`，端到端約 7.1 秒。
- Windows 傳入 WSL 的 inline JSON schema 會有引號轉義風險；正式 adapter 固定傳入 Code Root 內的 schema 檔路徑。
- `mattpocock/skills` 的 research skill 可固定在 commit `2ab958093e83e0ec752e6c1c5932da465bf23e0c`；其 Git blob SHA 為 `0ba594a07f306479baa67104381f48e209ab6aae`，上游採 MIT License。

## 尚未驗證

- Antigravity WebSearch 與正式研究負載下的延遲、額度及權限邊界。
- Core、3 個 GPT subagents、3 個 Claude Opus 與 1 個 Gemini Pro 同時執行時，是否全數在 T+4:45 前交付有效資料。
- 最壞情況在 T+10 停止辯論後，Core 是否能在 T+13 前完成可追溯報告。

## 必要架構修正

1. Canonical skills 保留於 `.agents/skills/`，供 Codex 與 Antigravity 使用。
2. Claude Code 官方只保證自動發現 `.claude/skills/`；本案不宣稱自動發現，而由已核准的 Prompt Builder 讀取 canonical `.agents/skills/research/SKILL.md`，把同一份完整內容注入 Claude 席並在 preflight 比對雜湊。
3. 七個研究席本身已是 research skill 所要求的背景 agent；禁止 skill 再建立額外投票 agent。
4. Python 標準函式庫沒有通用 JSON Schema validator。MVP 使用明確的欄位、型別與 enum 驗證函式；schema 檔仍作為模型輸出 contract。

## 成本、時間與風險

- 成本：設計目標是只使用既有 ChatGPT、Claude Max、Google AI Ultra 訂閱與公開資料，不啟用按量 API key；訂閱額度仍可能成為限制。
- 競賽時間：流程預算為研究 5 分鐘、辯論最晚 10 分鐘、報告最晚 13 分鐘、人工檢查 2 分鐘；數學上可容納，但沒有端到端實測。
- 主要風險：跨供應商延遲或限額、Antigravity headless 格式、權限提示、搜尋品質、模型 alias 漂移，以及高門檻投票導致無共識。

## 繼續條件

使用者已覆核並要求繼續到規格階段。實作完成後，competition roster 仍必須通過既定 preflight；未通過時只能標記 `NOT READY`，不得用本覆核紀錄取代。

## 來源

- [OpenAI Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage)
- [Claude Code tools reference](https://code.claude.com/docs/en/tools-reference)
- [Claude Code skills](https://code.claude.com/docs/en/slash-commands)
- [Google Antigravity CLI codelab](https://codelabs.developers.google.com/antigravity-cli-hands-on)
- [Google Antigravity skills](https://antigravity.google/docs/skills)
- [Gemini Code Assist consumer migration](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals)
- [Pinned research skill](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/research/SKILL.md)
