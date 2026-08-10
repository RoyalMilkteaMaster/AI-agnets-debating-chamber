# ADR 0005：run 日期分層目錄＋SQLite 衍生索引

- 日期：2026-08-05
- 狀態：已核准

## 背景

比賽版 `_data/runs/` 是扁平目錄（UTC 時戳-slug-hash），36 個 run 後已難以人工瀏覽；賽後日常使用會持續累積，且新前端需要按日期／題目／標的／燈號查詢歷史。

## 決策

1. run 目錄改為 `_data/runs/YYYY-MM-DD/HHMM-題目slug-hash/`；日期夾以 Asia/Taipei 本地時區決定，run_id 內部仍含 UTC 時戳保證唯一。run 內部檔案契約（write-once、快照、報告）完全不變。
2. 新增 `hoya_market_agents/run_index.py` 為 `_data/runs/index.db`（stdlib sqlite3）唯一寫入者；run FINALIZED 後 upsert 一列（run_id、日期、題目、資產類別、標的、題型、燈號、採納立場、票數、共識狀態、報告路徑、事後驗證結果）。
3. index.db 是可重建衍生資料：提供 backfill 命令全量重建；損毀即刪除重建，不備份、不作為事實來源。run artifact 仍是唯一事實。
4. index.db 與被索引資料同居 `runs/`，不另建 `_data/databases/`。

## 理由

- 資料夾按日期＋題目命名是使用者明確要求（人眼可讀）；SQLite 補足跨欄位查詢（資料夾名做不到）。
- 「檔案為事實、資料庫為索引」保留既有不可變稽核設計，資料庫損毀零風險。
- 單一功能索引就近放置，比通用 databases/ 目錄少一層間接。

## 主要後果

- `run_store` 建目錄邏輯、`latest.json` 指標路徑與其所有讀者（前端、codex_bridge、run_verifier）須同步改版，屬一次性遷移（舊 run 已清空，無歷史搬遷）。
- 每次 FINALIZED 多一次索引寫入；index 寫入失敗不得阻擋 run 完成（索引可事後 backfill）。
