# 01 — 規則檔 schema v2 與載入器

- Spec：`../spec.md`
- Blocked by：無
- 需求覆蓋：R-007（schema 與遷移）、R-006（`confidence` 區塊原樣保留）

## 交付成果

`config/debate_rules.json` 升為 schema_version 2：`timeline.vote_rounds`＝`[{open_offset_ms, threshold}]`（預設 60000/7、150000/6、240000/5、330000/4）＋`timeline.final_settle_offset_ms`（360000）；`confidence` 區塊原樣。載入器只接受 v2 並 fail-closed 驗證：offset 嚴格遞增、threshold 嚴格遞減、結算時刻大於末輪、未知鍵拒絕；輪數由陣列長度決定，程式不寫死。規則物件提供由輪陣列推導的門檻與階段查詢。manifest 規則快照序列化為 v2；帶 v1 快照的既有 manifest 照舊可讀取為當時規則。

## 驗收條件

- 載入交付的 v2 設定檔成功，數值與 Spec R-001 時間表一致；載入 v1 形狀的檔案被拒，錯誤訊息指明版本不符。
- 非法 v2 各自被拒：offset 不遞增、threshold 不遞減、結算 ≤ 末輪、未知鍵、輪數為零。
- 新 run 的 manifest 規則快照為 v2 且 round-trip（序列化→讀回）一致；讀取帶 v1 快照的 manifest 成功回傳該 run 當時的規則。
- 全套件 `python3 -m unittest discover -s tests`（WSL）全綠。
