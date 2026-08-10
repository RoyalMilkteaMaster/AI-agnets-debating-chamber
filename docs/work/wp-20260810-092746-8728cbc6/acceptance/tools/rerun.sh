#!/usr/bin/env bash
# Ticket 09 驗收證據重跑入口。
#
#   bash docs/work/wp-20260810-092746-8728cbc6/acceptance/tools/rerun.sh
#
# 路徑全部從這個腳本自己的位置推得，所以在 repo 內哪個目錄下呼叫都一樣，
# 也可以用絕對路徑呼叫（例：wsl.exe -d Ubuntu-24.04 -- bash /mnt/d/.../rerun.sh）。
#
# 預設會覆寫 acceptance/ 底下的證據。要重跑但不覆寫（複驗常見），設輸出目錄：
#
#   HOYA_ACCEPTANCE_OUT=/tmp/t09-rerun bash .../rerun.sh
#
# 退出碼：0＝全部判定 PASS，1＝有 FAIL，9＝找不到 Code Root。
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CODE_ROOT="${HOYA_CODE_ROOT:-$(cd "$SCRIPT_DIR/../../../../.." && pwd)}"

if [ ! -d "$CODE_ROOT/hoya_market_agents" ]; then
  echo "找不到 Code Root（$CODE_ROOT 底下沒有 hoya_market_agents）" >&2
  echo "RESULT_EXIT=9"
  exit 9
fi

cd "$CODE_ROOT" || exit 9
export HOYA_CODE_ROOT="$CODE_ROOT"
export PYTHONDONTWRITEBYTECODE=1

python3 "$SCRIPT_DIR/gen_evidence.py"
CODE=$?
echo "GENERATOR_EXIT=$CODE"
echo "RESULT_EXIT=$CODE"
exit $CODE
