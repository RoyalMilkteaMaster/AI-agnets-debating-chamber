# 07 Phase 4a：run 目錄日期分層

- 狀態：完成（三方共識，6 輪）
- Spec：`../spec.md`（Phase 4）；ADR 0005
- Blocked by：06

## 目標

新 run 落在 `_data/runs/YYYY-MM-DD/HHMM-題目slug-hash/`，人眼按日期＋題目即可瀏覽。

## 使用者價值

「要按照日期、問的問題來規劃資料夾」。

## 範圍

1. `run_store`：run 目錄建立改為日期分層（日期夾用 Asia/Taipei；run_id 內部仍含 UTC 時戳保證唯一）；目錄名含題目 slug。
2. run 內部檔案契約完全不變（write-once／append-only／快照語意原樣）。
3. `latest.json` 格式保留、指向新分層路徑；讀者（`codex_bridge`、`run_verifier`、直播/前端讀取點）同步。
4. Windows 檔名安全：slug 過濾非法字元、長度上限。

## 已確認實作決策

- 舊 run 已於 01 全刪，無歷史搬遷。
- run_id 字串格式維持可回溯（含 UTC 時戳＋slug＋短 hash）。

## 驗收條件

- fixture launch 後 run 目錄位於 `runs/<今日 Asia/Taipei 日期>/<HHMM-slug-hash>/`，內部檔案結構與現制逐檔一致。
- `latest.json` 指向新路徑且 `verify-run` PASS。
- 含中文／特殊字元的題目 slug 在 Windows 與 WSL 都能建目錄。
- `python3 -m unittest discover -s tests`（WSL）全綠、案例數只增不減。

## 測試與證據

- 測試接縫：run_store 暫存目錄測試（既有 test_run_store 模式）。
- 必跑指令：`python3 -m unittest discover -s tests`（WSL）。
- 必交證據：新 run 目錄樹、verify-run 輸出、測試結果、變更摘要。
- 保存位置：本 Ticket 的「執行與 Review 紀錄」。

## 依賴

- Depends on：06
- Blocks：08

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
| 開票前基準案例數 | 1080 |
| 結案案例數 | **1168** |
| 輪次 | **6 輪**（每輪雙 Reviewer 獨立審查） |

環境注意事項（沿用前票，本票同樣適用）：

- `PYTHONDONTWRITEBYTECODE=1` 為強制——殘留 `__pycache__` 曾造成假紅。
- Diff 一律加 `--ignore-cr-at-eol`——本 repo 行尾混用。
- `codex exec resume` 不吃 `-C`／`--sandbox`，需要 `-c sandbox_workspace_write.network_access=true`。
- Reviewer 委派一律禁止讀取 `docs/work/`，驗收條件內嵌於委派文字（見 §7）。

---

### 2. 最終變更歸屬

本票累計（對開票前基準）：

| 檔案 | 增／刪 |
|---|---|
| `hoya_market_agents/run_store.py` | +555 / −19 |
| `tests/test_run_store.py` | +1098 / −1 |
| `hoya_market_agents/live_dashboard.py` | +56 / −14 |
| `hoya_market_agents/codex_bridge.py` | +17 / −7 |
| `hoya_market_agents/run_verifier.py` | +3 / −2 |
| `hoya_market_agents/launcher.py` | +1 / −1 |
| `hoya_market_agents/competition_drill.py` | +1 / −1 |
| `tests/test_live_dashboard.py` | +85 / −9 |
| `tests/test_codex_bridge.py` | +54 / −2 |
| `tests/test_cli.py` | +18 / −7 |
| `tests/test_launcher.py` | +8 / −4 |
| `tests/test_run_controller.py` | +6 / −1 |
| 另 4 份文件 | — |

最終 Snapshot：`run_store.py=04fa8001ae59`、`test_run_store.py=1f1969da4537`。

**保護檔案**（兩位 Reviewer 各自獨立以全樹逐檔 sha256 覆核，全部 UNCHANGED）：

```
test_verify_run.py                 8e320063f7b2
test_debate_driver.py              5f14526eb5c1
test_competition_drill.py          18def58caf2c
test_reviewer_complete_attack.py   d9b94b50522d
```

Ticket 06 三檔 SHA 亦未變。13 個禁改檔全部 UNCHANGED。

---

### 3. 核心設計：run_id 的取得與歸還

