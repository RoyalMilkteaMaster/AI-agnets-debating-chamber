# 06 Live 同頁 Launch、精確 Run 與開始辯論倒數

- Spec：`../spec.md`
- Spec 覆蓋：R-005、R-006
- Blocked by：01、03、04

## 交付成果

1. Live form 由 production `live.js` 攔截並以 `POST /launch` 送題；送出後停留在原 Live 頁、停用重複提交並顯示小型等待動畫，不再導向獨立 waiting page。
2. `/launch` 成功回 `202` 與 opaque `launch_token`；busy 回 `409`；其他可見錯誤只顯示單行「啟動失敗：原因 [重試]」，詳細技術內容寫入既有 log，不占用主畫面。
3. `/launch/status?token=...` 只回 `pending`、`launched + run_id`、`failed + reason` 或 unknown token `404`；狀態由 `LaunchLock`、child process 與 token-bound atomic handshake 共同持有。
4. child 必須把 token 與精確 `run_id` 寫入指定 handshake；server 不得用 latest／newest run、目錄排序或 run index 猜測剛啟動的 run。
5. 取得 `run_id` 後，同頁以 `history.replaceState` 綁定 URL，並只連線 `/live/events?run=<run_id>`；snapshot、append、done、reconnect 與 refresh 都不可切到其他 run。
6. 分析完成只顯示單行 `分析完成 [查看市場報告]`；不自動跳頁、不播放聲音、不發瀏覽器通知。
7. authoritative elapsed 從 `question.json.created_at_utc` 計算；有效 manifest 存在後凍結。單一 run clock 供 HTML、SSE snapshot、append、done、reconnect、refresh 共用。
8. `ChatRoom` 保留 sticky `debate_opened`。`debate_start_remaining_ms` 在未開始時由 `research_deadlines(question_type).seal_ms - authoritative_elapsed_ms` 計算，開始後為 `null`。
9. UI 第二格顯示「開始辯論剩餘時間」；1～999ms 向上顯示 `00:01`，到達 seal 或收到 `debate_opened` 立即顯示「辯論已開始」，永遠不顯示 `00:00`。
10. 同一 run 的 client elapsed 單調不減、remaining 單調不增、started latch 不降級；切換不同 run 時才重設狀態。
11. webapp launch 不再以 READY certificate 作必要前置，但保留真正必要的 launch validation、單一 active run 與既有 log。

## 交付邊界

- 本票處理 webapp launch/status/handshake、Live HTML／SSE／JavaScript 與權威倒數。
- 不修改 Provider process lifecycle、research proof、backup policy、投票門檻、17 分鐘總時程或離線報告內容。

## 驗收條件

1. production JavaScript harness 實際執行 `live.js`，涵蓋 submit、pending、launched、failed、retry、snapshot、append、done、reconnect 與換 run；不可只搜尋原始碼字串。
2. 連續點擊或重複 submit 只建立一個 launch；busy 與失敗都維持同頁，錯誤最多一行並可重試。
3. 兩個相近 run 與 stale latest/index 的測試證明，token 只能取得 handshake 綁定的精確 run_id，SSE 只讀該 run。
4. 一般題與比較題在 seal 前 1ms 顯示 `00:01`；seal 當下、seal 後或人工較早 `debate_opened` 均顯示「辯論已開始」，沒有 `00:00`。
5. stale snapshot／append／done 不得讓 elapsed 倒退、remaining 增加或 started latch 回到數字；換 run 才清除舊 latch。
6. initial HTML、SSE snapshot／append／done、reconnect、refresh 與 finalized run 對同一欄位投影一致。
7. report deadline、總時程、rules／vote offsets 的既有測試保持全綠。
8. Python webapp 定向測試、完整 webapp suite、production JavaScript harness 與 JavaScript syntax check 全綠；測試不呼叫真實 Provider。
