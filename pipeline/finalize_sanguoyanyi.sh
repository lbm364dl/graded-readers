#!/usr/bin/env bash
set -uo pipefail

# Unattended final quality gate for the six full-book 三国演义 readers.
# Optional arguments are PIDs of earlier orchestration watchers to await.

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

run_root="runs/graded-readers"
low_run="$run_root/sanguoyanyi-full-hsk123"
high_run="$run_root/sanguoyanyi-full-hsk456"
audit_run="$run_root/sanguoyanyi-omission-audit"
log_dir="$run_root/sanguoyanyi-finalizer-logs"
success_marker="$log_dir/published.success"
mkdir -p "$log_dir"
rm -f "$success_marker"

stamp() { date --iso-8601=seconds; }
note() { echo "$(stamp) $*" | tee -a "$log_dir/finalizer.log"; }

for watched_pid in "$@"; do
  if [[ ! "$watched_pid" =~ ^[0-9]+$ ]]; then
    note "invalid watcher PID: $watched_pid"
    exit 64
  fi
  while kill -0 "$watched_pid" 2>/dev/null; do
    sleep 30
  done
done

note "generation, deep repair, and initial annotation orchestration finished"

level_roots=(
  --level-root "hsk1=$low_run" --level-root "hsk2=$low_run"
  --level-root "hsk3=$low_run" --level-root "hsk4=$high_run"
  --level-root "hsk5=$high_run" --level-root "hsk6=$high_run"
)

note "preflight: requiring exactly 720 complete accepted generation artifacts"
if ! python3 -m pipeline.sanguoyanyi_finalizer preflight \
    --run-dir "$low_run" --run-dir "$high_run" "${level_roots[@]}" \
    --expected-chapters 120 --recover \
    --report "$log_dir/preflight.json" >>"$log_dir/preflight.log" 2>&1; then
  note "generation preflight/recovery remained incomplete"
  exit 2
fi

omission_ok=0
if python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1])).get("status") != "complete")' \
    "$audit_run/report.json" 2>/dev/null; then
  note "reusing completed initial omission audit checkpoint"
  omission_ok=1
else
  for attempt in 1 2 3; do
    extra=()
    # Attempt two extends cached targeted healing through rounds six/seven.
    # Reserve a global refresh for the last attempt, where it can recover a
    # semantically invalid cached response rather than needlessly regenerating
    # hundreds of already valid classifications on every retry.
    (( attempt > 2 )) && extra=(--refresh)
    note "omission audit attempt $attempt"
    if python3 -m pipeline.audit_compact_omissions \
        --run-dir "$low_run" --run-dir "$high_run" \
        --output-dir "$audit_run" --concurrency 9 \
        --classify-effort high --repair-effort xhigh --review-effort high \
        --max-repair-rounds 7 \
        --promote-passed "${extra[@]}" \
        >>"$log_dir/omission.log" 2>&1; then
      omission_ok=1
      break
    fi
  done
fi
if (( ! omission_ok )); then
  note "omission audit remained blocked after three attempts"
  exit 2
fi

sources=()
for source_path in books/chinese/sanguoyanyi/chapter_*.txt; do
  sources+=(--source "$source_path")
done

run_continuity() {
  local level=$1
  local run_dir=$2
  local cap=$3
  local repair_rounds=${4:-2}
  local attempt
  for attempt in 1 2 3; do
    local extra=()
    (( attempt > 1 )) && extra=(--refresh)
    note "continuity $level attempt $attempt"
    if python3 -m pipeline.book_continuity \
        "${sources[@]}" --book-run-dir "$run_dir" --level "$level" \
        --concurrency "$cap" --review-effort medium --repair-effort xhigh \
        --max-repair-rounds "$repair_rounds" "${extra[@]}" \
        >>"$log_dir/continuity-$level.log" 2>&1; then
      return 0
    fi
  done
  return 2
}

# Six independent level audits, sharing a conservative total of nine calls.
continuity_pids=()
run_continuity hsk1 "$low_run" 2 & continuity_pids+=("$!")
run_continuity hsk2 "$low_run" 2 & continuity_pids+=("$!")
run_continuity hsk3 "$low_run" 2 & continuity_pids+=("$!")
run_continuity hsk4 "$high_run" 1 & continuity_pids+=("$!")
run_continuity hsk5 "$high_run" 1 & continuity_pids+=("$!")
run_continuity hsk6 "$high_run" 1 & continuity_pids+=("$!")
continuity_failed=0
for child_pid in "${continuity_pids[@]}"; do
  wait "$child_pid" || continuity_failed=1
done
if (( continuity_failed )); then
  note "one or more continuity levels remained blocked"
  exit 2
fi

