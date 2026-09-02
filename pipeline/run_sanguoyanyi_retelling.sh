#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

python3 -m pipeline.prepare_retelling_sources \
  --source-dir books/chinese/sanguoyanyi \
  --output-dir runs/retelling-sources/sanguoyanyi \
  --levels hsk1 hsk2 hsk3

mkdir -p runs/graded-readers/sanguoyanyi-retelling-logs
pids=()
for spec in hsk1:300 hsk2:500 hsk3:750; do
  level=${spec%%:*}
  target=${spec##*:}
  python3 -m pipeline.agent_harness book \
    --source-dir "runs/retelling-sources/sanguoyanyi/$level" \
    --levels "$level" \
    --book-run-id "sanguoyanyi-retelling-$level" \
    --target-chars "$target" \
    --concurrency 2 --chapter-concurrency 2 --chapter-retries 2 \
    --length-repair-rounds 0 --max-repairs 2 \
    --adapt-effort low --review-effort low \
    --repair-effort low --final-effort low \
    --skip-annotations \
    >>"runs/graded-readers/sanguoyanyi-retelling-logs/$level.log" 2>&1 &
  pids+=("$!")
done

failed=0
for pid in "${pids[@]}"; do
  wait "$pid" || failed=1
done
exit "$failed"
