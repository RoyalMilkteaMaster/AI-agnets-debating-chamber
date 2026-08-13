<#
.SYNOPSIS
    在 Windows 桌面建立（或重建）「開啟辯論室」與「關閉辯論室」兩個捷徑。

.DESCRIPTION
    這支腳本由 setup-wsl.sh 呼叫，不必自己執行。它存在的理由是 .lnk 是二進位檔：
    沒有它，那兩個捷徑就是兩個沒人講得清楚內容、也重建不出來的檔案。

    兩個捷徑指向同一支 scripts\wsl-shortcut.ps1，差別只在 -Action。捷徑本身不知道
    ownership 是什麼，wsl-shortcut.ps1 也不知道 —— 判斷全部在 WSL 裡的 Bash 與
    Python。

    可以重複執行：同名捷徑會被重寫成這裡寫的內容，數量不會變多。

    「視窗要藏起來」是兩層一起做的：powershell 自己吃 -WindowStyle Hidden，捷徑再
    把 WindowStyle 設成 7（最小化）。.lnk 格式本身沒有「隱藏」這個選項，只有正常／
    最大化／最小化三種，所以真正把視窗藏掉的是前者，後者是讓它連閃一下都不要。

    **檔名證明不了任何事，所以檔名不決定任何事。** 一個 .lnk 要算「本專案的」，
    必須同時通過四關：它的 TargetPath 正規化後**完整等於**這次要寫的那一支
    powershell.exe（不分大小寫；比的是整個路徑，不是 basename——誰都能在自己的
    資料夾裡放一個叫 powershell.exe 的東西）；它的 Arguments 裡有一個帶引號的
    絕對路徑 -File；那個路徑**精確等於**本專案可退役入口清單上的某一支（同樣是
    完整路徑相等，不是前綴——上層目錄底下還住著別人的專案）；而且它帶著那支入口
    該有的參數。少一關就不是，就留著。

    清單只有四個資料夾展開得出來：Code Root 與它的 scripts\，舊工作區根目錄與它的
    scripts\。sibling 專案的子目錄不在裡面，即使它就在同一個上層目錄底下、腳本
    同名、參數看起來一模一樣。

    這條規則有兩個方向，兩個都會用到：

      - **刪**：只有四關全過的舊捷徑會被精確刪除。別人的 .lnk 不會被掃描或比對。
      - **寫**：「開啟辯論室／關閉辯論室」這兩個固定名稱如果已經存在、卻證明不了
        是本專案的，這支腳本一個字都不寫、一個檔都不刪，直接以非零退出。覆蓋過去
        會毀掉一個我們根本認不出來是誰的檔案，而那是不可逆的。

.PARAMETER ShortcutScript
    scripts\wsl-shortcut.ps1 的 Windows 路徑（由 setup-wsl.sh 以 wslpath -w 產生）。

.PARAMETER Distro
    捷徑要進入的 WSL 發行版名稱。

.PARAMETER CodeRoot
    Code Root 在 WSL 裡的 Linux 路徑。

.PARAMETER DesktopPath
    捷徑要放的資料夾。省略時是這個 Windows 使用者的桌面。測試用它換成暫存資料夾。

.PARAMETER LegacyShortcutDir
    另一個要精確清理舊同名捷徑的資料夾（例如舊版把捷徑放的工作區根目錄）。
    省略時只清理桌面。

.PARAMETER FolderShortcutDir
    除了桌面以外，同樣兩個捷徑也寫進這個資料夾——讓 clone 下來的人在檔案總管裡
    就有可雙擊的入口。省略時是 -ShortcutScript 所在的資料夾（repo 的 scripts\）。
    **預設值就是真的工作樹，所以測試一定要用這個參數換成暫存資料夾。**

.EXAMPLE
    .\install-shortcuts.ps1 -ShortcutScript C:\proj\scripts\wsl-shortcut.ps1 -Distro Ubuntu -CodeRoot /home/me/proj
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ShortcutScript,
    [Parameter(Mandatory = $true)][string]$Distro,
    [Parameter(Mandatory = $true)][string]$CodeRoot,
    [string]$DesktopPath = "",
    [string]$LegacyShortcutDir = "",
    [string]$FolderShortcutDir = ""
)

$ErrorActionPreference = "Stop"

