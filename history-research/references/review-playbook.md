# 无上下文审阅

历史写作最容易犯的错，不是“写得不好”，而是**作者自己相信了一个顺滑但错误的版本**。所以成稿前必须让没有参与调研的 agent 从多个角度独立审阅。

## 什么叫“无上下文”

每个审阅 agent 只拿到：

- `report.md`（唯一真源）
- `sources.tsv`（证据表）
- `report.html` / `report.pdf`（渲染产物）
- 一段角色 prompt

**不**给它：本会话的聊天记录、作者的调研摘要、作者的结论、其他审阅者的意见。这样它不会顺着作者的思路补全，而是被迫自己判断。

在 Claude Code 中：

- 用 `Agent`（或环境提供的 fresh subagent 工具）为每个 prompt 起一个 fresh subagent；
- prompt 原文传入，不要加“我们刚才查到……”；
- 每个 agent 独立读全文，不要抽查；
- 输出落到 `review/out/<key>.out.md`。

在 paseo 托管会话或不允许嵌套子代理的环境里，用 shell 版（每个 prompt 一个新鲜 `claude -p` 进程）：

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$HOME/.claude/skills/history-research}"
"$SKILL_DIR/scripts/run_reviews.sh" <report-dir>
```

它会对每个 `review/*.prompt.md` 起一个 `claude -p` 新会话，且只允许读文件和联网核验。

## 七个角度

先 `build` 一版报告再审阅：`review-kit` 需要 `report.html` 和 `report.pdf` 存在，否则会报错。交付前用 `check --strict --require-review` 验证 7 个审阅输出和覆盖矩阵都齐全。

`review-kit` 会生成以下 prompt：

1. **事实与来源核验**：日期、数字、人名、地名、引文；引文是否真的在来源页里；强主张是否只有弱来源。
2. **时间线与因果**：独立重建时间轴；找倒置、错代、无证据因果、被叙事抹平的中断。
3. **地方细节与反方证据**：以地方史/领域专家视角挑错；行政区划、地名词源、传说当信史、名人故居争议。
4. **图片、出处与许可**：图是否真的拍的是它；外部图的来源页是否是原始页面；作者/许可是否明确；分辨率是否够印刷；图注是否过度声称。用户提供的照片不强制外部 URL，但图注/出处必须写清拍摄者、时间、地点，并确认适合公开。
5. **结构与可读性**：开头、章节顺序、过渡、重复、术语、段落长度、结尾；建议删减而不只是补充。
6. **HTML/PDF 渲染与交付**：封面、目录、字体、分页、图是否被截断、表格溢出、链接、页脚页码、构建哈希。
7. **红队**：努力推翻报告；找“传说当历史”“单一来源过度使用”“第一/唯一/最早无一手来源”“规划当实施”“引文指向的页面没有这句话”。

## 严重度

- **P0**：事实错误、伪造/错配来源、图片无出处、HTML/PDF 损坏、构建不同步。
- **P1**：重要主张无来源、时间线错误、误导性框架、图片许可问题。
- **P2**：清晰度、风格、小错。

**P0/P1 必须修复并重跑对应审阅。** P2 可以记录后延，但不能全部忽略；至少处理影响理解的。

## 覆盖要求

`review/coverage.md` 是章节 × 角度的矩阵。每个 agent 都必须通读全文，并对每个章节给出“已检查/问题数”。交付前：

- 每个章节至少被 7 个角度各读一遍；
- 矩阵无空行；
- `review.md` 记录每轮审阅者、角度、结论、P0/P1 数量、未解决问题。

“所有内容都审过”不是一句口号，而是这张矩阵没有空格。

## 修复循环

1. 跑 7 个审阅，保存输出。
2. 合并去重，按 P0 → P1 → P2 排序。
3. 只改 `report.md`（不要手改 HTML/PDF）。
4. 重新 `build` 和 `review-kit`（重新绑定当前哈希），再跑受影响的审阅（P0 修复后建议 7 个全跑）。
5. 直到没有 P0/P1，且 coverage 矩阵填满。
6. 在 `review.md` 写最终结论；未解决的 P2 和“无法核实”的事项写进报告的“还没答案”。

## 增量影响面

每轮全跑 7 个角度很贵，尤其是局部改一句话。`review-kit` 会写版本快照：

```text
review/manifest.json
review/manifests/<review_id>.json
review/scopes.json
```

`review_id` 是 `md_sha256 + sources_sha256` 的复合哈希（只改证据表而正文不变时不会覆盖旧快照）。清单包含：整篇哈希、`sources_sha256`、frontmatter 哈希、每个 H2 章节的正文哈希（不含图片块）、每节的图片块哈希、构建产物哈希和图片哈希。

改完 `report.md` 或 `sources.tsv` 后：

```bash
python3 "$SKILL_DIR/scripts/history_research.py" build ./my-report/report.md
python3 "$SKILL_DIR/scripts/history_research.py" review-kit ./my-report/report.md
python3 "$SKILL_DIR/scripts/history_research.py" review-impact ./my-report/report.md --write
```

`review-kit` 必须重跑，否则 `REVIEW_ONLY` 用的 prompt 还绑着旧哈希。`review-impact` 输出 `current` / `impacted` / `not-impacted`：

- 文字角度（`01`、`02`、`03`、`05`、`07`）：默认任何章节正文变化都要重跑；
- `01-facts`、`02-chronology`、`03-context-contrarian`、`07-red-team`：`sources.tsv` 变化也要重跑；
- `04-images-licensing`：只有图片块（图片行+图源行）或构建后的图片哈希变化时才重跑；
- `06-render-qa`：只有 `report.md` 或构建产物变化时才重跑，纯 `sources.tsv` 变化可跳过。

默认范围是保守的。某个角度只关心特定章节时，在 `review/scopes.json` 里按标题子串收窄（`["*"]` 表示全部章节，也可只在文件里写要覆盖的角度）：

```json
{
  "03-context-contrarian": ["地方传说", "地名考"]
}
```

只跑受影响的：

```bash
REVIEW_ONLY=01-facts,05-structure-readability \
  "$SKILL_DIR/scripts/run_reviews.sh" ./my-report
```

**增量模式只用于迭代。** 最终交付仍要求 7 份审阅都绑定同一个整篇哈希；`check --require-review` 不会接受旧哈希的“局部豁免”。

## 审阅输出模板

```markdown
## Verdict: NEEDS WORK

| Severity | Section | Issue | Evidence / source | Required fix |
|---|---|---|---|---|
| P0 | 二、转折点 | 1966 年被写成“一期通水”，但一期是 1961 年，1966 年是二期 | [来源](https://...) | 改为“1966 年二期通水” |
| P1 | 三、今天的痕迹 | “温泉行宫”无志书依据 | 海淀史志未见此称 | 改为“温泉堂”，或标注为旅游叙事 |

## Coverage
- 问题的提出 — checked, 0 issues
- 时间轴 — checked, 1 issue
...

## Could not verify
- ...
```

每个输出还必须以机器可读 JSON 结尾（`review-kit` 的 prompt 已包含模板）：

```json
{
  "angle": "01-facts",
  "md_sha256": "构建时的 report.md SHA256",
  "sources_sha256": "构建时的 sources.tsv SHA256",
  "verdict": "PASS",
  "p0": 0,
  "p1": 0,
  "coverage": ["每个二级章节标题，逐字"]
}
```

`check --strict --require-review` 会校验：7 个输出齐全、`angle` 匹配、`md_sha256`
等于当前报告、P0/P1 都为 0（`verdict` 可为 PASS 或 NEEDS WORK；P2 记录后延）、每个章节都出现在 `coverage` 中。
报告改动后旧审阅会因哈希不匹配而失效，必须重跑。

## 常见漏审点

- 图片：只看了 Markdown 里的 URL，没有打开 HTML/PDF 看实际渲染。
- 引文：只看了来源标题，没有打开页面确认句子。
- 同名：没有确认“沙河”“温泉”“周家巷”到底指哪一个。
- 状态：没有区分规划、立项、开工、建成、运营。
- 行政区划：用今天的镇/村边界去套历史材料。
- 日期：把开工、竣工、通水、蓄水混为一谈。
- 传说：把“相传”“据说”改写成陈述句。
