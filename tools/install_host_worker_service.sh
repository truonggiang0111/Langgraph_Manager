#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
UNIT_TEMPLATE="${REPO_ROOT}/ops/systemd/langgraph-host-worker.service"
AUTOSTART_TEMPLATE="${REPO_ROOT}/ops/autostart/langgraph-host-worker.desktop"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
DEFAULT_TARGET_USER="${SUDO_USER:-$(stat -c '%U' "${REPO_ROOT}" 2>/dev/null || true)}"
TARGET_USER="${TARGET_USER:-${DEFAULT_TARGET_USER:-${USER}}}"
TARGET_HOME="${TARGET_HOME:-$(getent passwd "${TARGET_USER}" | cut -d: -f6)}"
USER_SYSTEMD_DIR="${TARGET_HOME}/.config/systemd/user"
UNIT_PATH="${USER_SYSTEMD_DIR}/langgraph-host-worker.service"
AUTOSTART_DIR="${TARGET_HOME}/.config/autostart"
AUTOSTART_PATH="${AUTOSTART_DIR}/langgraph-host-worker.desktop"

if [[ -z "${NODE_BIN}" ]]; then
  echo "node is not installed or not on PATH" >&2
  exit 1
fi

if [[ -z "${TARGET_HOME}" ]]; then
  echo "could not resolve home directory for TARGET_USER=${TARGET_USER}" >&2
  exit 1
fi

run_as_target_user() {
  if [[ "$(id -un)" == "${TARGET_USER}" ]]; then
    "$@"
    return
  fi
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "${TARGET_USER}" -- "$@"
    return
  fi
  su - "${TARGET_USER}" -c "$(printf '%q ' "$@")"
}

mkdir -p "${USER_SYSTEMD_DIR}"
mkdir -p "${AUTOSTART_DIR}"

python3 - <<'PY' "${UNIT_TEMPLATE}" "${UNIT_PATH}" "${REPO_ROOT}" "${NODE_BIN}"
from pathlib import Path
import sys

template = Path(sys.argv[1]).read_text(encoding="utf-8")
unit_path = Path(sys.argv[2])
repo_root = Path(sys.argv[3]).resolve()
node_bin = Path(sys.argv[4]).resolve()
worker_path = (repo_root / "tools" / "host_browser_bridge.mjs").resolve()

updated = template.replace(
    "WorkingDirectory=/home/giang/Work/AgentStack/LangGraph_Manager/LangGraph_Manager",
    f"WorkingDirectory={repo_root}",
).replace(
    "ExecStart=/usr/bin/env node /home/giang/Work/AgentStack/LangGraph_Manager/LangGraph_Manager/tools/host_browser_bridge.mjs",
    f"ExecStart={node_bin} {worker_path}",
)
unit_path.write_text(updated, encoding="utf-8")
PY

python3 - <<'PY' "${AUTOSTART_TEMPLATE}" "${AUTOSTART_PATH}" "${REPO_ROOT}" "${NODE_BIN}"
from pathlib import Path
import sys

template = Path(sys.argv[1]).read_text(encoding="utf-8")
desktop_path = Path(sys.argv[2])
repo_root = Path(sys.argv[3]).resolve()
node_bin = Path(sys.argv[4]).resolve()
worker_path = (repo_root / "tools" / "host_browser_bridge.mjs").resolve()

updated = template.replace(
    "Path=/home/giang/Work/AgentStack/LangGraph_Manager/LangGraph_Manager",
    f"Path={repo_root}",
).replace(
    "Exec=/usr/bin/env node /home/giang/Work/AgentStack/LangGraph_Manager/LangGraph_Manager/tools/host_browser_bridge.mjs",
    f"Exec={node_bin} {worker_path}",
)
desktop_path.write_text(updated, encoding="utf-8")
PY

SYSTEMD_STARTED=0
if run_as_target_user env XDG_RUNTIME_DIR="/run/user/$(id -u "${TARGET_USER}")" systemctl --user daemon-reload >/dev/null 2>&1; then
  run_as_target_user env XDG_RUNTIME_DIR="/run/user/$(id -u "${TARGET_USER}")" systemctl --user enable --now langgraph-host-worker.service
  SYSTEMD_STARTED=1
fi

if [[ "${SYSTEMD_STARTED}" -eq 0 ]]; then
  if ! pgrep -u "$(id -u "${TARGET_USER}")" -af "host_browser_bridge.mjs" >/dev/null 2>&1; then
    run_as_target_user sh -lc "nohup $(printf '%q' "${NODE_BIN}") $(printf '%q' "${REPO_ROOT}/tools/host_browser_bridge.mjs") >/tmp/langgraph-host-worker.log 2>&1 &"
    sleep 1
  fi
fi

if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "${TARGET_USER}" >/dev/null 2>&1 || true
fi

if [[ "${SYSTEMD_STARTED}" -eq 1 ]]; then
  echo "Installed and started langgraph-host-worker.service for ${TARGET_USER}"
  echo "Check: sudo -u ${TARGET_USER} XDG_RUNTIME_DIR=/run/user/$(id -u "${TARGET_USER}") systemctl --user status langgraph-host-worker.service"
else
  echo "Installed fallback desktop autostart at ${AUTOSTART_PATH} for ${TARGET_USER}"
  echo "Started host worker with nohup because systemd --user is unavailable in this session"
  echo "Check: pgrep -u $(id -u "${TARGET_USER}") -af host_browser_bridge.mjs"
fi