note "healing strict per-chapter HSK1<HSK2<...<HSK6 prose lengths"
if ! python3 -m pipeline.sanguoyanyi_finalizer length \
    --run-dir "$low_run" --run-dir "$high_run" "${level_roots[@]}" \
    --expected-chapters 120 --heal --max-rounds 6 \
    --report "$log_dir/length-healing.json" >>"$log_dir/length-healing.log" 2>&1; then
  note "six-level monotonic length healing remained blocked"
  exit 2
fi

# Healing is a fresh source-grounded generation. A scene reviewer may pass
# while still recording an omission for later materiality judgment, so the
# pre-healing omission audit cannot certify the new prose. Re-run it after the
# final broad rewrite and permit only independently reviewed promotions.
post_length_omission_ok=0
for attempt in 1 2 3; do
  note "post-length omission audit attempt $attempt"
  if python3 -m pipeline.audit_compact_omissions \
      --run-dir "$low_run" --run-dir "$high_run" \
      --output-dir "$audit_run" --concurrency 9 \
      --classify-effort high --repair-effort xhigh --review-effort high \
      --max-repair-rounds 7 \
      --promote-passed --refresh \
      >>"$log_dir/post-length-omission.log" 2>&1; then
    post_length_omission_ok=1
    break
  fi
done
if (( ! post_length_omission_ok )); then
  note "post-length omission audit remained blocked"
  exit 2
fi

# An omission repair aims to retain length, but publication requires strict
# per-chapter monotonicity, not an approximation. Fail closed here rather than
# launching another rewrite that would invalidate the just-completed audit.
note "post-omission read-only monotonic length verification"
if ! python3 -m pipeline.sanguoyanyi_finalizer length \
    --run-dir "$low_run" --run-dir "$high_run" \
    --expected-chapters 120 \
    --report "$log_dir/post-omission-length.json" \
    >>"$log_dir/length-healing.log" 2>&1; then
  note "post-omission repairs broke strict six-level lengths"
  exit 2
fi

# Length healing performs a fresh source-grounded rewrite. Even a good rewrite
# (and the subsequent omission repair) can alter a handoff already checked
# above, so continuity must be proven again after the last prose mutation. This
# final pass is read-only/fail-closed:
# annotations and publication never start if any new edge is questionable.
note "post-length continuity verification: no further prose mutation allowed"
continuity_pids=()
run_continuity hsk1 "$low_run" 2 0 & continuity_pids+=("$!")
run_continuity hsk2 "$low_run" 2 0 & continuity_pids+=("$!")
run_continuity hsk3 "$low_run" 2 0 & continuity_pids+=("$!")
run_continuity hsk4 "$high_run" 1 0 & continuity_pids+=("$!")
run_continuity hsk5 "$high_run" 1 0 & continuity_pids+=("$!")
run_continuity hsk6 "$high_run" 1 0 & continuity_pids+=("$!")
continuity_failed=0
for child_pid in "${continuity_pids[@]}"; do
  wait "$child_pid" || continuity_failed=1
done
if (( continuity_failed )); then
  note "post-length continuity verification failed"
  exit 2
fi

note "post-promotion preflight: confirming all 720 reports remain complete"
if ! python3 -m pipeline.sanguoyanyi_finalizer preflight \
    --run-dir "$low_run" --run-dir "$high_run" \
    --expected-chapters 120 --report "$log_dir/post-promotion-preflight.json" \
    >>"$log_dir/preflight.log" 2>&1; then
  note "post-promotion artifact preflight failed"
  exit 2
fi

note "refreshing only missing or stale annotations"
annotation_ok=0
for attempt in 1 2; do
  if python3 -m pipeline.annotate_chinese \
      --run-dir "$low_run" --run-dir "$high_run" \
      --batch-report "$run_root/sanguoyanyi-final-annotations.json" \
      --concurrency 9 --chapter-concurrency 9 --max-annotation-repairs 1 \
      --annotation-mode constrained-delta --annotation-review-policy chapter \
      --annotation-metadata-batch-targets 150 \
      >>"$log_dir/annotations.log" 2>&1; then
    annotation_ok=1
    break
  fi
  note "annotation-only retry $attempt failed; retrying resumably"
done
if (( ! annotation_ok )); then
  note "annotation-only gate remained blocked"
  exit 2
fi

note "running deterministic six-level publication audit"
if ! python3 -m pipeline.publish_sanguoyanyi \
    --run-dir "$low_run" --run-dir "$high_run" \
    --levels hsk1 hsk2 hsk3 hsk4 hsk5 hsk6 \
    --expected-chapters 120 --build-app-content --build-dictionary \
    >>"$log_dir/publication.log" 2>&1; then
  note "deterministic publication failed; no success marker written"
  exit 2
fi

note "all six 三国演义 readers published successfully"
touch "$success_marker"
