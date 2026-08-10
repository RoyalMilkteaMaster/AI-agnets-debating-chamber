# 兩組 fixture 的端到端流程紀錄

每一組都是：主頁標的選單送出 → webapp 交給 launcher 的參數 →
同一份參數在暫存 Data Root 跑完一趟 run → 報告與逐字稿產出 →
以假轉換器匯出兩份 PDF。全程不啟動瀏覽器、不呼叫 codex（席位池、Core 撰稿、
命題撰寫三個外部邊界都是注入的替身）。

## 台股

- run_id：`20260314T015926Z-2330-aaa111`
- webapp 交給 launcher 的參數：`{"asset_class": "tw_stock", "assets": "['2330']", "data_root": "/tmp/tmpwcbr69sq", "question": "分析 2330 過去 14 日市場狀態"}`
- `question.json` asset_class／assets：`tw_stock`／`['2330']`
- `report.json` asset_class：`tw_stock`
- `votes.json` 票數：`{"bullish": 6, "bearish": 1, "neutral": 0}`
- 辯論室畫面上逐立場讀到的票數（立場詞→數字）：`{"偏多": 6, "偏空": 1, "方向不明": 0}`
- PDF 匯出（假轉換器）：ok=True｜已在這個 run 的資料夾產生 report.pdf、debate.pdf。
- run 目錄第一層：agents、debate.html、debate.jsonl、debate.pdf、diagnostics、events.jsonl、evidence.jsonl、late、manifest.json、question.json、report.html、report.json、report.md、report.pdf、reports、snapshots、votes.json
- 渲染存檔：`rendered/tw_stock-closed.html`、`rendered/tw_stock-history.html`、`rendered/tw_stock-not_found.html`、`rendered/tw_stock-room.html`、`rendered/tw_stock-run_detail.html`、`rendered/tw_stock-settings.html`
- 離線兩頁存檔：`rendered/tw_stock-offline-report.html`、`rendered/tw_stock-offline-debate.html`

## 加密資產

- run_id：`20260315T015926Z-btc-bbb222`
- webapp 交給 launcher 的參數：`{"asset_class": "crypto", "assets": "['BTC']", "data_root": "/tmp/tmpntj4kk7t", "question": "分析 BTC 過去 14 日市場狀態"}`
- `question.json` asset_class／assets：`crypto`／`['BTC']`
- `report.json` asset_class：`crypto`
- `votes.json` 票數：`{"bullish": 6, "bearish": 1, "neutral": 0}`
- 辯論室畫面上逐立場讀到的票數（立場詞→數字）：`{"偏多": 6, "偏空": 1, "方向不明": 0}`
- PDF 匯出（假轉換器）：ok=True｜已在這個 run 的資料夾產生 report.pdf、debate.pdf。
- run 目錄第一層：agents、debate.html、debate.jsonl、debate.pdf、diagnostics、events.jsonl、evidence.jsonl、late、manifest.json、question.json、report.html、report.json、report.md、report.pdf、reports、snapshots、votes.json
- 渲染存檔：`rendered/crypto-closed.html`、`rendered/crypto-history.html`、`rendered/crypto-not_found.html`、`rendered/crypto-room.html`、`rendered/crypto-run_detail.html`、`rendered/crypto-settings.html`
- 離線兩頁存檔：`rendered/crypto-offline-report.html`、`rendered/crypto-offline-debate.html`
