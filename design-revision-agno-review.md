# Design Revision: Agno 文档深度审查后的修正与增强

> 基于 docs.agno.com 全量文档审查，针对 multi-agent-orchestration-design.md 的修正。

---

## 审查发现摘要

| # | 发现 | 影响 | 严重程度 |
|---|------|------|----------|
| F1 | Team 有四种显式 `TeamMode`，原设计未使用 | Workflow 编排逻辑需重写 | 🔴 高 |
| F2 | Workflow 构建块是 `Step/Loop/Parallel/Condition/Router`，原设计用了错误的伪代码 | 代码示例不可用 | 🔴 高 |
| F3 | Agno 内置 `pre_hooks` / `post_hooks` 机制，与 cadence hooks 职责重叠 | 需要明确分层 | 🟡 中 |
| F4 | Tracing 基于 OpenTelemetry，通过 `setup_tracing(db=db)` 一行启用 | chronicler hook 大部分功能可被原生 tracing 替代 | 🟡 中 |
| F5 | Session 管理有原生 `session_id` + `user_id` 隔离机制 | session-guard hook 可简化 | 🟢 低 |
| F6 | HITL 使用 `@tool(requires_confirmation=True)` + `@approval` 装饰器 | PR review 审批流可用原生机制 | 🔴 高 |
| F7 | Callable Factories 支持运行时动态解析 members | 完美匹配我们的 Role Registry 设计 | 🟢 增强 |
| F8 | HITL 在 Team/Workflow 层面标注 "near future" | Phase 3 的 PR review 自动化可能受限 | 🟡 中 |
| F9 | `LearningMachine(session_context=True)` 提供会话级上下文跟踪 | 可替代部分 session-guard 功能 | 🟢 增强 |
| F10 | `WorkflowAgent` 支持对话式工作流 | 可用于 `/orchestra` 命令的交互模式 | 🟢 增强 |

---

## 修正 1：Team 模式选择（F1）

### 原设计问题

原设计将所有协作都用一个通用的 `Team` 概念处理，没有区分不同阶段需要的协作模式。

### 修正

Agno Team 2.0 引入了四种显式 `TeamMode`，每个阶段应使用最合适的模式：

```python
from agno.team import Team
from agno.team.mode import TeamMode

# Phase 1: Design — 使用 coordinate 模式
# Maestro 分解任务，分配给 Probe，综合结果
design_team = Team(
    name="Design Squad",
    model=Claude(id="claude-sonnet-4-6"),
    members=[probe, oracle],
    mode=TeamMode.coordinate,  # ← 默认模式，leader 控制分解+综合
    instructions="Decompose the design task, delegate to specialists, synthesize final_design.md",
)

# Phase 2: Cross-Model Review — 使用 broadcast 模式
# 同一个 final_design.md 同时发给 Critic 和 Challenger
review_team = Team(
    name="Review Board",
    model=Claude(id="claude-sonnet-4-6"),
    members=[critic, challenger],
    mode=TeamMode.broadcast,   # ← 所有成员同时收到同一任务
    instructions="Synthesize review findings, track issue count per round",
)

# Phase 3: Task Execution — 使用 route 模式
# Maestro 根据 task 类型路由到最合适的 specialist
impl_team = Team(
    name="Implementation Squad",
    model=Claude(id="claude-sonnet-4-6"),
    members=[probe, oracle],
    mode=TeamMode.route,       # ← 路由到单个专家，直接返回结果
    determine_input_for_members=False,  # 原始 task 直接传递
)

# Phase 3: PR Review — 使用 tasks 模式
# 自主循环：分解 review 任务 → 执行 → 标记完成
pr_review_team = Team(
    name="PR Review Board",
    model=Claude(id="claude-sonnet-4-6"),
    members=[critic, challenger],
    mode=TeamMode.tasks,       # ← 自主任务循环，直到 review 完成
    max_iterations=4,
)
```

### 为什么这很重要

不同模式的 token 成本和延迟差异显著：

| 模式 | 适用阶段 | 协调成本 | 延迟 |
|------|----------|----------|------|
| coordinate | Design | 高（分解+综合） | 串行 |
| broadcast | Cross-Review | 中（仅综合） | 并行成员执行 |
| route | Task 分配 | 低（仅选择） | 快 |
| tasks | PR Review | 高（规划+迭代） | 迭代循环 |

