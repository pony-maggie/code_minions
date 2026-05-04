# code_minions

> AI 研发工作流引擎。开源、配置友好、skill 可插拔。

📚 **文档**：[快速上手](docs/quickstart.md) · [skills 指南](docs/skills.md) · [workflow 指南](docs/workflows.md)

🇬🇧 **English**：[README.md](README.md)

## 这是什么

`code_minions` 把 AI 辅助研发流程变成可重复执行的 CLI run。它按 YAML workflow
逐步执行，在 workflow 声明的 workspace mode 中工作，把状态保存到 SQLite，并支持
失败后 resume。改代码类 workflow 使用隔离 git worktree；轻量 smoke test 可以不依赖 git。

核心模型：

- **不绑定具体大模型**：LLM 调用统一走 LiteLLM；只要在 `devflow.yaml`
  里切换 provider/model，并导出对应 API key，同一套 workflow 就能跑在大多数主流模型上。
- **外部系统走 MCP**：Jira、GitHub 等产品通过 `.mcp.json` 接入。
- **skill 可装配**：每个 workflow step 都是一个 skill。skill 使用 Claude 风格的 `SKILL.md` frontmatter，也可以声明确定性的 `entrypoint-script`。
- **理解项目约定**：LLM skill prompt 会注入 `AGENTS.md`，让执行过程遵守当前 repo 的约定。

## 架构图

[![code_minions 分层架构图](docs/assets/architecture-zh.svg)](docs/assets/architecture-zh.svg)

## 安装

`code-minions` 还没有发布到 PyPI。当前需要从源码安装：

```bash
git clone https://github.com/malu/code-minions.git
cd code-minions
pip install -e .
```

要求 Python 3.11+。

## 两分钟烟雾测试

```bash
cd your-project
code-minions init .
code-minions run hello-world --input name=world
code-minions list-runs
code-minions status <run-id>
```

`hello-world` 不需要 LLM key、MCP server 或 git repo。它用来验证初始化、run 存储、
scratch workspace 创建和确定性 skill 执行是否正常。`prd-to-commit`、`prd-to-pr`
这类会修改代码的 workflow 仍然要求本地 git repo，且至少已有一个 commit。

PRD workflow 建议在 PRD 中写清 `Delivery Contract` / `delivery_profile`，
明确交付物类型、语言、构建系统、测试命令、必须出现的文件和禁止作为产品代码的语言。
模板和示例见 [PRD template](docs/prd-template.md)，里面包含 Swift macOS、Go service、
Python CLI、React app 示例。

## 模型 Provider

`code_minions` 的设计重点之一是不绑定某一家大模型。底层通过 LiteLLM 调用模型，
所以 OpenAI、Anthropic、Gemini、DeepSeek、MiniMax、Ollama，以及其他
LiteLLM 兼容 provider，都可以复用同一套 workflow engine。对常见云模型来说，
配置通常只需要三步：

1. 在 `devflow.yaml` 里加入 provider/model。
2. 导出该 provider 对应的 API key 环境变量。
3. 把 `llm.default` 切到你想使用的 provider。

示例：

```yaml
llm:
  default: minimax
  providers:
    openai:
      model: gpt-5.5
      api_key_env: OPENAI_API_KEY
    anthropic:
      model: claude-sonnet-4-6
      api_key_env: ANTHROPIC_API_KEY
    gemini:
      model: gemini-3.1-pro-preview
      api_key_env: GEMINI_API_KEY
    minimax:
      model: MiniMax-M2.7
      api_key_env: MINIMAX_API_KEY
      api_base: https://api.minimaxi.com/v1
```

LLM provider 细节、Jira/GitHub MCP 示例、完整 PRD-to-PR 前置条件，请看
[快速上手](docs/quickstart.md)。

## 内置 Workflows