目錄佈局：

```
runs/<台北日期>/<HHMM-題目slug-hash>/
runs/<台北日期>/.<HHMM>-<hash>.run-claim        ← claim，兩行
```

```python
def run_dir_hash(run_id):
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]

MAX_RUN_DIR_SLUG_BYTES = 233   # 205 + (16−4) + (32−16)；4+1+233+1+16 = 255 = NAME_MAX
```

claim 內容（第 6 輪起為兩行）：

```
20260806T154612Z-btc-ccf75a
f60ccdf6d6ec58791ec705a5b0ffd29a      ← 本次 acquisition 的 nonce
```

**釋放條件（合取，四項皆必要、無一單獨充分）：**

```python
def _release_run_id(claim, run_id, taken, started_directory):
    if not taken:                                          # ① 沒取得 → 不釋放
        return
    if started_directory is not None and started_directory.exists():
        return                                             # ② 半成品還在 → 純否決
    # ③ inode 相符  ④ run_id 相符  ⑤ nonce 相符
    claim.unlink(missing_ok=True)
```

`(inode, nonce)` 在 `os.link` **之前**就放進 `taken`——這是刻意的，**不可改成 link 之後**（見 §5 第 3 輪）。授權不是 `taken` 本身，而是「磁碟上的 claim 真的帶著本次的 nonce」，而只有本次的 `os.link` 能把它放上去。

**最終授權面稽核（兩位獨立 grep 覆核一致）：**

| 稽核項 | 數量 |
|---|---|
| `claim.unlink` | **1 處**（`_release_run_id` 內） |
| `_release_run_id` | 1 定義 ＋ **2 個呼叫處**，皆傳同一個 `taken` |
| `taken` 寫入語句 | **1 處**（只有 `append`；`clear` 已於第 6 輪消失） |
| claim 判斷中的 `run_path.exists()` | **0 處** |

---

### 4. 本票的主軸：同一類錯誤的四種變體

這張票花了 6 輪，四個阻擋級 Finding **全是同一類**——**擁有權的判定來自某個「不是『我這次取得成功了嗎』」的東西**。

| 輪 | 用什麼決定「這是我的」 | 為什麼不成立 | 提出者 |
|---|---|---|---|
| 3 | `mkdir` 回來之後才設的布林旗標 | 旗標要等 mkdir 返回才寫，中斷落在中間就沒賦值 | 雙方 |
| 4 | 有沒有被某個私有例外攔到 | 例外自己要先建構完成才攔得到，建構也可被中斷 | 雙方 |
| 5 | `run_path.exists()` ＋ claim 內容 | 兩者都不能證明「**這一次呼叫的** link 成功了」 | 雙方 |
| 6a | `taken.clear()` 這一行 | 清理已確定失敗，卻還要再跑一行撤權才生效 | Reviewer B |
| 6b | unlink 後可被回收的 inode 號碼 | ext4 實測**第一次配置就重用**；不是持久身分 | Reviewer B |

**收斂的方式不是逐一堵漏，而是換掉判定依據的來源：**

1. **授權**（第 5 輪）：綁在不可搶先的磁碟事實上——只有成功的 `os.link` 能讓 claim 帶著我的東西。
2. **撤權**（第 6 輪）：**取消掉「需要被執行的撤權動作」**。半成品在不在，改成釋放當下才問磁碟的純否決。
3. **身分**（第 6 輪）：從「可回收的 inode 號碼」換成「每次取得都新鮮的 nonce」。

第 6 輪 Developer **拒絕了 Coordinator 與 Reviewer B 建議的「先撤權、成功才恢復」**，理由正確且被兩位 Reviewer 採納：

> 那只是把窗口移位——中斷若落在進入 handler 之後、第一行撤權之前，授權照樣還在而清理還沒跑。

> **Reviewer B 覆核：** Developer 的替代 T1 設計確實比「先撤權、成功才恢復」更強：它**不需要建立或恢復任何 Python 授權狀態**。

結果是程式**比第 5 輪還簡單**：`taken` 從兩個寫入者減為一個。

---

### 5. 逐輪 Finding 與處置

