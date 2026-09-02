#!/usr/bin/env bash
set -uo pipefail

# Transfer the shared model-call budget to Japanese only after verified Chinese
# publication. The sole argument is the Chinese finalizer PID.

if (( $# != 1 )) || [[ ! "$1" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 CHINESE_FINALIZER_PID" >&2
  exit 64
fi

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

chinese_pid=$1
run_root="runs/graded-readers"
chinese_log="$run_root/sanguoyanyi-finalizer-logs/finalizer.log"
chinese_success="$run_root/sanguoyanyi-finalizer-logs/published.success"
japanese_run="$run_root/wagahai-full"
japanese_log="$run_root/wagahai-finalizer.log"
japanese_success="$run_root/wagahai-published.success"
split_runs=(
  "runs/japanese-smoke-agent/wagahai-n5n4-full"
  "$run_root/wagahai-n3-full-smoke-20260807"
  "$run_root/wagahai-full-n2n1-isolated"
)

while kill -0 "$chinese_pid" 2>/dev/null; do
  sleep 30
done

if [[ ! -f "$chinese_success" ]]; then
  echo "Chinese finalizer did not record publication success; Japanese run not started" >>"$japanese_log"
  exit 2
fi

split_count=0
for run in "${split_runs[@]}"; do
  [[ -f "$run/book-report.json" ]] && ((split_count += 1))
done

publish_args=()
if (( split_count > 0 )); then
  if (( split_count != ${#split_runs[@]} )); then
    echo "$(date --iso-8601=seconds) Incomplete split-run set; refusing duplicate Japanese generation" >>"$japanese_log"
    exit 2
  fi
  echo "$(date --iso-8601=seconds) Reusing three existing Japanese split runs" >>"$japanese_log"
  # Drain the foreground split coordinators and any detached model children.
  # Do not wait for a "complete" book report here: a recoverable blocked run
  # is exactly what the resumable refresh below is meant to heal.
  split_process_pattern='(japanese_agent_harness|codex exec).*(wagahai-n5n4-full|wagahai-n3-full-smoke-20260807|wagahai-full-n2n1-isolated)'
  while pgrep -f "$split_process_pattern" >/dev/null; do
    sleep 30
  done

  # The low-level foreground generator deliberately skips annotations to keep
  # scarce model slots focused on prose.  Refresh every split run resumably
  # here, after Chinese calls have drained, so strict publication can never
  # race a prose-only "complete" report.  Existing reviewed chunks are cached.
  annotation_pids=()
  python3 -m pipeline.japanese_agent_harness book \
    --source-dir books/japanese/wagahai_wa_neko_de_aru \
    --book-run-id wagahai-n5n4-full --levels n5 n4 \
    --runs-dir runs/japanese-smoke-agent \
    --concurrency 3 --chapter-concurrency 3 --chapter-retries 2 \
    --length-repair-rounds 0 --max-repairs 3 --final-effort low \
    --max-annotation-repairs 2 \
    >>"$japanese_log" 2>&1 &
  annotation_pids+=("$!")
  python3 -m pipeline.japanese_agent_harness book \
    --source-dir books/japanese/wagahai_wa_neko_de_aru \
    --book-run-id wagahai-n3-full-smoke-20260807 --levels n3 \
    --concurrency 3 --chapter-concurrency 3 --chapter-retries 2 \
    --length-repair-rounds 0 --max-repairs 3 --final-effort low \
    --max-annotation-repairs 2 \
    >>"$japanese_log" 2>&1 &
  annotation_pids+=("$!")
  python3 -m pipeline.japanese_agent_harness book \
    --source-dir books/japanese/wagahai_wa_neko_de_aru \
    --book-run-id wagahai-full-n2n1-isolated --levels n2 n1 \
    --concurrency 3 --chapter-concurrency 3 --chapter-retries 2 \
    --length-repair-rounds 0 --max-repairs 3 --final-effort low \
    --max-annotation-repairs 2 \
    >>"$japanese_log" 2>&1 &
  annotation_pids+=("$!")
  annotation_failed=0
  for pid in "${annotation_pids[@]}"; do
    if ! wait "$pid"; then
      annotation_failed=1
    fi
  done
  if (( annotation_failed )); then
    echo "$(date --iso-8601=seconds) Japanese split-run annotation refresh failed" >>"$japanese_log"
    exit 2
  fi

  for run in "${split_runs[@]}"; do
    publish_args+=(--run-dir "$run")
  done
else
  if ! python3 -m pipeline.japanese_agent_harness book \
    --source-dir books/japanese/wagahai_wa_neko_de_aru \
    --book-run-id wagahai-full --levels n5 n4 n3 n2 n1 \
    --concurrency 5 --chapter-concurrency 5 \
    --chapter-retries 2 --length-repair-rounds 2 --max-repairs 3 \
    --final-effort low --max-annotation-repairs 2 \
    >>"$japanese_log" 2>&1; then
    echo "$(date --iso-8601=seconds) Japanese generation failed" >>"$japanese_log"
    exit 2
  fi
  publish_args=(--run-dir "$japanese_run")
fi

if ! python3 -m pipeline.publish_wagahai \
  "${publish_args[@]}" --levels n5 n4 n3 n2 n1 \
  --expected-chapters 11 --build-app-content \
  >>"$japanese_log" 2>&1; then
  echo "$(date --iso-8601=seconds) Japanese publication failed" >>"$japanese_log"
  exit 2
fi

echo "$(date --iso-8601=seconds) Japanese readers published successfully" >>"$japanese_log"
printf '%s\n' "$(date --iso-8601=seconds)" >"$japanese_success"
