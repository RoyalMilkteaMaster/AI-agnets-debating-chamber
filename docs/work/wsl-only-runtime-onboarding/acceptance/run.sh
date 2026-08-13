#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd -P)"
TOOL="$ROOT/docs/work/wsl-only-runtime-onboarding/acceptance/tools/acceptance.py"
GIT_DIR_WSL="$(python3 "$TOOL" resolve-git-dir --code-root "$ROOT")"
MODE="${1:-offline}"
cd "$ROOT"

if [ "$MODE" = offline ]; then
  DENY_BIN="$(mktemp -d)"
  COMPILE_ROOT="$(mktemp -d)"
  trap 'rm -rf -- "$DENY_BIN" "$COMPILE_ROOT"' EXIT
  for provider in codex claude agy; do
    printf '#!/usr/bin/env bash\necho "offline gate invoked provider: %s" >&2\nexit 97\n' \
      "$provider" > "$DENY_BIN/$provider"
    chmod 700 "$DENY_BIN/$provider"
  done
  export PATH="$DENY_BIN:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  export HOYA_OFFLINE_PROVIDER_GUARD=1
  export PYTHONPATH="$ROOT/docs/work/wsl-only-runtime-onboarding/acceptance/offline_guard:$ROOT/tests${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONDONTWRITEBYTECODE=1
  export PYTHONPYCACHEPREFIX="$COMPILE_ROOT/pycache"

  bash -n setup-wsl.sh START-HERE.sh STOP-HERE.sh \
    docs/work/wsl-only-runtime-onboarding/acceptance/run.sh
  mapfile -t test_files < <(find tests -maxdepth 1 -type f -name 'test_*.py' -print | LC_ALL=C sort)
  executed_module_count=0
  for test_file in "${test_files[@]}"; do
    test_module="${test_file%.py}"
    test_module="${test_module//\//.}"
    if [ "$test_module" = tests.test_wsl_entrypoints ]; then
      GIT_DIR="$GIT_DIR_WSL" GIT_WORK_TREE="$ROOT" \
        python3 -m unittest -v "$test_module"
    else
      python3 -m unittest -v "$test_module"
    fi
    executed_module_count=$((executed_module_count + 1))
  done
  if [ "$executed_module_count" -ne "${#test_files[@]}" ]; then
    echo "offline gate module count mismatch: executed=$executed_module_count found=${#test_files[@]}" >&2
    exit 1
  fi
  python3 -m compileall -q -f hoya_market_agents tests \
    docs/work/wsl-only-runtime-onboarding/acceptance/tools/acceptance.py
  node --check hoya_market_agents/webapp/static/live.js
  node tests/js/live_harness.js
  git -c core.whitespace=cr-at-eol --git-dir="$GIT_DIR_WSL" \
    --work-tree="$ROOT" diff --check
  for path in \
    hoya_market_agents/antigravity_adapter.py \
    hoya_market_agents/debate_driver.py \
    docs/work/wsl-only-runtime-onboarding/acceptance/run.sh \
    docs/work/wsl-only-runtime-onboarding/acceptance/README.md \
    docs/work/wsl-only-runtime-onboarding/acceptance/offline_guard/sitecustomize.py \
    docs/work/wsl-only-runtime-onboarding/acceptance/tools/acceptance.py \
    tests/test_antigravity_adapter.py \
    tests/test_debate_driver.py \
    tests/test_frontend_redesign_acceptance.py \
    tests/test_webapp_asset_picker.py \
    tests/test_webapp_shutdown.py \
    tests/test_wsl_release_acceptance.py; do
    if grep -nE '[[:blank:]]+$' "$path"; then
      echo "trailing whitespace in $path" >&2
      exit 1
    fi
  done
  echo 'OFFLINE_ACCEPTANCE=PASS'
  exit 0
fi

if [ "$MODE" != provider ]; then
  echo 'usage: run.sh [offline|provider]' >&2
  exit 2
fi
if [ "${HOYA_EXECUTE_PROVIDER_ACCEPTANCE:-}" != YES ]; then
  echo 'provider mode requires HOYA_EXECUTE_PROVIDER_ACCEPTANCE=YES' >&2
  exit 2
fi
: "${HOYA_ACCEPTANCE_EVIDENCE:?set evidence directory}"
: "${HOYA_PROVIDER_BIN_DIR:?set Linux provider bin directory}"
mkdir -p "$HOYA_ACCEPTANCE_EVIDENCE/providers"
for provider in codex claude antigravity; do
  python3 "$TOOL" canary "$provider" --execute-provider \
    --provider-bin-dir "$HOYA_PROVIDER_BIN_DIR" \
    --scratch-root "$(mktemp -d)/$provider" \
    --output "$HOYA_ACCEPTANCE_EVIDENCE/providers/$provider.json"
  python3 "$TOOL" audit-processes \
    --output "$HOYA_ACCEPTANCE_EVIDENCE/providers/$provider-process-audit.json"
done
echo 'PROVIDER_ACCEPTANCE=PASS'
