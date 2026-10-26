# kai-skills

可复用的 agent skills 集合。每个子目录是一个独立 skill，复制或软链到 `~/.claude/skills/` 即可使用。

## Skills

_（持续添加中）_

## 安装

```bash
git clone git@github.com:Xingkai98/kai-skills.git
ln -s "$PWD/kai-skills/<skill-name>" ~/.claude/skills/<skill-name>
```

## 编写约定

- 每个 skill 一个目录，至少包含 `SKILL.md`（frontmatter 里写 `name` 和 `description`）。
- 可选：`agents/openai.yaml`、脚本、模板、示例。
- **不放密钥、私有地址、个人环境配置。** 路径等环境相关信息用「环境变量 → 本地配置文件 → 自动探测」的方式解析，并附 `env.example.md` 模板。
- 脚本需带 shebang，并在仓库里保留可执行位。
- 改动尽量保持 skill 自包含、可跨环境复用。