| Workflow | 适合什么时候用 | 最小命令 |
|---|---|---|
| `hello-world` | 验证安装和 runtime 基础能力；不需要 AI 或外部服务。 | `code-minions run hello-world --input name=world` |
| `summarize-file` | 做一个小型 AI smoke test：确定性读取本地文件，再调用一次 LLM 输出摘要。 | `code-minions run summarize-file --input file=./README.md` |
| `prd-to-commit` | 跑 PRD -> 任务拆解 -> 实现 commit -> report，不接 Jira/GitHub。 | `code-minions run prd-to-commit --input prd=./my-prd.md` |
| `react-vite-prd-to-commit` | 跑同一条本地 commit 链路，但预先固定 React + TypeScript + Vite 规则。 | `code-minions run react-vite-prd-to-commit --input prd=./my-prd.md` |
| `swift-xcodegen-prd-to-commit` | 跑同一条本地 commit 链路，但预先固定 Swift + SwiftUI + XcodeGen 规则。 | `code-minions run swift-xcodegen-prd-to-commit --input prd=./my-prd.md` |
| `go-service-prd-to-commit` | 跑同一条本地 commit 链路，但预先固定 Go service 规则。 | `code-minions run go-service-prd-to-commit --input prd=./my-prd.md` |
| `python-cli-prd-to-commit` | 跑同一条本地 commit 链路，但预先固定 Python CLI 规则。 | `code-minions run python-cli-prd-to-commit --input prd=./my-prd.md` |
| `react-vite-prd-to-pr` | 跑完整 PR 链路，并预先固定 React + TypeScript + Vite 规则。 | `code-minions run react-vite-prd-to-pr --input prd=./my-prd.md --input project_key=ABC --input epic_title="Feature"` |
| `python-cli-prd-to-pr` | 跑完整 PR 链路，并预先固定 Python CLI 规则。 | `code-minions run python-cli-prd-to-pr --input prd=./my-prd.md --input project_key=ABC --input epic_title="Feature"` |
| `prd-to-pr` | 跑自定义栈或 PRD 已经写完整交付契约的完整 PR 链路。 | 见 [快速上手](docs/quickstart.md#run-a-workflow)。 |

`prd-to-commit` 是通用入口。它不会默认使用 React/Vite；如果 PRD 中有
`delivery_profile`，它会按该配置执行，否则会依赖栈推断。已知产品栈时，优先用
`react-vite-prd-to-commit`、`python-cli-prd-to-commit` 这类 stack-specific
workflow，让 harness 从一开始就固定交付约束。通用 workflow 也可以显式指定：

```bash
code-minions run prd-to-commit \
  --input prd=./my-prd.md \
  --input delivery_stack_id=python-cli
```

PRD-to-PR 也建议优先使用匹配项目的 stack-specific workflow。它们复用
`prd-to-pr` 的 Jira/GitHub 链路，但会在 parse 和 plan 之前固定技术栈：

```bash
code-minions run python-cli-prd-to-pr \
  --input prd=./my-prd.md \
  --input project_key=ABC \
  --input epic_title="Text count CLI"

code-minions run react-vite-prd-to-pr \
  --input prd=./my-prd.md \
  --input project_key=ABC \
  --input epic_title="Gomoku web app"
```

run 结束后：

```bash
code-minions status <run-id>
ls .devflow/runs/<run-id>/
code-minions resume <run-id>
```

对于会改代码的 run，实现 commit 在 `.devflow/runs/<run-id>/worktree`
里的 `code-minions/<run-id>` 分支上。验收 worktree 和 `report.md` 后，把分支
merge 回你的项目分支：

```bash
git switch main
git merge --no-ff code-minions/<run-id>
git worktree remove .devflow/runs/<run-id>/worktree
git branch -d code-minions/<run-id>
```

更多验收、冲突处理和清理说明见 [quickstart](docs/quickstart.md#land-worktree-results)。

`code-minions run` 会在当前 terminal 持续输出 step 进度。长 workflow 执行时，
也可以另开一个 terminal 用 `status` 或 `list-runs` 查看同一个 run。

workflow YAML 细节见 [workflow 指南](docs/workflows.md)，自定义 skill 见
[skills 指南](docs/skills.md)。

## Web Dashboard

```bash
code-minions web
```

打开 `http://127.0.0.1:8080/` 后，可以查看 run 列表、查看 step 状态、从表单发起
workflow，并接收从 Web 发起的 run 的 SSE 实时更新。

当前限制：

- 只面向本机使用，没有鉴权
- CLI 发起的 run 会显示在 Web UI，但不会跨进程实时推送
- Web `Cancel` 是 advisory，Web `Resume` 目前是同步请求

## 工作原理

1. `code-minions run [workflow]` 从配置的 `workflow.search_paths` 或内置 workflow 加载 YAML。
2. 如果省略 `[workflow]`，会使用 `devflow.yaml -> workflow.default`；命令行显式传入的 workflow 总是优先。
3. Engine 创建该 workflow 的 workspace：scratch 目录、项目根目录只读模式，或 `.devflow/runs/<run-id>/worktree` 分支。
4. DAGRunner 按依赖顺序执行每个 step。
5. 确定性 skill 执行声明的 `entrypoint-script`；LLM skill 读取 `SKILL.md` + `AGENTS.md` 并调用被允许的工具。
6. run 状态保存到 `.devflow/runs.db`，所以可以查看状态和 resume。

## 内置 Skills

`hello-world`、`summarize-file`、`parse-prd`、`plan-tasks`、
`create-jira-tickets`、`implement-with-tdd`、`ai-code-review`、
`compile-report`、`open-github-pr`。

本地查看：

```bash
code-minions skill list
code-minions skill info parse-prd
code-minions skill test
```

## License

MIT。
