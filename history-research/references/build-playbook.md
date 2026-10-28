# 构建与交付

`report.md` 是唯一真源。`report.html` 和 `report.pdf` 是编译产物；`report.build.json` 记录源文件与产物的哈希，`check` 用它判断三份是否同步。

## 目录结构

```text
my-report/
├── report.md            # 唯一真源；只改这里
├── sources.tsv          # 证据表：claim / source_url / source_title / quote / confidence / notes
├── review.md            # 审阅记录
├── review/              # 无上下文审阅 prompts 与输出
├── assets/img/          # 构建时下载/复制的图片（可提交，保证 HTML 可移植）
├── report.html          # 生成物
├── report.pdf           # 生成物
├── report.build.json    # 哈希清单
└── .build/              # 临时文件（gitignore）
```

## 命令

```bash
# skill 安装目录（Claude Code 会注入 CLAUDE_SKILL_DIR）
SKILL_DIR="${CLAUDE_SKILL_DIR:-$HOME/.claude/skills/history-research}"

# 1. 新建（报告目录放在用户项目里）
python3 "$SKILL_DIR/scripts/history_research.py" init ./my-report --title "标题" --subtitle "副标题"

# 2. 写 report.md / sources.tsv，先编译一版给审阅者看
python3 "$SKILL_DIR/scripts/history_research.py" build ./my-report/report.md

# 3. 生成 7 个无上下文审阅 prompt
python3 "$SKILL_DIR/scripts/history_research.py" review-kit ./my-report/report.md

# 4. 审阅、修复后重新编译
python3 "$SKILL_DIR/scripts/history_research.py" build ./my-report/report.md

# 5. 交付校验：图片出处、引用密度、md/html/pdf 同步、审阅覆盖
python3 "$SKILL_DIR/scripts/history_research.py" check ./my-report/report.md --strict --require-review

# 可选：联网检查来源 URL 是否可访问
python3 "$SKILL_DIR/scripts/history_research.py" check ./my-report/report.md --strict --online
```

## 图片规则

Markdown 里每张图必须是独立一行，下一行是来源行：

```markdown
![图 1：京密引水渠团城湖段](https://example.com/canal.jpg)
*图源：[北京市水务局](https://example.com/article) · 摄影/许可*

![图 2：用户现场拍摄的公示牌](assets/sign.jpg)
*图源：用户提供 · 2026-10-28 摄于北京海淀温泉镇*
```

- `build` 会下载远程图片到 `assets/img/`，用 URL 哈希命名；HTML/PDF 用本地副本，图注和文末“图片出处”保留原始链接。
- 图片 URL 含括号时，用尖括号包住：`![说明](<https://.../x_(1).png>)`；其他写法会被判为“未管理图片”并让构建失败。
- 外部图片没有来源行、来源行没有 http(s) 链接、下载失败（非草稿模式）、返回内容不是图片，都会让 `build` 失败。
- 用户提供的现场照片可以没有 URL，但必须以 `用户提供 / 作者拍摄 / 自摄 / 作者自绘` 开头，并写清拍摄者/时间/地点；`check` 会校验这个标注。
- 草稿可用 `--allow-missing-images` 生成占位图；`check` 会把占位图判为错误，最终交付不能带占位图。
- 本地图片也必须有来源行；图片路径相对于 `report.md`。
- 建议提交 `assets/img/`，这样别人 clone 后 HTML 仍然有图。若报告不公开，可在 `.gitignore` 里忽略图片，但 PDF 仍然自带图。

## 同步语义

`report.build.json` 记录：

- `md_sha256`：构建时的 Markdown 哈希；
- `html_sha256` / `pdf_sha256`：生成物哈希；
- 每张图的原始 URL、来源页、本地路径、图片哈希；
- `built_at`：构建时间（仅记录，不参与同步判断）。

`check` 会：

1. 重新计算 `report.md` 的哈希；与 manifest 不一致 → 报“改了 MD 没重新 build”。
2. 重新计算 HTML/PDF 哈希；与 manifest 不一致 → 报“生成物被手改或过期”。
3. 检查每个 figure 的 `data-source-url`、本地图片、文末出处表。
4. 检查每个二级章节是否有内链来源（短章节和参考资料除外）。
5. 检查 `sources.tsv` 的 URL 格式；`--online` 时再检查可达性。

**不要手改 HTML/PDF。** 任何修改都回到 `report.md`，重新 build。

## 审阅闸门

`check --require-review` 是交付前的第二道闸门（第一道是 `--strict` 的内容/同步检查）：

- `review/` 里必须有 7 个 `*.prompt.md`，`review/out/` 里必须有对应的 7 个非空输出；
- 每个输出末尾的 JSON 必须绑定当前 `report.md` 和 `sources.tsv` 的哈希，`verdict=PASS` 且 P0/P1 为 0，`coverage` 包含所有章节；
- `review/coverage.md` 必须包含当前报告的所有二级章节；
- 覆盖矩阵里不能残留 `☐`，至少要有一个 `☑`；
- `review.md` 至少 7 条填写的审阅记录，每条有角度。

草稿可以只用 `check --strict`；正式交付用 `check --strict --require-review`。

## PDF 引擎

默认 `auto`，依次尝试：

1. **Playwright Chromium**（推荐；中文、图片、页脚页码都稳定）
2. **Chrome/Chromium CLI** `--headless --print-to-pdf`
3. **WeasyPrint**

安装 Playwright：

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

指定引擎：

```bash
python3 "$SKILL_DIR/scripts/history_research.py" build report.md --pdf-engine playwright
```

## 中文与排版

- CSS 使用 `Noto Sans CJK SC`，回退到 Source Han Sans / PingFang / Microsoft YaHei。
- 本机若没有中文字体，Debian/Ubuntu 可装 `fonts-noto-cjk`。
- PDF 页脚包含标题、页码、源文件哈希；封面和目录各占一页。
- 图片和表格设置 `page-break-inside: avoid`，避免跨页截断。
- 需要改版式时，改 `templates/style.css`，不要改生成后的 HTML。

## 常见问题

**图下载失败**
- 站点可能需要 Referer/UA；脚本已带 UA，但仍可能被防盗链拦截。换原始页面/机构图库的直链。
- 返回 `text/html` 说明拿到的不是图片；打开 URL 确认。
- 大图超过 30 MB 会被拒绝；先压缩或换图。

**PDF 没有图**
- 检查 `assets/img/` 是否存在、HTML 里的相对路径是否正确。
- 用 `file://` 打开 HTML 看图片是否显示；如果不显示，是图片路径问题，不是 PDF 引擎问题。
- Playwright 使用 `wait_until="load"` 并等待字体加载；外部请求会被拦截，图片应全部在本地 `assets/img/`。

**PDF 中文变方框**
- 安装 `fonts-noto-cjk`；或改 CSS 字体为系统已有字体。
- Chromium 的页脚模板不继承正文字体，页脚已用 `sans-serif`。

**check 说不同步**
- 正常：改了 `report.md` 或手改了 HTML/PDF。重新 `build`。
- 如果刚 build 完仍报错，检查是否有多个 `report.md` 路径/软链，或构建时用了不同目录。

**sources.tsv 的 URL 403**
- 有些机构站会拦自动化请求；`--online` 只是辅助，403 记为 warning。人工打开确认。

## Git 提交建议

提交：

- `report.md`、`sources.tsv`、`review.md`、`review/`
- `assets/img/`（公开报告）
- `report.html`、`report.pdf`、`report.build.json`（需要交付时）

不提交：

- `.build/`（临时）
- 含隐私/未公开联系人的材料
- 未经授权的图片（只保留链接，或换有许可的图）
