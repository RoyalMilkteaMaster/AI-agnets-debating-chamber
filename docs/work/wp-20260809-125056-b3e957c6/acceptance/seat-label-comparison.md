# 席位標籤逐席比對表（webapp 渲染後 ↔ 離線報告渲染後）

讀法（Reviewer B 的 Finding B8-F1 修正後）：兩邊都只讀**渲染後的可見文字**，
用 `html.parser` 剖析，不斷言標籤名稱、巢狀層數或 class。逐席判定靠的是
「這一席三套 profiles 的候選名稱裡，畫面上出現了哪一個」——候選由權威給，
出現哪一個是讀出來的，而七席名稱互不重複，所以不需要 DOM 位置。
`seats.seat_display_names(資產類別)` 是第三個獨立來源。

## 台股（run_id `20260314T015926Z-2330-aaa111`，report.json asset_class = `tw_stock`）

| # | seat_id | webapp 畫面 | 離線市場報告 | 離線完整辯論 | roster 讀取口 | 一致 |
|---|---|---|---|---|---|---|
| Agent 1 | `spot-technical` | 圖表偵探 | 圖表偵探 | 圖表偵探 | 圖表偵探 | ✔ |
| Agent 2 | `derivatives` | 籌碼雷達 | 籌碼雷達 | 籌碼雷達 | 籌碼雷達 | ✔ |
| Agent 3 | `onchain` | 資金流獵人 | 資金流獵人 | 資金流獵人 | 資金流獵人 | ✔ |
| Agent 4 | `official-events` | 官方哨兵 | 官方哨兵 | 官方哨兵 | 官方哨兵 | ✔ |
| Agent 5 | `news` | 新聞探員 | 新聞探員 | 新聞探員 | 新聞探員 | ✔ |
| Agent 6 | `social-macro` | 社群觀察員 | 社群觀察員 | 社群觀察員 | 社群觀察員 | ✔ |
| Agent 7 | `counter-evidence` | 基本面研究員 | 基本面研究員 | 基本面研究員 | 基本面研究員 | ✔ |

## 加密資產（run_id `20260315T015926Z-btc-bbb222`，report.json asset_class = `crypto`）

| # | seat_id | webapp 畫面 | 離線市場報告 | 離線完整辯論 | roster 讀取口 | 一致 |
|---|---|---|---|---|---|---|
| Agent 1 | `spot-technical` | 圖表偵探 | 圖表偵探 | 圖表偵探 | 圖表偵探 | ✔ |
| Agent 2 | `derivatives` | 槓桿雷達 | 槓桿雷達 | 槓桿雷達 | 槓桿雷達 | ✔ |
| Agent 3 | `onchain` | 鏈上獵人 | 鏈上獵人 | 鏈上獵人 | 鏈上獵人 | ✔ |
| Agent 4 | `official-events` | 官方哨兵 | 官方哨兵 | 官方哨兵 | 官方哨兵 | ✔ |
| Agent 5 | `news` | 新聞探員 | 新聞探員 | 新聞探員 | 新聞探員 | ✔ |
| Agent 6 | `social-macro` | 社群觀察員 | 社群觀察員 | 社群觀察員 | 社群觀察員 | ✔ |
| Agent 7 | `counter-evidence` | 基本面研究員 | 基本面研究員 | 基本面研究員 | 基本面研究員 | ✔ |

## 鑑別力

- 只屬幣圈套的名稱：槓桿雷達、鏈上獵人
- 只屬股票套的名稱：籌碼雷達、資金流獵人
- 台股 run 的 `report.html`／`debate.html` 對前者零命中，由
  `tests/test_frontend_redesign_acceptance.py::TheSameSeatIsNamedTheSameEverywhereTest::test_a_taiwan_stock_runs_offline_pages_hold_no_crypto_seat_name` 斷言；
  反向控制為同類別的 `…_do_hold_the_stock_seat_names`（每一席恰好一個名稱）。
- 突變證明（一）：把台股 run 的 `report.json` 拿掉 `asset_class` 後重新渲染，逐席比對即在 derivatives（籌碼雷達→槓桿雷達）與 onchain（資金流獵人→鏈上獵人）兩席轉為不一致。
- 突變證明（二，B8-F1 的反方向）：把真實辯論室頁面的 **429 個 class 屬性全部拿掉**並把 span／strong／small／article／p 五種標籤換名後重讀，逐席結果完全相同——讀法對版面調整免疫，對印錯名字仍敏感。

## 退化路徑（報告驗證失敗）也逐席一致

本表兩組是 Core 稿件被收下的正常路徑。報告的另一個生產端（一次修正後仍不通過驗證時的
紅字稽核骨架）曾漏掉 `asset_class`，即本票第一輪發現的缺陷 D-1；修復關閉後，同一 fixture
跑一趟「Core 報告兩次未通過驗證」的台股 run，實測七席 webapp 與離線報告全部一致且全部
等於股票套，兩份離線頁面對幣圈席名零命中。逐條數字與突變驗證見 `a1-a5-matrix.md`
〈缺陷 D-1（已關閉）〉；測試為
`tests/test_frontend_redesign_acceptance.py::TheDegradedReportStillKnowsItsMarketTest`（3 條）。
