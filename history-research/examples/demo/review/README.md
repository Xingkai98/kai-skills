# 无上下文审阅

本目录由 `history_research.py review-kit` 生成。每个 `*.prompt.md` 是一个
独立审阅任务，覆盖一个角度；`coverage.md` 是章节覆盖矩阵。

## 规则

1. **每个 prompt 起一个全新 agent**。不要把本会话的调研过程、结论或摘要
   带进去。agent 只应看到 prompt 里列出的文件。
2. **通读全文**，不要抽查。每个 agent 都要对每个章节给出结论。
3. 审阅输出存为 `review/out/<key>.out.md`，然后把问题修进 `report.md`。
4. P0/P1 必须修复并重跑对应审阅；P2 可记录后延。
5. 每个输出末尾必须有机器可读 JSON（`angle` / `review_id` / `md_sha256` /
   `sources_sha256` / `verdict` / `p0` / `p1` / `coverage`）；
   `check --require-review` 会校验版本哈希、P0/P1 为零、以及每个章节都在
   coverage 里。
6. 改完 `report.md` 后依次运行 `build`、`review-kit`（重新绑定哈希并生成
   新 prompt）、`review-impact`，只重跑受影响的角度；可用 `scopes.json`
   按报告收窄某个角度的章节范围。最终交付前仍要对同一整篇哈希跑完
   7 个角度（见 `../review/impact.md`）。
7. 把每轮结果记进 `../review.md`，并更新 `coverage.md` 的勾选。

## 运行

在 Claude Code 里用 `Agent`（或环境提供的 fresh subagent 工具），每个
prompt 一个 fresh subagent，原文作为任务，不要附加对话历史。

在 paseo 托管、或不允许嵌套子代理的环境里，用 shell 版（每个 prompt 一个
新鲜 `claude -p` 进程）：

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$HOME/.claude/skills/history-research}"
"$SKILL_DIR/scripts/run_reviews.sh" .
```

## 审阅角度

- `01-facts` — 事实与来源核验
- `02-chronology` — 时间线与因果
- `03-context-contrarian` — 地方细节与反方证据
- `04-images-licensing` — 图片、出处与许可
- `05-structure-readability` — 结构与可读性
- `06-render-qa` — HTML/PDF 渲染与交付
- `07-red-team` — 红队：努力推翻报告
