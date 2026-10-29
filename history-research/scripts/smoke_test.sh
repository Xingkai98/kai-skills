#!/usr/bin/env bash
# End-to-end smoke test: init-like scaffold -> build -> check -> review-kit.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"

cp "$SKILL_DIR/examples/demo/assets/timeline.png" ./timeline.png
cat > report.md <<'EOF'
---
title: "Smoke test"
subtitle: "Build and review pipeline"
date: "2026-10-28"
scope: "test"
sources_cutoff: "2026-10-28"
lang: "zh-CN"
---

# Smoke test

> 这是一条构建冒烟测试，不是正式报告。

## 一、测试图

![图 1：测试时间轴](timeline.png)
*图源：用户提供 · 2026-10-28 摄于测试环境*

测试正文。[示例来源](https://example.com/source)

## 参考资料

1. [示例来源](https://example.com/source)
EOF
cat > sources.tsv <<'EOF'
claim	source_url	source_title	quote	confidence	notes
测试主张	https://example.com/source	示例来源	示例引文	medium	smoke test
EOF

python3 "$SKILL_DIR/scripts/history_research.py" build report.md >/dev/null
python3 "$SKILL_DIR/scripts/history_research.py" check report.md --strict
python3 "$SKILL_DIR/scripts/history_research.py" review-kit report.md >/dev/null

test -s report.html
test -s report.pdf
test -s report.build.json
test -s review/01-facts.prompt.md
test -s review/coverage.md
test -s review/manifest.json
test -s review/manifests/*.json
test -s review/scopes.json
grep -q '"angle": "01-facts"' review/01-facts.prompt.md

# A sources-only change must add a new manifest, not overwrite the old one.
first_manifest="$(ls review/manifests/*.json)"
printf '补充主张\thttps://example.com/source\t示例来源\t示例引文\tlow\tsmoke test\n' >> sources.tsv
python3 "$SKILL_DIR/scripts/history_research.py" review-kit report.md >/dev/null
manifest_count="$(ls review/manifests/*.json | wc -l)"
if [[ "$manifest_count" -ne 2 ]]; then
  echo "error: expected 2 review manifests after a sources-only change, got $manifest_count" >&2
  exit 1
fi
python3 - "$first_manifest" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path("review/manifest.json").read_text(encoding="utf-8"))
assert len(manifest["current"]) == 64, manifest["current"]
assert manifest["current"] != manifest["md_sha256"], "review id must include sources"
assert manifest["sources_sha256"]
first = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert first["review_id"] != manifest["current"], "old manifest was overwritten"
PY
python3 "$SKILL_DIR/scripts/history_research.py" review-impact report.md --json --write >/dev/null
test -s review/impact.md
echo "smoke test OK: $TMP (cleaned up)"