#### 第 1 輪
- **Q1** 目錄 hash 只取 run_id 前綴 → near-miss run 撞名。改為對**完整 run_id** 取 sha256。
- **Q2** claim 用 check-then-create → 兩個呼叫可同時取得。改為 `os.link` 原子落地。
- **Q3** token 未驗證 → 可注入路徑分隔字元。**Reviewer A 判 `[建議]`、Reviewer B 判 `[重要]`，Coordinator 採 B。**
- **Q5** dashboard 對半成品 run 的行為。
- **Q6** slug 過濾把附著在保留字元上的 Unicode Mark 一起吃掉。**Reviewer A 判 `[重要]`、B 判 `[建議]`，Coordinator 採 A**（結構性一行修正）。

#### 第 2 輪
- **R1** 三個故障注入點的 claim 行為。

#### 第 3 輪 —— 變體一
- **S1**：`mkdir` 回來之後才設的布林旗標當擁有權依據。中斷落在 mkdir 與賦值之間 → 旗標為假 → 誤放。

#### 第 4 輪 —— 變體二
- 改用「有沒有被私有例外 `_RunIdAlreadyTaken` 攔到」判斷。**例外物件的建構本身可被中斷**，且 `_discard_quietly` 吞掉的 `OSError` 會遮蔽它 → 誤放既有 claim。
- **B4**：`/api/runs` 會列出 claim 形狀的目錄。**Reviewer A 說要過濾，Reviewer B 說這符合既有「所有壞掉的目錄都照列」契約，Coordinator 採 B——不修改。**

#### 第 5 輪 —— 變體三
- 改用 `run_path.exists()` ＋ claim 內容。兩者都不能證明「本次 link 成功」。
- 修法：授權 = `claim.stat().st_ino == 本次 scratch 的 inode`，`taken` 在 link **之前**填好。
- **Reviewer A 簽署通過；Reviewer B 不簽署**，提出下面兩項。

#### 第 6 輪 —— 變體四與五
- **T1 `[阻擋]`（僅 B）**：`_remove_started_directory` 已回 `False`、`taken.clear()` 尚未執行時中斷 → 外層仍釋放 claim。

  ```
  cleanup_result [False]   corpse_exists True   claim_count 0
  retry_succeeded True     directory_count 2    resolve_after_retry None
  ```

- **T2 `[重要]`（B 提；A 判可接受，Coordinator 採 B）**：

  ```
  reuse_found True attempt 1 inode 335708          ← 第一次配置就重用
  release_without_A_link before True after False   ← 從未成功 link 的舊呼叫刪掉了新 caller 的 claim
  ```

  **Coordinator 採 B 的理由**：單一寫入者模型的意思是「一個 process 擁有 Data Root」，**不是「只會有一次 `create_run` 呼叫」**。claim 這個機制存在的理由就是多個呼叫會競爭同一個 run_id，所以「另一個 caller 合法取得同一個 run_id」在模型**之內**。且 B 實測第一次配置就重用，「機率可忽略」不成立。

- **T3 `[建議]` → Coordinator 升為必修（兩位都提）**：`create_run` docstring 寫 `taken`「only after its own link returned」，與刻意的 link-前記錄相反。

  > **Reviewer A 的影響評估：** 未來維護者若依註解移動 append，會**重建已知的 post-link interruption 缺口**。

  一段告訴下一個人去重建你花三輪才修好的 bug 的註解，不是普通的文件失準。已改為明確反向警告；變異體 `taken_recorded_after_the_link` 守著它。

- **T4 `[建議]`（僅 A）**：`st_ino == 0` 未 fail-closed。**採 nonce 後自動涵蓋**，兩位實測確認。

---

### 6. Coordinator 額外要求查證、Developer 未主動討論的三項

第 6 輪 Coordinator 在委派中另加三項；**三項全部命中**，其中兩項推翻了 Developer 的樂觀描述。

| 項目 | 結果 |
|---|---|
| claim 格式 1 行→2 行的**其他讀者** | ✅ 安全。production 只有 `run_store` 讀內容；`resolve_run_dir`／兩個列舉者／`/api/runs`／`live_dashboard`／`run_verifier`／`codex_bridge` 都不解析內容，列舉者以 `is_dir()` 排除 claim 檔 |
| nonce 有沒有洩進逐檔比對的產物 | ✅ 安全。固定 nonce 跑完整 fixture：`nonce_hits_run_dir []`、`RUN_FILES 40`，只存在 claim 檔 |
| **冗餘的 inode 比較會不會誤拒** | ⚠️ **會**。Developer 稱「留著不痛」，兩位獨立實測推翻 |
| **`started_directory.exists()` 自己丟例外** | ⚠️ **會遮蔽原例外**。Developer 完全未提 |

