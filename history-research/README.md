# history-research

深入调研任何东西的历史与来龙去脉，产出**图文并茂、来源可核、Markdown/HTML/PDF 三份同步**的长文。

## 它解决什么

普通“搜一下”会给你一堆互不相关的网页；历史写作真正难的是：

- 把时间线、地理、制度、人物和记忆串成一条可读的线索；
- 分清**信史、争议、地方传说**；
- 每张图都能回到原始页面，而不是一张无法追溯的截图；
- 成稿前让没有参与调研的 agent 从事实、时间线、地方细节、图片、结构、渲染、红队七个角度独立审阅；
- 保证 Markdown、HTML、PDF 三份内容一致，而不是三份各改各的。

## 输入

不是表单，是一份研究 brief；**文字、图片，或图文一起，至少给一种**：

- **一段描述**：“我想了解京密引水渠（沙河）流域的历史”“这个村名是怎么来的”。
- **1–N 张图**：路牌、公示牌、建筑、器物、旧照片、地图、展签、截图，可以完全不带文字。
- **图文一起**：图片给现场线索，文字给问题边界；多张图先分别提取候选实体，再合并。

有图时先做读图取证（OCR、地标/器物/年代/地点线索），再决定搜索角度。用户自己拍的照片在报告里标 `*图源：用户提供 · 拍摄者/时间/地点*`，不强制外部 URL；外部图片仍必须有原始页面链接。

## 安装

```bash
git clone git@github.com:Xingkai98/kai-skills.git
mkdir -p ~/.claude/skills
ln -s "$PWD/kai-skills/history-research" ~/.claude/skills/history-research

# 建议用 venv（Debian/Ubuntu/Homebrew 的 Python 常是 externally-managed）
python3 -m venv .venv
.venv/bin/pip install -r kai-skills/history-research/requirements.txt
.venv/bin/python -m playwright install --with-deps chromium
```

之后用 `.venv/bin/python` 运行 `scripts/history_research.py`；或把 venv 的 bin 加到 PATH。

## 快速开始

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$HOME/.claude/skills/history-research}"

# 报告建在你自己的项目里，不要建在 skill 仓库内部
python3 "$SKILL_DIR/scripts/history_research.py" init ./my-report --title "我的历史调研"
# 编辑 my-report/report.md 和 sources.tsv
python3 "$SKILL_DIR/scripts/history_research.py" build ./my-report/report.md        # 先出审阅版
python3 "$SKILL_DIR/scripts/history_research.py" review-kit ./my-report/report.md   # 7 个审阅 prompt
# 用 7 个 fresh agent 审阅；输出存到 my-report/review/out/<key>.out.md，末尾带 JSON
# 修复 P0/P1 后重跑
python3 "$SKILL_DIR/scripts/history_research.py" build ./my-report/report.md
python3 "$SKILL_DIR/scripts/history_research.py" check ./my-report/report.md --strict --require-review
```

产物：

```text
my-report/
├── report.md          # 唯一真源
├── sources.tsv        # 证据表
├── review.md          # 审阅记录
├── review/            # 7 个审阅 prompt + 覆盖矩阵
├── assets/img/        # 图片本地副本（保留原始链接）
├── report.html
├── report.pdf
└── report.build.json  # 哈希清单
```

## 三个关键机制

### 1. 图片必须有出处

```markdown
![图 1：京密引水渠团城湖段](https://example.com/canal.jpg)
*图源：[北京市水务局](https://example.com/article) · 摄影/许可*

![图 2：用户现场拍摄的公示牌](assets/sign.jpg)
*图源：用户提供 · 2026-10-28 摄于北京海淀温泉镇*
```

`build` 会下载外部图片到 `assets/img/`，在图注和文末“图片出处”表保留原始页面链接；缺少来源行、来源不是 http(s)、图片下载失败，都会让构建失败。用户自己拍的照片不强制 URL，但必须用 `用户提供/作者拍摄/自摄` 标注并写清拍摄者、时间、地点。

### 2. MD 是唯一真源

`report.html` 和 `report.pdf` 由 `report.md` 编译。`report.build.json` 记录三份文件的哈希；`check` 会告诉你“改了 MD 没重新 build”或“生成物被手改”。

### 3. 无上下文审阅

`review-kit` 生成 7 个 prompt：

1. 事实与来源核验
2. 时间线与因果
3. 地方细节与反方证据
4. 图片、出处与许可
5. 结构与可读性
6. HTML/PDF 渲染与交付
7. 红队：努力推翻报告

每个 prompt 起一个**全新 agent**，只给报告、证据表、HTML/PDF，不带本会话的调研过程。`review/coverage.md` 是章节 × 角度矩阵，填满才算审完。

## 依赖

- Python 3.10+
- `markdown-it-py`、`Jinja2`、`beautifulsoup4`、`requests`、`PyYAML`
- PDF：Playwright Chromium（推荐），或 Chrome/Chromium CLI，或 WeasyPrint
- 中文字体：`fonts-noto-cjk`（Linux）

## 冒烟测试

```bash
scripts/smoke_test.sh
```

会在临时目录里跑一遍 `build → check --strict → review-kit`，验证图片、HTML/PDF、哈希同步和审阅 prompt 生成。

## 设计文档

- [SKILL.md](SKILL.md) — 工作流与硬性规则
- [references/source-playbook.md](references/source-playbook.md) — 来源层级、搜索角度、证据表、地方史难点
- [references/writing-playbook.md](references/writing-playbook.md) — 结构、开头、时间轴、图片、语言
- [references/review-playbook.md](references/review-playbook.md) — 无上下文审阅协议
- [references/build-playbook.md](references/build-playbook.md) — 构建、同步、PDF 排错

## 示例

`examples/demo/` 是一个最小可构建示例，展示图片出处、时间轴、正文和 HTML/PDF 产物。
