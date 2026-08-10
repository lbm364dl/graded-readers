#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <active-prose-pid> <hsk123|hsk456> <concurrency>" >&2
  exit 2
fi

active_pid="$1"
group="$2"
concurrency="$3"

case "$group" in
  hsk123)
    levels=(hsk1 hsk2 hsk3)
    run_id="sanguoyanyi-full-hsk123"
    ;;
  hsk456)
    levels=(hsk4 hsk5 hsk6)
    run_id="sanguoyanyi-full-hsk456"
    ;;
  *)
    echo "unknown level group: $group" >&2
    exit 2
    ;;
esac

while kill -0 "$active_pid" 2>/dev/null; do
  sleep 15
done

exec python3 -m pipeline.agent_harness book \
  --source-dir books/chinese/sanguoyanyi \
  --levels "${levels[@]}" \
  --book-run-id "$run_id" \
  --concurrency "$concurrency" \
  --chapter-concurrency "$concurrency" \
  --chapter-retries 2 \
  --length-repair-rounds 2 \
  --max-repairs 3 \
  --final-effort xhigh \
  --skip-annotations