# 這支腳本是被 WSL 的 Bash 呼叫的，而 Bash 那一側只看得懂 UTF-8。Windows PowerShell
# 預設把主控台輸出編成系統的 ANSI code page，捷徑名稱是中文，於是使用者會在 setup
# 的畫面上看到一堆亂碼。這一行是唯一的修法。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Test-Path -LiteralPath $ShortcutScript)) {
    throw "找不到捷徑要跑的腳本：$ShortcutScript"
}
if ($DesktopPath -eq "") {
    $DesktopPath = [Environment]::GetFolderPath("Desktop")
}
if (-not (Test-Path -LiteralPath $DesktopPath)) {
    throw "找不到要放捷徑的資料夾：$DesktopPath"
}
if ($FolderShortcutDir -eq "") {
    $FolderShortcutDir = Split-Path -Parent $ShortcutScript
}
if (-not (Test-Path -LiteralPath $FolderShortcutDir)) {
    throw "找不到要放資料夾捷徑的資料夾：$FolderShortcutDir"
}

# 寫絕對路徑的 powershell.exe：捷徑不該靠 PATH 找得到什麼。
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $powershell)) {
    throw "找不到 powershell.exe：$powershell"
}

# .lnk 的視窗狀態只有 1（正常）／3（最大化）／7（最小化）三種，沒有「隱藏」。
$minimized = 7

# 這兩個是最終狀態：桌面上屬於本專案的捷徑，就是這兩個，沒有第三個。
$shortcuts = @(
    @{
        Name        = "開啟辯論室.lnk"
        Action      = "start"
        Description = "在 WSL 啟動 AI agnets debating chamber，然後開瀏覽器。"
    },
    @{
        Name        = "關閉辯論室.lnk"
        Action      = "stop"
        Description = "關閉 WSL 裡的 AI agnets debating chamber（只關本專案自己的那一個）。"
    }
)

# 舊版留下的四個檔名。名字對上只代表「值得看一眼」，不代表可以刪。
$legacyNames = @(
    "WSL 開啟辯論室.lnk",
    "WSL 關閉辯論室.lnk",
    "開啟辯論室.lnk",
    "關閉辯論室.lnk"
)

# 本專案歷來自己建立過的 Windows 入口腳本檔名。第一個是現在這一版的薄殼，其餘全部
# 已退役。這是「值得再往下查」的名單，不是判斷本身：檔名任何人都能取一樣的。
$ourScripts = @(
    "wsl-shortcut.ps1",
    "start-webapp.ps1",
    "stop-webapp.ps1",
    "wsl-open-webapp.ps1",
    "START-HERE.ps1",
    "STOP-HERE.ps1"
)

function Get-CanonicalPath {
    <#
    .SYNOPSIS
        一個路徑的正規化完整形式，正規化不了就回空字串。
    #>
    param([string]$Path)

    if ($Path -eq "") { return "" }
    try {
        return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    } catch {
        return ""
    }
}

function Get-FileArgument {
    <#
    .SYNOPSIS
        Arguments 裡 -File 指的那個路徑，沒有就回空字串。
    #>
    param([string]$Arguments)

    if ($Arguments -match '-File\s+"([^"]+)"') { return $Matches[1] }
    if ($Arguments -match '-File\s+(\S+)') { return $Matches[1] }
    return ""
}

# 本專案曾經放過入口腳本的兩個位置，各只有兩層：Code Root 自己與它的 scripts\，
# 舊工作區根目錄與它的 scripts\。這是四個確切的資料夾，**不是**它們底下的整棵樹：
# 授權整個上層目錄，就等於順便授權了旁邊每一個 sibling 專案，而 sibling 專案裡放一支
# 同名的 wsl-shortcut.ps1 是完全正常的事。
$entryDirectories = @()
$codeRootWindows = Split-Path -Parent (Split-Path -Parent $ShortcutScript)
if ($codeRootWindows) {
    $entryDirectories += $codeRootWindows
    $entryDirectories += (Join-Path $codeRootWindows "scripts")
}
if ($LegacyShortcutDir -ne "" -and (Test-Path -LiteralPath $LegacyShortcutDir)) {
    $legacyRoot = (Resolve-Path -LiteralPath $LegacyShortcutDir).ProviderPath
    $entryDirectories += $legacyRoot
    $entryDirectories += (Join-Path $legacyRoot "scripts")
}

