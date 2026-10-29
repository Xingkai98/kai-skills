---
name: history-research
description: 深入调研任何人物、地点、事件、机构、技术、物件、地名或观念的历史与来龙去脉，产出图文并茂、来源可核、Markdown/HTML/PDF 三份同步的长文。用于用户要“调研/考据/梳理……的历史、沿革、发展史、起源、为什么是现在这样”，或要求老照片、地方志、时间线、图文报告、PDF 交付时。不用于一次性新闻/事实查询、产品对比、代码库或 API 调研，以及不需要长文和出处的任务。
---

# History Research

把“它为什么会是现在这样”做成一份可发布的历史长文：**先证据后叙事**，每张图有原始出处，`report.md` 是唯一真源，HTML/PDF 由它编译，成稿前由无上下文 agent 从多个角度审阅。

## 产出

```text
<report-dir>/
├── report.md          # 唯一真源
├── sources.tsv        # 证据表（claim / url / quote / confidence）
├── review.md          # 审阅记录
├── review/            # 7 个无上下文审阅 prompt + 输出 + 覆盖矩阵 + scopes.json + impact.md
├── review/manifests/  # 每次 review-kit 的版本快照（md+sources 复合 id）
├── assets/img/        # 构建时下载的图片（保留本地副本）
├── report.html        # 由 report.md 生成
├── report.pdf         # 由 report.md 生成
└── report.build.json  # md/html/pdf 哈希，check 用它验同步
```

## 输入

输入是一份研究 brief，不是表单。**文字、图片，或图文一起都可以；至少给其中一种。**

- **一段描述**：“我想了解京密引水渠（沙河）流域的历史”“这个村名是怎么来的”“这家机构为什么在这个时间成立”。
- **1–N 张图**：路牌、公示牌、建筑、器物、旧照片、地图、展签、截图。可以完全不带文字。
- **图文一起**：图片给现场线索，文字给问题边界；这是最常用的形式。
- **多张图**：分别提取，不要假设它们一定属于同一地点/年代；先列出每张图的候选实体，再合并。

有图时，先做**读图取证**：OCR 可见文字，辨认地标/建筑/器物/年代/地点线索，列出候选实体（人名、地名、机构、工程名、年代）。有歧义就明确写出假设再搜，不要把猜测当结论。图片可以只作为检索线索，也可以作为报告里的证据；用户自己拍的照片用“用户提供/作者拍摄/自摄”标注，不强制外部 URL。

## 硬性规则

1. **每个事实主张有来源**。日期、数字、人名、地名、引文都要能点回原始页面；强主张至少一手或两份独立二手。证据强弱用 `high / medium / low` 标，正文把“信史 / 争议 / 地方传说”分开写。
2. **图文并茂**。标准长文至少 3 张有出处的图。外部图片每张图下一行（或隔一个空行）必须是 `*图源：[原始页面](https://...) · 作者/许可*`；用户提供的照片写 `*图源：用户提供 · 拍摄者/时间/地点*`。没有来源行的图会让构建失败。
3. **三份同步**。只改 `report.md`；`build` 生成 HTML/PDF；`check` 用哈希验证三份一致。不要手改生成物。
4. **全文无上下文审阅**。每个章节都要被 7 个角度各读一遍；P0/P1 修复后重跑；`review/coverage.md` 填满才算完成。
5. **状态说清楚**。规划、立项、开工、建成、通水、运营是不同状态；“拟论证”不能写成“已实施”。

## 工作流

