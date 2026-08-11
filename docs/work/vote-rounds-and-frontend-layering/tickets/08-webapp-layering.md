# 08 — webapp 分層搬家

- Spec：`../spec.md`
- Blocked by：06 概述摺疊基線驗收納管、07 規則顯示端同步
- 需求覆蓋：R-009、R-010、R-012（保護區與離線報告邊界）

## 交付成果

webapp 前端分層成立且被實際使用：`webapp/templates/`（頁面骨架 HTML 檔）、`webapp/static/site.css`（全站唯一樣式表，`var()` 引 token）、`webapp/static/live.js`（原 LIVE_SCRIPT 內容）、`webapp/pages/` 套件（各頁組裝模組＋重複元件模組）；`server.py` 新增 `/static/*` 白名單路由；webapp 頁面 CSP `style-src` 收緊為 `'self'`；design token 的 `:root` 供應與既有兩份重複實作合併為一。全部頁面行為（路由、表單、SSE、概述摺疊、保護區、導覽）與分層前一致。離線 `report.html`／`debate.html` 維持自足單檔，不受影響。

## 驗收條件

- 頁面輸出實際來自 templates／static／pages 套件；單一巨檔不再是頁面來源；`/live.js` 內容來自 `static/live.js`。
- CSP header 斷言：webapp 頁面 `style-src 'self'`、無 `unsafe-inline`；頁面 HTML 無內嵌 `<style>`；`script-src` 維持現制。
- `/static/site.css` 與 `/static/live.js` 回 200 與正確 Content-Type；白名單外路徑（含路徑跳脫嘗試）回 404。
- 既有行為測試遷移後全綠：概述測試、保護區（聊天室／燈位／票數）、`ContrastTest`、`LiveScriptTest`（改讀 `static/live.js`）、樣式單一性測試（改讀樣式供應輸出）。
- 離線報告 renderer 與 `run_verifier` 無改動；全套件測試（WSL）全綠。