# 展開成一份「可退役入口的正規化完整路徑」清單。ownership 比的是這份清單裡的
# 精確路徑相等，不是任何前綴。清單以外的每一個路徑都是別人的。
$ownedScripts = @()
foreach ($directory in $entryDirectories) {
    foreach ($name in $ourScripts) {
        $candidate = Get-CanonicalPath -Path (Join-Path $directory $name)
        if ($candidate -ne "") { $ownedScripts += $candidate }
    }
}
# 這次要寫的那一支一定算數，即使 -ShortcutScript 被指到別的地方。
$canonicalShortcutScript = Get-CanonicalPath -Path $ShortcutScript
if ($canonicalShortcutScript -ne "") { $ownedScripts += $canonicalShortcutScript }

# 兩個要寫入捷徑的資料夾：桌面與 repo 的 scripts\（或 -FolderShortcutDir 指定處）。
# 兩者其實是同一個資料夾時只寫一次，不然後寫的會把前面剛驗完的又覆蓋一遍。
$shortcutDirectories = @($DesktopPath)
$canonicalDesktop = Get-CanonicalPath -Path $DesktopPath
$canonicalFolder = Get-CanonicalPath -Path $FolderShortcutDir
if ($canonicalFolder -ne "" -and -not [System.String]::Equals(
        $canonicalFolder, $canonicalDesktop,
        [System.StringComparison]::OrdinalIgnoreCase)) {
    $shortcutDirectories += $FolderShortcutDir
}

