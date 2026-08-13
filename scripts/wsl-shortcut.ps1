<#
.SYNOPSIS
    兩個桌面捷徑共用的薄殼：把視窗藏起來，然後呼叫 WSL 裡的 Bash 入口。

.DESCRIPTION
    這支腳本刻意不做任何判斷。它不讀 ownership、不解析 JSON、不認得 instance、
    不知道 active_run 是什麼，也不會執行任何 Provider CLI 或 Python，更不會自己
    送出任何 HTTP 請求。啟停與 ownership 規則全部在 START-HERE.sh／STOP-HERE.sh
    與 Python 那一側，因為同一份規則寫在兩個語言裡，就是兩份會各自漂移的規則。

    它只多做一件事，而且是它非做不可的一件：**把那個 Yes/No 問出來。**

    捷徑是隱藏視窗，所以 STOP-HERE.sh 的互動提問沒有人看得到。於是分工是這樣的：
    有分析正在進行而現場沒有終端機時，STOP-HERE.sh 不送出任何東西，改以
    $NeedsConfirmation 這個專用退出碼結束；這裡看到那個碼——**而且只看那個碼**——
    才彈一個最小的 Yes/No。按 Yes 就重新呼叫同一支 STOP-HERE.sh 並加上 --yes，
    真正的關閉仍然由它連同 precondition 一起送出。按 No 則什麼都不做。

    這裡沒有任何一條路會產生 shutdown 請求，也沒有任何一條路會判斷「這台是不是
    我們的」。那個問題從頭到尾只有 Python 回答過一次。

.PARAMETER Action
    start 對應 START-HERE.sh，stop 對應 STOP-HERE.sh。

.PARAMETER Distro
    要進入哪一個 WSL 發行版。setup 執行時記下來，寫進捷徑的參數裡。

.PARAMETER CodeRoot
    Code Root 在 WSL 裡的路徑（Linux 路徑，不是 Windows 路徑）。

.PARAMETER ConfirmAnswer
    測試接縫，兩個桌面捷徑都不會傳它。給 yes 或 no 就直接當成使用者的答案，
    一個對話框都不會開；不傳（預設）才是真的問使用者。

    它是參數而不是環境變數，而且理由是實際踩過的：這支腳本是從 WSL 裡叫起來的，
    而 WSL 傳進 Windows 程序的環境變數要另外列在 WSLENV 裡才會過去。漏列的那一次，
    自動測試在沒有人看著的機器上彈出了真的對話框並停在那裡。argv 不會漏。

.EXAMPLE
    .\wsl-shortcut.ps1 -Action start -Distro Ubuntu -CodeRoot /home/me/AI-agnets-debating-chamber
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("start", "stop")][string]$Action,
    [Parameter(Mandatory = $true)][string]$Distro,
    [Parameter(Mandatory = $true)][string]$CodeRoot,
    [ValidateSet("", "yes", "no")][string]$ConfirmAnswer = ""
)

$ErrorActionPreference = "Stop"

# STOP-HERE.sh 說「有分析在跑，我這裡問不到人」時用的退出碼。跨語言的另一份在
# STOP-HERE.sh 的 EXIT_NEEDS_CONFIRMATION，要改請兩邊一起改。
$NeedsConfirmation = 10

$entry = if ($Action -eq "start") { "./START-HERE.sh" } else { "./STOP-HERE.sh" }

# 單引號字串裡沒有轉義字元，所以內含的單引號只能接到外面去：
# ~/my's code -> '~/my'\''s code'。Linux 路徑可以合法含有單引號，直接包一對引號
# 會把整行命令咬斷。
$quotedCodeRoot = "'" + ($CodeRoot -replace "'", "'\''") + "'"

function Confirm-Interrupt {
    <#
    .SYNOPSIS
        最小的 Yes/No：要不要中斷正在進行的分析。預設是「否」。
    #>
    if ($ConfirmAnswer -ne "") { return $ConfirmAnswer -eq "yes" }

    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "目前有分析正在進行，關閉辯論室會中斷它。確定要關嗎？",
        "關閉辯論室",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Warning,
        [System.Windows.Forms.MessageBoxDefaultButton]::Button2)
    return $answer -eq [System.Windows.Forms.DialogResult]::Yes
}

# 這兩次呼叫沒有包成函式，而那是刻意的：PowerShell 的函式會把它裡面每一行輸出都
# 當成回傳值，所以 `$code = Invoke-Entry` 收到的是「Bash 印的每一行，再加上退出碼」
# 的一個陣列 —— 使用者看不到 Bash 說了什麼，而 `exit $code` 拿到的是陣列。
& wsl.exe -d $Distro -- bash -lc "cd $quotedCodeRoot && $entry"
$code = $LASTEXITCODE

if ($Action -eq "stop" -and $code -eq $NeedsConfirmation) {
    if (-not (Confirm-Interrupt)) {
        # 使用者說不要。這裡沒有送出過任何東西，也不會送出。
        Write-Host "已取消，辯論室維持運行。"
        exit 0
    }
    & wsl.exe -d $Distro -- bash -lc "cd $quotedCodeRoot && $entry --yes"
    $code = $LASTEXITCODE
}

exit $code