---

### 7. Reviewer 委派的內容過濾防護（本 Task 通用教訓）

本 Task 在 Ticket 03 兩度撞上 codex 的內容過濾器，其中一次**燒掉 1,051,661 token 才中斷**——而且是在讀 Coordinator 自己寫的 Ticket 散文（「偽造前／偽造後」「打穿」）時觸發。

自 Ticket 03 起，**每一份 Reviewer 委派都必須**：

1. 開頭聲明模組是我們自己的元件、本輪是一般正確性審查。
2. **明寫「本輪請不要讀取 `docs/work/` 目錄」**，並把該票所有驗收條件內嵌於委派文字。
3. 措辭中性——不用「攻擊」「繞過」「偽造」「打穿」等詞。

本票 6 輪 12 次委派全部遵守，**零次觸發**。

---

### 8. Mutation testing（第 6 輪最終）

於 `/tmp` 副本執行，工作樹唯讀；**控制組先跑**：

```
null                                  SURVIVED  ✅ 控制組（程式換成自己 → 必須存活）
poison                                KILLED    ✅ 控制組（無條件 raise → 必須被殺）
```

```
utc_date_folder                KILLED   release_needs_no_take_at_all       KILLED
no_character_filter            KILLED   taken_recorded_after_the_link      KILLED ← T3
no_length_cap                  KILLED   scratch_cleanup_can_replace_...    KILLED
ignore_question                KILLED   release_ignores_the_nonce          KILLED ← T2
run_id_from_folder_name        KILLED   nonce_is_not_fresh                 KILLED ← T2
hash_from_token_only           KILLED   veto_dropped_entirely              KILLED ← T1
hash_ignores_the_seconds       KILLED   veto_remembered_instead_of_asked   KILLED ← T1
no_token_validation            KILLED   corpse_is_left_behind              KILLED
drop_combining_marks           KILLED   claim_is_check_then_link           KILLED
keep_orphan_marks              KILLED   directory_cleanup_not_nested       KILLED
```

**21/22 killed。** Coordinator 票面指定的兩個變異體——`veto_remembered_instead_of_asked`（忠實重建第 5 輪那三行形狀）與 `release_ignores_the_nonce`（只用 inode 不驗 nonce）——都被殺。

**唯一存活者 `release_ignores_the_inode`**：Developer 誠實標示 inode 採用 nonce 後已不承重，並主動說「若兩位偏好精簡，刪掉它是安全的」。兩位覆核後**都不同意「無害」這個描述**（見 §9 第 2 項）。

控制組的必要性（本 Task 在 Ticket 03 學到）：沒有 null／poison 對照，「測試抓到了」與「什麼都沒跑」看起來完全一樣。

---

### 9. 結案時的兩個 `[建議]`（兩位獨立提出，同一行號）

兩位 Reviewer 在完全隔離的情況下給出**行號相同**的兩個 `[建議]`，且**都明確拒絕升為 `[重要]`**（不造成錯誤釋放、資料覆寫或重複 run）。依本 Task 既定標準（`[阻擋]`＋`[重要]` 必修、`[建議]` 順延）順延。

**① `run_store.py:471` — `started_directory.exists()` 的 `OSError` 會遮蔽原始失敗**

```
CALLER_SAW PermissionError [Errno 13] DENIED_DURING_RELEASE
DIRECTORY_GONE True   CLAIM_KEPT True   RETRY RunAlreadyExistsError
```

該檢查位於外層 `except BaseException` handler 內，例外不會再被同層攔截。**安全上 fail-closed**（claim 保留、無目錄殘留、第三 caller 被拒），但呼叫端看到的是釋放檢查的 `PermissionError`，而非原始的建立失敗；該 run_id 也被占住。

> 建議修法（兩位一致）：在該檢查捕捉 `OSError` 後直接 `return`，讓不確定性仍然是 veto，同時保留原例外繼續傳出。

**② `run_store.py:475` — 不承重的 inode 比較可能造成可用性誤拒**

```
MATCHING_NONCE_WRONG_INODE claim_kept True   retry RunAlreadyExistsError
inode_mismatch_correct_nonce survived True   intact True
```