function Test-OwnedScriptPath {
    <#
    .SYNOPSIS
        這個完整路徑是不是清單上那幾支入口腳本之一（不分大小寫的精確相等）。
    #>
    param([string]$Path)

    $full = Get-CanonicalPath -Path $Path
    if ($full -eq "") { return $false }
    foreach ($owned in $ownedScripts) {
        if ([System.String]::Equals(
                $full, $owned, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

# 這次要寫進捷徑的那一支 powershell.exe，正規化後的完整路徑。ownership 的第一關比的
# 就是它，所以它正規化不出來就沒有第一關可比，直接停。
$canonicalPowerShell = Get-CanonicalPath -Path $powershell
if ($canonicalPowerShell -eq "") {
    throw "無法正規化 powershell.exe 的路徑：$powershell"
}

$shell = New-Object -ComObject WScript.Shell
try {
    function Test-OurShortcut {
        <#
        .SYNOPSIS
            這個 .lnk 能不能被證明是本專案建立的。證明不了就是不是。
        #>
        param([string]$Path)

        try {
            $link = $shell.CreateShortcut($Path)
        } catch {
            return $false
        }

        # 一、它要由「這次要寫的那一支」powershell.exe 啟動——比的是正規化後的完整
        # 路徑，不是檔名。powershell.exe 是一個檔名，誰都能在自己的資料夾裡放一個
        # 同名的東西。一個指著某個暫存資料夾裡的 powershell.exe 的捷徑，其他每一關
        # 都可以做得很像，唯一露餡的地方就是它到底啟動了哪一個二進位檔。
        $target = Get-CanonicalPath -Path ("" + $link.TargetPath)
        if ($target -eq "") { return $false }
        if (-not [System.String]::Equals(
                $target, $canonicalPowerShell,
                [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }

        # 二、它要跑一個帶絕對路徑的 -File。相對路徑證明不了它在跑哪一支。
        $arguments = "" + $link.Arguments
        $file = Get-FileArgument -Arguments $arguments
        if ($file -eq "") { return $false }
        if (-not [System.IO.Path]::IsPathRooted($file)) { return $false }

        # 三、那個完整路徑要精確等於清單上某一支可退役入口。比的是整個路徑，不是
        # 前綴：只授權到「上層資料夾」的話，隔壁那個 sibling 專案裡同名的
        # scripts\wsl-shortcut.ps1 也會被當成我們的，然後被刪掉。
        if (-not (Test-OwnedScriptPath -Path $file)) { return $false }

        # 薄殼還有一個預期參數：本專案從來不會不帶 -Action 就呼叫它。
        if ([System.IO.Path]::GetFileName($file).ToLower() -eq "wsl-shortcut.ps1") {
            if ($arguments -notmatch '-Action\s+(start|stop)\b') { return $false }
        }

        return $true
    }

    # --- 先確認兩個固定名稱可以寫，再動任何一個檔 ---------------------------
    #
    # 這一段在刪除與寫入之前，而且順序就是重點：任何一個固定名稱被證明不了是本專案
    # 的，這次執行就整個不做——不寫捷徑，也不清舊捷徑。做一半的安裝比不安裝難查。
    $blocked = @()
    foreach ($directory in $shortcutDirectories) {
        foreach ($wanted in $shortcuts) {
            $linkPath = Join-Path $directory $wanted.Name
            if ((Test-Path -LiteralPath $linkPath) -and -not (Test-OurShortcut -Path $linkPath)) {
                $blocked += $linkPath
            }
        }
    }
    if ($blocked.Count -gt 0) {
        foreach ($path in $blocked) {
            Write-Host "沒有覆蓋：$path 已經存在，但無法確認它是本專案建立的。"
        }
        Write-Host "沒有建立任何捷徑，也沒有移除任何舊捷徑。"
        Write-Host "請先把上面這些檔案移走或改名，再重跑 setup。"
        exit 3
    }

    function Remove-LegacyShortcuts {
        param([string]$Directory, [string[]]$Names)

        if ($Directory -eq "" -or -not (Test-Path -LiteralPath $Directory)) { return }
        foreach ($name in $Names) {
            $path = Join-Path $Directory $name
            if (-not (Test-Path -LiteralPath $path)) { continue }
            if (Test-OurShortcut -Path $path) {
                Remove-Item -LiteralPath $path -Force
                Write-Host "已移除舊捷徑：$path"
            } else {
                Write-Host "保留（無法確認是本專案建立的）：$path"
            }
        }
    }

    # 桌面上先清舊的兩個 WSL 前綴名稱；「開啟辯論室／關閉辯論室」不在這裡清，
    # 因為上面已經確認過它們可以寫，下面正要把它們重寫成正確內容。
    Remove-LegacyShortcuts -Directory $DesktopPath -Names @(
        "WSL 開啟辯論室.lnk", "WSL 關閉辯論室.lnk"
    )

    # 舊版把捷徑放在工作區根目錄。那裡的四個舊名稱都清，但同樣只清確認得出來是
    # 本專案的；而且如果它其實就是桌面，就整段跳過 —— 否則會把剛寫好的刪掉。
    if ($LegacyShortcutDir -ne "" -and (Test-Path -LiteralPath $LegacyShortcutDir)) {
        $legacyFull = (Resolve-Path -LiteralPath $LegacyShortcutDir).ProviderPath
        $desktopFull = (Resolve-Path -LiteralPath $DesktopPath).ProviderPath
        if ($legacyFull -ne $desktopFull) {
            Remove-LegacyShortcuts -Directory $legacyFull -Names $legacyNames
        }
    }

    foreach ($directory in $shortcutDirectories) {
        foreach ($wanted in $shortcuts) {
            $linkPath = Join-Path $directory $wanted.Name
            $link = $shell.CreateShortcut($linkPath)
            $link.TargetPath = $powershell
            $link.Arguments = '-NoProfile -NonInteractive -WindowStyle Hidden ' +
                '-ExecutionPolicy Bypass -File "' + $ShortcutScript + '"' +
                ' -Action ' + $wanted.Action +
                ' -Distro "' + $Distro + '"' +
                ' -CodeRoot "' + $CodeRoot + '"'
            # WorkingDirectory 留空是刻意的：Code Root 可能在 WSL 檔案系統裡，指定成
            # UNC 路徑會讓 powershell 每次啟動都先抱怨一次，而這支腳本本來就不讀 cwd。
            $link.WorkingDirectory = ""
            $link.WindowStyle = $minimized
            $link.Description = $wanted.Description
            $link.Save()
            Write-Host "已寫入捷徑：$linkPath"
            Write-Host "  目標  $powershell"
            Write-Host "  參數  $($link.Arguments)"
        }
    }
} finally {
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}

Write-Host ""
Write-Host "桌面資料夾：$DesktopPath"
Write-Host "資料夾捷徑：$FolderShortcutDir"
exit 0