---

## 修正 2：Workflow 构建块（F2）

### 原设计问题

原设计的 workflow 定义使用了非法的 dict 结构，不符合 Agno 的实际 API。

### 修正

Agno Workflow 的构建块是 `Step`, `Loop`, `Parallel`, `Condition`, `Router`：

```python
from agno.workflow import Workflow, Step, Loop, Parallel, Condition, Router
from agno.workflow.types import StepInput, StepOutput

# ── 自定义函数步骤 ──────────────────────────────────

def check_review_issues(step_input: StepInput) -> StepOutput:
    """检查 review 是否还有新 issue"""
    previous = step_input.previous_step_content
    has_new_issues = "new_issues" in previous and int(previous["new_issues"]) > 0
    return StepOutput(
        content=previous,
        metadata={"has_new_issues": has_new_issues}
    )

def setup_github_project(step_input: StepInput) -> StepOutput:
    """从 final_design.md 创建 GitHub issues"""
    # 调用 GitHub API 创建 roadmap 和 issues
    design_content = step_input.previous_step_content
    # ... GitHub Projects API 逻辑
    return StepOutput(content="GitHub project setup complete")

# ── 完整 Workflow ────────────────────────────────────

dev_workflow = Workflow(
    name="Full Development Pipeline",
    description="Design → Review → Implement → PR Review",
    steps=[
        # Step 1: Design phase（Team 作为 step executor）
        Step(executor=design_team, name="design"),

        # Step 2: Review loop — 最多 3 轮
        Loop(
            steps=[
                Step(executor=review_team, name="cross_review"),
                Step(executor=check_review_issues, name="check_issues"),
            ],
            max_iterations=3,
            # Loop 在 check_issues 返回 no new issues 时退出
        ),

        # Step 3: GitHub project setup（函数作为 step）
        Step(executor=setup_github_project, name="github_setup"),

        # Step 4: Implementation — 并行执行独立 tasks
        # （这里用 Maestro agent 来做 task 分解和分配）
        Step(executor=impl_team, name="implementation"),

        # Step 5: PR Review
        Step(executor=pr_review_team, name="pr_review"),
    ],
)
```

---

## 修正 3：Hook 分层——Agno Hooks vs cadence Hooks（F3）

### 发现

Agno 原生支持两类 hooks：

1. **Agent pre_hooks / post_hooks**：在 LLM 执行前后运行，可访问 `RunInput`、`session_state`
2. **Tool hooks**：在 tool 调用前后运行，可做验证、日志、参数修改

原设计中的 `chronicler` 和 `session-guard` 与 Agno hooks 有功能重叠。

### 修正：明确分层

```
┌─────────────────────────────────────────────────┐
│            cadence hooks（Claude Code 层）        │
│                                                  │
│  chronicler  → 捕获 cadence skill/command 调用    │
│  session-guard → Claude Code session 策略         │
│                                                  │
│  ⚠ 仅在 Claude Code CLI 内部生效                  │
│  ⚠ 无法感知 Agno 层的行为                          │
└────────────────────┬────────────────────────────┘
                     │ JSONL trace events
                     ▼
┌─────────────────────────────────────────────────┐
│            Agno hooks（编排层）                    │
│                                                  │
│  pre_hooks  → 输入验证、PII 检测、安全检查          │
│  post_hooks → 输出质量检查、合规过滤               │
│  tool_hooks → tool 调用日志、耗时统计              │
│                                                  │
│  ✅ 通过 setup_tracing() 自动捕获全量 trace        │
│  ✅ 存储在你自己的数据库中                          │
└─────────────────────────────────────────────────┘
```

### 实际代码

