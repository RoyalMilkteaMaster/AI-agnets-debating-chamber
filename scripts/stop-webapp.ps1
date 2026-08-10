<#
.SYNOPSIS
    關閉 hoya-bit 辯論室（打 POST /shutdown）。

.DESCRIPTION
    工作區根目錄的「關閉辯論室」捷徑就是在跑這一支。它是備援：正常收工是按頁面右上角
    的「關閉伺服器」，這裡給的是「頁面已經關掉了、伺服器還在跑」時的另一條路。

    兩條路走的是同一個端點、同一條優雅停機路徑，伺服器照常寫下 server_stop。這支腳本
    不會、也不該去終止任何程序：強制終止會讓那一行 server_stop 消失，log 就再也看不出
    這台伺服器是怎麼結束的。

.PARAMETER Port
    伺服器監聽的埠。省略時用 webapp-common.ps1 的預設值（8765）。

.EXAMPLE
    .\stop-webapp.ps1

.EXAMPLE
    .\stop-webapp.ps1 -Port 8790
#>

[CmdletBinding()]
param(
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "webapp-common.ps1")

$Port = Resolve-WebappPort -Port $Port

if (-not (Test-WebappListening -Port $Port)) {
    Write-Host "127.0.0.1:$Port 沒有人在聽，辯論室本來就沒有在跑。"
    exit 0
}

$shutdownUrl = Get-WebappShutdownUrl -Port $Port
try {
    Invoke-WebRequest -Uri $shutdownUrl -Method Post -UseBasicParsing -TimeoutSec 15 | Out-Null
} catch {
    # 伺服器是「先回應、再停機」，所以走到這裡通常是回應在路上被切斷，而停機已經開始。
    # 不在這裡下結論：下面等埠真的關掉，用那個結果回答。
    Write-Host "送出關閉請求時連線中斷了：$($_.Exception.Message)" -ForegroundColor Yellow
}

if (-not (Wait-WebappPort -Port $Port -Listening $false -TimeoutSeconds 20)) {
    Write-Host "127.0.0.1:$Port 20 秒後還在聽，沒有停下來。" -ForegroundColor Yellow
    Write-Host "原因看 _data/logs/webapp.jsonl；這支腳本不會強制終止任何程序。" -ForegroundColor Yellow
    exit 1
}

Write-Host "辯論室已停止監聽 127.0.0.1:$Port（server_stop 已寫入 webapp.jsonl）。"
exit 0
