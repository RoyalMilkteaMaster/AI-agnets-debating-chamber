#!/usr/bin/env bash
#
# 關閉辯論室。這是 WSL、MobaXterm 與 Windows 桌面捷徑共用的唯一關閉入口。
#
# 它只關「剛剛才確認過是本專案的那一個 instance」：先讀 /health 取得 instance，
# 再把 expect_runtime 與 expect_instance 一起送出。伺服器在收到 POST 的當下重新
# 比對；listener 已經換過的話它回 409 並繼續跑。所以這支腳本不可能關掉別人的
# 程式，也不可能關掉那個換上來的新伺服器。
#
# 分析進行中（active_run=true）時預設不關。互動 Bash 會當場問一次（預設不關）；
# 沒有終端機時（例如隱藏視窗的桌面捷徑）不送出，而是以
# EXIT_NEEDS_CONFIRMATION 退出，讓呼叫者自己去把那個 Yes/No 問出來。
#
# 用法：
#   ./STOP-HERE.sh          # 分析進行中會先問
#   ./STOP-HERE.sh --yes    # 已經確定要中斷分析
#
# 測試接縫（平常不要設）：
#   HOYA_PYTHON     改用哪一個 python3
#   HOYA_PORT       改用哪一個 port（不設就用 Python 那一側的預設值）

set -euo pipefail

CODE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="${HOYA_PYTHON:-python3}"

# 「有分析在跑，而我這裡沒有人可以問」的專用退出碼。它必須跟「關不掉」分得開：
# 隱藏視窗的 Windows 捷徑只有這個碼可以判斷該不該彈確認框，而在真的失敗之後彈框
# 問「要中斷嗎」是最糟的一種問法。
#
# 跨語言的另一份在 scripts/wsl-shortcut.ps1 的 $NeedsConfirmation，要改請兩邊一起改；
# tests/test_wsl_entrypoints.py 的 ShortcutConfirmationTest 會實際跑一遍把兩邊釘在一起。
EXIT_NEEDS_CONFIRMATION=10

assume_yes="no"
for argument in "$@"; do
    case "$argument" in
        --yes|-y) assume_yes="yes" ;;
        *)
            echo "不認得的參數：$argument（只接受 --yes）"
            exit 2
            ;;
    esac
done

PORT_FLAG=()
if [ -n "${HOYA_PORT:-}" ]; then
    PORT_FLAG=(--port "$HOYA_PORT")
fi

cd "$CODE_ROOT"

state=""
instance=""
active_run=""
url=""
reason=""

while IFS='=' read -r key value; do
    case "$key" in
        state) state="$value" ;;
        instance) instance="$value" ;;
        active_run) active_run="$value" ;;
        url) url="$value" ;;
        reason) reason="$value" ;;
    esac
done < <("$PYTHON" -m hoya_market_agents.webapp.runtime_control probe "${PORT_FLAG[@]}")

case "$state" in
    free)
        echo "辯論室本來就沒有在跑（$url 沒有人在聽）。"
        exit 0
        ;;
    foreign)
        echo "沒有關閉：$reason"
        echo "這支腳本只關本專案的 WSL webapp，不會終止別人的程式。"
        exit 1
        ;;
esac

# 這一個只有在真的有人回答「要」的時候才會變成 yes，而且只有它會讓 POST 帶上同意。
# 沒有人回答過的關閉不帶同意，於是 probe 之後才開始的分析會被 server 以 409 擋下來
# ——那個空隙是關不掉的，能做的是不要在空隙裡假裝有人同意過。
consent="no"
if [ "$assume_yes" = "yes" ]; then
    consent="yes"
fi

if [ "$active_run" = "yes" ] && [ "$assume_yes" != "yes" ]; then
    if [ ! -t 0 ]; then
        echo "還沒關閉：目前有分析正在進行，這裡沒有終端機可以確認。"
        echo "確定要中斷的話，請回答呼叫者的確認，或執行：$CODE_ROOT/STOP-HERE.sh --yes"
        exit "$EXIT_NEEDS_CONFIRMATION"
    fi
    printf '目前有分析正在進行，關閉會中斷它。確定要關嗎？[y/N] '
    read -r answer || answer=""
    case "$answer" in
        y|Y|yes|YES) consent="yes" ;;
        *)
            echo "已取消，辯論室維持運行。"
            exit 0
            ;;
    esac
fi

CONSENT_FLAG=()
if [ "$consent" = "yes" ]; then
    CONSENT_FLAG=(--allow-active-run)
fi

stop_output=""
stop_code=0
if ! stop_output="$("$PYTHON" -m hoya_market_agents.webapp.runtime_control stop \
        --instance "$instance" "${CONSENT_FLAG[@]}" "${PORT_FLAG[@]}")"; then
    stop_code=1
fi

stop_reason=""
while IFS='=' read -r key value; do
    case "$key" in
        reason) stop_reason="$value" ;;
    esac
done <<< "$stop_output"

if [ "$stop_code" -eq 0 ]; then
    echo "辯論室已關閉（$url）。"
    exit 0
fi

echo "沒有關閉：${stop_reason:-伺服器沒有停止，原因見 webapp.jsonl。}"
exit 1
