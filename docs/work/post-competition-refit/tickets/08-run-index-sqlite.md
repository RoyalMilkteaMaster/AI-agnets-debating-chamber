# 08 Phase 4b：SQLite 查詢索引（run_index）

- 狀態：完成（三方共識，7 輪）
- Spec：`../spec.md`（Phase 4）；ADR 0005
- Blocked by：07

## 目標

`_data/runs/index.db` 可依日期／題目／標的／燈號查歷史 run；損毀可全量重建。

## 使用者價值

「有一個資料庫專門架構清楚地處理」——歷史查詢不再靠翻資料夾。

## 範圍

1. 新增 `run_index` 模組：index.db 唯一寫入者（stdlib sqlite3）。
2. 欄位：run_id、日期、題目原文、slug、資產類別、標的、題型、燈號、採納立場、票數分佈、共識狀態、報告路徑、事後驗證結果（本票先留空欄）。
3. run FINALIZED 後 upsert 一列；索引寫入失敗記 log 不阻擋 run 完成。
4. backfill 命令：掃描 runs/ 全量重建。
5. 查詢介面：依日期範圍／類別／燈號／關鍵字（題目 LIKE）回傳列表——供 09 前端使用。

## 已確認實作決策

- index.db 是可重建衍生資料，非事實來源；不備份。
- 單表起步，不建 ORM、不加第二張表。
- 與被索引資料同居 `runs/`，不建 `_data/databases/`。

## 驗收條件

- fixture run 完成後 index.db 查得到該 run 全欄位。
- 刪除 index.db→backfill→查詢結果與刪除前一致。
- 模擬索引寫入失敗（唯讀 db）→run 照常 FINALIZED，log 有紀錄。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：暫存目錄 index.db；假 run 目錄樹餵 backfill。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：查詢輸出樣本、backfill 等價性測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：07
- Blocks：09

## Agent 分工

- Developer：負責實作、驗證、修正或以證據反駁 Findings
- Reviewer A：使用隔離上下文執行 Review
- Reviewer B：使用另一個隔離上下文執行 Review
- Reviewer 標準：兩者都載入 `$milktea-skills-code-review`，並同時執行 Standards 與 Spec Review
- CLI 與模型：由執行 Task 的 Coordinator 依目前 Task 分工與實際可用能力決定

## 完成規則

- 三個角色已處理所有可重現且有證據的問題。
- 沒有未解決的正確性、可執行性、可讀性、架構或衍生風險。
- 三個角色對完成狀態達成共識。

## 執行與 Review 紀錄

### 1. 執行環境與基準

