#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <hsk123-prose-pid> <hsk456-prose-pid>" >&2
  exit 2
fi

children=()
stop_children() {
  for pid in "${children[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
}
trap stop_children EXIT INT TERM

bash pipeline/resume_sanguoyanyi_constrained.sh "$1" hsk123 4 \
  >> runs/graded-readers/sanguoyanyi-full-hsk123/orchestrator.log 2>&1 &
wrapper123=$!
children+=("$wrapper123")
echo "$wrapper123" > runs/graded-readers/sanguoyanyi-full-hsk123/wrapper.pid

bash pipeline/resume_sanguoyanyi_constrained.sh "$2" hsk456 5 \
  >> runs/graded-readers/sanguoyanyi-full-hsk456/orchestrator.log 2>&1 &
wrapper456=$!
children+=("$wrapper456")
echo "$wrapper456" > runs/graded-readers/sanguoyanyi-full-hsk456/wrapper.pid

bash pipeline/finalize_sanguoyanyi.sh "$wrapper123" "$wrapper456" \
  >> runs/graded-readers/sanguoyanyi-finalizer-logs/finalizer.log 2>&1 &
finalizer=$!
children+=("$finalizer")
echo "$finalizer" > runs/graded-readers/sanguoyanyi-finalizer.pid

bash pipeline/resume_wagahai_after_chinese.sh "$finalizer" \
  >> runs/graded-readers/wagahai-watcher.log 2>&1 &
watcher=$!
children+=("$watcher")
echo "$watcher" > runs/graded-readers/wagahai-watcher.pid

wait
