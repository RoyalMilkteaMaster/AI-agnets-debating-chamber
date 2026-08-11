<#
.SYNOPSIS
    啟動 AI agnets debating chamber（隱藏視窗），並開瀏覽器。

.DESCRIPTION
    工作區根目錄的「開啟辯論室」捷徑就是在跑這一支；捷徑只是薄殼，判斷全在這裡。

    這支腳本原本放在工作區根目錄（start-webapp.ps1），現在併入 Code Root 的
    scripts\，工作區根目錄不再留第二份。和原本那一支的差別只有三件事：

    1. 伺服器以隱藏視窗啟動，不再霸占一個主控台。原本的黑框關不掉，是這次改造的
       起因（Spec R4）。
    2. 埠已經有人在聽時，這支腳本只開瀏覽器就結束，不再當成錯誤——雙擊第二次的
       意思是「讓我看到畫面」，不是「再開一台」。
    3. 啟動完就結束，不再前景等在伺服器前面。伺服器本身仍然是前景程序模型：它跑在
       自己那個（隱藏的）主控台裡，不是服務、不會自動重啟，關掉就是關掉。

    停止方式有兩個，都走優雅路徑：頁面右上角的「關閉伺服器」按鈕，或工作區根目錄的
    「關閉辯論室」捷徑（scripts\stop-webapp.ps1）。兩者都會讓伺服器照常寫下
    server_stop，沒有任何一條路是強制終止。

.PARAMETER Port
    監聽埠。省略時用 webapp-common.ps1 的預設值（8765）。

.PARAMETER DataRoot
    資料根目錄。省略時用專案自己的預設值（AI-agnets-debating-chamber_data）。

.PARAMETER NoBrowser
    只啟動伺服器，不開瀏覽器。

.EXAMPLE
    .\start-webapp.ps1

.EXAMPLE
    .\start-webapp.ps1 -Port 8790 -NoBrowser
#>

[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$DataRoot = "",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "webapp-common.ps1")

$Port = Resolve-WebappPort -Port $Port
$codeRoot = Resolve-WebappCodeRoot -ScriptRoot $PSScriptRoot
$url = Get-WebappUrl -Port $Port

function Open-Browser {
    param([Parameter(Mandatory = $true)][string]$Address)

    if ($NoBrowser) { return }
    Start-Process $Address | Out-Null
}

# --- 已經在跑就只開瀏覽器 -----------------------------------------------------
if (Test-WebappListening -Port $Port) {
    Write-Host "127.0.0.1:$Port 已經有人在聽，當成辯論室已經開著：只開瀏覽器，不再啟動第二台。"
    Write-Host "  網址  $url"
    Open-Browser -Address $url
    exit 0
}

# --- WSL 可用性 ---------------------------------------------------------------
if ($null -eq (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Host "找不到 wsl.exe。這個專案的 Python 必須跑在 WSL。" -ForegroundColor Red
    exit 1
}

# --- 隱藏視窗啟動 -------------------------------------------------------------
# 不指定 -d：沿用原本那一支的行為，跟著 WSL 的預設發行版走，改名了也不會壞。
# 整行 bash 命令用雙引號包成單一參數；命令本身只用單引號，所以不會互相咬到。
$serveCommand = Get-WebappServeCommand -CodeRoot $codeRoot -Port $Port -DataRoot $DataRoot
$server = Start-Process -FilePath "wsl.exe" `
    -ArgumentList @("-e", "bash", "-lc", ('"' + $serveCommand + '"')) `
    -WindowStyle Hidden -PassThru

if (-not (Wait-WebappPort -Port $Port -Listening $true -TimeoutSeconds 40)) {
    Write-Host "伺服器沒有在 40 秒內開始監聽 127.0.0.1:$Port。" -ForegroundColor Yellow
    Write-Host "看原因請直接在 WSL 跑同一行，它會把失敗印出來：" -ForegroundColor Yellow
    Write-Host "  wsl -e bash -lc `"$serveCommand`""
    exit 1
}

Write-Host ""
Write-Host "AI agnets debating chamber 已啟動（隱藏視窗）" -ForegroundColor Cyan
Write-Host "  網址    $url"
if ($DataRoot -ne "") {
    Write-Host "  資料    $DataRoot"
} else {
    Write-Host "  資料    專案預設（AI-agnets-debating-chamber_data）"
}
Write-Host "  程序    wsl.exe PID $($server.Id)"
Write-Host "  停止    頁面右上角「關閉伺服器」，或工作區的「關閉辯論室」捷徑"
Write-Host ""

Open-Browser -Address $url
exit 0