```python
from agno.run.agent import RunInput
from agno.tracing import setup_tracing

# ── 全局 tracing 设置（一次调用，覆盖所有 agent/team/workflow）──
db = SqliteDb(db_file="traces/orchestra.db")
setup_tracing(db=db)  # OpenTelemetry-based，自动捕获所有执行细节

# ── Agent pre-hook：输入验证 ──
def validate_task_input(run_input: RunInput) -> None:
    """确保每个 task 有必要的上下文"""
    if not run_input.input_content:
        raise InputCheckError("Empty task input")

# ── Tool hook：tool 调用日志 ──
def tool_logger(function_name: str, function_call, arguments: dict):
    """记录所有 tool 调用的耗时"""
    start = time.time()
    result = function_call(**arguments)
    duration = time.time() - start
    logger.info(f"Tool {function_name}: {duration:.2f}s, args={arguments}")
    return result

# ── 应用到 agent ──
probe = Agent(
    name="Probe",
    model=Claude(id="claude-sonnet-4-6"),
    pre_hooks=[validate_task_input],
    tool_hooks=[tool_logger],
    db=db,  # trace 自动存储到这个数据库
)
```

### 影响：简化 chronicler

原设计的 `chronicler` cadence hook 现在只需负责**捕获 Claude Code 内部的 cadence skill/command 使用情况**——Agno 层的 agent/team/workflow trace 由原生 `setup_tracing()` 全自动覆盖。

---

## 修正 4：Session 隔离用原生机制（F5, F9）

### 原设计问题

`session-guard` hook 试图在 cadence 层面做 session 隔离，但 Agno 有原生的 session 管理。

### 修正

```python
# 每个 task 使用独立 session_id，Agno 原生支持
for task in tasks:
    impl_team.run(
        task.description,
        session_id=f"task_{task.id}",     # ← 独立 session，天然隔离
        user_id="adrian",
    )

# 如果需要跨 task 共享上下文（如 final_design.md），
# 使用 LearningMachine 的 session context
from agno.learn import LearningMachine, SessionContextConfig

maestro = Agent(
    name="Maestro",
    model=Claude(id="claude-sonnet-4-6"),
    learning=LearningMachine(
        session_context=SessionContextConfig(enable_planning=True),
    ),
    db=db,
)
```

`SessionContextConfig(enable_planning=True)` 会让 agent 自动跟踪：当前目标是什么、已完成了哪些步骤、下一步是什么——正好是你想在 Timeline View 里看到的信息。

---

## 修正 5：HITL 用于 PR Review（F6, F8）

### 发现

Agno 的 HITL 通过 `@tool(requires_confirmation=True)` 和 `@approval` 装饰器实现。Agent 级别已稳定，但**文档明确说 "Team and Workflow will be supported in the near future"**。

### 修正：PR review 审批的正确实现

```python
from agno.tools import tool
from agno.approval import approval

@approval(type="audit")
@tool(requires_confirmation=True)
def merge_pull_request(pr_number: int, repo: str) -> str:
    """Merge a PR after review approval.
    Args:
        pr_number: The PR number to merge
        repo: The repository name
    Returns:
        Confirmation message
    """
    # 实际的 GitHub merge 逻辑
    return f"PR #{pr_number} merged in {repo}"

# Critic agent 使用这个 tool 时，会自动暂停等待你的确认
critic = Agent(
    name="Critic",
    model=Gemini(id="gemini-2.5-pro"),
    tools=[merge_pull_request],
    db=db,
)

# 通过 AgentOS API 处理审批
# POST /agents/critic/runs → status: "paused"
# POST /agents/critic/runs/{run_id}/continue → confirmed: true
```

### ⚠ 限制与 Workaround

由于 HITL 在 Team/Workflow 层面尚未完全支持，PR review 阶段应将 Critic 和 Challenger 作为**独立 Agent 串行调用**（而非 Team），每个 agent 独立暂停等待审批：

```python
# Phase 3 PR Review — 避免使用 Team，改用 Workflow 的串行 Step
pr_review_workflow = Workflow(
    name="PR Review Pipeline",
    steps=[
        Step(executor=critic, name="critic_review"),     # 可暂停等待 HITL
        Step(executor=challenger, name="challenger_review"),  # 可暂停等待 HITL
    ],
)
```

---

## 修正 6：Callable Factories 实现 Role Registry（F7）

### 发现

Agno Team 支持 `callable factories`——`members` 参数可以是一个函数，在每次 run 时动态解析。这完美替代了我们设计的角色加载机制。

### 修正

