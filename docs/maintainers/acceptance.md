# 验收测试指引

按"干净程度"从上到下做，每层过了再做下一层。全部跑完约 15-20 分钟（L3 需要 LLM token，花费 < $0.01）。

## L0. 代码层（CI 已覆盖）

```bash
pytest -q                                         # 71 passed
pytest tests/unit -q --cov --cov-fail-under=70    # 覆盖率 ≥ 70%
ruff check src tests                              # All checks passed
```

## L1. 安装层 · 2 分钟

**目的**：证明"别人 pip 装一下就能用"。

```bash
python -m venv /tmp/cm-fresh
source /tmp/cm-fresh/bin/activate
cd /Users/lucasma/Documents/code_minions
pip install -e .
code-minions --help
deactivate
```

**通过标准**：`--help` 列出 7 个命令（`init` / `run` / `status` / `list-runs` / `resume` / `cancel`）。

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

## L3. 真 LLM + 内置本地工具层 · 10 分钟（烧 < $0.01）

**目的**：验证 AI 链路真能跑。需要 `ANTHROPIC_API_KEY`（或任意 LiteLLM 支持的 provider）。文件读取通过内置 `Read` 工具完成，不需要额外 MCP。

```bash
export ANTHROPIC_API_KEY=sk-ant-...                  # 你的真 key
cd /tmp/l2-test                                      # 复用上面的目录

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

建议留给你有真 PRD 和真 Jira 项目时再做。M4 Task 8 已经在 CI 里跑了分层 smoke（workflow 结构 + skill 可加载 + parse-prd 首步真 E2E），结构性正确性已经被保证。

## 通过后怎么办

- 有信心开源 → 按 [docs/maintainers/release.md](release.md) 走发布流程。
- 发现 bug → 开 GitHub issue，附 `code-minions status <id>` 输出 + `.devflow/runs/<id>/logs/`（**注意先删掉 key**）。
- 想加新 skill → 看 [docs/skills.md](skills.md)。
