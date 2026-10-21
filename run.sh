#!/usr/bin/env bash
# 短线雷达启动脚本（macOS / Linux）
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "找不到 python3。先装 Python 3.8 以上版本。"
  exit 1
fi

exec python3 app.py "$@"