```python
from agno.agent import Agent
from agno.team import Team

def resolve_team_members(team: Team, session_state: dict) -> list[Agent]:
    """根据项目 tags 动态加载角色"""
    project_tags = session_state.get("project_tags", [])

    members = [
        # 基础角色——始终加载
        Agent(name="Critic", role="Design Reviewer", model=Gemini(id="gemini-2.5-pro")),
        Agent(name="Challenger", role="Design Challenger", model=OpenAI(id="codex-mini")),
    ]

    # 领域角色——按 tag 条件加载
    if "ebpf" in project_tags:
        members.append(
            Agent(name="Probe", role="eBPF Expert", model=Claude(id="claude-sonnet-4-6"))
        )
    if "frontend" in project_tags:
        members.append(
            Agent(name="Artisan", role="Frontend Expert", model=Claude(id="claude-sonnet-4-6"))
        )
    if "data" in project_tags:
        members.append(
            Agent(name="Oracle", role="Data Expert", model=Claude(id="claude-sonnet-4-6"))
        )

    return members

# Team 在每次 run 时动态解析成员
design_team = Team(
    name="Design Squad",
    model=Claude(id="claude-sonnet-4-6"),
    members=resolve_team_members,  # ← callable factory！
    mode=TeamMode.coordinate,
)

# 运行时通过 session_state 传入 tags
design_team.run(
    "Design the system architecture",
    session_state={"project_tags": ["ebpf", "rust", "perf"]},
)
```

### 影响

**角色 TOML 文件仍然保留**（作为 instructions 的来源），但加载逻辑从自定义的 `/cadence init-roles` 命令迁移到 Agno 原生的 callable factory。

---

## 修正 7：Tracing 替代大部分 chronicler（F4）

### 发现

Agno 的 `setup_tracing(db=db)` 基于 OpenTelemetry，**一行代码**即可自动捕获：

- Agent/Team/Workflow 的完整执行 trace
- 每个 LLM 调用的 input/output/tokens/latency
- 每个 tool 调用的参数和结果
- span 层级关系（parent-child）

### 修正：chronicler 的职责缩减

```
原设计 chronicler 职责         →    修正后的职责分配
─────────────────────────────────────────────────────
skill.invoked                 →    cadence chronicler（保留）
skill.completed               →    cadence chronicler（保留）
hook.fired                    →    cadence chronicler（保留）
session.created               →    Agno setup_tracing 自动覆盖
session.resumed               →    Agno setup_tracing 自动覆盖
tool.called                   →    Agno setup_tracing 自动覆盖
model.request                 →    Agno setup_tracing 自动覆盖
model.response                →    Agno setup_tracing 自动覆盖
file.modified                 →    Agno tool_hooks 覆盖
git.commit                    →    Agno tool_hooks 覆盖
pr.created                    →    Agno tool_hooks 覆盖
review.submitted              →    Agno HITL audit 自动记录
```

**结论**：cadence 的 chronicler hook 只需关注 **cadence skill/hook 自身的调用记录**。其余 90% 的可观测性由 Agno 原生 tracing 覆盖。

查询 trace 也很简单：

```python
traces, count = db.get_traces(agent_id=probe.id, limit=10)
for trace in traces:
    print(f"{trace.name}: {trace.duration_ms}ms ({trace.status})")
```

---

## 修正 8：新增 Conversational Workflow 支持（F10）

### 发现

Agno 支持 `WorkflowAgent`——一个特殊的 Agent，可以决定是直接回答用户，还是触发 workflow 运行。

### 增强

给 `/orchestra` 命令增加对话模式：

```python
from agno.workflow import WorkflowAgent

orchestra_agent = WorkflowAgent(
    model=Claude(id="claude-sonnet-4-6"),
    num_history_runs=4,  # 可以看到最近 4 次 workflow 运行的结果
    instructions="""You are the Orchestra conductor.
    - If the user asks about previous design/review results, answer from history.
    - If the user requests a new design or implementation, run the workflow.
    - If the user asks to modify a specific agent's behavior, adjust and re-run.
    """,
)

dev_workflow = Workflow(
    name="Full Development Pipeline",
    agent=orchestra_agent,  # ← 对话式入口
    steps=[...],
    db=db,
)

# 现在可以这样交互：
dev_workflow.print_response("Design a cascade redistribution algorithm for wPerf")
# → 触发完整 workflow

dev_workflow.print_response("What issues did Critic find in the last review?")
# → 直接从 history 回答，不重新运行 workflow

dev_workflow.print_response("Re-run the review phase with stricter criteria")
# → 仅重新运行 review 步骤
```

