# WSL release acceptance

所有命令均從 Ubuntu-24.04 WSL 的 Code Root 執行。預設只跑離線 gate：

```bash
bash docs/work/wsl-only-runtime-onboarding/acceptance/run.sh offline
```

真 provider 必須明確解鎖，且只接受 Linux CLI 目錄；任一 canary 失敗即停止：

```bash
HOYA_EXECUTE_PROVIDER_ACCEPTANCE=YES \
HOYA_PROVIDER_BIN_DIR="$HOME/.local/bin" \
HOYA_ACCEPTANCE_EVIDENCE="$PWD/docs/work/wsl-only-runtime-onboarding/acceptance/evidence/<id>" \
bash docs/work/wsl-only-runtime-onboarding/acceptance/run.sh provider
```

Data Root 稽核、精確 launch token 與 final run reconciliation 由 `tools/acceptance.py`
的 `snapshot-data-root`、`compare-data-root`、`poll-launch`、`reconcile-run` 子命令完成。
證據只保存安全摘要、hash、版本、狀態與計數；不保存 prompt、response、raw stderr、
credential 或 launch token。scratch canary 內容在摘要完成後刪除。

單次 canary 或單次 7/7 run 只證明本次驗收通過，不保證 Provider 未來永久可靠。
