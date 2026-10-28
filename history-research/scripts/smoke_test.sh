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
*图源：[示例来源](https://example.com/source) · 作者自绘*

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
grep -q '"angle": "01-facts"' review/01-facts.prompt.md
echo "smoke test OK: $TMP (cleaned up)"
