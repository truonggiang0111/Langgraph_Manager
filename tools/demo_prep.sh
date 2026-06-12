#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/giang/Work/AgentStack/LangGraph_Manager/LangGraph_Manager"

echo "[1/5] Restart host worker"
sudo -u giang XDG_RUNTIME_DIR=/run/user/1000 systemctl --user set-environment FACEBOOK_DEMO_MODE=1 >/dev/null
sudo -u giang XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart langgraph-host-worker.service >/dev/null

echo "[2/5] Restart langgraph-manager container"
docker restart langgraph-manager >/dev/null

echo "[3/5] Wait for app"
timeout 40s bash -lc 'until curl -sf http://127.0.0.1:8899/docs >/dev/null; do sleep 1; done'

echo "[4/5] Cleanup old debug tabs if any"
curl -sf http://127.0.0.1:3342/health >/dev/null

echo "[5/5] Launch debug browser on Facebook"
curl -sf -X POST http://127.0.0.1:3342/launch-debug \
  -H 'content-type: application/json' \
  --data '{"browser":"brave","url":"https://www.facebook.com/","port":9222}' >/dev/null

echo
echo "Demo environment is ready."
echo "Open: http://127.0.0.1:8899"
echo "Recommended query: thuc tap devops hcm"