| 項目 | 值 |
|---|---|
| Execution environment | WSL Ubuntu（`wsl.exe -e bash -lc`），專案於 `/mnt/d/workstationD/hoya bit/hoya-bit-market-agents-final` |
| 基準版本 | `main @ 9b8a4510ec9406f19506e21d50af7918da2385d4`（工作樹未提交） |
| Developer | Claude（一般臨時 Agent） |
| Reviewer A | Codex（GPT-5），`review_engine: native` |
| Reviewer B | Codex（GPT-5），`review_engine: native`（本 Task 未啟用 OCR delegate） |
| 必跑指令 | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests` |
| 開票前基準案例數 | 1168 |
| 結案時本票案例數 | **1282**（全套 1309，其中 27 案屬 Ticket 11 併行工作） |
| 輪次 | **7 輪**，每輪雙 Reviewer 獨立審查 |

環境注意事項：

- `PYTHONDONTWRITEBYTECODE=1` 強制——殘留 `__pycache__` 曾造成假紅。
- Diff 一律加 `--ignore-cr-at-eol`——本 repo 行尾混用。
- **跑全套必須攔截 codex**（見 §12）。
- Reviewer 委派一律禁止讀取 `docs/work/`，驗收條件內嵌於委派文字。

---

### 2. 最終變更歸屬

| 檔案 | 累計 +/− | 結案 SHA |
|---|---|---|
| `hoya_market_agents/run_index.py` | +812 / −0（新增） | `5a80fd0204e4` |
| `tests/test_run_index.py` | +2028 / −0（新增） | `40f29983c107` |
| `hoya_market_agents/cli.py` | +54 / −0 | `112560374bb7` |
| `tests/test_cli.py` | +160 / −0 | `b9331b360e86` |
| `hoya_market_agents/debate_driver.py` | **+9 / −1** | `4c9289b525a4` |
| `docs/operator-runbook.md` | +162 / −12 | `505ce5e1ab1d` |

**2 新增 ＋ 4 修改 ＋ 0 刪除**，兩位 Reviewer 各輪獨立以全樹逐檔 sha256 覆核。

**受保護檔案**（全部 UNCHANGED）：

```
test_verify_run.py               8e320063f7b2      prompt_builder.py    b61352bfac84
test_debate_driver.py            5f14526eb5c1      market_scopes.json   e4d5aa1231aa
test_competition_drill.py        18def58caf2c      test_prompt_builder  85febfb6ae25
test_reviewer_complete_attack.py d9b94b50522d      real_provider.py     ec3b7d9f3f1c
run_store.py                     04fa8001ae59      test_run_store.py    1f1969da4537
```

後兩者與 Ticket 07 結案 SHA 逐字相同。

**併行工作的處理**：第 6 輪起工作樹上同時有 Ticket 11 blocker（B1 快取 reload）的變更。Coordinator 在委派中**列舉**了那四個檔案（`debate_rules.py`、`report_contract.py`、`tests/test_debate_rules.py`、`tests/test_report_contract.py`），要求標為 `PARALLEL_EXCLUDED` 排除、**並確認除此之外沒有其他意外變更**。例外清單是列舉的，不是把檢查關掉。兩位都正確執行。

---

### 3. 核心設計（最終形狀）

```
runs/<台北日期>/<HHMM-slug-hash>/     ← Ticket 07 的佈局
runs/.<HHMM>-<hash>.run-claim         ← Ticket 07 的 claim
runs/index.db                         ← 本票；可重建衍生資料，非事實來源
runs/.index.lock                      ← 本票；flock 的錨點
runs/.index.db.<亂數>.tmp             ← 本票；rebuild 的暫存，安裝後即消失
```

**寫入路徑（兩條，共用同一把鎖）**

```python
deadline = time.monotonic() + _BUSY_TIMEOUT_SECONDS   # 單一預算，flock 與 SQLite 共用
with _writer_lock(runs_root, deadline):
    row = run_row(run_dir)          # ← 讀取在鎖內（第 5 輪）
    with _writing(data_root, deadline):
        _upsert(conn, row)
```

```python
with _writer_lock(runs_root, deadline):     # 掃描＋安裝全程持鎖（第 4 輪）
    scratch = mkstemp(dir=runs_root)
    ...建完整索引、commit、close...
    os.replace(scratch, index_db_path)       # 原子安裝（第 3 輪）
```

**競用判準**（第 7 輪定案）：

```python
_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})

except OSError as exc:
    if exc.errno not in _CONTENTION_ERRNOS:
        raise
```

這個集合是**兩條實際程式路徑各自契約的聯集**，不是從單一段文件推來的：

| 程式路徑 | 誰在用 | 競用 errno |
|---|---|---|
| native `flock(2)` | Linux／Apple——**本機實際走的** | `EWOULDBLOCK` |
| `fcntl(F_SETLK)` 模擬 | CPython 在沒有 native `flock` 的系統 | `EACCES`／`EAGAIN` |

Reviewer A 引 CPython 3.12.3 原始碼確認關鍵前提：

```c
#ifdef HAVE_FLOCK
    ret = flock(fd, code);
#else
    ret = fcntl(fd, (code & LOCK_NB) ? F_SETLK : F_SETLKW, &l);