---

## 修正 9：完整代码——修正后的 Orchestra 系统

```python
"""
orchestra.py — wPerf Multi-Agent Development Pipeline
"""
import time
from agno.agent import Agent
from agno.team import Team
from agno.team.mode import TeamMode
from agno.workflow import Workflow, Step, Loop, Parallel
from agno.workflow.types import StepInput, StepOutput
from agno.models.anthropic import Claude
from agno.models.google import Gemini
from agno.models.openai import OpenAI
from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.tools import tool
from agno.tools.mcp import MCPTools
from agno.tracing import setup_tracing
from agno.learn import LearningMachine, SessionContextConfig
from agno.run.agent import RunInput

# ══════════════════════════════════════════════════════
# Infrastructure
# ══════════════════════════════════════════════════════

db = SqliteDb(db_file="traces/orchestra.db")
setup_tracing(db=db)  # 一行启用全量 trace

# ══════════════════════════════════════════════════════
# Hooks
# ══════════════════════════════════════════════════════

def validate_task(run_input: RunInput) -> None:
    if not run_input.input_content or len(run_input.input_content.strip()) < 10:
        from agno.exceptions import InputCheckError, CheckTrigger
        raise InputCheckError(
            "Task description too short",
            check_trigger=CheckTrigger.INPUT_NOT_ALLOWED,
        )

def tool_timer(function_name: str, function_call, arguments: dict):
    start = time.time()
    result = function_call(**arguments)
    duration = time.time() - start
    print(f"  ⏱ {function_name}: {duration:.2f}s")
    return result

# ══════════════════════════════════════════════════════
# Tools
# ══════════════════════════════════════════════════════

@tool(requires_confirmation=True)
def merge_pr(pr_number: int, repo: str) -> str:
    """Merge a pull request after review approval."""
    return f"PR #{pr_number} merged in {repo}"

@tool
def create_github_issues(design_content: str, repo: str) -> str:
    """Create GitHub issues from design document."""
    return f"Created issues in {repo} from design"

# ══════════════════════════════════════════════════════
# Agents
# ══════════════════════════════════════════════════════

maestro = Agent(
    name="Maestro",
    model=Claude(id="claude-sonnet-4-6"),
    role="Chief Architect & Task Orchestrator",
    instructions="...",  # 从 roles/_base/maestro.toml 加载
    tools=[MCPTools(url="..."), create_github_issues],
    pre_hooks=[validate_task],
    tool_hooks=[tool_timer],
    learning=LearningMachine(
        session_context=SessionContextConfig(enable_planning=True),
    ),
    db=db,
)

probe = Agent(
    name="Probe",
    model=Claude(id="claude-sonnet-4-6"),
    role="eBPF Domain Expert",
    instructions="...",
    pre_hooks=[validate_task],
    tool_hooks=[tool_timer],
    db=db,
)

critic = Agent(
    name="Critic",
    model=Gemini(id="gemini-2.5-pro"),
    role="Design & Code Reviewer",
    instructions="...",
    tools=[merge_pr],
    tool_hooks=[tool_timer],
    db=db,
)

challenger = Agent(
    name="Challenger",
    model=OpenAI(id="codex-mini"),
    role="Design Challenger & Devil's Advocate",
    instructions="...",
    tools=[merge_pr],
    tool_hooks=[tool_timer],
    db=db,
)

oracle = Agent(
    name="Oracle",
    model=Claude(id="claude-sonnet-4-6"),
    role="Data & Validation Expert",
    instructions="...",
    tool_hooks=[tool_timer],
    db=db,
)

# ══════════════════════════════════════════════════════
# Teams
# ══════════════════════════════════════════════════════

design_team = Team(
    name="Design Squad",
    model=Claude(id="claude-sonnet-4-6"),
    members=[probe, oracle],
    mode=TeamMode.coordinate,
    instructions="Produce final_design.md with decision appendix",
    db=db,
)

review_team = Team(
    name="Review Board",
    model=Claude(id="claude-sonnet-4-6"),
    members=[critic, challenger],
    mode=TeamMode.broadcast,
    instructions="Review design, report issues count",
    db=db,
)

# ══════════════════════════════════════════════════════
# Workflow Functions
# ══════════════════════════════════════════════════════

def check_review_complete(step_input: StepInput) -> StepOutput:
    content = step_input.previous_step_content or ""
    no_issues = "0 new issues" in content.lower() or "no new issues" in content.lower()
    return StepOutput(
        content=content,
        metadata={"review_complete": no_issues},
    )

# ══════════════════════════════════════════════════════
# Workflow
# ══════════════════════════════════════════════════════

dev_workflow = Workflow(
    name="Orchestra Pipeline",
    description="Design → Review → GitHub Setup → Implement → PR Review",
    steps=[
        Step(executor=design_team, name="design"),
        Loop(
            steps=[
                Step(executor=review_team, name="review"),
                Step(executor=check_review_complete, name="check"),
            ],
            max_iterations=3,
        ),
        Step(executor=maestro, name="github_setup"),
        Step(executor=probe, name="implementation"),
        Step(executor=critic, name="critic_review"),
        Step(executor=challenger, name="challenger_review"),
    ],
    db=db,
)

# ══════════════════════════════════════════════════════
# AgentOS
# ══════════════════════════════════════════════════════

agent_os = AgentOS(
    description="wPerf Development Orchestra",
    agents=[maestro, probe, critic, challenger, oracle],
    teams=[design_team, review_team],
    workflows=[dev_workflow],
    tracing=True,
)

app = agent_os.get_app()
# uvicorn orchestra:app --port 7777
```

