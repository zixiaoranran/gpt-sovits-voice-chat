#!/usr/bin/env bash
# 一键安装局部依赖（Linux / macOS）
#   ./install_deps.sh              # 只装核心依赖
#   ./install_deps.sh --asr        # 连实时对讲/语音识别一起装（约 2GB）
#   ./install_deps.sh --mirror tsinghua
set -u
cd "$(dirname "$0")" || exit 1

PY="python3"
if [ -x "./runtime/python.exe" ]; then PY="./runtime/python.exe"; fi
if [ -x "./runtime/bin/python3" ]; then PY="./runtime/bin/python3"; fi

echo "=============================================================="
echo " Using interpreter: $PY"
echo "=============================================================="
"$PY" ./install_deps.py "$@"
RC=$?
echo
if [ "$RC" -eq 0 ]; then
  echo "[DONE] Dependencies are ready."
else
  echo "[FAILED] Exit code $RC - see the messages above."
fi
exit "$RC"
