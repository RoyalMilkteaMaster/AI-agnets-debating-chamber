<#
.SYNOPSIS
    啟動與關閉辯論室共用的判斷、等待與路徑轉換。

.DESCRIPTION
    這個檔案只被 dot-source，不單獨執行：`start-webapp.ps1` 與 `stop-webapp.ps1`
    共用的每一項判斷都在這裡，只寫一次。

    「伺服器在不在跑」在這一層一律等於「127.0.0.1:<埠> 有沒有人在聽」。這不是
    偷懶：伺服器本身就拒絕綁一個被占用的埠（webapp/server.py 的 _bind_failure），
    所以這個判斷和它自己的判斷是同一個，兩邊不會各說各話。代價要講清楚——如果
    占用那個埠的是別的程式，這一層會把它當成辯論室已經開著。

    跨語言邊界的三個值（預設埠、停機網址、啟動命令）在 PowerShell 這一側各只有
    一份，並且都在註解裡指名 Python 那一側的權威位置。
#>

# 預設埠。Python 那一側的權威是 hoya_market_agents/webapp/server.py 的
# DEFAULT_PORT，由 tests/test_webapp.py 的
# test_the_default_port_is_the_one_the_ticket_names 釘住。要換埠請兩邊一起改。
$WebappDefaultPort = 8765

# 停機端點。Python 那一側的權威是 hoya_market_agents/webapp/pages.py 的
# SHUTDOWN_PATH（server.py 從那裡讀，所以按鈕與路由永遠同一個拼法）。
$WebappShutdownPath = "shutdown"

function Resolve-WebappPort {
    <#
    .SYNOPSIS
        把「沒有指定」換成預設埠。
    .DESCRIPTION
        param() 區塊在 dot-source 之前就綁好了，所以預設值不能直接寫成共用變數；
        入口腳本一律用 0 表示「沒有指定」，再交給這裡換成真正的埠。
    #>
    param([int]$Port)

    if ($Port -gt 0) { return $Port }
    return $WebappDefaultPort
}

function Get-WebappUrl {
    param([Parameter(Mandatory = $true)][int]$Port)

    return "http://127.0.0.1:$Port/"
}

function Get-WebappShutdownUrl {
    param([Parameter(Mandatory = $true)][int]$Port)

    return (Get-WebappUrl -Port $Port) + $WebappShutdownPath
}

function Test-WebappListening {
    <#
    .SYNOPSIS
        127.0.0.1:<埠> 現在有沒有人在聽。
    #>
    param([Parameter(Mandatory = $true)][int]$Port)

    $probe = New-Object System.Net.Sockets.TcpClient
    try {
        $probe.Connect("127.0.0.1", $Port)
        return $true
    } catch {
        # 連不上就是沒人在聽。這是問句的答案，不是錯誤。
        return $false
    } finally {
        $probe.Dispose()
    }
}

function Wait-WebappPort {
    <#
    .SYNOPSIS
        等到那個埠變成指定狀態，或等到逾時。
    .DESCRIPTION
        啟動要等「開始聽」，關閉要等「不再聽」——同一個迴圈，方向由 -Listening 決定，
        所以這裡沒有兩份等待邏輯可以各自漂移。回傳是否等到。
    #>
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][bool]$Listening,
        [int]$TimeoutSeconds = 25
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ((Test-WebappListening -Port $Port) -eq $Listening) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return (Test-WebappListening -Port $Port) -eq $Listening
}

function ConvertTo-WslPath {
    <#
    .SYNOPSIS
        Windows 路徑 -> WSL 的 /mnt/<磁碟> 路徑。
    #>
    param([Parameter(Mandatory = $true)][string]$WindowsPath)

    $full = [System.IO.Path]::GetFullPath($WindowsPath)
    $drive = $full.Substring(0, 1).ToLower()
    $rest = $full.Substring(2) -replace '\\', '/'
    return "/mnt/$drive$rest"
}

function ConvertTo-ShellQuoted {
    <#
    .SYNOPSIS
        把一個值包成 POSIX shell 的單引號字串，內含的單引號照規矩接回去。

    .DESCRIPTION
        單引號字串裡沒有轉義字元這種東西，所以唯一的辦法是把引號本身接到外面去：

            Leslie's data   ->   'Leslie'\''s data'

        讀法是「收單引號、跳脫一個單引號、再開單引號」，三段接起來仍然是同一個
        argument。

        為什麼這樣就夠：Windows 路徑可以合法含有單引號（`D:\Leslie's data`），但
        雙引號是路徑的非法字元，永遠不會出現。所以「一律單引號、單引號自己接回去」
        對每一個合法 Windows 路徑都成立，沒有第二種情況要判斷。
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    return "'" + ($Value -replace "'", "'\''") + "'"
}

function Resolve-WebappCodeRoot {
    <#
    .SYNOPSIS
        從 scripts\ 的位置推出 Code Root，推不出來就明確失敗。
    .DESCRIPTION
        $PSScriptRoot 不能在這裡讀——它屬於「正在執行的那個腳本」，而這個檔案是被
        dot-source 的。所以由入口腳本把自己的 $PSScriptRoot 交進來。
    #>
    param([Parameter(Mandatory = $true)][string]$ScriptRoot)

    $codeRoot = Split-Path -Parent $ScriptRoot
    if (-not (Test-Path -LiteralPath (Join-Path $codeRoot "hoya_market_agents"))) {
        throw "$codeRoot 看起來不是 Code Root（找不到 hoya_market_agents）。scripts\ 必須留在 Code Root 底下。"
    }
    return $codeRoot
}

function Get-WebappServeCommand {
    <#
    .SYNOPSIS
        在 WSL 裡把伺服器跑起來的那一行 bash 命令。
    .DESCRIPTION
        這個專案的 Python 跑在 WSL（用到 fcntl、flock、zoneinfo 等 POSIX 設施，
        Windows 端的 Python 起不來），所以啟動一律是「包一層 WSL」。

        每個路徑都經過 ConvertTo-ShellQuoted，不是「包一對單引號就算了」：工作區
        資料夾名稱本來就含空白，而單引號也是合法的 Windows 路徑字元——
        `D:\Leslie's data` 直接包起來會把整行命令咬斷，bash 會回 unmatched quote。
        埠不用轉義，它是 [int]，型別本身就擋掉了。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$CodeRoot,
        [Parameter(Mandatory = $true)][int]$Port,
        [string]$DataRoot = ""
    )

    $arguments = "--port $Port"
    if ($DataRoot -ne "") {
        $dataRootWsl = ConvertTo-WslPath -WindowsPath $DataRoot
        $arguments += " --data-root " + (ConvertTo-ShellQuoted -Value $dataRootWsl)
    }
    $codeRootQuoted = ConvertTo-ShellQuoted -Value (ConvertTo-WslPath -WindowsPath $CodeRoot)
    return "cd $codeRootQuoted && PYTHONDONTWRITEBYTECODE=1 python3 -m hoya_market_agents webapp $arguments"
}
