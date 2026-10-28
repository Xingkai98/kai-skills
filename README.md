# kai-skills

可复用的 agent skills 集合。每个子目录是一个独立 skill，复制或软链到 `~/.claude/skills/` 即可使用。

## Skills

- [history-research](history-research/) — 深入调研任何东西的历史与来龙去脉：图文并茂、图片有原始出处，Markdown/HTML/PDF 三份同步，成稿前由无上下文 agent 从 7 个角度审阅。

## 安装

```bash
# SSH（本机 github.com:443 被封时可用）
git clone git@github.com:Xingkai98/kai-skills.git
# 或 HTTPS：git clone https://github.com/Xingkai98/kai-skills.git

mkdir -p ~/.claude/skills
ln -s "$PWD/kai-skills/<skill-name>" ~/.claude/skills/<skill-name>

# 某些 skill 有 Python 依赖，按该 skill 的 requirements.txt 安装，例如：
python3 -m venv ~/.venvs/history-research
~/.venvs/history-research/bin/pip install -r kai-skills/history-research/requirements.txt
```

安装或更新软链后重启 Claude Code（或重新加载会话），新 skill 才会出现在可用列表里。

## 编写约定

- 每个 skill 一个目录，至少包含 `SKILL.md`（frontmatter 里写 `name` 和 `description`）。
- 可选：`agents/openai.yaml`、脚本、模板、示例。
- **不放密钥、私有地址、个人环境配置。** 路径等环境相关信息用「环境变量 → 本地配置文件 → 自动探测」的方式解析，并附 `env.example.md` 模板。
- 脚本需带 shebang，并在仓库里保留可执行位。
- 改动尽量保持 skill 自包含、可跨环境复用。
