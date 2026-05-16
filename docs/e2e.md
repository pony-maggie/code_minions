# E2E 测试与 Web UI 验收机制

本文整理 code_minions 当前项目里的 e2e 测试做法，特别说明带 Web UI 页面交互的 PRD 验收是如何覆盖的、哪些环节是自动化、哪些环节仍然需要人工浏览器验收。

配套架构图见 [docs/e2e.html](e2e.html)。

## 总体原则

code_minions 的 e2e 不是单一的大型浏览器测试套件，而是分层验证：

1. **仓库自身功能 e2e**：用 pytest 直接驱动 `Engine`、内置 workflow、fake LLM、真实 SQLite/worktree，验证 CLI/engine/skill/runtime 的端到端路径。
2. **Web dashboard 路由与 SSE 自动测试**：用 FastAPI `TestClient` 和 ASGI 级测试覆盖页面、表单、控制按钮、SSE 事件流。
3. **可选真实浏览器 smoke**：用 Playwright 启动真 Chromium，跑一条最高价值的 dashboard 路径。
4. **维护者人工 Web UI 验收**：按 `docs/maintainers/phase-c-acceptance.md` 在真实浏览器里检查 list/detail/new/cancel/resume/orphan scanner。
5. **PRD 产品验收 gate**：`product-acceptance-review` 在实现完成后做确定性产品验收，检查 delivery profile、语言/构建系统/必需文件、任务覆盖和测试证据，并输出 `acceptance_items` 与 `verifier_rounds`。

这种设计的核心取舍是：CI 保持稳定、快速、可重复；真实浏览器只覆盖少量关键路径或由维护者手工执行。对生成出来的 Web 产品，code_minions 当前不会自动打开任意 PRD 产物做全量浏览器视觉验收。

## 当前测试层级

### L0: 普通单元与集成测试

默认验证命令：

```bash
pytest
ruff check .
```

仓库的 verification gate 要求完整 pytest、ruff 全绿，覆盖率门槛在 `pyproject.toml` 里配置为 70%。

这层覆盖：

- workflow YAML 加载和 step 结构。
- Engine start/resume/cancel、DAG runner、RunStore、worktree。
- skill runtime、LLM fake backend、local tools。
- delivery profile 和 runtime gates。
- Web route HTML、SSE、run controls、新建 run 表单。

### L1: Engine 级 e2e

位置：

- `tests/integration/test_hello_world_e2e.py`
- `tests/integration/test_summarize_file_e2e.py`
- `tests/integration/test_prd_to_pr_e2e.py`

特点：

- 不启动浏览器。
- 直接构造 `Engine`，加载内置 skills/workflows。
- 使用临时 git repo 或普通目录作为 project root。
- 对 LLM 路径使用 scripted `FakeLLM`，避免 CI 依赖真实 token。
- 验证真实 `.devflow/runs/<run-id>/workspace` 或 worktree 产物。

`prd-to-pr` 的完整真实 e2e 没有在 CI 里跑到底，因为它需要多轮 LLM、Coder/Reviewer loop、测试子进程、Jira MCP、GitHub MCP。当前做法是分层 smoke：

- workflow YAML 能加载。
- workflow 引用的 skills 都能被 Engine 找到。
- `parse-prd` 首步用 fake LLM 和内置 `Read` 工具真实跑通。

### L2: Web dashboard route-level 测试

位置：

- `tests/unit/test_web_routes.py`
- `tests/unit/test_web_start.py`
- `tests/unit/test_web_controls.py`
- `tests/unit/test_web_sse.py`
- `tests/unit/test_orphan_scanner.py`

这层不启动 uvicorn，也不启动浏览器。它用 FastAPI `TestClient` 或直接驱动 ASGI app 来验证：

- `/` 空列表和已有 run 列表。
- `/runs/<id>` detail 页面、steps、gate findings。
- `/new` workflow 下拉。
- `/new/inputs` HTMX fragment 和本地文件 datalist。
- `POST /new` 创建 run 并 303 跳转。
- `POST /runs/<id>/cancel` 和 `/resume`。
- `/runs/<id>/events` SSE endpoint 的 content type、late subscriber 初始快照、terminal `run.finished`。
- web restart 后 orphan scanner 对 stuck running run 的处理。

