# 07 — 規則顯示端同步

- Spec：`../spec.md`
- Blocked by：01 規則檔 schema v2 與載入器
- 需求覆蓋：R-007（設定頁文案與視覺化）、R-012（顯示端同步）

## 交付成果

直播頁的規則時間軸與門檻標籤按輪陣列顯示（reload-aware 取值照舊）；設定頁 v2 鍵顯示 Spec R-007 文案表的中文標籤與白話說明、時間軸視覺化改為每輪一列（第 N 輪＋開票時刻＋門檻）、「燈號規則」分組沿用、未翻譯鍵顯示原鍵名＋「尚未翻譯」照舊；設定頁存檔走 v2 fail-closed 驗證。

## 驗收條件

- 直播頁渲染顯示四輪開票時刻與門檻；改規則存檔→重新整理即見新值。
- 設定頁渲染含文案表中五個中文標籤與白話說明；修改 `vote_rounds` 數值存檔成功；非法值（如 threshold 不遞減）被拒並顯示誠實錯誤。
- 模擬規則檔新增未翻譯鍵→顯示原鍵名＋「尚未翻譯」且照常可編輯。
- 全套件測試（WSL）全綠。

## 必要寫入範圍

- `hoya_market_agents/webapp/live.py`、`hoya_market_agents/webapp/pages.py`、`hoya_market_agents/webapp/settings.py`、`tests/test_webapp.py`——與 Ticket 06 修改相同檔案，兩票不得並行執行。
