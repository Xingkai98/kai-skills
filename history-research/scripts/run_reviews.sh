#!/usr/bin/env bash
# Run the context-free review prompts with fresh Claude Code sessions.
# Each angle gets a scratch directory containing only the report inputs, so it
# cannot read review.md, earlier reviews, or project CLAUDE.md/settings.
#
# Usage: scripts/run_reviews.sh <report-dir> [model]
#        REVIEW_ONLY=01-facts,05-structure-readability scripts/run_reviews.sh <report-dir>
# Run `review-impact` first to see which angles actually need a rerun.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <report-dir> [model]" >&2
  exit 2
fi

DIR="$(cd "$1" && pwd)"
MODEL="${2:-${REVIEW_MODEL:-}}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
ONLY="${REVIEW_ONLY:-}"

if [[ ! -d "$DIR/review" ]]; then
  echo "error: $DIR/review not found; run review-kit first" >&2
  exit 2
fi
for required in report.md sources.tsv report.html report.pdf report.build.json; do
  if [[ ! -f "$DIR/$required" ]]; then
    echo "error: $DIR/$required missing; build the review copy first" >&2
    exit 2
  fi
done

if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  echo "error: $CLAUDE_BIN not found; run the prompts manually or install Claude Code" >&2
  exit 2
fi

mkdir -p "$DIR/review/out"
shopt -s nullglob
prompts=("$DIR"/review/*.prompt.md)
if [[ ${#prompts[@]} -eq 0 ]]; then
  echo "error: no review/*.prompt.md files; run review-kit first" >&2
  exit 2
fi

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# Only the inputs go into the scratch directory. No review/, review.md,
# CLAUDE.md, .claude/, or earlier reviewer output.
cp "$DIR/report.md" "$DIR/sources.tsv" "$DIR/report.html" "$DIR/report.pdf" "$DIR/report.build.json" "$WORK/"
if [[ -d "$DIR/assets" ]]; then
  cp -a "$DIR/assets" "$WORK/"
fi

echo "report: $DIR"
echo "prompts: ${#prompts[@]}"
echo "scratch: $WORK (fresh cwd per angle; no review/history files)"
echo

for prompt in "${prompts[@]}"; do
  key="$(basename "$prompt" .prompt.md)"
  if [[ -n "$ONLY" ]]; then
    case ",$ONLY," in
      *",$key,"*) ;;
      *) echo "skip $key (not in REVIEW_ONLY)"; continue ;;
    esac
  fi
  out="$DIR/review/out/${key}.out.md"
  echo "==> $key"
  args=(
    -p "$(cat "$prompt")"
    --allowedTools "Read,Grep,Glob,WebFetch,WebSearch"
    --append-system-prompt "You are a fresh, context-free reviewer. Do not assume any prior conversation, summary, or conclusion. Read only the files named in the prompt and judge independently. Do not read review/, review.md, or other reviewers' outputs; they are not present."
    --setting-sources ""
    --strict-mcp-config
    --disable-slash-commands
    --no-session-persistence
    --output-format text
  )
  if [[ -n "$MODEL" ]]; then
    args+=(--model "$MODEL")
  fi
  (cd "$WORK" && "$CLAUDE_BIN" "${args[@]}") > "$out"
  echo "    -> $out"
done

echo
echo "done. Fix P0/P1, rebuild, and rerun affected angles; then run check --strict --require-review."