nonce 與 run_id 完全正確、僅 inode 不符時，claim 永久保留、retry 被拒 → **run_id 被燒掉**。

> **Reviewer A：** 不應稱為完全無害的冗餘。
> **Reviewer B：** 在 inode 回報不穩定的檔案系統上可能造成永久 fail-closed。

在本票指定的 ext4／NTFS 環境，hard link 的 inode 穩定相同，兩位實測未自然重現。建議二擇一：移除 inode 條件，或把「支援的檔案系統必須提供穩定 `st_ino`」明列為平台前提。

---

### 10. 驗收條件重驗（第 6 輪，兩位各自獨立執行）

**① 真實時鐘 → 日期分層目錄，hash 以外部 `sha256sum` 當預言機**

```
SAMPLED_UTC 2026-08-06T16:07:33Z
RUN_ID     20260806T160733Z-btc-r6real
RUN_REL    runs/2026-08-07/0007-btc-過去-14-日的市場狀態如何-fb06e62ec5f8bf30
HASH_EXTERNAL fb06e62ec5f8bf30   NAME_MATCH True
```

台北跨日正確（UTC 08-06 16:07 → 台北 08-07 00:07）。

**② `latest.json` 五鍵不變、`verify-run` 通過**

```
['debate_html', 'report_html', 'report_md', 'run_dir', 'run_id']
latest_matches True
status VERIFIED   seat_count 7
```

**③ Windows 對抗性名稱 8/8**

Reviewer A 另以 **Windows PowerShell 經 UNC 路徑**建立，再回 WSL 驗證：

```
WINDOWS_CREATED=8   WSL_VISIBLE=8   SETS_EQUAL=True
```

涵蓋純非法字元、`CON.`、中文、天城體 combining marks、阿拉伯附標、全形、emoji、長中文。

**④ 開票前後同 token 重跑 → run 內部逐檔一致**

```
FILE_COUNTS 40 40   SAME_SET True
BYTE_DIFFS [('manifest.json', 'bytes')]
MANIFEST_DIFF_KEYS ['code_root', 'data_root', 'run_dir']
```

39 檔 byte-identical，唯一差異是 `manifest.json` 的三個路徑鍵。**run 目錄內一個檔案都沒多**，claim 檔在日期夾、不在 run 內。

---

### 11. 回歸與全套

| 項目 | 結果 |
|---|---|
| 全套 | `Ran 1168 tests in 42.6s / 43.0s` — **OK**（兩位各自跑） |
| Ticket 03／04 保護 180 案 | OK |
| `run_store + codex_bridge + live_dashboard` 157 案 | OK |
| `tests.test_run_store` 67 案 | OK |
| Q2 原子競爭 race 測試 | A 獨立跑 5 次全綠；B 跑 3 次全綠 |
| 第 1～5 輪 Finding（Q1／Q2／Q3／Q5／Q6／R1／S1／第 4 輪 scratch cleanup／第 5 輪 inode reuse 與撤權中斷） | 全部未回歸 |
| 案例數演進 | 1080 → 1127 → 1150 → 1158 → 1163 → 1166 → **1168** |

---

### 12. Developer 誠實標示的項目（兩位覆核，標示屬實）

Developer 主動標示三件對自己不利的事，兩位覆核後確認**標示誠實**：

1. 兩個新測試放到第 5 輪程式上是 `TypeError: 'int' object is not subscriptable` 與 `IndexError: list index out of range`，屬**型別與機制不相容的 ERROR，不是行為 red**。T2 的行為證據是另一支跨樹腳本。兩位確認該腳本**必要且充分**。
2. 變異體 `release_ignores_the_inode` 存活，inode 已不承重（兩位進一步指出它也不是無害的，見 §9）。
3. Reviewer A 構造的「第二個寫入者把本次呼叫的 scratch 直接 link 成 claim」**nonce 關不掉**——因為那份 scratch 就帶著本次的 nonce。Developer 不宣稱已解決。

```
EXTERNAL_EXACT_SCRATCH_LINK claim_released True
```

兩位一致：這需要外部操作者取得本次的私有 scratch 路徑，**不屬於透過 `RunStore.create_run` 競爭的合法 caller**，仍在已聲明的單一寫入者模型之外。

---

### 13. 未解風險（結案時記錄，兩位共同要求）