---

## 修正 10：更新实施路径

| 阶段 | 原计划 | 修正后 |
|------|--------|--------|
| Phase 0 | 安装 Agno | 不变，但增加 `setup_tracing(db=db)` |
| Phase 1 | cadence 扩展（chronicler + session-guard + add-roles + /orchestra） | **缩减**：chronicler 只记录 cadence skill 调用；session-guard 改用 Agno 原生 `session_id`；add-roles 保留但加载逻辑迁移到 callable factory |
| Phase 2 | Agno workflow | **重写**：使用正确的 `Step/Loop/Parallel` API；Team 使用 `TeamMode`；HITL 用 `@tool(requires_confirmation=True)` |
| Phase 3 | AgentOS UI 定制 | **简化**：trace 数据已由 `setup_tracing` 自动采集，Timeline View 可直接查询 `db.get_traces()`，无需自建 trace 采集管道 |

### 新增 Phase 0.5：验证 HITL 在 Workflow 中的支持程度

由于文档标注 Team/Workflow HITL "near future"，建议在 Phase 1 前先做一个最小验证：

```python
# 验证脚本：HITL 在 Workflow Step 中是否能正确暂停
from agno.workflow import Workflow, Step

test_workflow = Workflow(
    name="HITL Test",
    steps=[
        Step(executor=critic, name="review"),  # critic 有 requires_confirmation tool
    ],
)

response = test_workflow.run("Review this code")
print(f"Status: {response.status}")  # 预期: "paused"
print(f"Requirements: {response.active_requirements}")
```

如果 HITL 在 Workflow Step 中不支持暂停，则 Phase 3 的 PR review 需降级为**直接调用独立 Agent**，不经过 Workflow 编排。

---

## 总结：关键变更清单

1. ✅ Team 使用 `TeamMode.coordinate / broadcast / route / tasks` 显式声明
2. ✅ Workflow 使用 `Step / Loop / Parallel / Condition / Router` 构建块
3. ✅ Tracing 用 `setup_tracing(db=db)` 一行启用，覆盖 90% 可观测需求
4. ✅ Session 隔离用 Agno 原生 `session_id` 参数
5. ✅ HITL 用 `@tool(requires_confirmation=True)` + `@approval` 装饰器
6. ✅ Role Registry 用 Callable Factories 动态解析 team members
7. ✅ 对话式入口用 `WorkflowAgent`
8. ⚠ 验证 HITL 在 Workflow 层面的支持程度（可能需要 workaround）
9. ⚠ cadence chronicler 职责大幅缩减（仅记录 cadence 自身的 skill/hook 调用）