SSE 测试里有一个关键细节：`TestClient` 对无限流会缓冲响应，所以测试直接驱动 ASGI `scope/receive/send`，拿到 response headers 或初始 body 后主动断开，避免死锁。

### L3: 可选 Playwright browser smoke

位置：`tests/browser/test_web_smoke.py`

运行方式：

```bash
pip install -e '.[dev,web-e2e]'
python -m playwright install chromium
CODE_MINIONS_BROWSER_E2E=1 pytest tests/browser -q
```

默认不设置 `CODE_MINIONS_BROWSER_E2E=1` 时，测试会被 skip。

这条 smoke 做的事：

1. 在临时目录 `git init` 并 `code-minions init .`。
2. 找一个空闲端口，用 `python -m code_minions.cli.main web --port <port>` 启动真实 Web server。
3. 用 Playwright Chromium 打开 dashboard。
4. 验证空列表显示 `No runs yet`。
5. 点击 `+ New Run`。
6. 选择 `hello-world`。
7. 填写 `name=browser`。
8. 点击 `Start Run`。
9. 等待 run status 和 `greet` step 都变成 `success`。
10. 断言 URL 进入 `/runs/r_...`。

失败时会在 pytest 临时目录写一张 full-page screenshot，辅助定位页面状态。

它的定位很薄：只证明真实浏览器、HTMX 表单、BackgroundTasks、SSE detail 更新这条最高价值路径没有断。它不做截图基线、不做视觉回归、不覆盖 cancel/resume/orphan scanner。

## Web UI PRD 验收怎么做

这里要区分两类 Web UI。

### code_minions 自己的 Web dashboard

dashboard 是本仓库的 Web UI，验收文档是 `docs/maintainers/phase-c-acceptance.md`。它是维护者人工验收脚本，覆盖范围比 Playwright smoke 更完整：

- A. 启动 `code-minions web` 并加载空列表。
- B. CLI 启动 run 后，刷新 Web 列表和 detail 验证状态。
- C. Web 页面启动 run，检查 SSE 实时从 `running` 变 `success`。
- D. 手工把 DB 状态改成 `running` 后验证 Cancel。
- E. 手工把 DB 状态改成 `failed` 后验证 Resume。
- F. `kill -9` Web 进程后重启，验证 orphan scanner。

这部分验收使用真实浏览器，因为它检查的是用户能看到和操作的 dashboard 行为，包括 HTMX fragment、SSE EventStream、按钮可见性、重定向和状态徽章。

CLI 启动的 run 有一个已知边界：实时 SSE 事件在 CLI 进程内的 EventBus，不跨进程传给 Web，所以 dashboard 需要刷新后才能看到 CLI run 的新状态。

### PRD 生成出来的 Web 产品

对于 `react-vite-prd-to-commit`、`python-web-prd-to-commit` 这类 PRD 工作流，当前验收链路是：

1. `parse-prd` 解析 PRD，得到功能、约束和 delivery profile。
2. `plan-tasks` 或 stack-specific planner 拆任务。
3. `implement-with-tdd` 对每个 task 让实现者写代码和测试，并运行该 stack 的 test command。
4. delivery gates 在实现前后检查项目形态和常见失败模式。
5. `product-acceptance-review` 扫描最终 worktree，对 PRD/delivery profile/任务覆盖做确定性验收。
6. `compile-report` 把实现结果、blockers、warnings、`acceptance_items`、`verifier_rounds` 写入报告。

这条链路不是通用浏览器自动验收器。它依赖目标 stack 自己的测试来覆盖交互：

- React/Vite：通常是 Vitest + Testing Library + `userEvent`，适合验证按钮点击、表单输入、状态文本、ARIA label、核心交互流程。
- Python web/FastAPI：通常是 pytest + FastAPI `TestClient`，适合验证路由、表单提交、模板渲染、OpenAPI、HTTP 响应。

