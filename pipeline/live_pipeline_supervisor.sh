#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <hsk123-prose-pid> <hsk456-prose-pid>" >&2
  exit 2
fi

prose123="$1"
prose456="$2"
children=()

stop_children() {
  for pid in "${children[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
}
trap stop_children EXIT INT TERM

bash pipeline/resume_sanguoyanyi_constrained.sh "$prose123" hsk123 4 \
  >> runs/graded-readers/sanguoyanyi-full-hsk123/orchestrator.log 2>&1 &
wrapper123=$!
children+=("$wrapper123")
echo "$wrapper123" > runs/graded-readers/sanguoyanyi-full-hsk123/wrapper.pid

bash pipeline/resume_sanguoyanyi_constrained.sh "$prose456" hsk456 5 \
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
japanese_watcher=$!
children+=("$japanese_watcher")
echo "$japanese_watcher" > runs/graded-readers/wagahai-watcher.pid

python3 -m pipeline.japanese_agent_harness book \
  --source-dir books/japanese/wagahai_wa_neko_de_aru \
  --levels n5 n4 \
  --book-run-id wagahai-n5n4-full \
  --runs-dir runs/japanese-smoke-agent \
  --concurrency 1 --chapter-concurrency 1 --chapter-retries 2 \
  --length-repair-rounds 2 --max-repairs 3 --final-effort max \
  --timeout 1200 --skip-annotations \
  >> runs/japanese-smoke-agent/wagahai-n5n4-full/orchestrator.log 2>&1 &
n5n4=$!
children+=("$n5n4")
echo "$n5n4" > runs/japanese-smoke-agent/wagahai-n5n4-full/coordinator.pid

(
  while pgrep -f 'codex exec.*wagahai-full-n2n1-isolated' >/dev/null; do
    sleep 10
  done
  exec python3 -m pipeline.japanese_agent_harness book \
    --source-dir books/japanese/wagahai_wa_neko_de_aru \
    --book-run-id wagahai-full-n2n1-isolated \
    --levels n2 n1 --concurrency 1 --chapter-concurrency 1 \
    --chapter-retries 2 --length-repair-rounds 2 \
    >> runs/graded-readers/wagahai-full-n2n1-isolated/orchestrator.log 2>&1
) &
n2n1=$!
children+=("$n2n1")
echo "$n2n1" > runs/graded-readers/wagahai-full-n2n1-isolated/coordinator.pid

wait
