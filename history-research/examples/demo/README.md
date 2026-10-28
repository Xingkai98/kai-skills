# Demo

最小可构建示例：展示时间轴、图片出处、证据分级，以及 Markdown → HTML/PDF 的同步构建。

```bash
python3 ../../scripts/history_research.py build report.md
python3 ../../scripts/history_research.py check report.md --strict
```

产物：`report.html`、`report.pdf`、`report.build.json`。

**注意**：`review/coverage.md` 故意留空。这是构建演示，不是经过 7 个角度无上下文审阅的正式报告；正式报告必须跑完 `review-kit` 生成的 7 个 prompt，并用 `check --strict --require-review` 过闸门。
