# Maintainer Acceptance Guide

按"干净程度"从上到下做，每层过了再做下一层。全部跑完约 15-20
分钟；L3 需要真实 LLM token。

## L0. Repository gate

```bash
pytest
ruff check .
git diff --check
```

通过标准：

- `pytest` 全绿，覆盖率满足 `pyproject.toml` 里的 `--cov-fail-under=70`。
- `ruff check .` 输出 `All checks passed!`。
- `git diff --check` 无 trailing whitespace / conflict marker 输出。

## L0.5. Runtime boundary gate

`code_minions` 本体只能沉淀通用 workflow / stack / language / framework
规则。PRD 或目标产品特有的修复应该进入目标项目 worktree、项目测试或
`.devflow/memory.md`，不能写进 shipped runtime。

改 shipped runtime 前先写清楚一条非业务的 Root Cause Class，例如
`React hook mutators can miss terminal-state guards after status transitions`。
如果只能用目标产品词汇才能解释问题，例如棋子颜色、具体游戏规则、某个
PRD 的坐标序列或业务文案，这个修复不能进入 builtin runtime。

通用性证明必须落在系统层面，而不是找另一个相似 PRD。合格类别包括：
语言语义、框架生命周期、测试框架约定、stack-pack 配置、可恢复执行和
交付报告契约。不合格类别包括：某个游戏规则、某个业务状态机、某个页面
文案、某个 PRD 的测试 fixture。

新增 stabilizer / runtime finding / prompt guidance 的测试名也必须使用系统
类别命名，不能用产品领域命名。业务逻辑测试不合法时，优先让 LLM 自修或
把信息写入项目本地 memory；只有跨项目重复出现的系统缺陷才沉淀为 runtime
规则。

```bash
pytest tests/unit/test_builtin_skill_policies.py -q
```

通过标准：

- `test_builtin_skill_policies.py` 全绿。
- 该测试维护 forbidden marker 列表，并扫描 shipped runtime，防止项目域规则
  回流到 builtin code。

## L1. 安装层 · 2 分钟

**目的**：证明"别人 pip 装一下就能用"。

```bash
python -m venv /tmp/cm-fresh
source /tmp/cm-fresh/bin/activate
cd /path/to/code_minions
pip install -e .
code-minions --help
deactivate
```

**通过标准**：`--help` 列出 CLI 命令，包括 `init`、`run`、`status`、
`list-runs`、`resume`、`cancel`、`web`。

## L2. README quickstart 层 · 5 分钟（不花 token）

**目的**：证明 README 里的"5 分钟 quickstart"不是吹牛。只用纯确定性 skill，不碰 LLM/MCP。

```bash
source /tmp/cm-fresh/bin/activate
cd /tmp && rm -rf l2-test && mkdir l2-test && cd l2-test
git init -q -b main
git commit --allow-empty -m init -q
code-minions init .
ls                                                   # 应看到 devflow.yaml / AGENTS.md / .mcp.json / .devflow/
code-minions run hello-world --input name=验收
code-minions list-runs                               # 表格里有一行 success
cat .devflow/runs/r_*/workspace/greeting.txt         # 应该是 "hello, 验收!"
```

**通过标准**：5 个命令全通、产物文件内容正确。

## L3. 真 LLM + 内置本地工具层 · 10 分钟

**目的**：验证 AI 链路真能跑。需要任意 LiteLLM 支持的 provider，例如
Anthropic、OpenAI、Gemini、MiniMax 或 Ollama。文件读取通过内置 `Read`
工具完成，不需要 filesystem/shell MCP。

```bash
cd /tmp/l2-test                                      # 复用上面的目录

# 配置 devflow.yaml 里的 llm.provider / llm.model，或先导出对应 provider key。
# 例如：
# export ANTHROPIC_API_KEY=sk-ant-...
# export GEMINI_API_KEY=...
# export MINIMAX_API_KEY=...

# 准备一个小文件
echo "This is a calculator library. It has add(a,b) and subtract(a,b) functions." > target.txt
git add . && git commit -m seed -q

# 跑 summarize-file
code-minions run summarize-file --input file=target.txt

# 查看结果（找最近一个 run id 替换下面的 <id>）
code-minions list-runs
code-minions status <id>
```

**通过标准**：
- `status` 为 `success`
- 步骤 `summ` 的 `output_json.summary` 是一段有意义的英文摘要（不是 null、不是报错）

## L4. `prd-to-pr` 完整流（可选）· 20 分钟+

**目的**：验证端到端的 PRD→reviewed 分支流程。

**不推荐专门为了验收做**，因为需要：
- LLM key
- **真 Jira 实例 + jira MCP**
- **GitHub MCP**

建议留给你有真 PRD 和真 Jira 项目时再做。CI 和 unit/integration tests
已经覆盖 workflow 结构、skill 可加载、deterministic gates、report evidence
和基础 E2E smoke。

## L5. Web UI PRD evidence（按需）

如果改动影响 Web UI PRD workflow、browser evidence、dashboard 或 run
详情页，再补跑：

```bash
open docs/maintainers/phase-c-acceptance.md
```

按文档里的 A-F 人工脚本走完。普通 engine/gate/failure-playbook 改动不强制跑
Web dashboard 人工验收。

## 通过后怎么办

- 有信心开源 → 按 [docs/maintainers/release.md](release.md) 走发布流程。
- 发现 bug → 开 GitHub issue，附 `code-minions status <id>` 输出 + `.devflow/runs/<id>/logs/`（**注意先删掉 key**）。
- 想加新 skill → 看 [docs/skills.md](skills.md)。
