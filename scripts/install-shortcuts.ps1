<#
.SYNOPSIS
    在工作區根目錄建立（或重建）「開啟辯論室」與「關閉辯論室」兩個捷徑。

.DESCRIPTION
    捷徑是薄殼：它只記「用哪個 powershell、跑 scripts\ 裡的哪一支、視窗要藏起來」，
    判斷全在被它呼叫的腳本裡。這支安裝腳本存在的理由是 .lnk 是二進位檔——沒有它，
    那兩個捷徑就是兩個沒人講得清楚內容、也重建不出來的檔案。

    可以重複執行：既有的同名捷徑會被重寫成這裡寫的內容。

    「視窗要藏起來」是兩層一起做的：powershell 自己吃 -WindowStyle Hidden，捷徑再
    把 WindowStyle 設成 7（最小化）。.lnk 格式本身沒有「隱藏」這個選項，只有正常／
    最大化／最小化三種，所以真正把視窗藏掉的是前者，後者是讓它連閃一下都不要。

.PARAMETER WorkspaceRoot
    要放捷徑的資料夾。省略時是 Code Root 的上一層，也就是工作區根目錄。

.EXAMPLE
    .\install-shortcuts.ps1
#>

[CmdletBinding()]
param(
    [string]$WorkspaceRoot = ""
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "webapp-common.ps1")

$codeRoot = Resolve-WebappCodeRoot -ScriptRoot $PSScriptRoot
if ($WorkspaceRoot -eq "") {
    $WorkspaceRoot = Split-Path -Parent $codeRoot
}
if (-not (Test-Path -LiteralPath $WorkspaceRoot)) {
    throw "找不到要放捷徑的資料夾：$WorkspaceRoot"
}

# 寫絕對路徑的 powershell.exe：捷徑不該靠 PATH 找得到什麼。
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $powershell)) {
    throw "找不到 powershell.exe：$powershell"
}

# .lnk 的視窗狀態只有 1（正常）／3（最大化）／7（最小化）三種，沒有「隱藏」。
$minimized = 7

$shortcuts = @(
    @{
        Name        = "開啟辯論室.lnk"
        Script      = "start-webapp.ps1"
        Description = "啟動 hoya-bit 辯論室（隱藏視窗）；已經在跑就只開瀏覽器。"
    },
    @{
        Name        = "關閉辯論室.lnk"
        Script      = "stop-webapp.ps1"
        Description = "關閉 hoya-bit 辯論室（POST /shutdown 優雅停機）。"
    }
)

$shell = New-Object -ComObject WScript.Shell
try {
    foreach ($wanted in $shortcuts) {
        $scriptPath = Join-Path (Join-Path $codeRoot "scripts") $wanted.Script
        if (-not (Test-Path -LiteralPath $scriptPath)) {
            throw "找不到捷徑要跑的腳本：$scriptPath"
        }
        $linkPath = Join-Path $WorkspaceRoot $wanted.Name
        $link = $shell.CreateShortcut($linkPath)
        $link.TargetPath = $powershell
        $link.Arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $scriptPath + '"'
        $link.WorkingDirectory = $codeRoot
        $link.WindowStyle = $minimized
        $link.Description = $wanted.Description
        $link.Save()
        Write-Host "已寫入捷徑：$linkPath"
        Write-Host "  目標  $powershell"
        Write-Host "  參數  $($link.Arguments)"
    }
} finally {
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}

Write-Host ""
Write-Host "工作區根目錄：$WorkspaceRoot"
exit 0
