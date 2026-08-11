# 🏁 AI agnets debating chamber 比賽操作卡（2026-08-02）

## 賽前 60 秒檢查（WSL）

```bash
pgrep -af 'hoya_market_agents live'   # 應有一個；沒有就用下方「直播復活」那行
test -f '/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber_data/preflight/latest-ready.json' && echo READY
```

## 正式啟動（首選：貼給 Claude Code 新 session）

把下面整段貼上，題目換成抽到的原文：

```text
立即執行，不用計畫、不用確認、不要讀其他文件。

用 Bash 執行這條命令啟動 AI agnets debating chamber 七席市場分析：

MSYS_NO_PATHCONV=1 wsl -e bash -lc 'cd "/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber" && setsid nohup python3 -m hoya_market_agents launch --question "〈抽到的題目原文貼這裡〉" --data-root "/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber_data" --handshake-file /tmp/hoya-comp.json >/tmp/hoya-comp.log 2>&1 & sleep 3; cat /tmp/hoya-comp.json'

看到 LAUNCHED JSON 後，持續監看 /tmp/hoya-comp.log（wsl tail），
在 SEALED 與 FINALIZED 出現時各回報一次重點（run_id、七席採納狀況、
票數、共識結論、report.html 路徑）。全程不執行任何其他操作。
直播頁：http://127.0.0.1:8765/live.html
```

## 後備：自己在 WSL 終端機直接跑

```bash
cd '/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber' && setsid nohup python3 -m hoya_market_agents launch \
  --question '〈抽到的題目原文〉' \
  --data-root '/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber_data' \
  --handshake-file /tmp/hoya-comp.json >/tmp/hoya-comp.log 2>&1 & \
sleep 3; cat /tmp/hoya-comp.json
tail -f /tmp/hoya-comp.log   # LAUNCHED → SEALED → FINALIZED
```

## 觀賽

`http://127.0.0.1:8765/live.html` — 自動跟最新 run；辯論逐句蹦、
證據籤片點了開原始來源；歷史下拉可回看任何一場。

直播復活（萬一 server 死了）：

```bash
cd '/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber' && setsid nohup python3 -m hoya_market_agents live --data-root '/mnt/d/workstationD/AI agnets debating chamber/AI-agnets-debating-chamber_data' --host 127.0.0.1 --port 8765 >/tmp/hoya-live.log 2>&1 &
```

## 時間軸與判讀

- `LAUNCHED` 秒回 → `SEALED` T+4:00（兩幣比較題 T+4:30）→ 辯論最晚 T+10 → `FINALIZED`＋報告 ≤T+13
- 三種示範題型任抽任貼；對不上的題目自動走開放命題（Core 當場定義正反方）
- 報告：`FINALIZED` JSON 裡的 `report_html` 路徑，`explorer.exe "$(wslpath -w <路徑>)"` 開啟

## 異常處置

- exit 2（缺 READY 憑證）→ 不要繞過；憑證在 Data Root `preflight/latest-ready.json`
- 個別席位缺席／無共識 → 系統誠實處理照常出報告，這不是故障
- 賽後稽核：`python3 -m hoya_market_agents verify-run --run-id <RUN_ID> --data-root "$DATA_ROOT"`
  （注意：兩幣比較題 4:30 封存的 verify-run 認可尚待收尾票 R7，賽後補）
