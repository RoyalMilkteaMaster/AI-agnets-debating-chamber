# AI agnets debating chamber

![AI agnets debating chamber](docs/assets/readme-hero.png)

七席 AI 一起研究一個市場問題、公開辯論、然後投票，最後給你一份可以離線閱讀的報告。

這份說明假設你完全沒有用過 WSL、也沒有裝過任何一個 AI CLI。從第 1 步照著做就好。

- 需要的環境：Windows 10 或 Windows 11。
- 每段指令都標了要在哪裡貼：`[Windows]` 是 Windows 的 PowerShell 視窗，
  `[WSL／Ubuntu]` 是 Ubuntu 的黑底終端機。
- 已經裝過 WSL2 Ubuntu 的人：跳過第 1、2 步，直接從第 3 步開始；
  但第 4 步的設定**每台機器都要跑一次**，捷徑就是那一步建立的。

---

## 1. 在 Windows 安裝 WSL2 Ubuntu

按開始鍵，輸入 `PowerShell`，在「Windows PowerShell」上按右鍵選「以系統管理員身分執行」，
然後貼上這一行：

```powershell
# [Windows]
wsl --install -d Ubuntu
```

## 2. 重新開機，然後建立 Ubuntu 帳號

重新開機。開機後 Ubuntu 會自己跳出一個黑色視窗，要你輸入使用者名稱與密碼。

- 使用者名稱用小寫英文，不要有空白。
- 輸入密碼時畫面不會有任何反應，這是正常的，打完直接按 Enter。

如果沒有自己跳出來，按開始鍵輸入 `Ubuntu` 並開啟它。

看到 `你的名字@電腦名:~$` 這樣的提示字元，就是成功了。之後所有 `[WSL／Ubuntu]` 的指令
都貼在這個視窗裡。

## 3. 在 Ubuntu 裡把程式抓下來

```bash
# [WSL／Ubuntu]
sudo apt update
sudo apt install -y git python3
```

```bash
# [WSL／Ubuntu]
cd ~
git clone https://github.com/RoyalMilkteaMaster/AI-agnets-debating-chamber.git
cd AI-agnets-debating-chamber
```

`cd ~` 是回到你自己的家目錄。程式會放在 `~/AI-agnets-debating-chamber`，
執行結果會放在它旁邊的 `~/AI-agnets-debating-chamber_data`，兩者不會互相覆蓋。

## 4. 執行一次設定

程式抓下來之後、第一次啟動之前，先跑這一次設定（每台機器跑一次；
之後重跑也無害）：

```bash
# [WSL／Ubuntu]
cd ~/AI-agnets-debating-chamber
bash setup-wsl.sh
```

這一步會檢查環境，並建立兩個捷徑：**開啟辯論室**、**關閉辯論室**——你的 Windows
桌面一份、專案的 `scripts\` 資料夾裡也一份（在檔案總管裡雙擊就能用）。

它不會替你安裝或登入任何 AI CLI，也不會碰你的帳號密碼。重複執行結果一樣，捷徑不會
越長越多。

## 5. 安裝並登入三個 AI CLI

三個都要裝，因為七席分別由它們支援。每一段的第二行會開啟互動登入，照畫面指示用瀏覽器
登入你自己的帳號即可。

**Codex（OpenAI）**

```bash
# [WSL／Ubuntu]
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex
```

**Claude Code（Anthropic）**

```bash
# [WSL／Ubuntu]
curl -fsSL https://claude.ai/install.sh | bash
claude
```

**Antigravity CLI（Google）**

```bash
# [WSL／Ubuntu]
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy
```

裝完後關掉 Ubuntu 視窗再開一次，讓 `PATH` 生效。用下面這行確認三個都找得到：

```bash
# [WSL／Ubuntu]
command -v codex claude agy
```

三行路徑都印出來才算完成。少哪一個就回去重跑那一段。

> 指令來源：Codex 為 OpenAI 官方文件（`https://developers.openai.com/codex/cli`）、
> Claude Code 為 Anthropic 官方文件（`https://code.claude.com/docs/en/quickstart`）、
> Antigravity 為 Google 官方文件（`https://antigravity.google/docs/cli/install`）。

## 6. 開始使用

下面的兩個捷徑都是第 4 步 setup 建立的（桌面與 `scripts\` 各一份）；
找不到捷徑就回去把第 4 步跑完。

**用桌面捷徑（最簡單）**

- 雙擊 **開啟辯論室**：辯論室會在背景啟動，瀏覽器自動打開。
- 雙擊 **關閉辯論室**：關掉它。

**或者在 Ubuntu 視窗裡**

```bash
# [WSL／Ubuntu]
cd ~/AI-agnets-debating-chamber
./START-HERE.sh
```

```bash
# [WSL／Ubuntu]
cd ~/AI-agnets-debating-chamber
./STOP-HERE.sh
```

網址固定是 `http://127.0.0.1:8765/`。在頁面上輸入你的市場問題就會開始一場分析。

分析進行中時，`關閉辯論室` 捷徑不會直接關掉它——中斷分析需要有人明確同意，而捷徑
是沒有視窗的。真的要中斷的話，在 Ubuntu 視窗執行 `./STOP-HERE.sh` 並回答 `y`。

## 7. 用 MobaXterm 連進同一套環境

MobaXterm 只是另一個進入同一個 Ubuntu 的視窗，不會變成第二套程式或第二份資料。

1. 開啟 MobaXterm，點左上角 **Session**。
2. 選 **WSL**，`WSL distribution` 選 **Ubuntu**，按 **OK**。
3. 在跳出來的視窗裡執行和上面完全一樣的指令：

```bash
# [WSL／Ubuntu]
cd ~/AI-agnets-debating-chamber
./START-HERE.sh
```

## 8. 卡住的時候

| 畫面上寫什麼 | 怎麼辦 |
| --- | --- |
| `command not found: codex`／`claude`／`agy` | 回到第 5 步重裝那一個，然後關掉視窗重開一次。 |
| CLI 叫你先登入 | 在 Ubuntu 執行 `codex`、`claude` 或 `agy`，照畫面登入一次。 |
| `沒有啟動：…127.0.0.1:8765…` | 那個埠被別的程式占用了。本程式不會改用其他埠，也不會去關別人的程式；請先關掉占用它的程式。 |
| 捷徑按了沒反應 | 在 Ubuntu 執行 `./START-HERE.sh`，它會把原因印在畫面上。 |
| 想看詳細紀錄 | `~/AI-agnets-debating-chamber_data/logs/webapp.jsonl`，每一行是一筆。 |
| 分析結果放在哪 | `~/AI-agnets-debating-chamber_data/runs/`，依日期分資料夾，報告是裡面的 `report.html`。 |