1. `started_directory.exists()` 的 `OSError` 會遮蔽原始失敗（§9 ①）。
2. inode 比較在 `st_ino` 不穩定的檔案系統上可能造成 fail-closed 可用性損失（§9 ②）。
3. `mkstemp` 返回前被硬中斷 → 留下 scratch 檔與 descriptor。不燒 run_id、不污染列舉，已記錄於 docstring。
4. `exists`／`stat`／`unlink` 無法完成時可能永久 fail-closed。
5. 128-bit nonce 理論碰撞。
6. **保證限定單一寫入者模型**；能直接操作本次 scratch 路徑的外部寫入者不在保證內。
7. 硬砍（SIGKILL）／斷電不在守備範圍——兩位認定本票沒有製造這類 window。
8. 並行測試是機率性的（單機經驗估計、非上界）；機制那一半由 link 測試決定性覆蓋。
9. claim 累積（每 run 約 60 bytes，與日期夾同生共死）；64-bit 目錄 hash 碰撞造成無謂拒絕（可忽略）；收不掉目錄時 run_id 永久占用（**刻意取捨，有測試**）。
10. 舊格式（一行）claim 會 fail-closed 拒絕釋放。舊 run 已於 Ticket 01 全刪，實務上不存在。
11. `resolve_run_dir` 仍是掃描 → **Ticket 08 的索引處理**。
12. legacy `RunController` 未傳 question；Windows `MAX_PATH`(260) 未處理（既有限制）；`folder` 是 `/api/runs` 新增欄位，Phase 5 由 webapp 取代時一併重新設計。

---

### 14. 雙 Reviewer 有效性

| 輪 | Reviewer A | Reviewer B | 若只派一位會怎樣 |
|---|---|---|---|
| 1 | Q6 判 `[重要]`（採 A） | Q3 判 `[重要]`（採 B） | **兩項各有一位低估**，只派一位會漏掉一個 |
| 4 | 主張過濾 claim 形狀目錄 | 指出符合既有契約 | 只派 A 會做出不必要的改動 |
| 5 | **簽署通過** | **不簽署**，提出 T1 `[阻擋]` ＋ T2 `[重要]` | **只派 A 會帶著一個可決定性重現的阻擋級缺陷結案** |
| 6 | 🟢 簽署 | 🟢 簽署 | 兩位獨立給出**行號相同**的兩個 `[建議]` |

第 5 輪是本 Task 目前為止雙 Reviewer 價值最高的一次：**A 已經簽了字，B 用一支腳本把它推翻。**

Coordinator 在本票做了 4 次 A／B 分歧裁決：Q3 採 B、Q6 採 A、B4 採 B（不修改）、T2 採 B。

---

### 15. 共識

- Developer：`Ready for Review（第 6 輪）`，未自我核准、未標記完成、未接下一票、未派 Reviewer、未動 git `add`／`commit`。
- **Reviewer A：🟢，致命問題「無」，「Reviewer A 簽署 Ticket 07 通過。」**
- **Reviewer B：🟢，致命問題「無」，「Reviewer B 簽署 Ticket 07 通過。」**
- Coordinator：三方共識成立，Ticket 07 結案。

兩位 `review_engine` 皆為 `native`，無 OCR delegation、無降級。

---

### 16. 給後續 Ticket 的教訓

1. **擁有權必須來自「我這次取得成功了嗎」這件已經成立的事實**，不能來自任何還需要被執行的語句。本票四次違反、四次修正。
2. **當你發現自己在想辦法讓某一行早點跑，方向就錯了**——要問的是「這一行能不能不存在」。第 6 輪 Developer 拒絕 Coordinator 與 Reviewer B 的建議、改成釋放當下問磁碟，程式反而比前一輪簡單。
3. **「冗餘所以無害」需要證明，不能宣稱。** 冗餘檢查的反方向是誤拒；本票的 inode 比較就多開了一條燒 run_id 的路徑。
4. **修改資料格式時，要查的是所有讀者，不只是你改的那條路徑。** Developer 只論證了釋放路徑對舊格式 fail-closed；claim 兩行格式的相容面是 Coordinator 額外指定才查的。
5. **mutation 一定要有 null／poison 控制組**，否則「測試抓到了」與「什麼都沒跑」無法區分。
6. **Reviewer 委派禁止讀取 `docs/work/`**，措辭保持中性——本 Task 曾為此燒掉 105 萬 token。
