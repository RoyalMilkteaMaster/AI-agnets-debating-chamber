# 06 — 概述摺疊基線驗收納管

- Spec：`../spec.md`
- Blocked by：無
- 需求覆蓋：R-008

## 交付成果

工作區既有的未提交概述實作（伺服器端取 `public_reason` 第一句為概述、`<details>`／`<summary>` 原生展開收合、頁面與 SSE 共用同一份斷句、零 inline script）驗收確認並鎖定為基線：辯論頁訊息預設只顯示核心結論、「顯示全文」展開、「收合」收回；概述等於全文時不摺疊。此行為與其測試自此為本工作包的保護對象，後續 Ticket 不得回退。

## 驗收條件

- 既有概述測試（含斷句規則：全形句尾即斷；ASCII 句點僅後接空白／換行／結尾算句尾；無句尾 60 字截斷補省略號）全部通過。
- 辯論頁實際渲染：長理由訊息預設摺疊、展開後讀到恰好完整全文（不重複不缺字）；SSE 推送的新訊息同行為。
- 全套件測試（WSL）全綠。

## 必要寫入範圍

- `hoya_market_agents/webapp/live.py`、`hoya_market_agents/webapp/pages.py`、`tests/test_webapp.py`——與 Ticket 07 修改相同檔案，兩票不得並行執行。