下面的命令用 `$SKILL_DIR` 指 skill 安装目录。Claude Code 会注入 `CLAUDE_SKILL_DIR`；在普通 shell 里用默认值即可：

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$HOME/.claude/skills/history-research}"
```

报告目录放在**用户自己的项目里**（例如 `./research/<topic>`），不要建在 skill 仓库内部。

### 0. 初始化

```bash
python3 "$SKILL_DIR/scripts/history_research.py" init ./research/<topic> --title "标题" --subtitle "副标题"
```

完成标准：目录里有 `report.md`、`sources.tsv`、`review.md`、`assets/img/`。

### 1. 界定问题

在 `report.md` 开头写清：研究对象、时间范围、地理范围、读者、核心问题（“为什么是现在这样”）。把“不打算回答什么”也写一句，防止跑偏。

完成标准：一个陌生人读完开头 150 字，知道这份报告要回答什么、为什么值得读。

### 2. 建证据表

先读 [来源与证据](references/source-playbook.md)。至少从六个角度分头搜：正史/志书、学术、官方档案、地方媒体/口述、旧地图/空间、反方/同名。把每条可证伪的主张写进 `sources.tsv`，附原文摘录和置信度。

完成标准：核心问题至少有 3 条一手或高质量二手来源；记录至少 1 条反证/矛盾；同名、行政区划、日期口径都已确认。

### 3. 写初稿

读 [写作手册](references/writing-playbook.md)。先写时间轴，再写“最早的起点 → 关键转折 → 今天的痕迹”，最后写“信史、争议与传说”。图片优先用旧地图、档案、文物/现场照片；每张图都要有原始页面链接。

完成标准：每个二级章节至少一个来源链接；至少 3 张有出处图片；时间轴节点与正文一致；没有把传说写成陈述句。

### 4. 先编译审阅版

```bash
python3 "$SKILL_DIR/scripts/history_research.py" build ./<report-dir>/report.md
```

审阅者需要看到真实的 HTML/PDF（图片、分页、链接、页脚），所以先 build 一版。此时 `check --strict` 可能还有警告，没关系；这是给审阅用的草稿。

完成标准：`report.html` 和 `report.pdf` 存在，图片能打开。

### 5. 生成审阅任务并审阅

```bash
python3 "$SKILL_DIR/scripts/history_research.py" review-kit ./<report-dir>/report.md
```

这会生成 `review/01-facts.prompt.md` 到 `review/07-red-team.prompt.md`，以及 `review/coverage.md`。读 [无上下文审阅](references/review-playbook.md)，按其中的协议起 **fresh agent**：

- 每个 prompt 一个全新 agent，只给 prompt 里列出的文件；
- 不带本会话的调研过程、结论或摘要；
- 每个 agent 通读全文，不做抽查；
- 输出存到 `review/out/<key>.out.md`。

在 Claude Code 里可以用 `Agent` 工具为每个 prompt 起一个 fresh subagent；在 paseo 托管、或任何不允许嵌套子代理的环境里，直接用 shell 版（每个 prompt 一个新鲜 `claude -p` 进程）：

```bash
"$SKILL_DIR/scripts/run_reviews.sh" ./<report-dir>
```

完成标准：7 个角度都有输出；`review/coverage.md` 每个章节都勾选；P0/P1 全部修复并重跑；`review.md` 记录轮次和未解决问题。

### 6. 修复、增量重跑、交付

修复只改 `report.md` 或 `sources.tsv`。先重新编译、重新绑定审阅版本，再看影响哪些审阅角度：

```bash
python3 "$SKILL_DIR/scripts/history_research.py" build ./<report-dir>/report.md
python3 "$SKILL_DIR/scripts/history_research.py" review-kit ./<report-dir>/report.md
python3 "$SKILL_DIR/scripts/history_research.py" review-impact ./<report-dir>/report.md --write
```

`review-kit` 必须重跑：它把 7 个 prompt 和清单重新绑定到当前 `report.md` + `sources.tsv`，旧 prompt 里的哈希会让重跑结果过不了闸门。

`review-impact` 按 H2 章节正文、章节顺序、图片块、frontmatter、`sources.tsv` 和构建产物的哈希，输出每个角度是 `current`、`impacted` 还是 `not-impacted`，并给出建议的 `REVIEW_ONLY=...`。默认所有文字角度都依赖全部章节；只有图片、来源或构建产物单独变化时才能跳过一部分角度。某个角度只关心特定章节时，在 `review/scopes.json` 里按标题子串收窄，例如：

```json
{"03-context-contrarian": ["地方传说", "地名考"]}
```

只重跑受影响的角度：

```bash
REVIEW_ONLY=01-facts,05-structure-readability ... \
  "$SKILL_DIR/scripts/run_reviews.sh" ./<report-dir>
