#!/bin/sh
set -eu

HOME_DIR="${HOME:-/home/node}"
SSH_SOURCE_DIR="${SSH_SOURCE_DIR:-/ssh-host}"
SSH_DIR="$HOME_DIR/.ssh"

if [ -d "$SSH_SOURCE_DIR" ]; then
  mkdir -p "$SSH_DIR"
  cp -R "$SSH_SOURCE_DIR"/. "$SSH_DIR"/ 2>/dev/null || true
  chmod 700 "$SSH_DIR" 2>/dev/null || true
  find "$SSH_DIR" -type f -exec chmod 600 {} \; 2>/dev/null || true
  if command -v ssh-keyscan >/dev/null 2>&1; then
    touch "$SSH_DIR/known_hosts"
    chmod 600 "$SSH_DIR/known_hosts" 2>/dev/null || true
    if ! grep -q "github.com" "$SSH_DIR/known_hosts" 2>/dev/null; then
      ssh-keyscan github.com >> "$SSH_DIR/known_hosts" 2>/dev/null || true
    fi
  fi
fi

if command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory /workspace 2>/dev/null || true
  git config --global --add safe.directory /workspace/LangGraph_Manager 2>/dev/null || true
  if [ -d /workspace ]; then
    find /workspace -mindepth 1 -maxdepth 3 -type d -name .git 2>/dev/null | while read -r git_dir; do
      repo_dir=$(dirname "$git_dir")
      git config --global --add safe.directory "$repo_dir" 2>/dev/null || true
    done
  fi
fi

exec claude "$@"
