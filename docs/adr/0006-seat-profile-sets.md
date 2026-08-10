# 0006 席位方向套組（Seat profile sets）

- 日期：2026-08-09
- 狀態：已核准

## 背景

七席原是純幣圈分工（含「鏈上獵人」「反證稽核員」），但系統已支援台股／美股／開放題；席位名稱與研究方向散在 `agent_roster.json`、`seats.py`、`report_renderer.py` 三處，曾多次因多份副本走鐘（同類病：規則字面值複製）。使用者另指出第七席「立場未出就先找反證」定位不合理，且相似資訊面由同一模型家族研究會犯同樣的錯。

## 決策

1. `agent_roster.json` 為席位資訊唯一權威：每席 `profiles` 含 `stock`（台股美股共用）／`crypto`／`open` 三套 `{display_name, focus}`；載入 fail-closed。
2. 套組由 run 的資產類別決定：`tw_stock`／`us_stock`→stock、`crypto`→crypto、`open` 與跨類→open。所有顯示端（webapp、離線報告）改讀同一來源；歷史 run 一律用現行套組顯示（使用者核准接受舊逐字稿自稱不一致）。
3. `seat_id` 與 `output_dir` 永不隨套組或職能改變：`counter-evidence` 席轉職「基本面研究員」（股票：營收／財報／估值；幣圈：TVL／解鎖日曆／協議收入；開放：關鍵數據查核），ID 保留歷史名。
4. 相似資訊面跨模型家族：資金流獵人（原 onchain）→claude、新聞探員→codex，維持 codex 3／claude 3／gemini 1。

## 理由

- 單一權威消滅「改一處忘一處」整類缺陷；與 `debate_rules.json`、`research_deadlines` 的唯一權威模式一致。
- seat_id 不改是向後相容底線：歷史 run 目錄、provider 對應（`ANTIGRAVITY_SEAT_IDS`）、預檢全綁著它。
- 反證職能可安全退役：第一輪反方挑戰是全席共用機制（debate_driver），非第七席獨有；品質降級規則不變。

## 主要後果

- roster schema 升版，預檢與 fixtures 同步改。
- 舊 run 逐字稿中第七席自稱「反證稽核員」，標籤卻顯示新名（已核准的已知代價）。
- 只看 seat_id 會誤判第七席職能；本 ADR 即為解釋文件。