#endif
```

> 失敗時直接 `PyErr_SetFromErrno`，**沒有 errno 正規化**——所以兩條路徑各自的 errno 會原樣傳出，聯集才是正確判準。

兩位都確認**沒有第四個** would-block errno：`flock(2)` 的其餘錯誤（`EBADF`／`EINTR`／`EINVAL`／`ENOLCK`）都不是「鎖被他人持有」；`EDEADLK` 屬於阻塞式 `F_SETLKW`，不是本模組用的 nonblocking `F_SETLK`。

**最終稽核**：`os.replace` **1 處**（`:447`）、`unlink` **1 處**（`:575`，只碰 scratch 衍生路徑）。

---

### 4. 本票的主軸：同一類錯誤在新場地重演

Ticket 07 花 6 輪治的是「擁有權的判定來自不是『我這次取得成功了嗎』的東西」。本票換了場地，同一類問題出現三次：

| 輪 | 判定的依據 | 為什麼不成立 | 提出者 |
|---|---|---|---|
| 1 | `_child_dirs()` 吞掉 `OSError` 回 `[]` | 把「我讀不到」變成「這裡沒東西」，而 `DELETE` 在同一交易 → **靜默清空整個索引** | 雙方 |
| 2 | probe 的結果 → 稍後才 unlink | 判定與刪除之間沒有身分綁定，**過期判定會刪掉別人剛建好的健康 DB** | 雙方獨立、同一行號 |
| 3 | live writer 在鎖外算好的列 | 資料在它為真時算好，在它不再為真時寫下去 → **孤兒列** | 僅 Reviewer A |

**收斂的方式每次都是換掉判定依據的來源，不是把窗口縮小：**

1. **第 1 輪**：列舉錯誤**外拋**，讓交易 rollback。「讀不到」與「讀得到但不是 run」分開處理。
2. **第 3 輪**：**取消掉判定本身**。不再探測「這個 DB 壞了嗎」，改成永遠建暫存再 `os.replace`——損毀、空檔、開不起來、被鎖住、健康全部同一條路徑，**因為沒有任何東西是有條件的**。
3. **第 4 輪**：`flock` 覆蓋 rebuild 的**掃描＋安裝**。不變式：取鎖前寫的列，其 manifest 早已落地、掃描一定看得到；取鎖後才寫的列，等到安裝完成、寫進新索引。沒有第三種可能。
4. **第 5 輪**：`run_row` 移進鎖內——**讀取與寫入必須在同一個臨界區**。

第 3 輪 Developer **拒絕了 Coordinator 與兩位 Reviewer 的建議**（跨 process 鎖），理由被兩位接受：

> Reviewer B：Developer 的替代設計確實比「先撤權、成功才恢復」更強——它**不需要建立或恢復任何 Python 授權狀態**。

而第 4 輪它自己也發現「只鎖安裝」不夠，**是一個存活的變異體 `rebuild_takes_the_lock_only_for_the_install` 逼它找到掃描窗口的**。

---

### 5. 逐輪 Finding 與處置

#### 第 1 輪（兩位都不簽署）
- **`[阻擋]`（雙方）** `_child_dirs()` 吞列舉錯誤 ＋ `DELETE` 同交易 → 靜默清空索引。
- **`[阻擋]`（僅 A）** 損毀 DB 的修復指示**本身跑不起來**：訊息叫你 `index-backfill`，而 backfill 在損毀 DB 上先跑 `_SCHEMA` 立刻失敗。**票面第一句「損毀可全量重建」沒有成立。**
  > B 的第 20 項驗的是「訊息說得夠不夠清楚」（通過）；A 驗的是「照著訊息做會不會成功」（不通過）。**查證宣稱本身，與查證宣稱指向的東西，是兩件事。**
- **`[阻擋]`（僅 B）** runbook §8 的防呆**沒有形成控制流程**——一串裸 `test`，最後 `printf` 把 exit code 蓋成 0，四個危險目標全部印出「確認目標」並回傳成功。（Coordinator 自行覆核採 B：A 測的是每個判斷式各自的 exit code，B 測的是整段照文件貼進 shell。**破壞性複製貼上流程要用後者。** 補記：**原始版本有一模一樣的缺陷**，Developer 是重寫時原樣搬過來的。）
- `[重要]`（雙方）readable manifest 不是嚴格的 FINALIZED 證明；`_connect_for_read` 在 `try` 外。
- `[重要]`（A）／`[建議]`（B）finalized hook 讀不到 run 時靜默回 False → **採 A**。
- `[建議]`（A）／`[重要]`（B）`--limit -1` 解除上限 → **採 B**。
- `[建議]`（雙方）測試名稱比斷言強。**Coordinator 升為必修**——理由不是那兩個測試，是 Developer 交件時宣稱「已修掉」而兩位獨立判定過度樂觀。

#### 第 2 輪（兩位都不簽署）
- **`[阻擋]`（雙方獨立、同一行號）** probe → unlink 的 TOCTOU：過期的損毀判定會刪掉另一個 process 剛建好的健康 DB。A 讓自己在 unlink 前確認過那是健康的，仍然刪了（`first_saw_before_unlink: [["ok"]]`）。
- `[重要]`（僅 A）健康 DB 測試只比對 inode，而 unlink 後檔案系統可立刻重用同一 inode。A 建的 `healthy_db_is_deleted` 變異體**存活**。
  > 這正是 Ticket 07 §9 ② 的結論——已釋放的 inode 不是持久身分。Developer 在該票花一輪把 inode 換成 nonce，本票卻拿 inode 當「沒被刪過」的證明。

#### 第 3 輪（兩位都不簽署）
- **`[阻擋]`（雙方）** Developer 自己列為「可接受剩餘風險」的並行 lost update，兩位獨立裁定擋票。
  > **Coordinator 採兩位的理由**：Developer 自己寫了「第 2 輪的單一交易設計會讓那個 upsert 阻塞到逾時（**可見的失敗**），現在是**安靜的**」——**它在修一個阻擋的過程中，把可見的失敗換成了安靜的資料遺失**。A 另指出「加一行警告不足以恢復正確性——live hook 本身不知道交換是否隨後發生」。
- `[建議]`（僅 A）`scratch_is_named_like_a_run_directory` 被**實作細節斷言**殺掉，不是被列舉行為殺掉——**mutation 被殺，不代表被該殺它的理由殺掉**。

#### 第 4 輪（兩位都不簽署，無阻擋）
- `[重要]`（僅 A）`run_row` 在鎖外快照 → 孤兒列（見 §4）。
- `[重要]`（雙方）flock 10 秒 ＋ SQLite 10 秒**疊加近 20 秒**，Developer 宣稱的「上限 10 秒」不成立。
- `[重要]`（僅 B）close-before-install 的測試只證明**後置條件**（函式返回後沒有 descriptor），沒證明**時序**（安裝之前已經關閉）。
  > **這一項 Coordinator 在派第 4 輪時漏轉達**，Developer 沒有忽略它。
- `[建議]`（僅 B）`_take_lock` 對所有 `OSError` 重試 → `EBADF` 等滿 timeout 後被報成「另一個程序占用鎖」。**Coordinator 升為必修。**

#### 第 5 輪（B 簽署，A 不簽署）
- `[重要]`（A）／`[建議]`（B）E4 **修過頭**：收窄成只重試 `BlockingIOError`，於是 `EACCES` 競用變成硬錯誤。
  > **Coordinator 採 A，但理由不是「我把 E4 列為必修」**，而是那個修正的形狀：**回報的是 FP 方向，修法造出 FN 方向**——本 Task 反覆懲罰的「只修被回報的那一個方向」。B 第 4 輪原本的建議就是「`BlockingIOError`，**或** `EACCES`／`EAGAIN`」，Developer 做了前半。
- `[建議]`（雙方）mutation runner 不 hermetic（B 第一次跑得到 55/4、第二次才 58/1）、無 compile preflight；E1 測試沒真的製造孤兒列。

#### 第 6 輪（兩位都不簽署，無阻擋）
- `[重要]`（B）／`[建議]`（A）集合**又不完整**，漏 `EWOULDBLOCK`，且註解**引錯出處**——把 Python 文件裡講 `lockf()` 的段落當成 `flock()` 的契約。**Coordinator 採 B。**
- `[重要]`（僅 A）**F2 的修法自己留了同一個洞**：`compiles()` 只編譯 `run_index.py`，但 runner 也 mutation `debate_driver.py`，driver 的 syntax error 被歸成 `KILLED_UNNAMED`。
  > F2 這整項存在的理由，就是第 5 輪挖出的「編譯不過卻被計入 killed」。

#### 第 7 輪（**兩位都簽署 🟢**）
- G1／G2 全部關閉。剩一個兩位都提的 `[建議]`（見 §9）。

---

### 6. Coordinator 的 A／B 分歧裁決（共 8 次）

| 輪 | 項目 | A | B | 採 | 理由 |
|---|---|---|---|---|---|
| 1 | runbook 防呆 | 通過 | `[阻擋]` | **B** | 破壞性複製貼上流程要驗「整段照貼」，不是「每個判斷式」。Coordinator 自行讀 §8 覆核 |
| 1 | `--limit -1` | `[建議]` | `[重要]` | **B** | Ticket 09 會直接餵前端輸入；一行修正 |
| 1 | 掛載點靜默失敗 | `[重要]` | `[建議]` | **A** | 該函式只在「已完成 run」被呼叫，回 False 不記 log 直接違反票面第 3 條 |
| 1 | 吞整個 `Exception` | 合理 | 合理 | 不改 | 收窄會讓索引內部的 `TypeError` 去阻止 run 完成，違反票面 |
| 2 | 健康 DB 測試只比 inode | `[重要]` | 通過 | **A** | inode 可回收，Ticket 07 已有結論 |
| 4 | 孤兒列（鎖外快照） | `[重要]` | 未找到 | **A** | B 第 3、28 項明確說「沒有找到第三種安靜交錯」 |
| 5 | E4 修過頭（EACCES） | `[重要]` | `[建議]` | **A** | 修正的形狀是「只修被回報的方向」；B 判「WSL 下現在不會壞」也對，一併記錄 |
| 6 | 集合漏 `EWOULDBLOCK` | `[建議]` | `[重要]` | **B** | 集合已錯兩次，兩次都因為從錯誤出處推導 |
| 6 | compile preflight 不完整 | `[重要]` | 通過 | **A** | B 只測了第一個 target，A 多測了第二個 |

---

### 7. Reviewer 委派的內容過濾防護（沿用 Ticket 03 起的規則）

每一份委派都：(1) 開頭聲明模組是我們自己的元件、本輪是一般正確性審查；(2) 明寫「**本輪請不要讀取 `docs/work/` 目錄**」並內嵌驗收條件；(3) 措辭中性。

本票 7 輪 14 次委派**零次觸發**。

---

### 8. Mutation testing 的演進（本票最有價值的副產品之一）

報表格式在本票被逐步逼緊，每一次逼緊都挖出真東西：

| 階段 | 要求 | 挖出什麼 |
|---|---|---|
| 第 1～3 輪 | 控制組（`null` 必存活、`poison` 必被殺） | 基本可信度 |
| 第 4 輪 | **`KILLED_BY_ASSERTION` 與 `TIMEOUT` 分開** | 第 5 輪 Developer 因此挖出 `index_failure_propagates` **從第 1 輪起就編譯不過**——那個「已被殺」的變異體**從未驗證過任何東西** |
| 第 5 輪 | Reviewer A 再追一層 | `FAIL` 與 `ERROR` 也被混在一起：**純 assertion failure 只有 39 個**，不是 Developer 報的 58 |
| 第 6 輪 | 七類分類 ＋ `py_compile` preflight ＋ 每 mutant `mkdtemp` | B 發現 runner 不 hermetic（55/4 → 58/1 漂移） |
| 第 7 輪 | Reviewer A 發現 preflight 只涵蓋第一個 target | **F2 的修法自己留了同一個洞** |

**結案分佈**（兩位各自獨立重跑，第二次與第一次**逐字相同**——B 以 sha256 比對整份輸出 `1cff0e357a45c35b...`、`cmp_rc=0`）：

```
總計 64      COMPILE_ERROR                 2   ← 兩條 preflight 自測，預期
            KILLED_BY_FAILURE            39
            KILLED_BY_ERROR              10
            KILLED_BY_FAILURE_AND_ERROR  12
            KILLED_UNNAMED                0
            TIMEOUT                       0
            SURVIVED                      1   ← null 控制組