delivery gates 会补充一些静态和运行时质量检查，例如：

- React/Vite 是否有 `package.json`、`vite.config.ts`、测试文件、可解析 imports。
- TypeScript/Vitest 失败是否属于已知 contract drift。
- React board game 的 accessible state、click handler、测试 fixture 是否明显错误。
- Python web 是否有 `pyproject.toml`、src-layout、`pytest` pythonpath、FastAPI app、route/test 对齐。
- FastAPI `Form(...)` 是否声明 `python-multipart`。
- Jinja template markers 是否真的经过 Jinja renderer。
- 禁止 JavaScript 的 profile 下是否出现 inline `<script>`。

`product-acceptance-review` 再从产品角度判断：

- 每个 task 是否有 changed files 和 passing test evidence。
- delivery profile 是否通过。
- 语言、构建系统、必需文件是否符合 PRD。
- 是否存在 blocker 或 warning。
- 输出可审计的 `acceptance_items` 和 deterministic `verifier_rounds`。

因此，带 Web UI 交互的 PRD 当前是“生成测试 + stack gates + 产品验收 evidence”的组合，不是“每个生成页面都由 Playwright 打开并点击一遍”。

## 为什么不把所有 Web UI 都做成浏览器 E2E

当前项目有意不把 Playwright/Selenium 作为默认 CI gate：

- 任意 PRD 生成的 Web 产品形态不固定，统一浏览器脚本很难稳定。
- 视觉和布局断言容易脆弱，尤其是 jsdom、CSS module、响应式布局、动画和不同浏览器渲染之间。
- code_minions 的核心价值是 PRD 到实现/报告/PR 的 delivery harness，CI 更需要稳定地验证 harness 本身。
- 真浏览器验收保留在高价值 dashboard smoke 和维护者人工脚本里，避免普通开发循环变慢。

当 PRD 明确要求真实浏览器行为时，推荐把浏览器测试作为目标项目自己的测试资产，例如在生成的 React/Vite 项目中加入 Playwright spec，并让 delivery profile 的 `test_command` 或 `pre_test_command` 明确运行它。code_minions 当前不会自动替每个 PRD 推断并生成这层浏览器验收。

## 维护者常用命令

默认 gate：

```bash
pytest
ruff check .
```

只跑 Web route 相关测试：

```bash
pytest tests/unit/test_web_routes.py tests/unit/test_web_start.py tests/unit/test_web_controls.py tests/unit/test_web_sse.py tests/unit/test_orphan_scanner.py -q
```

只跑 Engine/integration smoke：

```bash
pytest tests/integration -q
```

跑可选浏览器 smoke：

```bash
pip install -e '.[dev,web-e2e]'
python -m playwright install chromium
CODE_MINIONS_BROWSER_E2E=1 pytest tests/browser -q
```

手工验收 dashboard：

```bash
open docs/maintainers/phase-c-acceptance.md
```

## 判断一次 PRD Web UI 验收是否足够

可以按这个顺序看 evidence：

1. 该 stack 的测试命令是否跑过且通过。
2. 测试里是否真的覆盖 PRD 的用户交互，而不是只检查组件存在。
3. delivery gates 是否没有 blocker。
4. `product-acceptance-review.accepted` 是否为 true。
5. `acceptance_items` 是否覆盖所有任务、delivery profile 和平台要求。
6. 对 dashboard 或高风险 UI，是否跑过 Playwright smoke 或 `phase-c-acceptance.md` 人工脚本。
7. 如果 PRD 明确要求视觉、响应式、真实浏览器 API、拖拽、文件上传、Canvas、音视频等，是否在目标项目内补了真实浏览器测试或手工验收证据。

简化说：pytest 证明 harness 和目标项目测试能跑，delivery gates 证明结构没有明显跑偏，product acceptance 证明交付物和 PRD/delivery profile 对齐；真实浏览器 smoke/人工验收用于补足浏览器实际交互与视觉层。