```

增量模式只用于迭代；**最终交付仍要求 7 份审阅都绑定同一个整篇 `report.md` 哈希**，所以最后还要对最终版本跑一次完整 7 角度，再执行：

```bash
python3 "$SKILL_DIR/scripts/history_research.py" build ./<report-dir>/report.md
python3 "$SKILL_DIR/scripts/history_research.py" check ./<report-dir>/report.md --strict --require-review
```

`check` 验证：

- 每张图有 http(s) 来源、本地副本、文末出处；
- 每个二级章节有内链来源；
- `report.md`、`report.html`、`report.pdf` 的哈希一致（改过就重建）；
- PDF 不是空文件/过小；
- `--require-review`：7 个审阅输出都在、每个输出末尾的 JSON 与当前 `report.md`/`sources.tsv` 哈希一致、P0/P1 为 0（`verdict` 可为 PASS 或 NEEDS WORK；P2 记录后延）、`coverage` 覆盖当前所有章节；覆盖矩阵每个章节都勾选，`review.md` 至少 7 条审阅记录。

完成标准：`check --strict --require-review` 输出 `OK`；打开 HTML 和 PDF 目检封面、目录、中文、图片、分页、链接、页脚页码。PDF 引擎和排版问题见 [构建与交付](references/build-playbook.md)。

交付时给出：

- 三份产物路径：`report.md` / `report.html` / `report.pdf`；
- 3–5 条最重要的发现；
- 证据最弱、争议最大的 2–3 处，明确说“还没答案”；
- 图片来源与许可注意事项。

## Markdown 约定

````markdown
![图 1：具体说明](https://example.com/photo.jpg)
*图源：[原始页面标题](https://example.com/article) · 作者/许可*

![图 2：用户现场拍摄的公示牌](assets/sign.jpg)
*图源：用户提供 · 2026-10-28 摄于北京海淀温泉镇*

## 一、章节标题

正文……[来源](https://example.com/source)。
````

- 图片必须独立成行，来源行紧随其后（允许一个空行）。
- 图片 URL 含括号时用尖括号包住：`![说明](<https://.../x_(1).png>)`；未按管理格式写的图片会让构建失败。
- 来源行必须包含至少一个 `http(s)://` 链接；文末会自动生成“图片出处”表。
- 正文引用用内链；文末 `## 参考资料` 列编号来源。
- 二级标题用于目录和审阅覆盖矩阵，请写得具体。

## 参考

- [来源与证据](references/source-playbook.md) — 来源层级、搜索角度、证据表、地方史难点、图片许可。
- [写作手册](references/writing-playbook.md) — 结构、开头、时间轴、图片密度、语言、交付自检。
- [无上下文审阅](references/review-playbook.md) — 7 个角度、严重度、覆盖矩阵、修复循环。
- [构建与交付](references/build-playbook.md) — 命令、同步语义、PDF 引擎、中文排版、排错。

## 常见坑

- **同名**：北京有两个“沙河”；“温泉”可能是村、镇、泉眼或旅游叙事。先确认是哪一个。
- **行政区划**：用今天的镇/村边界套历史材料，最容易错；查沿革表。
- **层累**：“行宫/故居/故道”类说法先查志书和文物名录，再查学术辨伪，最后才用旅游文案。
- **状态**：规划稿、征求意见稿、已批复、已开工、已建成，不能混写。
- **图片**：不要用搜索结果缩略图、Pinterest、百科缩略图当原始出处；回到机构页/档案页。
- **PDF**：中文变方框通常是缺 `fonts-noto-cjk`；图不显示通常是本地路径问题。

## 依赖

Python 3.10+。建议用 venv（Debian/Ubuntu/Homebrew 的 Python 常是 externally-managed）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium   # PDF 推荐引擎
```

之后用 `.venv/bin/python` 运行脚本。

没有 Playwright 时会尝试 Chrome/Chromium CLI 或 WeasyPrint；三者都没有则 `build` 报错并给出安装提示。
