#!/usr/bin/env bash
#
# 開啟辯論室。這是 WSL、MobaXterm 與 Windows 桌面捷徑共用的唯一啟動入口。
#
# 它只做三件事：問這個 port 現在屬於誰、必要時把 webapp 起來、把瀏覽器打開。
# 「屬於誰」不在這裡判斷 —— 那是 hoya_market_agents/webapp/runtime_control.py
# 的工作，這裡只讀它印出來的 key=value。JSON 在三個語言各解析一次，就是三個
# 會互相打架的答案。
#
# 別人的程式占用同一個 port 時，這支腳本印一行原因就退出：不換 port、不終止
# 對方、也不假裝啟動成功。
#
# 用法：
#   ./START-HERE.sh
#
# 測試接縫（平常不要設）：
#   HOYA_PYTHON     改用哪一個 python3
#   HOYA_PORT       改用哪一個 port（不設就用 Python 那一側的預設值）
#   HOYA_OPEN_URL   改用哪一個命令開瀏覽器

set -euo pipefail

# Code Root 從這支腳本自己的位置推導，Data Root 是它同層的固定名稱資料夾。
# 兩者都不寫死磁碟、Windows 使用者或 Linux 使用者。
CODE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DATA_ROOT="$(dirname -- "$CODE_ROOT")/AI-agnets-debating-chamber_data"
PYTHON="${HOYA_PYTHON:-python3}"

PORT_FLAG=()
if [ -n "${HOYA_PORT:-}" ]; then
    PORT_FLAG=(--port "$HOYA_PORT")
fi

cd "$CODE_ROOT"

# 啟動後最多等這麼久讓 webapp 開始監聽。子程序先死掉就不會等滿，所以這不是
# 「猜多久夠」，而是「等不到又沒死掉」的上限。
WAIT_TRIES=100
WAIT_SECONDS=0.2

state=""
instance=""
active_run=""
url=""
reason=""

probe() {
    state=""; instance=""; active_run=""; url=""; reason=""
    local key value
    while IFS='=' read -r key value; do
        case "$key" in
            state) state="$value" ;;
            instance) instance="$value" ;;
            active_run) active_run="$value" ;;
            url) url="$value" ;;
            reason) reason="$value" ;;
        esac
    done < <("$PYTHON" -m hoya_market_agents.webapp.runtime_control probe "${PORT_FLAG[@]}")
}

open_browser() {
    local address="$1"
    if [ -n "${HOYA_OPEN_URL:-}" ]; then
        "$HOYA_OPEN_URL" "$address" || true
        return
    fi
    if command -v wslview >/dev/null 2>&1; then
        wslview "$address" >/dev/null 2>&1 || true
        return
    fi
    if command -v explorer.exe >/dev/null 2>&1; then
        # explorer.exe 開得起來也會回非零，所以它的退出碼不能當成答案。
        explorer.exe "$address" >/dev/null 2>&1 || true
        return
    fi
    echo "請在瀏覽器開啟：$address"
}

probe

case "$state" in
    owned)
        echo "辯論室已經在跑，直接開啟畫面：$url"
        open_browser "$url"
        exit 0
        ;;
    foreign)
        echo "沒有啟動：$reason"
        echo "這支腳本不會換 port，也不會終止別人的程式。"
        exit 1
        ;;
esac

echo "正在啟動辯論室……"
echo "  程式  $CODE_ROOT"
echo "  資料  $DATA_ROOT"
nohup "$PYTHON" -m hoya_market_agents webapp \
    --data-root "$DATA_ROOT" "${PORT_FLAG[@]}" >/dev/null 2>&1 &
server_pid=$!

tries=0
while [ "$tries" -lt "$WAIT_TRIES" ]; do
    if ! kill -0 "$server_pid" 2>/dev/null; then
        echo "webapp 一啟動就結束了。原因寫在 $DATA_ROOT/logs/webapp.jsonl。"
        exit 1
    fi
    probe
    if [ "$state" = "owned" ]; then
        echo "辯論室已啟動：$url"
        open_browser "$url"
        exit 0
    fi
    if [ "$state" = "foreign" ]; then
        echo "沒有啟動：$reason"
        exit 1
    fi
    sleep "$WAIT_SECONDS"
    tries=$((tries + 1))
done

echo "webapp 還沒開始監聽。原因寫在 $DATA_ROOT/logs/webapp.jsonl。"
exit 1