```

64 隻全部攜帶自己的 target（`missing_targets=[]`）：`run_index.py` 56、`debate_driver.py` 4、`operator-runbook.md` 3、`null` 0。

**一個特別的變異體**：`contention_set_drops_the_ewouldblock_symbol` 在 Linux 上**值完全等價**（`EWOULDBLOCK == EAGAIN == 11`），執行期觀察不到差異。

```
值層：  with_symbol=[11,13]  without_symbol=[11,13]  runtime_equal=True   ← 抓不到
符號層：KILLED_BY_FAILURE
        killer: test_the_contention_set_names_every_errno_both_lock_paths_can_use
```

那條測試用 `ast` 解析原始碼、取出 `_CONTENTION_ERRNOS` 底下所有 `errno.<NAME>` 的屬性名比對。**值層測試抓不到證明了符號測試有存在必要；符號層抓得到證明了它不是裝飾品。** 值層斷言保留，但其註解明寫「它證明不了上面那件事」。

---

### 9. 結案時順延的 `[建議]`（兩位獨立提出同一項）

**`tests/test_run_index.py:1824-1826` — 舊測試 docstring 仍沿用錯誤歸因**

```
fcntl.flock is documented to report "would block" as either EAGAIN or EACCES
```

那正是造成 G1 整件事的說法。**產品註解、新的 AST 測試、實際行為全部正確**，只有這段測試說明文字是舊的。

> Reviewer B：不影響產品行為或測試決定性，但與本輪剛修正的權威鏈矛盾，**未來 Reviewer 可能再次把 `lockf/F_SETLK` 契約誤認成 native `flock`**。

建議改成「CPython 在缺少 native `flock` 時使用的 `fcntl(F_SETLK)` fallback 會回 `EACCES`／`EAGAIN`」。兩位都判**不擋票**，依標準順延。

---

### 10. 驗收條件重驗（第 7 輪，兩位各自獨立執行）

**① fixture run 完成後 index.db 查得到全欄位** — 13 欄到位，`outcome: null`。

**② 刪除 index.db → backfill → 查詢結果與刪除前一致** — `逐欄相同：True／差異：無`。

**③ 模擬索引寫入失敗（唯讀 db）→ run 照常 FINALIZED，log 有紀錄** — handshake `FINALIZED`、manifest／latest／report 齊全、stderr 有警告、解除唯讀後 backfill 補回。

**④ 全套全綠、案例數只增不減** — 見 §11。

**額外（第 1 輪 Reviewer A 的 `[阻擋]` 核心）**：損毀 → `index-query` exit 1 指向 `index-backfill` → backfill exit 0 → query 查得到。**照著錯誤訊息做真的會成功，而且不必手動刪檔。**

**runbook §8 逐字抽出整段執行，10 個目標**（兩位各自跑）：

```
normal                  rc=0 confirmed=True
runs_root               rc=1 confirmed=False      empty                   rc=1 confirmed=False
date_folder             rc=1 confirmed=False      missing                 rc=1 confirmed=False
data_root               rc=1 confirmed=False      file_not_directory      rc=1 confirmed=False
code_root               rc=1 confirmed=False      right_depth_wrong_root  rc=1 confirmed=False
workspace_root          rc=1 confirmed=False
```

那段測試**真的讀 `docs/operator-runbook.md`** 並抽出 §8 的第一個 bash fence，不是測試檔裡自己複製一份 shell——兩位都覆核過。

---

### 11. 回歸與全套

| 項目 | 結果 |
|---|---|
| 全套 | `Ran 1309 tests` — **OK**（兩位各自跑；46.6s／49.4s） |
| **本票案例數** | 1168 → 1236 → 1263 → 1268 → 1272 → 1277 → 1281 → **1282** |
| **Ticket 11 併行貢獻** | +27（`test_debate_rules` 100→117、`test_report_contract` 36→46） |
| 算術 | `1281 + 1 + 27 = 1309` ✅ 兩位各自核對 |
| Ticket 08 模組 | `tests.test_run_index tests.test_cli` — 126 案 OK |
| 禁改檔 180 案 | OK |
| Ticket 07 的 40 檔逐檔一致 | `40 vs 40`、`same_names=True`、唯一差異 `manifest.json` 的路徑鍵。**B 用兩個不同的 Code Root 與 Data Root**，所以三個鍵都真的不同 |
| `debate_driver.py` SHA | `4c9289b525a4`，第 2～7 輪逐字相同 |

**Developer 主動把全套數字拆開**並標示「那兩個模組的綠燈不是本票的證據」——併行工作一開始，全套數字就不再是單一票的證據。

---

### 12. 本票挖出的獨立事項：測試套件會消耗 codex 訂閱額度

第 2 輪 Developer 回報全套耗時 361 秒、兩位 Reviewer 卻量到 44 秒。追下去發現：

```
tests/test_debate_driver.py : run_launch=5 個呼叫點、proposition_adapter=0
→ default_proposition_adapter() → CodexExecAdapter → 真實的 codex exec
CODEX_CLI_PATH = "/home/leslie/.local/bin/codex"    ← 寫死的絕對路徑
PROPOSITION_TIMEOUT_SECONDS = 60
```

**未攔截地跑一次 `unittest discover` 會啟動 39 次 codex CLI ＝ 36 次 `exec`（`test_debate_driver`，30 個測試方法）＋ 3 次 `--version`（`test_system_cli`）。會消耗訂閱額度的是那 36 次。**

- Developer 把原因歸給 PATH 是**錯的**——路徑寫死，從 PATH 移除不會阻止呼叫。兩位都指出這一點。
- 兩位量到 44 秒是因為**它們的沙箱是唯讀的**，codex `148ms` 就初始化失敗（`failed to initialize in-process app-server client: Read-only file system`），沒碰到網路。
- 算術：`389 − 42 ≈ 347 秒 ÷ 36 ≈ 9.6 秒／次`，與 A 算的 8.9 秒／次一致。
- `test_debate_driver.py` SHA 與基準完全一致——**不是本票造成**。

`test_debate_driver.py` 在禁改清單內，Developer 正確地沒有動它。**此事項獨立記錄，需使用者核准才能處理**（修法很小：測試注入一個假 adapter）。自第 3 輪起，Developer 與兩位 Reviewer 一律使用 `PYTHONPATH=/tmp/t08-intercept` 在 `subprocess.run` 層攔截。

---

### 13. 本票期間的中斷：codex 額度耗盡

第 6 輪派工時兩位 Reviewer 同時失敗：

```
"You've hit your usage limit. ... try again at Aug 8th, 2026 11:32 AM."
credits: {"has_credits": false, "balance": "0"}
```

Reviewer A 該 session 累計 **38,118,534 token**。Coordinator 依流程**停止並回報**，未自行扮演 Reviewer、未宣稱三方共識，僅以 Coordinator 身分查證了全套與檔案歸屬供使用者決策。使用者更換帳號並在 **WSL**（非 PowerShell）重新 `codex login` 後恢復——這一點很關鍵，因為 `CODEX_CLI_PATH` 是 WSL 路徑、Reviewer 讀的是 `/home/leslie/.codex/auth.json`。

---

### 14. Developer 誠實標示且經覆核屬實的項目

1. 第 1 輪 Stage 0 的 red 是 `ImportError`（機制紅），**主動標示證據力不足**，另寫一版故意缺四條規則的實作取得四條行為紅。兩位判定充分。
2. 第 2 輪兩個新測試在舊程式上是 `TypeError`／`IndexError`（型別與機制不相容），**不是行為紅**；T2 的行為證據是跨樹腳本。兩位判定標示誠實、腳本必要且充分。
3. 第 3 輪它的第一版並行測試**對第 2 輪設計是通過的**（起始索引健康，第 2 輪不會 unlink），自己發現並改成損毀起點才決定性。而且觀察到的傷害比預期糟——不是 `MISSING` 而是 `ROWS:`（讀得起來但零筆），前端會畫成「查無資料」。
4. 第 3 輪一個存活變異體逼它查清楚**自己的 docstring 寫錯了**（說關閉連線是為了不留 rollback journal，但 SQLite 在 commit 時就刪了 journal），它沒有宣稱等價而是查明真相、改正註解、補測試殺掉。
5. 第 5 輪它挖出 `index_failure_propagates` 從第 1 輪起就編譯不過（見 §8）。
6. 真 ENOSPC 未跑到（無 passwordless sudo），改用 `RLIMIT_FSIZE`／`/dev/full`／mock；兩位判定替代充分。
7. 三次報出未實際量過的數字（「已修掉 2 條」實際 10、「只剩 4 條單斷言」實際 33、「39 次 exec」實際 36 exec＋3 version）。**Coordinator 於第 3 輪起要求「凡是報數字，要嘛附上產生它的指令，要嘛不要報」**，第 3 輪起遵守。

---

### 15. 未解風險（結案時記錄，兩位共同確認）

1. **`tests/test_run_index.py:1824` 的舊 docstring 仍沿用錯誤歸因**（§9）。
2. `_CONTENTION_ERRNOS` 是兩條路徑各自文件化 errno 的聯集；若某平台用第三個 errno 表示 would-block，會被當硬錯誤。本機實測走 native 路徑、回 `EAGAIN`(11)。
3. `EWOULDBLOCK` 在 Linux 與 `EAGAIN` 同值，其存在**只能在符號層驗證**；該測試讀原始碼，若未來把常數改成動態組成，測試需跟著改。
4. **run 在 `run_row` 返回之後才被刪** → 留下孤兒列，靠 backfill 對齊（有測試守著，先斷言孤兒存在再斷言消失）。任何鎖都無法回溯已發生的寫入。
5. `_BUSY_TIMEOUT_SECONDS` 是**等待**預算，不含 rebuild 的掃描工作時間；病態緩慢的檔案系統仍可能拖長 FINALIZED handshake。
6. **需要 POSIX `fcntl`**。原生 Windows 上 `cli`／`launcher`／`debate_driver`／`__main__` 全部 `ModuleNotFoundError`（`hoya_market_agents`、`live_dashboard`、`run_verifier` 仍可 import）。runbook 首段已明載所有命令在 WSL Ubuntu 執行。**未來若要支援原生 Windows，必須替換或封裝 flock，不只是延遲 import。**
7. 鎖只約束取這把鎖的 writer；第三方直接寫 index.db 不在保證內。
8. DrvFs 上 0400／0000 的 `index.db` 會讓 `os.replace` 失敗並保留原索引（ext4 會覆蓋）；已寫進 runbook。
9. SIGKILL 可能留下 `.index.db.*.tmp`（兩位裁定**不清理**——盲目刪除可能刪到另一個仍在執行的 rebuild）。
10. 能猜到本次 mkstemp 私有路徑的外部寫入者不在保證內（同 Ticket 07）。
11. **Ticket 12 的硬前置**：必須讓 `run_row` 讀 `outcome.json`，否則全量 backfill 會遺失驗證結果（`rebuild` 會 `DELETE FROM runs`）。已寫進原始碼註解。
12. `run_date` 直接信任日期夾名；非日期夾會列在 `unexpected_date_folders`。
13. 讀不到任何日期夾就整次 backfill 失敗（**刻意 fail-closed**）。
14. `competition_drill.py`（禁改）維持 manifest → latest 舊順序，演練 run 只由 backfill 收錄。
15. `index_finalized_run` 吞整個 `Exception`（含本模組自己的程式錯誤）——兩位裁定這是票面「不阻擋 run 完成」的合理直譯，收窄反而會讓索引的 bug 去阻止 run 完成。
16. 全套會發出 36 次真實 `codex exec`（§12，禁改檔，非本票造成）。
17. Mutation runner 位於 `/tmp`，屬 Review 證據工具而非產品交付物。

---

### 16. 雙 Reviewer 有效性

| 輪 | Reviewer A | Reviewer B | 只派一位會怎樣 |
|---|---|---|---|
| 1 | 找到「修復指示跑不起來」`[阻擋]` | 找到「runbook 防呆沒有控制流程」`[阻擋]` | **各自漏掉對方那個阻擋**；只派一位會帶著一個阻擋級缺陷結案 |
| 2 | 另找到 inode 測試的 `[重要]` | — | 只派 B 會讓一個假的「就地重建」保證留在測試裡 |
| 3 | 兩位獨立裁定同一個 `[阻擋]` | 同左 | 收斂一致，強化了裁定 |
| 4 | 找到孤兒列 `[重要]` | 找到 close-before-install 時序 `[重要]` | **B 明確說「沒有找到第三種安靜交錯」，A 找到了**；A 判該時序測試有決定性，B 用更精確的變異體推翻 |
| 5 | 不簽署（E4 修過頭） | 簽署 | **只派 B 會讓「只修被回報方向」的錯誤直接結案** |
| 6 | 找到 compile preflight 的第二個 target | 找到集合漏 `EWOULDBLOCK` | **各自漏掉對方那一項**；兩項都是本輪必修 |
| 7 | 🟢 簽署 | 🟢 簽署 | 兩位獨立提出**同一個** `[建議]` |

第 4 與第 6 輪最能說明價值：**兩位各自找到對方完全沒看見的東西，而且都是必修等級。**

---

### 17. 共識

- Developer：`Ready for Review（第 7 輪）`，未自我核准、未標記完成、未接下一票、未派 Reviewer、未動 git `add`／`commit`。
- **Reviewer A：🟢，兩軸通過，致命問題「無」，「Reviewer A 簽署 Ticket 08 通過。」**
- **Reviewer B：🟢，兩軸通過，致命問題「無」，「Reviewer B 簽署 Ticket 08 通過。」**
- Coordinator：三方共識成立，Ticket 08 結案。

兩位 `review_engine` 皆為 `native`，無 OCR delegation、無降級。

---

### 18. 給後續 Ticket 的教訓

1. **吞掉錯誤，就是把「我不知道」變成一個有自信的錯誤答案。** 本票三次栽在這上面：列舉失敗當成空目錄、過期判定當成刪除授權、`EBADF` 當成「另一個程序占用鎖」。
2. **讀取與寫入必須在同一個臨界區。** 在鎖外算好的東西，寫下去時可能已經不再為真。
3. **修 FP 方向時要同時檢查 FN 方向。** 本票第 5 輪把「非競用被誤稱競用」修成「真競用被當硬錯誤」，第 6 輪又漏一個 errno——**兩次都是從錯誤的出處推導集合**。
4. **註解宣稱某個權威背書了你的做法時，去讀那個權威。** 引到了講別的函式的段落，整個集合就錯了。
5. **mutation 的數字要能分辨三件事**：測試斷言抓到了、程式炸了、它根本沒跑起來。混在一起就會出現「四輪都計入 killed、但從未執行過」的變異體。
6. **mutation 被殺，不代表被該殺它的理由殺掉。** 要確認 killer 是名字宣稱該規則的那個測試。
7. **在執行期完全等價的變異體，需要符號層的測試。** 值層測試抓不到它，正好證明符號層測試有存在必要。
8. **修一個阻擋時，注意有沒有把可見的失敗換成安靜的失敗。** 那通常是退步，不是進步。
9. **驗證「訊息說得清不清楚」與「照著訊息做會不會成功」是兩件事。**
10. **破壞性的複製貼上流程，要驗整段照貼的結果**，不是每個判斷式各自的 exit code。
11. **併行工作一開始，全套數字就不再是單一票的證據**——要拆開，並列舉排除的檔案而不是關掉檢查。
