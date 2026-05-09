# Phase C Web Dashboard — 人工验收

和 `docs/maintainers/acceptance.md` 是互补关系：这份针对 Web UI。需要一个真浏览器（Chrome / Firefox / Safari 任一）手动走一遍。CI 不自动跑。

## 可选自动化 browser smoke

仓库提供一条很薄的 Playwright smoke，用来在真实 Chromium 中检查 Web
dashboard 的最高价值路径：空列表加载、进入 New Run、启动 `hello-world`、
详情页状态更新和 step 成功展示。它是**可选补充**，不做截图基线比对，也不替代
下方 A-F 的人工验收。

```bash
pip install -e '.[dev,web-e2e]'
python -m playwright install chromium
CODE_MINIONS_BROWSER_E2E=1 pytest tests/browser -q
```

默认 `pytest tests/browser -q` 会把 browser smoke 标记为 skipped，避免普通
unit/integration gate 依赖本机浏览器。截图只在失败时写入 pytest 的临时目录，
用于定位失败页面状态。

## 前置

- M1-M5 + Phase C1/C2/C3 全部合并到 main
- `code-minions web` 能在 shell 里跑
- 如需测试外部集成，本机已安装对应 MCP server 依赖；hello-world 和本地文件工具不需要额外 MCP

## 步骤

### A. 启动并加载

```bash
cd /tmp && rm -rf c-accept && mkdir c-accept && cd c-accept
git init -q -b main && git commit --allow-empty -m init -q
code-minions init .
code-minions web &
WEB_PID=$!
sleep 2
open http://127.0.0.1:8080/    # macOS；Linux 用 xdg-open；Windows 直接浏览器地址栏
```

**检查**：
- [ ] 页面加载，header 里看到 "🤖 code-minions"
- [ ] 列表空时显示 "No runs yet" + 提示文案
- [ ] 右上角 "+ New Run" 按钮可见

### B. CLI 启动 run + Web 可见

在**另一个 terminal**：
```bash
cd /tmp/c-accept
code-minions run hello-world --input name=AAA
```

**检查**：
- [ ] 浏览器刷新 `/`，表格里出现一行（status `success`）
- [ ] 点该行进入 `/runs/<id>` 详情页，看到 step `greet` = success
- [ ] status badge 是绿色

⚠️ 注意：CLI 启动的 run **没有**实时 SSE 更新到浏览器 —— 事件在 CLI 进程的 EventBus 里，跨进程不传。这是已知限制（spec §13）。刷新浏览器才能看到新状态。

### C. Web 启动 run + SSE 实时

1. 浏览器点顶部 "+ New Run"
2. 下拉选择 `hello-world`
3. HTMX 自动加载 inputs 表单 — 看到 `name` 输入框
4. 填 "BBB"
5. 点 "Start Run"

**检查**：
- [ ] 立刻跳转到 `/runs/<id>`（303 redirect）
- [ ] 能看到 step `greet` 从 `running` → `success`（SSE 驱动，**不用手动刷新**）
- [ ] run header 的 status badge 从 `running` → `success`
- [ ] 浏览器 DevTools Network 看到 `/runs/<id>/events` 是一个持续打开的 EventStream

### D. Cancel 按钮

最方便的做法是 mock 一个慢 skill。或直接手动改 DB 状态：

```bash
# 假设现在有一个 success 的 run
sqlite3 .devflow/runs.db "SELECT id, status FROM runs ORDER BY started_at DESC LIMIT 1"
# 把它强制改为 running
sqlite3 .devflow/runs.db "UPDATE runs SET status='running' WHERE id=(SELECT id FROM runs ORDER BY started_at DESC LIMIT 1)"
```

1. 浏览器刷新该 run 的详情页
2. 看到 status = running，应该出现 "Cancel" 按钮
3. 点 Cancel

**检查**：
- [ ] 页面重定向回 `/runs/<id>`
- [ ] status badge 变成 `cancelled`（灰色）
- [ ] Cancel 按钮消失

### E. Resume 按钮

```bash
# 手动造一个 failed
sqlite3 .devflow/runs.db "UPDATE runs SET status='failed' WHERE id=(SELECT id FROM runs ORDER BY started_at DESC LIMIT 1)"
```

1. 浏览器刷新 → 看到 status = failed → 出现 "Resume" 按钮
2. 点 Resume

**检查**：
- [ ] 等 Engine.resume_run 跑完（hello-world 很快）后，重定向到详情页
- [ ] status 变回 `success`

⚠️ 注意：Resume 是**同步** HTTP 请求（详情见 spec §4.3），长流程会让浏览器转圈。真实长 run 建议 CLI `code-minions resume` 代替。

### F. Orphan scanner

1. 起 `code-minions web &`，记下 $WEB_PID
2. 手动创建一个 "running" run：
   ```bash
   sqlite3 .devflow/runs.db "UPDATE runs SET status='running' WHERE id=(SELECT id FROM runs ORDER BY started_at DESC LIMIT 1)"
   ```
3. `kill -9 $WEB_PID` 模拟崩溃
4. 立刻重启：`code-minions web &`
5. 浏览器刷新该 run 页

**检查**：
- [ ] status 从 `running` 变成 `failed`
- [ ] Steps 表格里新增一行 `__orphaned__`，error 提示 "web process restarted; use 'code-minions resume' to continue"
- [ ] 可以点 Resume 续跑

### G. 清理

```bash
kill $WEB_PID 2>/dev/null
rm -rf /tmp/c-accept
```

## 已知脆性

- **`Orphan scanner`** 靠 `.devflow/web.pid` 文件判定。手动 `kill -9` 后 pid 文件还在，但里面的 pid 已死，所以下次启动能正确清理。如果两个 Web 进程同时启动同一项目会互相踩（此场景不支持）。
- **CLI run 的实时更新不跨进程**。想看 CLI run 的变化需要刷新浏览器。
- **浏览器后退/刷新**时 SSE 连接会丢，详情页会重新建立订阅 —— 有很短的"无更新窗口"。
- **Cancel 按钮是软取消**：只改 DB 状态，不真正中断正在跑的 skill 执行线程（v1 刻意简化；Phase C-B 再议）。
- **Resume 按钮是同步**：Engine.resume_run 块 HTTP 请求。长 run 建议走 CLI。
- **`window.location.reload()` on `run.finished`**：可能引起 SSE 连接被 React 式快速刷新打断 —— 真实场景中很少见，但 console 偶尔会有一条 `EventSource` 断开警告，可忽略。

## 通过条件

A / B / C / D / E / F 全绿即 Phase C 验收通过。
