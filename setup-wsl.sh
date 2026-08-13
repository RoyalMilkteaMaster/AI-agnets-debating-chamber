#!/usr/bin/env bash
#
# 一次性設定：確認這裡是 WSL2 Ubuntu、確認 python3 在、然後在 Windows 桌面建立
# 「開啟辯論室」與「關閉辯論室」兩個捷徑。
#
# 它刻意不做的事：
#   - 不建立 .venv。這個專案只用 Python 標準函式庫，沒有東西可以裝。
#   - 不下載、不更新、不登入任何 Provider CLI，也不讀寫任何憑證。
#     缺哪一個就把 README 上的官方安裝指令印出來，由你自己執行。
#   - 不動 Data Root。既有的 run、報告與設定原封不動。
#
# 重跑結果相同：兩個捷徑會被重寫成同樣的內容，數量不會變多。
#
# 用法：
#   bash setup-wsl.sh

set -euo pipefail

CODE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DATA_ROOT="$(dirname -- "$CODE_ROOT")/AI-agnets-debating-chamber_data"
PYTHON="${HOYA_PYTHON:-python3}"

# 測試接縫：改用哪一個 powershell.exe、把捷徑放到哪個資料夾、去哪裡清舊捷徑、
# 資料夾捷徑寫到哪。平常全部都不要設。
#
# HOYA_LEGACY_DIR 存在的理由是它會刪檔：舊捷徑預設清的是 Code Root 的上一層，
# 而測試如果只換了桌面、沒換這一個，跑一次測試就會動到真的工作區。
# HOYA_FOLDER_SHORTCUT_DIR 同理：資料夾捷徑預設寫進真的 scripts\，測試不換掉
# 就會把 .lnk 寫進工作樹。
POWERSHELL="${HOYA_POWERSHELL:-}"
DESKTOP_OVERRIDE="${HOYA_DESKTOP:-}"
LEGACY_OVERRIDE="${HOYA_LEGACY_DIR:-}"
FOLDER_OVERRIDE="${HOYA_FOLDER_SHORTCUT_DIR:-}"

fail() {
    echo "設定沒有完成：$1" >&2
    exit 1
}

# --- 1. 這裡是不是 WSL2 Ubuntu -------------------------------------------------

if ! grep -qi 'microsoft' /proc/version 2>/dev/null; then
    fail "這裡不是 WSL。本產品的正式執行環境是 Windows 10／11 上的 WSL2 Ubuntu。"
fi
if ! grep -qi 'wsl2' /proc/version 2>/dev/null && [ ! -d /run/WSL ]; then
    fail "這裡看起來是 WSL1。請先執行 wsl --set-version <發行版> 2 再重跑。"
fi
echo "WSL2：$(uname -r)"

if [ -r /etc/os-release ] && grep -q '^ID=ubuntu' /etc/os-release; then
    echo "發行版：$(. /etc/os-release && echo "$PRETTY_NAME")"
else
    echo "提醒：正式支援的發行版是 Ubuntu，其他 WSL2 發行版只提供盡力相容。"
fi

# --- 2. python3 ---------------------------------------------------------------

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "找不到 python3。請先執行：" >&2
    echo "  sudo apt update && sudo apt install -y python3" >&2
    fail "缺少 python3。"
fi
echo "Python：$("$PYTHON" --version 2>&1)"

# --- 3. Provider CLI 只檢查、不安裝 --------------------------------------------

missing=""
check_cli() {
    local command_name="$1" install_hint="$2"
    if command -v "$command_name" >/dev/null 2>&1; then
        echo "已安裝：$command_name"
    else
        echo "尚未安裝：$command_name"
        echo "  官方安裝指令：$install_hint"
        missing="yes"
    fi
}

echo ""
echo "Provider CLI（本腳本只檢查，不會替你安裝或登入）："
check_cli codex "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
check_cli claude "curl -fsSL https://claude.ai/install.sh | bash"
check_cli agy "curl -fsSL https://antigravity.google/cli/install.sh | bash"
if [ -n "$missing" ]; then
    echo "  裝好之後各執行一次 codex／claude／agy 完成互動登入，詳見 README。"
fi
echo ""

# --- 4. 桌面捷徑 ---------------------------------------------------------------

if [ -z "$POWERSHELL" ]; then
    if command -v powershell.exe >/dev/null 2>&1; then
        POWERSHELL="$(command -v powershell.exe)"
    elif [ -x /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe ]; then
        POWERSHELL="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    else
        fail "找不到 Windows 內建的 powershell.exe，無法建立桌面捷徑。"
    fi
fi

if ! command -v wslpath >/dev/null 2>&1; then
    fail "找不到 wslpath，無法把 Linux 路徑換成 Windows 看得懂的路徑。"
fi

installer_windows="$(wslpath -w "$CODE_ROOT/scripts/install-shortcuts.ps1")"
shortcut_windows="$(wslpath -w "$CODE_ROOT/scripts/wsl-shortcut.ps1")"
distro="${WSL_DISTRO_NAME:-}"
if [ -z "$distro" ]; then
    fail "讀不到 WSL_DISTRO_NAME，無法讓捷徑指名要進哪一個發行版。"
fi

# 舊版把兩個捷徑放在 Code Root 的上一層（工作區根目錄）。指名那一個資料夾，
# installer 才有辦法精確清掉它們，而不必去掃描任何人的桌面。
legacy_dir="${LEGACY_OVERRIDE:-$(wslpath -w "$(dirname -- "$CODE_ROOT")")}"

installer_arguments=(
    -NoProfile -NonInteractive -ExecutionPolicy Bypass
    -File "$installer_windows"
    -ShortcutScript "$shortcut_windows"
    -Distro "$distro"
    -CodeRoot "$CODE_ROOT"
    -LegacyShortcutDir "$legacy_dir"
)
if [ -n "$DESKTOP_OVERRIDE" ]; then
    installer_arguments+=(-DesktopPath "$DESKTOP_OVERRIDE")
fi
if [ -n "$FOLDER_OVERRIDE" ]; then
    installer_arguments+=(-FolderShortcutDir "$FOLDER_OVERRIDE")
fi

if ! "$POWERSHELL" "${installer_arguments[@]}"; then
    fail "桌面捷徑沒有建立完成（原因見上面 installer 印出來的訊息）。"
fi

echo ""
echo "設定完成。"
echo "  程式  $CODE_ROOT"
echo "  資料  $DATA_ROOT"
echo "  啟動  ./START-HERE.sh 或桌面的「開啟辯論室」"
echo "  關閉  ./STOP-HERE.sh 或桌面的「關閉辯論室」"
