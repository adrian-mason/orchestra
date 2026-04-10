# Orchestra: Multi-Agent Development Orchestration System

> 基于 Agno 框架的多 agent 开发编排系统，自动化 Research → Design → Cross-Model Review → Implementation → PR Lifecycle 完整开发工作流。
>
> 版本：v4.4 | 更新日期：2026-04-07
> 基于 [Agno 文档审查](design-revision-agno-review.md) + [6 项目深度调研](research/final_research_report.md)

---

## 目录

1. [架构总览](#1-架构总览)
2. [5 阶段工作流](#2-5-阶段工作流)
3. [Agent 定义与多模型配置](#3-agent-定义与多模型配置)
4. [Review Gate 系统](#4-review-gate-系统)
5. [Reaction Engine（事件驱动）](#5-reaction-engine事件驱动)
6. [Merge Queue（冲突解决）](#6-merge-queue冲突解决)
7. [Watchdog（健康监控）](#7-watchdog健康监控)
8. [可观测性](#8-可观测性)
9. [Learning 系统](#9-learning-系统)
10. [Agno 实现详解](#10-agno-实现详解)
11. [实施路径](#11-实施路径)
12. [风险与开放问题](#12-风险与开放问题)

---

## 1. 架构总览

### 1.1 核心设计原则

| 原则 | 说明 | 来源 |
|------|------|------|
| **层级化编排** | 2-3 层 Coordinator → Specialist 拓扑，支持递归嵌套 | 6/6 项目共识 |
| **跨模型对抗审查** | 写者与审查者永远使用不同模型 | Metaswarm |
| **阻塞性质量门控** | FAIL = 重试或升级，绝不跳过 | Metaswarm + Overstory |
| **OTel 原生可观测性** | Agno `setup_tracing()` + 自定义 EventStore | Agno 原生 + Overstory |
| **事件驱动 PR 自动化** | Reaction Engine 自动响应 CI/审查/冲突事件 | Composio + Metaswarm |
| **渐进式容错** | 3 层 Watchdog：warn → nudge → AI triage → terminate | Overstory |
| **知识累积** | Self-Reflect → JSONL 知识库 → 选择性注入 | Metaswarm + CCW |

### 1.2 系统架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Orchestra System                             │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────────────┐  │
│  │ /orchestra│  │ AgentOS  │  │ GitHub    │  │ Watchdog Daemon  │  │
│  │ CLI Entry │→ │ REST API │  │ Webhooks  │  │ (Tier 0/1/2)     │  │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └────────┬─────────┘  │
│       │              │              │                  │             │
│       ▼              ▼              ▼                  ▼             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Agno Workflow Engine                       │   │
│  │                                                              │   │
│  │  Research → Design → Review Loop → Implementation → PR Life  │   │
│  │     │          │         │              │              │      │   │
│  │     │     ┌────┴────┐   │    ┌─────────┴──────────┐   │      │   │
│  │     │     │ Design  │   │    │ WU Decompose        │   │      │   │
│  │     │     │ Team    │   │    │ → Per-Unit 4-Phase  │   │      │   │
│  │     │     │(coord.) │   │    │ → Integration Review│   │      │   │
│  │     │     └─────────┘   │    └────────────────────┘   │      │   │
│  │     │                   │                              │      │   │
│  │     │    ┌──────────────┴──────────────┐    ┌─────────┴───┐  │   │
│  │     │    │ Review Board (broadcast)    │    │ PR Shepherd  │  │   │
│  │     │    │ Plan Gate │ Design Gate     │    │ Reaction Eng │  │   │
│  │     │    │ 3 advers. │ 5-6 experts    │    │ Merge Queue  │  │   │
│  │     │    └─────────────────────────────┘    └─────────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│  ┌───────────────────────────┴──────────────────────────────────┐   │
│  │                    Persistence Layer                          │   │
│  │                                                              │   │
│  │  traces.db │ events.db  │ mail.db │ metrics.db │ merge.db   │   │
│  │  (Agno OTel) (EventStore  (Agent通信) (成本追踪)  (合并队列)  │   │
│  │              +DecisionGate                                   │   │
│  │              +stage1_outputs)                                │   │
│  │                                                              │   │
│  │  knowledge.jsonl          │ session checkpoints              │   │
│  │  (Learning 知识库)         │ (.orchestra/checkpoints/)       │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 5 阶段工作流

### 2.1 阶段定义

从原始三阶段（Design → Review → Implementation）扩展为五阶段，增加前端 Research 和后端 PR Lifecycle：

```
Stage 1        Stage 2        Stage 3             Stage 4                          Stage 5
RESEARCH  →    DESIGN    →    CROSS-MODEL    →    IMPLEMENTATION              →    PR LIFECYCLE
(可选)                        REVIEW              ┌──────────────────────────┐
                              ┌─ Plan Gate        │ 4a. GitHub Setup         │     ┌─ PR Shepherd
Scout Agent    Architect      │  3 对抗审查者       │ 4b. Work Unit Decompose  │     ├─ Reaction Engine
探索代码库      生成设计        │  ALL PASS          │ 4c. Per-Unit 4-Phase Loop│     ├─ Merge Queue
先例/约束      final_design   │  Max 3 轮           │     IMPL→VALIDATE→      │     ├─ CI 自动修复
               .md            └─ Design Gate       │     ADV.REVIEW→COMMIT   │     └─ Closure & Learn
                                 5-6 领域专家       │ 4d. Integration Review   │
                                 ALL APPROVE       └──────────────────────────┘
                                 Max 3 轮
```

### 2.2 Stage 间数据流

```python
# Stage 1 → 2: research_context → design_team (via previous_step_content)
# Stage 2 → 3: final_design.md → review_board (via previous_step_content)
# Stage 3 pass: review gate 写入 session_state["approved_design"]
# Stage 3 → 4a: github_setup 读取 session_state["approved_design"]，
#               创建 issues，写入 session_state["github_issues"]
# Stage 4a → 4b: decompose 读取 session_state["approved_design"] + ["github_issues"]
#               （不依赖 previous_step_content，因为那只是 setup 状态文本）
# Stage 4b → 4c: per WorkUnit → 4-phase loop (IMPL→VALIDATE→ADV.REVIEW→COMMIT)
# Stage 4c → 4d: all units done → final_integration_review (阻塞性 gate)
# Stage 4d → 5: integration_passed=True → pr_shepherd
#               integration_passed=False → Decision Gate 阻塞 workflow
```

**关键约束**：跨步骤传递大文档（如 approved_design）使用 `session_state` 而非 `previous_step_content`。`previous_step_content` 仅包含上一步的直接输出，当中间有 setup/utility 步骤时会丢失上下文。

> **实现注记 (AC-03, G0-C verified)**：Agno 2.5.14 中 `session_state` 的实际运行时路径为
> `step_input.workflow_session.session_data["session_state"][key]`，而非 `step_input.session_state[key]`。
> **实现约束（Phase 0+）**：所有运行时业务代码 **必须** 通过 `get_ss()` / `set_ss()` helper 访问
> session_state，禁止直接操作底层路径。DESIGN.md 伪代码中 `step_input.session_state[key]` 和
> `workflow_session.session_data` 的直接引用视为概念层简写，等效于 helper 调用，不受此约束。
> Phase 0 脚手架将提供以下封装：
>
> ```python
> # orchestra/utils/session.py — Phase 0 scaffold (AC-03)
> def get_session_state(step_input: StepInput) -> dict[str, Any]:
>     """Return the session_state dict, creating it if absent."""
>     assert step_input.workflow_session is not None
>     sd = step_input.workflow_session.session_data
>     assert sd is not None
>     if "session_state" not in sd:
>         sd["session_state"] = {}
>     return sd["session_state"]
>
> # Convenience read/write
> def get_ss(si: StepInput, key: str, default=None):
>     return get_session_state(si).get(key, default)
>
> def set_ss(si: StepInput, key: str, value: Any):
>     get_session_state(si)[key] = value
> ```
>
> `Workflow(session_state={...})` 会在初始化时 **deepcopy** 传入的 dict（G0-C B11 verified），
> 后续对原始 dict 的修改不会影响运行时状态。

### 2.3 Agno Workflow 定义

> **Gate 0 约束**：以下定义已按 AC-01 ~ AC-07 更新。关键变更：
> - Loop 使用 `end_condition` 退出（AC-01），`StepOutput(stop=True)` 终止整个 Workflow，不能用于 Loop 控制
> - 所有 gate step 显式 `on_error=OnError.fail`（AC-02）
> - `session_state` 实际路径为 `workflow_session.session_data`（AC-03），Phase 0 提供 helper 封装
> - Decision Gate 放在 Loop **之后**（AC-04），Loop 内 `requires_confirmation` 被静默忽略
> - Workflow 构造器必须指定 `db`（AC-05），否则 resume 语义失效
> - Team leader synthesis 成功不等于所有成员成功，需显式检查（AC-06）
> - `mode=` 参数优先于布尔标志位（AC-07）

```python
from agno.workflow import Workflow, Step, Loop, Parallel, Condition
from agno.workflow.types import StepInput, StepOutput, OnError, OnReject

orchestra_workflow = Workflow(
    name="Orchestra Pipeline",
    description="Research → Design → Review → Implement → PR Lifecycle",
    steps=[
        # Stage 1: Research (可选，通过 Condition 控制)
        Condition(
            condition=lambda ctx: ctx.get("enable_research", False),
            if_true=Step(executor=scout_agent, name="research"),
        ),

        # Stage 2: Design — 产出写入 session_data["latest_design_content"]
        Step(executor=design_team, name="design"),
        Step(executor=persist_design_output, name="persist_design"),

        # Stage 3: Cross-Model Review (Plan Gate + Design Gate)
        # AC-01: Loop 使用 end_condition 退出，不依赖 StepOutput(stop=True)
        # AC-04: Decision Gate（requires_confirmation）放在 Loop 之后
        Loop(
            name="plan_review_loop",
            steps=[
                Step(executor=plan_review_team, name="plan_review"),
                Step(executor=check_plan_gate, name="plan_gate_check",
                     on_error=OnError.fail),  # AC-02
            ],
            end_condition=lambda ctx: ctx.get("plan_gate_passed", False),
            max_iterations=3,
        ),
        # Post-Loop Decision Gate: 3 轮未通过时暂停等人工裁定（AC-04）
        Step(executor=check_plan_review_result, name="plan_review_result",
             on_error=OnError.fail),
        Step(executor=plan_decision_gate, name="plan_decision_gate",
             requires_confirmation=True,
             on_reject=OnReject.cancel,
             on_error=OnError.fail),

        Loop(
            name="design_review_loop",
            steps=[
                # 1. 审查当前设计（读 session_data["latest_design_content"]）
                Step(executor=design_review_team, name="design_review"),
                # 2. 检查审查结果 — 全部 APPROVED 则 end_condition 退出循环
                Step(executor=check_design_gate, name="design_gate_check",
                     on_error=OnError.fail),  # AC-02
                # 3. 未通过时：design_team 根据 feedback 修订设计
                #    previous_step_content 是 check_design_gate 返回的 feedback
                #    修订后更新 session_data["latest_design_content"]
                Step(executor=revise_design_from_feedback, name="revise_design"),
            ],
            end_condition=lambda ctx: ctx.get("design_gate_passed", False),
            max_iterations=3,
        ),
        # Post-Loop Decision Gate: 3 轮未通过时暂停等人工裁定（AC-04）
        Step(executor=check_design_review_result, name="design_review_result",
             on_error=OnError.fail),
        Step(executor=design_decision_gate, name="design_decision_gate",
             requires_confirmation=True,
             on_reject=OnReject.cancel,
             on_error=OnError.fail),

        # Stage 4a: GitHub Setup
        Step(executor=setup_github_project, name="github_setup"),

        # Stage 4b: Work Unit Decomposition
        # 将 approved_design 分解为带 DoD/file_scope/dependencies 的 WorkUnit DAG
        Step(executor=decompose_work_units, name="decomposition"),

        # Stage 4c: Per-WorkUnit Execution (4-phase loop)
        # Parallel 执行独立 work units，串行执行有依赖关系的
        Step(executor=execute_work_units, name="implementation"),

        # Stage 4d: Final Integration Review (阻塞性 gate)
        # AC-04: 使用 requires_confirmation 暂停，on_reject=cancel 终止 workflow
        Step(executor=final_integration_review, name="integration_review",
             on_error=OnError.fail),  # AC-02
        Step(executor=integration_decision_gate, name="integration_gate",
             requires_confirmation=True,
             on_reject=OnReject.cancel,
             on_error=OnError.fail),

        # Stage 5: PR Lifecycle
        # integration_gate 确认后才继续。on_reject=cancel 终止 workflow。

        # 5a: PR 创建 + CI 监控（PR Shepherd 自治状态机）
        Step(executor=pr_shepherd, name="pr_lifecycle"),

        # 5b: 持久化 PR 上下文（PR URL → session_data，供 merge 使用）
        Step(executor=persist_pr_context, name="persist_pr"),

        # 5c: Code Review — 独立 Agent 串行（非 Team），每个可 HITL 暂停
        Step(executor=critic, name="critic_review"),
        Step(executor=check_pr_review_gate, name="critic_gate",
             on_error=OnError.fail),  # AC-02
        Step(executor=critic_decision_gate, name="critic_decision",
             requires_confirmation=True,
             on_reject=OnReject.cancel,
             on_error=OnError.fail),
        Step(executor=challenger, name="challenger_review"),
        Step(executor=check_pr_review_gate, name="challenger_gate",
             on_error=OnError.fail),  # AC-02
        Step(executor=challenger_decision_gate, name="challenger_decision",
             requires_confirmation=True,
             on_reject=OnReject.cancel,
             on_error=OnError.fail),

        # 5d: Merge（仅在 critic + challenger 都确认后）
        Step(executor=execute_merge, name="merge"),
    ],
    # AC-05: db 是必须的，否则 resume 语义失效。生产环境用 SqliteDb，测试用 InMemoryDb。
    db=traces_db,
)
```

### 2.4 `latest_design_content` 写入点

`session_state["latest_design_content"]` 是设计文档在 review 循环中的活跃副本，`check_design_gate` 通过时将其固化为 `approved_design`。

```python
def persist_design_output(step_input: StepInput) -> StepOutput:
    """Stage 2 完成后：将 design_team 的输出持久化到 session_state。

    design_team 的产出是 final_design.md 的完整内容（previous_step_content）。
    写入 latest_design_content 供后续 review 循环和 check_design_gate 使用。
    """
    design_content = step_input.previous_step_content
    if not design_content or len(design_content.strip()) < 50:
        raise ValueError("Design team produced empty or too-short output")
    step_input.session_state["latest_design_content"] = design_content
    return StepOutput(content=design_content)

def revise_design_from_feedback(step_input: StepInput) -> StepOutput:
    """design_review_loop 第 3 步：design_team 根据审查 feedback 修订设计。

    此步骤仅在 check_design_gate 返回 FAIL（未通过）时执行——
    当 gate 通过时 Loop 退出，不会到达此步。

    previous_step_content 是 check_design_gate 返回的 format_feedback(verdicts)，
    包含 blockers/suggestions/questions。此函数将 feedback + 当前设计
    交给 design_team 修订，再将修订后的设计写回 session_state。
    """
    feedback = step_input.previous_step_content
    current_design = step_input.session_state["latest_design_content"]

    # design_team 根据 feedback 修订设计（不是简单传递 previous_step_content）
    revised = design_team.run(
        f"请根据以下审查反馈修订设计文档。\n\n"
        f"## 当前设计\n\n{current_design}\n\n"
        f"## 审查反馈\n\n{feedback}",
        session_id=f"design_revision_{step_input.metadata['run_id']}",  # run 级隔离，不共享
    )

    revised_content = revised.content
    if not revised_content or len(revised_content.strip()) < 50:
        # 修订失败时保留原设计，让下一轮审查继续
        revised_content = current_design

    step_input.session_state["latest_design_content"] = revised_content
    return StepOutput(content=revised_content)
```

**design_review_loop 完整数据流**（每轮）：

```
1. design_review_team   读 session_state["latest_design_content"] → 返回 verdicts
2. check_design_gate    解析 verdicts → ALL APPROVED? → 退出循环（写 approved_design）
                        未通过? → 返回 format_feedback(verdicts)
3. revise_design_from_feedback
                        读 feedback + session_state["latest_design_content"]
                        → design_team 重新运行修订 → 写回 session_state["latest_design_content"]
4. Loop 重入步骤 1，design_review_team 读到修订后的设计
```

### 2.5 Work Unit Decomposition（从 approved_design 分解）

来源：Metaswarm Phase 5 — Implementation 的前提步骤，生成带 DoD、file_scope、dependencies 的 WorkUnit DAG。

```python
@dataclass
class WorkUnit:
    id: str                           # wu-001
    title: str                        # "Implement auth middleware"
    description: str                  # 详细实施说明
    dod: list[str]                    # Definition of Done 检查项
    file_scope: list[str]             # ["src/auth/*.py", "tests/test_auth.py"]
    dependencies: list[str]           # 前置 WorkUnit ID
    estimated_complexity: Literal["S", "M", "L"]
    assigned_model: str | None = None # 运行时由路由策略填充

def decompose_work_units(step_input: StepInput) -> StepOutput:
    """将 approved_design + github_issues 分解为 WorkUnit DAG

    注意：previous_step_content 来自 github_setup（仅含 setup 状态），
    approved_design 必须从 workflow session_state 中获取——由 design_review_loop
    通过 metadata 传递。
    """
    # 从 session_state 获取完整设计文档（由 Review Gate 通过时写入）
    approved_design = step_input.session_state["approved_design"]
    github_issues = step_input.session_state.get("github_issues", [])

    decompose_input = (
        f"## Approved Design\n\n{approved_design}\n\n"
        f"## GitHub Issues\n\n{json.dumps(github_issues, indent=2)}"
    )

    # Architect agent 执行分解
    decompose_agent = Agent(
        name="Decomposer",
        model=Claude(id="claude-opus-4-6"),
        instructions="""将设计文档分解为独立的 WorkUnit。每个 WorkUnit 必须包含：
        - id: 唯一标识
        - title: 简短标题
        - dod: 完成定义（可验证的检查项列表）
        - file_scope: 影响的文件范围（glob 模式），各 unit 之间不可重叠
        - dependencies: 前置 unit ID 列表（构成 DAG）
        返回 JSON 数组。""",
    )
    result = decompose_agent.run(decompose_input)
    work_units = parse_work_units(result.content)

    # 验证 file_scope 不重叠
    validate_no_overlap(work_units)
    # 验证 dependencies 构成合法 DAG（无环）
    validate_dag(work_units)

    return StepOutput(
        content=json.dumps([wu.__dict__ for wu in work_units]),
        metadata={"work_unit_count": len(work_units)},
    )

def execute_work_units(step_input: StepInput) -> StepOutput:
    """按 DAG 顺序执行 work units — 独立单元并行，依赖单元串行"""
    # json.loads 返回 dict 列表，必须反序列化回 WorkUnit 实例
    raw_units = json.loads(step_input.previous_step_content)
    work_units = [WorkUnit(**wu) for wu in raw_units]

    project = step_input.session_state.get("project_name")
    dag = build_dag(work_units)
    results = []

    for batch_idx, batch in enumerate(dag.topological_batches()):
        # 当前 batch 开始前：后续 batch 的 units 标记为 blocked
        remaining = dag.units_after_batch(batch_idx)
        for blocked_wu in remaining:
            emit_event(
                "wu_blocked",
                agent_name=f"wu_{blocked_wu.id}",
                metadata={
                    "blocked_by": [dep for dep in blocked_wu.dependencies
                                   if dep not in [r["unit_id"] for r in results]],
                    "batch_idx": batch_idx,
                },
            )

        # 同一批次内的 units 无依赖，可并行
        batch_results = parallel_execute([
            run_four_phase_loop(wu, project=project) for wu in batch
        ])
        results.extend(batch_results)

        # batch 完成后：解除该 batch units 的阻塞状态
        for wu in batch:
            emit_event(
                "wu_unblocked",
                agent_name=f"wu_{wu.id}",
            )

    return StepOutput(
        content=json.dumps(results),
        metadata={"completed_units": len(results)},
    )

def run_four_phase_loop(work_unit: WorkUnit, project: str | None = None) -> dict:
    """单个 WorkUnit 的 4 阶段循环"""
    # 在执行前解析 implementer 模型（通过 6 级解析链）
    impl_model_id = resolve_model(role="implementer", project=project)
    work_unit.assigned_model = impl_model_id

    for attempt in range(3):  # 最多 3 次重试
        # Phase 1: IMPLEMENT
        impl_result = impl_team.run(
            work_unit.description,
            session_id=f"wu_{work_unit.id}",
        )
        # Phase 2: VALIDATE (test + lint + typecheck)
        if not run_quality_gates(work_unit):
            continue
        # Phase 3: ADVERSARIAL REVIEW (双 reviewer, 跨模型)
        # 审查矩阵为每个 implementer 配两个不同 provider 的 reviewer
        reviewers = create_fresh_adversarial_reviewers(work_unit.assigned_model)
        reviews = parallel_execute([
            r.run(f"Review changes for: {work_unit.title}") for r in reviewers
        ])
        if any(has_blockers(r) for r in reviews):
            continue
        # Phase 4: COMMIT
        commit_changes(work_unit)
        return {"unit_id": work_unit.id, "status": "completed"}

    return {"unit_id": work_unit.id, "status": "escalated"}
```

### 2.6 Final Integration Review

来源：Metaswarm Phase 7 — per-work-unit 循环后、PR 创建前的跨单元集成检查。

**为什么必须有这一步**：每个 work unit 在隔离环境中局部通过了 4-phase loop，但多个 unit 合并后可能引入：
- 接口不匹配（unit A 的输出格式 ≠ unit B 的输入预期）
- 隐式状态冲突（unit A 和 B 都修改了共享配置）
- 集成级别的回归（单元测试通过但集成测试失败）

```python
# AC-04: final_integration_review 不再自行暂停 workflow。
# 它只负责执行集成审查并返回结果。暂停由 post-step Decision Gate 处理。

def final_integration_review(step_input: StepInput) -> StepOutput:
    """跨 work unit 集成审查。

    通过：返回 integration_passed=True，后续 Decision Gate step 直接放行。
    失败：返回 integration_passed=False + verdicts，后续 Decision Gate step
         通过 requires_confirmation=True 暂停等人工裁定。
    """
    ss = step_input.workflow_session.session_data  # AC-03

    all_results = json.loads(step_input.previous_step_content)

    # 1. 运行集成级别 quality gates
    integration_passed = run_integration_tests()

    # 2. 跨模型集成审查（fresh reviewer）
    integration_reviewer = Agent(
        name="IntegrationReviewer",
        model=Gemini(id="gemini-pro"),
        instructions="""审查所有 work unit 的合并结果。重点检查：
        - 接口一致性：各模块的输入输出类型是否匹配
        - 共享状态：是否有未协调的共享资源修改
        - 集成回归：合并后是否引入新的失败
        返回 GateVerdict JSON。""",
    )
    review = integration_reviewer.run(
        f"Review integration of {len(all_results)} work units"
    )
    verdict = parse_verdict(review.content)

    passed = verdict.verdict == "APPROVED" and integration_passed

    # 写入 session_data 供 post-step Decision Gate 读取
    ss["integration_passed"] = passed
    ss["integration_verdicts"] = [v.__dict__ for v in [verdict]]

    if passed:
        return StepOutput(
            content="INTEGRATION_APPROVED",
            metadata={"integration_passed": True},
        )

    # 集成失败 — 创建 DecisionGate 记录（供 Dashboard / Watchdog 使用）
    # 但不暂停 workflow，暂停由后续 Step(requires_confirmation=True) 处理（AC-04）
    gate = create_decision_gate(
        step_input, gate_type="integration", verdicts=[verdict],
        extra_context={
            "integration_tests_passed": integration_passed,
            "unit_count": len(all_results),
        },
    )
    return StepOutput(
        content=f"INTEGRATION_FAILED: {len(verdict.blockers)} blockers",
        metadata={"integration_passed": False, "gate_id": gate.id},
    )
```

**暂停/恢复机制（AC-04 更新）**：所有 Decision Gate 场景（plan_review / design_review / integration / pr_review / pr_merge）统一使用 Agno 原生 `Step(requires_confirmation=True, on_reject=OnReject.cancel)` 暂停 workflow。gate check 函数负责检查结果并创建 DecisionGate 记录，但**不自行暂停**——暂停由紧跟其后的独立 Decision Gate Step 处理。详见 §10.4 和 §4.6。

### 2.7 Stage 5: PR Lifecycle 实现

Stage 5 展开为 7 个串行 Steps：PR Shepherd → Persist PR Context → Critic Review → Critic Gate → Challenger Review → Challenger Gate → Merge。

```python
# ── 5a: PR Shepherd（自治状态机，详见 5.2 节）──

pr_shepherd = Agent(
    name="PRShepherd",
    model=Claude(id=resolve_model(role="pr_shepherd")),
    role="PR lifecycle manager",
    instructions="""你是 PR 生命周期管理者。职责：
    1. 创建 PR（git push + gh pr create）
    2. 监控 CI 状态（每 60s 轮询）
    3. 自动修复简单 CI 失败（lint / typecheck / 测试中 agent 自身代码的错误）
    4. 转发审查评论给相关 agent
    5. 当 CI 绿且无 pending review 时，标记 PR 为 ready
    输出：PR URL + ready 状态。""",
    tools=[github_tools],
    db=traces_db,
)

# ── 5b: 持久化 PR 上下文 ──

def persist_pr_context(step_input: StepInput) -> StepOutput:
    """将 PR Shepherd 产出的 PR URL 写入 session_state。

    pr_shepherd 的输出（previous_step_content）包含 PR URL。
    解析后写入 session_state["pr_url"] 供 execute_merge 使用。
    如果解析失败（空或无效），抛出阻塞错误而非默默传空字符串。
    """
    output = step_input.previous_step_content
    pr_url = extract_pr_url(output)  # 正则提取或 JSON 解析

    if not pr_url or not pr_url.startswith("https://"):
        raise ValueError(
            f"PR Shepherd did not produce a valid PR URL. "
            f"Output was: {output[:200]}..."
        )

    step_input.session_state["pr_url"] = pr_url
    return StepOutput(content=output)

# ── 5c: Critic + Challenger + Gate Check ──
# Agno workaround: HITL 在 Team/Workflow 层 "near future"，
# 所以不用 TeamMode.tasks，而是作为独立 Workflow Step 串行执行。
# 每个 reviewer 后面跟一个 check_pr_review_gate 消费其 verdict。

# 工具隔离：reviewer 只能读 PR diff，不能写/merge
github_readonly_tools = github_tools.readonly()  # 仅 pr_diff / pr_comments / ci_status

critic = Agent(
    name="Critic",
    model=Gemini(id="gemini-2.5-pro"),  # 跨模型审查
    role="Code reviewer — correctness & design",
    instructions="""审查 PR diff。关注：
    - 正确性：逻辑错误、边界条件
    - 设计：是否符合 approved_design
    - 安全：OWASP Top 10 检查
    返回 GateVerdict JSON。""",
    tools=[github_readonly_tools],
    db=traces_db,
)

challenger = Agent(
    name="Challenger",
    model=OpenAI(id="codex-gpt-5.3"),  # 不同 provider
    role="Code reviewer — robustness & edge cases",
    instructions="""从对抗视角审查 PR diff。关注：
    - 遗漏的边界条件和竞态条件
    - 错误处理覆盖度
    - 性能退化风险
    返回 GateVerdict JSON。""",
    tools=[github_readonly_tools],
    db=traces_db,
)

def check_pr_review_gate(step_input: StepInput) -> StepOutput:
    """程序化消费 reviewer 的 GateVerdict。

    此函数跟在每个 reviewer 后面（critic_gate / challenger_gate），
    解析 previous_step_content 中的 GateVerdict JSON：
    - APPROVED → 通过，继续下一步
    - NEEDS_REVISION → 创建 DecisionGate 记录，返回 gate_passed=False

    AC-04: 不自行暂停 workflow。暂停由紧跟其后的
    Step(requires_confirmation=True, on_reject=OnReject.cancel) 处理。
    """
    ss = step_input.workflow_session.session_data  # AC-03
    reviewer_step = step_input.metadata.get("step_name", "pr_review")

    verdict = parse_verdict(step_input.previous_step_content)

    if verdict.verdict == "APPROVED":
        ss[f"{reviewer_step}_passed"] = True
        return StepOutput(content="GATE_PASSED", metadata={"gate_passed": True})

    # reviewer 不通过 → 创建 DecisionGate 记录（供 Dashboard / Watchdog）
    gate = create_decision_gate(
        step_input, gate_type="pr_review", verdicts=[verdict],
        extra_context={"reviewer_step": reviewer_step},
    )
    ss[f"{reviewer_step}_passed"] = False
    return StepOutput(
        content=f"GATE_FAILED: {verdict.blockers}",
        metadata={"gate_passed": False, "gate_id": gate.id},
    )

# ── 5c: Execute Merge ──

def execute_merge(step_input: StepInput) -> StepOutput:
    """Critic 和 Challenger 都通过后，执行最终合并。

    AC-04: 合并确认由前置 Decision Gate Step(requires_confirmation=True) 处理。
    此函数在 Decision Gate 确认后执行，只负责实际合并操作。
    """
    ss = step_input.workflow_session.session_data  # AC-03

    # 防御性校验 — checkpoint 恢复或手动调用可能丢失 session_data
    pr_url = ss.get("pr_url", "")
    if not pr_url or not pr_url.startswith("https://"):
        raise ValueError(f"execute_merge: missing or invalid pr_url in session_data: '{pr_url}'")

    github_merge(pr_url)
    emit_event("merge_attempt", pr_url=pr_url, result="success")
    return StepOutput(content=f"Merged: {pr_url}")
```

---

## 3. Agent 定义与多模型配置

### 3.1 Agent 角色体系

| Agent | 角色 | 默认模型 | TeamMode | 阶段 |
|-------|------|---------|----------|------|
| **Scout** | 代码库探索 + 先例研究 | claude-haiku-4-5 | — | Research |
| **Architect** | 系统设计 + 规格生成 | claude-opus-4-6 | coordinate | Design |
| **Specialist** | 领域专家（动态加载） | claude-sonnet-4-6 | coordinate | Design |
| **Plan Critic** | 可行性/完整性/范围对抗审查 | gemini-pro | broadcast | Review |
| **Design Expert** | PM/安全/架构/UX 领域审查 | 按角色配置 | broadcast | Review |
| **Implementer** | 编码执行 | codex / claude-sonnet | route | Impl |
| **Adversarial Reviewer** | 跨模型代码审查（Fresh Instance） | 与 Implementer 不同模型 | — | Impl |
| **PR Shepherd** | PR 生命周期自治管理 | claude-sonnet-4-6 | — | PR |
| **Watchdog** | 健康监控 + AI Triage | claude-haiku-4-5 | — | 全局 |

### 3.2 多模型配置

综合 Composio 6 级解析链、Metaswarm 成本优先路由、Overstory per-capability 分配：

```yaml
# orchestra.yaml
models:
  global_default: claude-sonnet-4-6

  # L4: 全局 role 默认模型
  roles:
    architect:     claude-opus-4-6       # 高价值设计决策
    plan_critic:   gemini-pro            # 跨模型对抗审查
    design_expert: claude-sonnet-4-6     # 领域审查
    implementer:   codex-gpt-5.3         # 最低成本编码
    scout:         claude-haiku-4-5      # 轻量探索
    adv_reviewer:  gemini-pro            # 跨模型代码审查
    pr_shepherd:   claude-sonnet-4-6     # PR 管理
    watchdog:      claude-haiku-4-5      # 健康监控（低成本高频）

  # L3: 项目级覆盖 — 嵌套结构与 resolve_model() 一致
  projects:
    cadence:
      # L3a: 项目级 role 覆盖（对应 Composio "角色级项目配置"）
      roles:
        implementer: claude-sonnet-4-6   # Rust/eBPF 需要更强的模型
        adv_reviewer: claude-opus-4-6    # 安全敏感代码用更强审查
      # L3b: 项目级 agent 默认（对应 Composio "项目级 agent"）
      default: claude-sonnet-4-6

  # 路由策略
  routing:
    strategy: cheapest-available         # cheapest-available | fastest | highest-quality
    escalation: [codex, gemini, claude]  # 失败升级链

  # 预算断路器
  budget:
    per_task_usd: 2.00
    per_session_usd: 20.00
    alert_threshold: 0.8                 # 80% 预算时告警
```

**6 级解析优先级**（高→低，来源：Composio `agent-selection.ts:29-51`）：

| 级别 | 来源 | YAML 路径 | 应用场景 |
|------|------|----------|---------|
| L1 | 持久化 agent 配置 | session 元数据 | agent 失败重试时保持同一模型 |
| L2 | 生成时 override | `--model` CLI flag | 运行时临时覆写（调试/测试） |
| L3a | 项目级 role config | `projects.{name}.roles.{role}` | 项目特定角色需不同模型 |
| L3b | 项目级 agent 默认 | `projects.{name}.default` | 项目整体偏好 |
| L4 | 全局 role config | `roles.{role}` | 角色级默认模型 |
| L5 | global_default | `global_default` | 兜底默认 |
| L6 | 硬编码 fallback | — | 配置缺失时的安全网 |

```python
def resolve_model(
    role: str,
    project: str | None = None,
    spawn_override: str | None = None,
    persisted_model: str | None = None,
) -> str:
    """6 级模型解析链 — YAML 路径与此函数严格对应"""
    # L1: 持久化 agent 配置（确保重试一致性）
    if persisted_model:
        return persisted_model
    # L2: 生成时 override
    if spawn_override:
        return spawn_override
    # L3a: 项目级 role config — 读 projects.{project}.roles.{role}
    if project and project in config.projects:
        proj_cfg = config.projects[project]
        proj_role_model = proj_cfg.roles.get(role) if proj_cfg.roles else None
        if proj_role_model:
            return proj_role_model
        # L3b: 项目级 agent 默认 — 读 projects.{project}.default
        if proj_cfg.default:
            return proj_cfg.default
    # L4: 全局 role config — 读 roles.{role}
    role_model = config.roles.get(role)
    if role_model:
        return role_model
    # L5: global_default
    if config.global_default:
        return config.global_default
    # L6: 硬编码 fallback
    return "claude-sonnet-4-6"
```

### 3.3 跨模型审查矩阵

写者与审查者永远使用不同模型（来源：Metaswarm）：

| Implementer 模型 | Adversarial Reviewer 1 | Adversarial Reviewer 2 |
|------------------|----------------------|----------------------|
| Codex (OpenAI)   | Gemini (Google)      | Claude (Anthropic)   |
| Gemini (Google)  | Codex (OpenAI)       | Claude (Anthropic)   |
| Claude (Anthropic)| Codex (OpenAI)      | Gemini (Google)      |

```python
def resolve_adversarial_reviewer(implementer_model: str) -> list[str]:
    """确保审查者与实现者使用不同模型 provider"""
    REVIEW_MATRIX = {
        "openai":    ["gemini-pro", "claude-sonnet-4-6"],
        "google":    ["codex-gpt-5.3", "claude-sonnet-4-6"],
        "anthropic": ["codex-gpt-5.3", "gemini-pro"],
    }
    provider = get_provider(implementer_model)
    return REVIEW_MATRIX[provider]
```

### 3.4 Callable Factory 动态角色加载

Agno 原生支持 `members` 参数为 callable，在每次 run 时动态解析（来源：Agno F7 修正）：

```python
def resolve_design_members(team: Team, session_state: dict) -> list[Agent]:
    """根据项目 tags 动态加载领域专家"""
    project_tags = session_state.get("project_tags", [])
    members = [
        Agent(name="Architect", model=Claude(id="claude-opus-4-6"),
              role="Chief Architect", instructions=load_role("architect")),
    ]
    # 条件加载领域专家
    tag_to_specialist = {
        "ebpf":     ("Probe",   "eBPF Domain Expert"),
        "frontend": ("Artisan", "Frontend Expert"),
        "data":     ("Oracle",  "Data & Validation Expert"),
        "security": ("Sentinel","Security Expert"),
    }
    for tag, (name, role) in tag_to_specialist.items():
        if tag in project_tags:
            members.append(Agent(
                name=name, model=Claude(id="claude-sonnet-4-6"),
                role=role, instructions=load_role(name.lower()),
            ))
    return members

design_team = Team(
    name="Design Squad",
    model=Claude(id="claude-sonnet-4-6"),
    members=resolve_design_members,  # callable factory
    mode=TeamMode.coordinate,
    db=traces_db,
)
```

---

## 4. Review Gate 系统

### 4.1 双层 Gate 架构

来源：Metaswarm `skills/plan-review-gate/` + `skills/design-review-gate/`

**原则：质量门控是阻塞性状态转换，非建议性——FAIL = 重试或升级，绝不跳过。**

```
                 Plan Review Gate              Design Review Gate
                 ┌─────────────┐              ┌──────────────────┐
                 │ 3 对抗审查者  │              │ 5-6 领域专家       │
final_design.md →│ • 可行性     │  ALL PASS →  │ • PM             │→ approved_design
                 │ • 完整性     │              │ • Architect       │
                 │ • 范围对齐   │              │ • Security        │
                 │              │              │ • UX              │
                 │ Max 3 轮     │              │ • CTO (可选)      │
                 │ → 人工升级    │              │                  │
                 └─────────────┘              │ Max 3 轮          │
                                             │ → 人工升级         │
                                             └──────────────────┘
```

### 4.2 Gate 判决协议

每个审查者返回结构化 JSON：

```python
class GateVerdict(BaseModel):
    reviewer: str
    verdict: Literal["PASS", "FAIL"]           # Plan Gate
    # 或
    verdict: Literal["APPROVED", "NEEDS_REVISION"]  # Design Gate
    blockers: list[str]        # 必须修复
    suggestions: list[str]     # 建议改进
    questions: list[str]       # 需要澄清
    # Design Gate 类型特定字段
    threat_model: str | None = None       # Security reviewer
    use_case_analysis: str | None = None  # PM reviewer
    quality_score: float                  # 0.0-1.0
```

### 4.3 Agno 实现

> **Gate 0 修正 (AC-01, AC-02, AC-04)**：gate check 函数不再 `raise WorkflowPauseRequest`。
> 人工升级由 Loop 后独立的 `decision_gate_step(requires_confirmation=True)` 处理。
> Loop 退出使用 `end_condition` callable（AC-01），gate step 使用 `on_error=OnError.fail`（AC-02）。
> `requires_confirmation` 在 Loop 内被静默忽略（AC-04），因此 Decision Gate 必须在 Loop 外。

```python
# Plan Review Gate — broadcast 模式，所有审查者同时收到 final_design.md
plan_review_team = Team(
    name="Plan Review Gate",
    model=Claude(id="claude-sonnet-4-6"),
    members=[
        Agent(name="Feasibility Critic", model=Gemini(id="gemini-pro"),
              instructions="评估计划可行性，返回 GateVerdict JSON"),
        Agent(name="Completeness Critic", model=OpenAI(id="codex-mini"),
              instructions="评估计划完整性，返回 GateVerdict JSON"),
        Agent(name="Scope Critic", model=Claude(id="claude-haiku-4-5"),
              instructions="评估范围对齐，返回 GateVerdict JSON"),
    ],
    mode=TeamMode.broadcast,  # 并行审查
    db=traces_db,
)

def check_plan_gate(step_input: StepInput) -> StepOutput:
    """检查 Plan Review Gate — ALL must PASS.

    在 review Loop 内作为 gate check step 执行。结果写入 session_data
    供 end_condition 判断退出和 post-Loop Decision Gate 使用。

    不再 raise WorkflowPauseRequest — 人工升级由 Loop 后独立的
    decision_gate_step(requires_confirmation=True) 处理 (AC-04)。
    """
    verdicts = parse_verdicts(step_input.previous_step_content)
    all_pass = all(v.verdict == "PASS" for v in verdicts)
    round_num = get_ss(step_input, "plan_review_round", 0) + 1  # AC-03 helper
    set_ss(step_input, "plan_review_round", round_num)

    if all_pass:
        set_ss(step_input, "plan_gate_passed", True)
        return StepOutput(content="GATE_PASSED", metadata={"gate_passed": True})

    # 未通过 — 存储反馈供 revise_plan_from_feedback 使用
    set_ss(step_input, "plan_gate_passed", False)
    set_ss(step_input, "plan_gate_verdicts", [v.dict() for v in verdicts])
    return StepOutput(
        content=format_feedback(verdicts),
        metadata={"gate_passed": False, "round": round_num},
    )


# Loop end_condition — 读取 step outputs 判断是否通过 (AC-01)
def plan_review_approved(step_outputs: list[StepOutput]) -> bool:
    """end_condition for plan_review_loop. 禁止使用 StepOutput(stop=True)。"""
    return any(o.metadata.get("gate_passed") is True for o in step_outputs)


# Post-Loop Decision Gate pattern (AC-04):
#
#   Loop(plan_review_loop, max_iterations=3, end_condition=plan_review_approved)
#   → Step(check_plan_review_result)    # 读 session_data 判断是否需要人工
#   → Step(plan_decision_gate,          # 仅在未通过时触发
#          requires_confirmation=True,
#          on_reject=OnReject.cancel,
#          on_error=OnError.fail)        # AC-02

def check_plan_review_result(step_input: StepInput) -> StepOutput:
    """Post-Loop step: 检查 plan review 是否通过。
    若已通过，直接放行；若未通过，创建 DecisionGate 供下一步暂停使用。"""
    if get_ss(step_input, "plan_gate_passed"):
        return StepOutput(content="PLAN_REVIEW_PASSED")

    # 未通过 — 创建 DecisionGate 记录
    verdicts = [GateVerdict(**v) for v in get_ss(step_input, "plan_gate_verdicts", [])]
    gate = create_decision_gate(step_input, gate_type="plan_review", verdicts=verdicts)
    set_ss(step_input, "pending_decision_gate_id", gate.id)
    round_num = get_ss(step_input, "plan_review_round", 0)
    return StepOutput(content=f"PLAN_REVIEW_FAILED_AFTER_{round_num}_ROUNDS")
```

### 4.4 check_design_gate 实现

Design Review Gate 是 `approved_design` 的唯一写入点。Plan Review Gate 验证计划可行性，Design Review Gate 验证设计完整性——**只有 design gate 通过后，最终设计文档才确认为 approved**。

```python
def check_design_gate(step_input: StepInput) -> StepOutput:
    """检查 Design Review Gate — ALL must APPROVE.

    这是 approved_design 写入 session_data 的唯一位置。
    Plan gate 通过的是计划可行性，design gate 通过的才是最终设计。

    与 check_plan_gate 相同的 post-Loop Decision Gate 模式 (AC-04)。
    """
    verdicts = parse_verdicts(step_input.previous_step_content)
    all_approved = all(v.verdict == "APPROVED" for v in verdicts)
    round_num = get_ss(step_input, "design_review_round", 0) + 1  # AC-03 helper
    set_ss(step_input, "design_review_round", round_num)

    if all_approved:
        # Design gate 通过 — 将最终设计写入 session_state
        set_ss(step_input, "approved_design", get_ss(step_input, "latest_design_content", ""))
        set_ss(step_input, "design_gate_passed", True)
        return StepOutput(content="GATE_PASSED", metadata={"gate_passed": True})

    # 未通过 — 存储反馈供 revise_design_from_feedback 使用
    set_ss(step_input, "design_gate_passed", False)
    set_ss(step_input, "design_gate_verdicts", [v.dict() for v in verdicts])
    return StepOutput(
        content=format_feedback(verdicts),
        metadata={"gate_passed": False, "round": round_num},
    )


def design_review_approved(step_outputs: list[StepOutput]) -> bool:
    """end_condition for design_review_loop (AC-01)."""
    return any(o.metadata.get("gate_passed") is True for o in step_outputs)


def check_design_review_result(step_input: StepInput) -> StepOutput:
    """Post-Loop step: 检查 design review 是否通过。
    若未通过，创建 DecisionGate 供下一步暂停使用。
    与 check_plan_review_result 对称。"""
    if get_ss(step_input, "design_gate_passed"):
        return StepOutput(content="DESIGN_REVIEW_PASSED")

    verdicts = [GateVerdict(**v) for v in get_ss(step_input, "design_gate_verdicts", [])]
    gate = create_decision_gate(step_input, gate_type="design_review", verdicts=verdicts)
    set_ss(step_input, "pending_decision_gate_id", gate.id)
    round_num = get_ss(step_input, "design_review_round", 0)
    return StepOutput(content=f"DESIGN_REVIEW_FAILED_AFTER_{round_num}_ROUNDS")
```

**`approved_design` 生命周期**：
1. Design Team 产出 `final_design.md` → 写入 `session_data["latest_design_content"]`
2. Plan Review Gate 验证计划可行性（不写 `approved_design`）
3. Design Review Gate 验证设计完整性 → **通过时写入 `session_data["approved_design"]`**
4. `github_setup` 和 `decompose_work_units` 从 `session_data["approved_design"]` 读取

### 4.5 create_decision_gate() 实现

所有 gate（plan_review / design_review / integration / pr_review / pr_merge）共用此函数创建 DecisionGate 对象。

```python
def create_decision_gate(
    step_input: StepInput,
    gate_type: str,
    verdicts: list[GateVerdict],
    extra_context: dict | None = None,
) -> DecisionGate:
    """创建 Decision Gate 并持久化到 events_db。

    调用者：check_plan_review_result / check_design_review_result /
           final_integration_review / check_pr_review_gate / execute_merge。
    创建后由 post-Loop decision_gate_step(requires_confirmation=True)
    触发 Agno 原生 HITL 暂停 (AC-04)。不再使用 WorkflowPauseRequest。

    extra_context: 调用方特有的上下文（如 reviewer_step、pr_url），
                   会合并到 gate.context 中，一次性持久化，
                   避免先 save 再 update 导致的短暂不一致。
    """
    agent_id = step_input.metadata.get(
        "agent_id",
        step_input.metadata.get("step_name", f"gate:{gate_type}"),
    )
    context = {
        "round": step_input.metadata.get("round", 0),
        "blockers": [b for v in verdicts for b in v.blockers],
        "verdicts": [v.dict() for v in verdicts],
    }
    if extra_context:
        context.update(extra_context)

    gate = DecisionGate(
        id=f"dg-{uuid4().hex[:12]}",
        workflow_run_id=step_input.metadata["run_id"],
        agent_id=agent_id,
        gate_type=gate_type,
        created_at=datetime.utcnow(),
        context=context,
    )
    events_db.save_decision_gate(gate)
    emit_event("decision_gate_created", gate_id=gate.id, gate_type=gate_type)
    return gate
```

### 4.6 Decision Gate 协议

来源：Overstory `src/types.ts:339-347` + Agno F6 HITL 修正

Decision Gate 是一个**可持久化、可观测的协议对象**，而非简单的元数据标记。它连接 Review Gate 的人工升级、Watchdog 的 Decision Gate 感知、以及 REST API 人工审批的暂停/恢复（Agno 原生 `requires_confirmation` + `resolve_gate`）。

#### 存储模型

```python
class DecisionGateStatus(Enum):
    PENDING = "pending"       # 等待人工决策
    APPROVED = "approved"     # 人工批准继续
    REJECTED = "rejected"     # 人工否决
    EXPIRED = "expired"       # 超时自动过期
    OVERRIDE = "override"     # 人工强制覆盖（跳过门控）

class DecisionGate(BaseModel):
    """持久化于 events.db 的 decision_gates 表"""
    id: str                                    # dg-{uuid}
    workflow_run_id: str                       # 关联的 workflow run
    agent_id: str                              # 被阻塞的 agent
    gate_type: Literal[
        "plan_review",      # Stage 3: Plan Review Gate 3 轮后升级
        "design_review",    # Stage 3: Design Review Gate 3 轮后升级
        "integration",      # Stage 4d: Final Integration Review 失败
        "pr_review",        # Stage 5c: Critic 或 Challenger 审查未通过
        "pr_merge",         # Stage 5d: 最终合并需人工确认
    ]
    status: DecisionGateStatus = DecisionGateStatus.PENDING
    created_at: datetime
    resolved_at: datetime | None = None
    resolver: str | None = None               # 人工审批者标识
    context: dict                              # 升级原因、blocker 列表、历史轮次
    ttl_minutes: int = 480                     # 8 小时后自动过期
```

#### SQLite Schema

```sql
CREATE TABLE decision_gates (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    gate_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolver TEXT,
    context TEXT,  -- JSON
    ttl_minutes INTEGER DEFAULT 480
);
CREATE INDEX idx_dg_status ON decision_gates(status, created_at);
CREATE INDEX idx_dg_agent ON decision_gates(agent_id, status);
```

#### 生命周期

```
创建（gate check 升级人工）
  │
  ▼
PENDING ──→ APPROVED (人工批准) ──→ workflow 继续
  │              
  ├──→ REJECTED (人工否决) ──→ workflow 终止 + 通知
  │
  ├──→ OVERRIDE (人工强制) ──→ workflow 跳过门控继续
  │
  └──→ EXPIRED (TTL 到期) ──→ 通知 + 自动关闭 workflow run
```

#### TTL Reaper（过期 gate 自动关闭）

Watchdog `monitor_loop` 每轮额外扫描过期 gate：

```python
async def reap_expired_gates(self):
    """扫描 TTL 到期的 pending gate，标记为 EXPIRED 并 abort workflow。"""
    expired = events_db.execute(
        "SELECT * FROM decision_gates WHERE status = 'pending' "
        "AND datetime(created_at, '+' || ttl_minutes || ' minutes') < datetime('now')",
    ).fetchall()
    for gate_row in expired:
        gate = DecisionGate(**gate_row)
        gate.status = DecisionGateStatus.EXPIRED
        gate.resolved_at = datetime.utcnow()
        events_db.update_decision_gate(gate)
        emit_event("decision_gate_resolved", gate_id=gate.id, action="expired")
        workflow_engine.abort(gate.workflow_run_id, reason=f"Decision gate {gate.id} expired after {gate.ttl_minutes}m")
```

此方法在 `monitor_loop` 每轮末尾调用（与 agent 健康检查同频率）。

#### 与 Watchdog 集成

```python
def has_pending_decision_gate(agent_id: str, workflow_run_id: str | None = None) -> bool:
    """查询 events_db 而非内存标记 — Watchdog（7.2）和 ActivityState（8.2）共用。

    按 agent_id 或 workflow_run_id 匹配，覆盖两种场景：
    - agent_id 直接匹配（per-agent 级别的 gate）
    - workflow_run_id 匹配当前 agent 所属的 run（workflow 级别的 gate）

    同步函数——events_db 查询不需要 async。
    """
    if workflow_run_id:
        gates = events_db.execute(
            "SELECT id FROM decision_gates "
            "WHERE (agent_id = ? OR workflow_run_id = ?) AND status = 'pending'",
            (agent_id, workflow_run_id),
        ).fetchall()
    else:
        gates = events_db.execute(
            "SELECT id FROM decision_gates "
            "WHERE agent_id = ? AND status = 'pending'",
            (agent_id,),
        ).fetchall()
    return len(gates) > 0
```

#### 与 Gate Check 集成

Gate step 在需要人工升级或确认时使用 Agno 原生 `Step(requires_confirmation=True, on_reject=OnReject.cancel)` 暂停 workflow。触发条件因场景而异：plan/design review 是 3 轮 Loop 后 post-Loop Decision Gate 暂停，pr_review 是 reviewer verdict 不通过后 Decision Gate 暂停，pr_merge 是默认要求人工最终确认。

> **AC-04 约束**：所有 Decision Gate 必须放在 Loop **之后**，不能放在 Loop 内。Loop 内 `requires_confirmation` 被静默忽略。

所有场景遵循同一暂停/恢复模式：

1. **Loop 执行**：review 循环通过 `end_condition` 退出（AC-01），或达到 `max_iterations`
2. **Post-Loop check**：检查 Loop 结果——通过则 Decision Gate step 直接返回成功
3. **Decision Gate 暂停**：未通过 → `Step(requires_confirmation=True)` 暂停等待人工确认
4. **人工审批**：通过 REST API resolve gate → `on_reject=OnReject.cancel` 终止或确认继续
5. **Resume**：workflow 从 Decision Gate step 继续执行后续 Stage

#### 人工恢复路径

```python
# AgentOS REST API 暴露 Decision Gate 操作
@app.post("/decision-gates/{gate_id}/resolve")
async def resolve_gate(gate_id: str, action: DecisionGateStatus, resolver: str):
    gate = events_db.get_decision_gate(gate_id)       # ← events_db
    gate.status = action
    gate.resolved_at = datetime.utcnow()
    gate.resolver = resolver
    events_db.update_decision_gate(gate)               # ← events_db
    emit_event("decision_gate_resolved", gate_id=gate_id, action=action)

    if action in (DecisionGateStatus.APPROVED, DecisionGateStatus.OVERRIDE):
        # APPROVED: 审查通过后恢复；OVERRIDE: 人工强制跳过门控
        workflow_engine.resume(gate.workflow_run_id)
    elif action == DecisionGateStatus.REJECTED:
        workflow_engine.abort(gate.workflow_run_id, reason="Human rejected")
```

### 4.7 Fresh Reviewer 规则

来源：Metaswarm — 对抗审查者永远是全新实例，防止锚定偏差：

```python
def create_fresh_adversarial_reviewers(implementer_model_id: str) -> list[Agent]:
    """创建两个 fresh reviewer 实例，对应审查矩阵的 Reviewer 1 和 Reviewer 2。

    参数 implementer_model_id 是实际的模型 ID（如 "codex-gpt-5.3"），
    不是角色名。直接查审查矩阵获取两个 reviewer 的模型 ID，
    用 instantiate_model() 构造——不经过 resolve_model()（角色解析链）。
    跨模型审查的模型选择完全由审查矩阵决定，不受配置层级影响。
    """
    reviewer_model_ids = resolve_adversarial_reviewer(implementer_model_id)
    return [
        Agent(
            name=f"AdversarialReviewer{i+1}-{uuid4().hex[:8]}",
            model=instantiate_model(model_id),
            instructions=load_rubric("adversarial_code_review"),
            # 无 session_id，无 learning — 完全无状态
        )
        for i, model_id in enumerate(reviewer_model_ids)
    ]

def instantiate_model(model_id: str):
    """从模型 ID 构造 Agno Model 对象"""
    MODEL_REGISTRY = {
        "claude-opus-4-6":    lambda: Claude(id="claude-opus-4-6"),
        "claude-sonnet-4-6":  lambda: Claude(id="claude-sonnet-4-6"),
        "claude-haiku-4-5":   lambda: Claude(id="claude-haiku-4-5"),
        "gemini-pro":         lambda: Gemini(id="gemini-2.5-pro"),
        "codex-gpt-5.3":     lambda: OpenAI(id="codex-gpt-5.3"),
    }
    factory = MODEL_REGISTRY.get(model_id)
    if not factory:
        raise ValueError(f"Unknown model ID: {model_id}")
    return factory()
```

---

## 5. Reaction Engine（事件驱动）

来源：Composio `lifecycle-manager.ts` + Metaswarm `skills/pr-shepherd/SKILL.md`

### 5.1 事件-动作映射

```python
from dataclasses import dataclass
from enum import Enum

class EventType(Enum):
    CI_FAILED = "ci_failed"
    CHANGES_REQUESTED = "changes_requested"
    BUGBOT_COMMENTS = "bugbot_comments"
    MERGE_CONFLICTS = "merge_conflicts"
    APPROVED_AND_GREEN = "approved_and_green"
    AGENT_IDLE = "agent_idle"
    AGENT_STUCK = "agent_stuck"
    AGENT_ERRORED = "agent_errored"
    STALE_PR = "stale_pr"

@dataclass
class Reaction:
    event: EventType
    action: str                # 动作描述
    retries: int = 2           # 最大重试次数
    escalate_after_min: int = 30   # 超时升级（分钟）
    cooldown_sec: int = 60     # 防重复触发冷却
    fingerprint_fn: str | None = None  # 去重指纹函数

REACTIONS: list[Reaction] = [
    Reaction(EventType.CI_FAILED,
             action="forward_ci_details_to_agent",
             retries=2, escalate_after_min=30),
    Reaction(EventType.CHANGES_REQUESTED,
             action="forward_review_comments",
             fingerprint_fn="sorted_comment_ids"),  # 指纹去重
    Reaction(EventType.BUGBOT_COMMENTS,
             action="forward_bot_review"),
    Reaction(EventType.MERGE_CONFLICTS,
             action="notify_agent_rebase"),
    Reaction(EventType.APPROVED_AND_GREEN,
             action="auto_merge_or_notify",
             retries=1),
    Reaction(EventType.AGENT_IDLE,
             action="nudge_agent",
             escalate_after_min=10),
    Reaction(EventType.AGENT_STUCK,
             action="escalate_to_human",
             escalate_after_min=30),
    Reaction(EventType.AGENT_ERRORED,
             action="ai_triage_then_retry_or_terminate"),
    Reaction(EventType.STALE_PR,
             action="nudge_pr_shepherd",
             escalate_after_min=60, cooldown_sec=300),
]
```

### 5.2 PR Shepherd 状态机

来源：Metaswarm PR Shepherd — 自治 CI 监控 + 自动修复

```
        ┌──────────────────────────────────┐
        │                                  │
        ▼                                  │
  MONITORING ──→ FIXING ──→ HANDLING_REVIEWS
       │              │              │
       │              └──→ MONITORING│
       │                             │
       ├──→ WAITING_FOR_USER ←───────┘
       │         │
       │         ▼
       └──→   DONE
```

- **MONITORING**: GTG（Good-To-Go）检查，每 60 秒轮询
- **FIXING**: 自动修复简单问题（lint 错误、类型错误、agent 代码测试失败）
- **HANDLING_REVIEWS**: 转发审查评论给 agent，等待修复
- **WAITING_FOR_USER**: 超过 agent 能力范围，等待人工
- **DONE**: PR merged 或 closed
- **软超时**: 4 小时，触发检查点保存

---

## 6. Merge Queue（冲突解决）

来源：Overstory `src/merge/resolver.ts` — 4 层渐进升级

### 6.1 4 层解冲突管道

```python
class MergeResolver:
    """4-tier escalation merge conflict resolver with historical learning"""

    def __init__(self, merge_db: SqliteDb):
        self.merge_db = merge_db  # 冲突历史存储在 merge_db（见 10.1）

    def resolve(self, source_branch: str, target_branch: str) -> MergeResult:
        # Tier 1: Clean merge
        result = self._try_clean_merge(source_branch, target_branch)
        if result.success:
            return result

        # Tier 2: Auto-resolve (parse conflict markers, keep incoming)
        # Skip if canonical side has meaningful content
        result = self._try_auto_resolve(result.conflicts)
        if result.success:
            return result

        # Tier 3: AI-resolve (per conflicted file)
        # Validate output is code, not prose
        if self._should_skip_tier(source_branch, "ai_resolve"):
            pass  # 历史学习：跳过该文件历史上失败的 tier
        else:
            result = self._try_ai_resolve(result.conflicts)
            if result.success:
                return result

        # Tier 4: Re-imagine (abort merge, reimplement from scratch)
        result = self._try_reimagine(source_branch, target_branch)
        self._record_pattern(source_branch, result)  # 记录供未来学习
        return result

    def _should_skip_tier(self, branch: str, tier: str) -> bool:
        """查询历史：该文件在该 tier 是否反复失败"""
        history = self.merge_db.query_merge_history(branch, tier)
        return history.failure_rate > 0.8  # 80% 以上失败则跳过
```

### 6.2 File Scoping

每个 agent 的文件范围互不重叠，减少冲突概率：

```python
# 简化示意结构——完整 WorkUnit 定义见 2.5 节
# 此处仅展示 validate_no_overlap 所需的字段子集
@dataclass
class WorkUnit:
    id: str
    file_scope: list[str]  # ["src/auth/*.py", "tests/test_auth.py"]
    dependencies: list[str]  # 其他 WorkUnit ID

def validate_no_overlap(work_units: list[WorkUnit]) -> None:
    """确保 work units 文件范围不重叠。

    两层检查：
    1. 模式级：检测两个 unit 是否声明了相同的模式或互相匹配的模式
       （捕获对尚未存在的新文件的重叠声明）
    2. 文件级：对已存在的文件做 glob 展开检查实际重叠
       （捕获模式不同但匹配同一文件的情况，如 "src/*.py" vs "src/auth.py"）
    """
    # Layer 1: 模式级去重（不依赖文件系统，能发现新文件冲突）
    all_patterns: dict[str, str] = {}  # pattern → wu.id
    for wu in work_units:
        for pattern in wu.file_scope:
            # 精确模式重复
            if pattern in all_patterns:
                raise FileOverlapError(
                    f"Pattern '{pattern}' claimed by both "
                    f"{all_patterns[pattern]} and {wu.id}")
            # 交叉匹配：现有模式是否匹配新模式（或反之）
            for existing_pattern, existing_wu_id in all_patterns.items():
                if fnmatch(pattern, existing_pattern) or fnmatch(existing_pattern, pattern):
                    raise FileOverlapError(
                        f"Patterns overlap: '{existing_pattern}' ({existing_wu_id}) "
                        f"vs '{pattern}' ({wu.id})")
            all_patterns[pattern] = wu.id

    # Layer 2: 文件级展开（补充捕获模式不同但匹配同一已存在文件的情况）
    all_files: dict[str, str] = {}  # resolved_file → wu.id
    for wu in work_units:
        for pattern in wu.file_scope:
            for f in glob(pattern):
                if f in all_files:
                    raise FileOverlapError(
                        f"File '{f}' matched by both "
                        f"{all_files[f]} and {wu.id}")
                all_files[f] = wu.id
```

---

## 7. Watchdog（健康监控）

来源：Overstory `src/watchdog/daemon.ts` — 3 层渐进升级 + ZFC 原则

### 7.1 设计原则

- **ZFC 原则**：可观测状态（进程存活、RPC 响应）是事实来源，非数据库记录状态
- **Decision Gate 感知**：跳过等待人工决策的 agent
- **故障记录**：写入知识库供未来学习

### 7.2 3 层架构

```python
class WatchdogDaemon:
    """3-tier progressive escalation watchdog"""

    def __init__(self, check_interval_sec: int = 30):
        self.interval = check_interval_sec

    async def monitor_loop(self):
        """主监控循环 — 使用 ActivityState 时间衰减模型（见 8.2）判定 agent 健康状态。

        ActivityState 由 get_agent_activity_state() 返回，已内置 Decision Gate 感知
        （WAITING_INPUT 状态），因此此处无需额外跳过逻辑。
        """
        while True:
            for agent in self.get_active_agents():
                state = get_agent_activity_state(
                    agent.id,
                    workflow_run_id=getattr(agent, "current_run_id", None),
                )  # 8.2 定义的自由函数，传 run_id 确保 workflow 级 gate 也能被检测到

                match state:
                    case ActivityState.ACTIVE | ActivityState.READY:
                        # 正常工作中 — 重置升级计数器
                        agent.escalation_level = 0
                    case ActivityState.WAITING_INPUT:
                        # 等待人工决策（Decision Gate）— 不升级
                        continue
                    case ActivityState.BLOCKED:
                        # 被依赖阻塞 — 不升级，但记录
                        self.log_warning(agent, reason="blocked on dependency")
                    case ActivityState.IDLE:
                        # 长时间无活动 — 渐进升级
                        agent.escalation_level += 1
                        match agent.escalation_level:
                            case 1:  # Tier 0: warn
                                self.log_warning(agent, reason="idle")
                            case 2:  # Tier 0: nudge
                                await self.send_nudge(agent)
                            case _:  # Tier 1: AI triage
                                verdict = await self.ai_triage(agent)
                                match verdict:
                                    case "retry":
                                        await self.restart_agent(agent)
                                    case "extend":
                                        agent.timeout *= 2
                                    case "terminate":
                                        await self.terminate_and_notify(agent)
                    case ActivityState.EXITED:
                        # 进程已退出 — 直接终止流程 + 记录
                        await self.terminate_and_notify(agent)
                        self.record_failure_to_knowledge(agent)

            # 每轮末尾：扫描过期 Decision Gate（见 4.6 TTL Reaper）
            await self.reap_expired_gates()

            await asyncio.sleep(self.interval)

    async def ai_triage(self, agent) -> str:
        """Tier 1: AI 辅助故障分类"""
        last_logs = await self.get_agent_logs(agent, lines=50)
        triage_agent = Agent(
            name="Triage",
            model=Claude(id="claude-haiku-4-5"),
            instructions="分类 agent 故障：retry / terminate / extend",
        )
        response = triage_agent.run(f"Agent {agent.name} appears stuck.\nLogs:\n{last_logs}")
        return parse_triage_verdict(response.content)
```

---

## 8. 可观测性

### 8.1 双层 Trace 架构

来源：Agno 原生 OTel（F4 修正）+ Overstory EventStore

**关键差异化：Orchestra 是 6 个参考项目中唯一实现 OpenTelemetry 兼容 trace 导出的系统。**

```
┌───────────────────────────────────────────────────┐
│              Agno 原生 Tracing（自动）              │
│                                                   │
│  setup_tracing(db=traces_db)                      │
│  自动捕获：                                        │
│  • Agent/Team/Workflow 执行 trace                  │
│  • LLM 调用 input/output/tokens/latency           │
│  • Tool 调用参数和结果                              │
│  • Span 层级关系（parent-child）                   │
│  • OpenTelemetry 兼容导出                          │
│                                                   │
│  查询：traces_db.get_traces(agent_id=x, limit=10)  │
└───────────────────────┬───────────────────────────┘
                        │
┌───────────────────────┴───────────────────────────┐
│            Orchestra EventStore（自定义）           │
│                                                   │
│  SQLite events.db — 16 事件类型 + 7 索引            │
│  事件：tool_start/end, session_start/end,          │
│        mail_sent/received, spawn, error,           │
│        gate_verdict, reaction_fired,               │
│        merge_attempt, checkpoint,                  │
│        decision_gate_created,                      │
│        decision_gate_resolved,                     │
│        wu_blocked, wu_unblocked                    │
│                                                   │
│  每行含 correlation_id 字段（跨系统追踪键）          │
│                                                   │
│  索引：(agent_name, created_at)                    │
│        (run_id, created_at)                        │
│        (event_type, created_at)                    │
│        (tool_name, agent_name)                     │
│        (level='error')                             │
│        (event_type='decision_gate_%', status)      │
│        (correlation_id)                            │
└───────────────────────────────────────────────────┘
```

### 8.2 Correlation ID + Activity State

来源：Composio `observability.ts` — 关联 ID 跨系统追踪 + 活动状态时间衰减

#### Correlation ID

每个外部触发（webhook、REST 请求、CLI 命令）生成唯一 `correlation_id`，贯穿该触发引起的所有后续操作（Reaction Engine 响应、agent 消息、Decision Gate 创建、EventStore 事件）。

```python
import uuid
from contextvars import ContextVar

# 全局 context var — 所有同一请求链路上的代码共享
current_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")

def create_correlation_id() -> str:
    cid = f"cid-{uuid.uuid4().hex[:16]}"
    current_correlation_id.set(cid)
    return cid

# REST API — 接受或生成 correlation_id
@app.middleware("http")
async def correlation_middleware(request, call_next):
    cid = request.headers.get("x-correlation-id") or create_correlation_id()
    current_correlation_id.set(cid)
    response = await call_next(request)
    response.headers["x-correlation-id"] = cid
    return response

# Webhook handler — 从 GitHub delivery ID 派生
@app.post("/webhooks/github")
async def github_webhook(request):
    delivery_id = request.headers.get("x-github-delivery", "")
    cid = f"cid-gh-{delivery_id[:12]}" if delivery_id else create_correlation_id()
    current_correlation_id.set(cid)
    # ... 处理 webhook，后续所有 emit_event / Reaction 都携带此 cid

# emit_event 自动附加 correlation_id
def emit_event(event_type: str, **kwargs):
    events_db.execute(
        "INSERT INTO events (event_type, correlation_id, ...) VALUES (?, ?, ...)",
        (event_type, current_correlation_id.get(), ...),
    )
```

**排障场景**：当某个 PR 的 CI 修复行为异常时，查询 `SELECT * FROM events WHERE correlation_id = 'cid-gh-abc123' ORDER BY created_at` 即可看到从 webhook 到 Reaction 到 agent 响应的完整链路。

#### Activity State 时间衰减模型

来源：Composio `observability.ts` — 6 状态 + 时间衰减，供 Watchdog 判定使用

```python
class ActivityState(Enum):
    ACTIVE = "active"             # 正在执行 tool/LLM 调用
    READY = "ready"               # 刚完成一步，等待下一步
    IDLE = "idle"                 # 长时间无活动
    WAITING_INPUT = "waiting_input"  # 等待人工输入（Decision Gate）
    BLOCKED = "blocked"           # 被依赖阻塞
    EXITED = "exited"             # 进程已退出

# 时间衰减规则
STATE_DECAY = {
    ActivityState.ACTIVE: (ActivityState.READY, timedelta(seconds=30)),
    ActivityState.READY:  (ActivityState.IDLE,  timedelta(minutes=5)),
}

def get_agent_activity_state(
    agent_id: str,
    workflow_run_id: str | None = None,
) -> ActivityState:
    """根据最近事件和 Decision Gate 状态推导 agent 活动状态"""
    # 优先检查 Decision Gate（传 workflow_run_id 确保 workflow 级 gate 可被检测）
    if has_pending_decision_gate(agent_id, workflow_run_id=workflow_run_id):
        return ActivityState.WAITING_INPUT

    # 检查是否被 WorkUnit 依赖阻塞（DAG 中前置 unit 未完成）
    if _is_blocked_on_dependency(agent_id):
        return ActivityState.BLOCKED

    last_event = events_db.query_one(
        "SELECT * FROM events WHERE agent_name = ? ORDER BY created_at DESC LIMIT 1",
        (agent_id,),
    )
    if not last_event:
        return ActivityState.EXITED

    elapsed = datetime.utcnow() - parse_datetime(last_event["created_at"])
    base_state = (
        ActivityState.ACTIVE if last_event["event_type"] in ("tool_start", "spawn")
        else ActivityState.READY
    )

    # 应用时间衰减
    current = base_state
    while current in STATE_DECAY:
        next_state, threshold = STATE_DECAY[current]
        if elapsed > threshold:
            current = next_state
        else:
            break

    return current

def _is_blocked_on_dependency(agent_id: str) -> bool:
    """检查 agent 是否在等待 DAG 中的前置 WorkUnit 完成。

    使用专用事件类型 'wu_blocked' / 'wu_unblocked' 而非依赖 spawn 事件的
    metadata 字段。这避免了 JSON null 序列化歧义问题。

    事件协议：
    - execute_work_units() 在 batch 开始前为后续 units 发 'wu_blocked'
    - execute_work_units() 在 batch 完成后为该 batch 的 units 发 'wu_unblocked'
    - 此函数检查最近一条事件是 blocked 还是 unblocked
    """
    last = events_db.query_one(
        "SELECT event_type FROM events "
        "WHERE agent_name = ? AND event_type IN ('wu_blocked', 'wu_unblocked') "
        "ORDER BY created_at DESC LIMIT 1",
        (agent_id,),
    )
    return last is not None and last["event_type"] == "wu_blocked"
```

**与 Watchdog 集成**：Watchdog 的 `monitor_loop()`（见 7.2）调用 `get_agent_activity_state(agent.id, workflow_run_id=agent.current_run_id)` 获取衰减后的状态。传递 `workflow_run_id` 确保 workflow 级 Decision Gate（plan_review / design_review / integration）也能被正确识别为 `WAITING_INPUT`。`IDLE` 触发 nudge，`WAITING_INPUT` 跳过升级，`BLOCKED` 记录但不升级，`EXITED` 触发终止流程。

### 8.3 多格式日志

来源：Overstory `src/logging/logger.ts`

每个 agent session 输出 4 个文件：

```
.orchestra/logs/{agent-name}/{session-timestamp}/
├── session.log          # 人类可读 [TIMESTAMP] LEVEL EVENT key=value
├── events.ndjson        # 机器可解析 NDJSON
├── tools.ndjson         # 工具使用日志
└── errors.log           # 带上下文的堆栈追踪
```

### 8.4 Secret 脱敏

来源：Overstory `src/logging/sanitizer.ts`

```python
import re

REDACT_PATTERNS = [
    (re.compile(r'sk-ant-[a-zA-Z0-9\-_]+'), '[REDACTED_ANTHROPIC_KEY]'),
    (re.compile(r'github_pat_[a-zA-Z0-9]+'), '[REDACTED_GITHUB_PAT]'),
    (re.compile(r'ghp_[a-zA-Z0-9]+'), '[REDACTED_GITHUB_TOKEN]'),
    (re.compile(r'Bearer\s+[a-zA-Z0-9\-_.]+'), 'Bearer [REDACTED]'),
    (re.compile(r'ANTHROPIC_API_KEY=[^\s]+'), 'ANTHROPIC_API_KEY=[REDACTED]'),
]

def sanitize(text: str) -> str:
    for pattern, replacement in REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
```

### 8.5 Hook 分层

来源：Agno F3 修正

```
cadence hooks（Claude Code 层）         Agno hooks（编排层）
├── chronicler → skill/command 调用      ├── pre_hooks → 输入验证、PII 检测
├── session-guard → CLI session 策略     ├── post_hooks → 输出质量、合规过滤
│                                       ├── tool_hooks → 调用日志、耗时统计
⚠ 仅 Claude Code CLI 内部生效            ✅ setup_tracing() 自动捕获全量 trace
```

chronicler 职责大幅缩减——仅负责 cadence skill/hook 调用记录。90% 可观测性由 Agno 原生 tracing 覆盖。

---

## 9. Learning 系统

来源：Metaswarm Self-Reflect + CCW Memory Consolidation

### 9.1 Self-Reflect 管道

```
┌── Session 结束 / PR 合并后 ───────────────────────┐
│                                                   │
│  Phase A: PR 评论分析                              │
│    └── 获取 PR 评论 → 质量过滤 → ACCEPT/REJECT    │
│        质量判据：能否防止 bug？节省审查？agent 可行？ │
│                                                   │
│  Phase B: 对话 & 会话挖掘                          │
│    └── 战略模式提取                                │
│        匹配："问题是..."、"我们决定..."、"与预期不同" │
│                                                   │
│  Phase C: 配置反思                                 │
│    └── 审查 agent 指令 → 改进建议                  │
│                                                   │
│  Phase D: 整合 & 存储                              │
│    ├── 规范化：去除 PR 引用 / 泛化路径 / 包含 WHY  │
│    ├── 去重 & 冲突解决                             │
│    └── 写入知识库 knowledge.jsonl                  │
│                                                   │
│  Phase E: 选择性注入（下次 session）                │
│    └── 按 affectedFiles + tags 过滤相关知识注入    │
└───────────────────────────────────────────────────┘
```

### 9.2 知识库 Schema

来源：Metaswarm `knowledge/README.md`

```python
from pydantic import BaseModel
from typing import Literal

class KnowledgeEntry(BaseModel):
    id: str                                             # k-{date}-{seq}
    type: Literal["pattern", "gotcha", "decision",
                  "anti_pattern", "convention",
                  "dependency", "environment"]
    fact: str                                           # 事实描述
    recommendation: str                                 # 建议行动
    confidence: float                                   # 0.0-1.0
    provenance: str                                     # 来源（pr-123-review）
    tags: list[str]                                     # ["auth", "security"]
    affected_files: list[str]                           # ["src/auth/*.py"]
    usage_count: int = 0                                # 被注入次数
    helpful_count: int = 0                              # 被采纳次数
    outdated_reports: int = 0                           # 被标记过时次数
```

### 9.3 选择性知识注入

来源：Metaswarm `bd prime` 模式

```python
def prime_knowledge(
    knowledge_db: str,
    affected_files: list[str],
    tags: list[str],
    max_entries: int = 10,
) -> list[KnowledgeEntry]:
    """按 affectedFiles 和 tags 选择相关知识条目"""
    entries = load_knowledge(knowledge_db)
    scored = []
    for entry in entries:
        score = 0
        # 文件匹配
        for pattern in entry.affected_files:
            if any(fnmatch(f, pattern) for f in affected_files):
                score += 3
        # Tag 匹配
        score += len(set(entry.tags) & set(tags))
        # 质量加权
        score *= entry.confidence
        # 惩罚过时
        score -= entry.outdated_reports * 0.5
        scored.append((score, entry))

    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:max_entries]]
```

### 9.4 两阶段 Memory Consolidation（F5 修正）

来源：CCW `memory-consolidation-pipeline.ts`（原项目按 session 提取，Orchestra 映射为 per workflow run）

Self-Reflect（9.1）负责从 PR 评论和对话中提取单条知识，但缺少跨 run 的知识整合机制。Memory Consolidation 解决：多个 workflow run 产生的碎片化知识如何合并、去重、升级为高置信度的全局知识。

#### 两阶段数据流

```
Phase 1: Per-Run Extraction（每次 workflow run 结束时触发）
┌──────────────────────────────────────────────────┐
│  输入：workflow run 的 traces + events + git diff  │
│                                                  │
│  提取（per workflow run）：                        │
│    • 工具使用模式（哪些工具组合频繁出现）            │
│    • 文件编辑热点（哪些文件反复修改）               │
│    • 错误模式（什么类型的错误反复出现）              │
│    • 审查模式（哪些审查意见反复出现）                │
│    • 时间分布（哪些阶段耗时最长）                   │
│                                                  │
│  存储：SQLite stage1_outputs 表                   │
│    run_id | extracted_at | payload (JSON)         │
└──────────────────────────────────────────────────┘
                    │
                    ▼ （累积 N 个 run 后 或 手动触发）
Phase 2: Global Consolidation
┌──────────────────────────────────────────────────┐
│  输入：stage1_outputs 中所有未整合的 run 提取       │
│                                                  │
│  整合：                                           │
│    1. 跨 run 模式聚合                              │
│       - 同一 pattern 在 ≥3 run 出现 → 升级 confidence │
│       - 冲突 pattern → 保留最新 + 标记 conflict    │
│    2. 与现有 knowledge.jsonl 合并                  │
│       - 重复条目 → 合并（+usageCount）             │
│       - 新条目 → 追加                              │
│       - 过时条目 → +outdatedReports                │
│    3. 物化写出                                    │
│       - 更新 knowledge.jsonl                      │
│       - 更新 MEMORY.md（人类可读摘要）              │
│                                                  │
│  触发条件：                                       │
│    - 自动：每 5 个 workflow run 完成后              │
│    - 手动：/orchestra consolidate                 │
│    - 定时：cron 每日一次                           │
└──────────────────────────────────────────────────┘
```

#### 实现

```python
class MemoryConsolidation:
    """两阶段 memory consolidation pipeline

    使用两个 DB：
    - traces_db: 读取 Agno OTel traces（get_traces）
    - events_db: 读取 Orchestra 事件 + 写入 stage1_outputs
    """

    def __init__(self, traces_db: SqliteDb, events_db: SqliteDb, knowledge_path: str):
        self.traces_db = traces_db
        self.events_db = events_db
        self.knowledge_path = knowledge_path

    # ── Phase 1: Per-Run Extraction ──

    def extract_session(self, run_id: str, session_id: str | None = None) -> dict:
        """从单个 workflow run 的 traces + events 中提取知识候选。

        run_id:     workflow run 标识，用于查询 events_db（events 按 run_id 索引）
        session_id: agent session 标识，用于查询 traces_db（traces 按 session_id 索引）
                    如果不传，默认使用 run_id（单 agent 场景二者相同）
        """
        trace_key = session_id or run_id
        traces = self.traces_db.get_traces(session_id=trace_key)   # traces_db: 按 session_id
        events = self.events_db.query(
            "SELECT * FROM events WHERE run_id = ?", (run_id,)     # events_db: 按 run_id
        )

        extraction = {
            "run_id": run_id,
            "session_id": trace_key,
            "extracted_at": datetime.utcnow().isoformat(),
            "tool_patterns": self._extract_tool_patterns(traces),
            "file_hotspots": self._extract_file_hotspots(events),
            "error_patterns": self._extract_error_patterns(events),
            "review_patterns": self._extract_review_patterns(traces),
            "timing": self._extract_timing(traces),
        }

        # 持久化到 stage1_outputs（events_db），以 run_id 为主键
        self.events_db.execute(
            "INSERT INTO stage1_outputs (run_id, extracted_at, payload) VALUES (?, ?, ?)",
            (run_id, extraction["extracted_at"], json.dumps(extraction)),
        )
        return extraction

    # ── Phase 2: Global Consolidation ──

    def consolidate(self) -> ConsolidationResult:
        """合并所有未整合的 workflow run 提取到全局知识库"""
        # 获取未整合的提取（events_db）
        pending = self.events_db.query(
            "SELECT * FROM stage1_outputs WHERE consolidated = 0"
        )
        if not pending:
            return ConsolidationResult(new=0, updated=0, conflicts=0)

        existing = load_knowledge(self.knowledge_path)
        new_entries, updated, conflicts = [], 0, 0

        for extraction in pending:
            payload = json.loads(extraction["payload"])
            candidates = self._patterns_to_knowledge_entries(payload)

            for candidate in candidates:
                match = self._find_duplicate(candidate, existing)
                if match:
                    # 合并：提升 confidence，增加 usageCount
                    match.usage_count += 1
                    match.confidence = min(1.0, match.confidence + 0.05)
                    updated += 1
                elif conflict := self._find_conflict(candidate, existing):
                    # 冲突：保留最新，标记
                    conflict.outdated_reports += 1
                    candidate.confidence = max(0.3, candidate.confidence - 0.1)
                    new_entries.append(candidate)
                    conflicts += 1
                else:
                    new_entries.append(candidate)

        # 写出
        existing.extend(new_entries)
        save_knowledge(self.knowledge_path, existing)

        # 标记为已整合（events_db）
        self.events_db.execute("UPDATE stage1_outputs SET consolidated = 1 WHERE consolidated = 0")

        return ConsolidationResult(
            new=len(new_entries), updated=updated, conflicts=conflicts
        )
```

#### SQLite Schema（追加到 events.db）

```sql
CREATE TABLE stage1_outputs (
    run_id TEXT PRIMARY KEY,          -- workflow run_id（非 session_id）
    extracted_at TEXT NOT NULL,
    payload TEXT NOT NULL,  -- JSON
    consolidated INTEGER DEFAULT 0
);
CREATE INDEX idx_s1_consolidated ON stage1_outputs(consolidated);
```

---

## 10. Agno 实现详解

### 10.1 基础设施

```python
"""orchestra.py — Multi-Agent Development Pipeline"""
from agno.agent import Agent
from agno.team import Team
from agno.team.mode import TeamMode
from agno.workflow import Workflow, Step, Loop, Condition, Parallel
from agno.workflow.types import StepInput, StepOutput, OnError, OnReject
from agno.models.anthropic import Claude
from agno.models.google import Gemini
from agno.models.openai import OpenAI
from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.tracing import setup_tracing
from agno.learn import LearningMachine, SessionContextConfig

# ── 持久化：按功能分库（参考 Overstory 5 DB 模式）──
# 每个 DB 职责明确，WAL 模式支持多 agent 并发读写

traces_db = SqliteDb(db_file=".orchestra/traces.db")     # Agno OTel traces
events_db = SqliteDb(db_file=".orchestra/events.db")     # EventStore + decision_gates + stage1_outputs
mail_db   = SqliteDb(db_file=".orchestra/mail.db")       # Agent 间消息
metrics_db = SqliteDb(db_file=".orchestra/metrics.db")   # Token/成本追踪
merge_db  = SqliteDb(db_file=".orchestra/merge.db")      # Merge queue + 冲突历史

setup_tracing(db=traces_db)  # OTel traces 写入 traces_db

# ── DB 职责边界 ──
# traces_db:  Agno setup_tracing 自动写入，查询用 traces_db.get_traces()
# events_db:  Orchestra 自定义事件（16 类型）、decision_gates 表、stage1_outputs 表
#             所有 emit_event() 和 DecisionGate CRUD 操作指向此 DB
# mail_db:    Agent 间异步消息（如需实现 SQLite 邮件系统）
# metrics_db: per-run 成本估算、token 消耗统计
# merge_db:   Merge queue FIFO 表、冲突解决历史（tier 成功/失败率）
#
# Agent/Team 构造时传 traces_db（用于 Agno 原生 tracing）：
#   Agent(..., db=traces_db)
# Orchestra 业务逻辑使用 events_db（事件/gate/learning）：
#   events_db.save_decision_gate(gate)
#   events_db.execute("INSERT INTO stage1_outputs ...")
```

### 10.2 Team 模式选择

来源：Agno F1 修正 + **Gate 0 G0-D 验证**

> **AC-07**：始终使用 `mode=TeamMode.xxx` 显式指定模式。`mode=` 参数优先于布尔标志位
> （如 `delegate_to_all_members`、`respond_directly`）——混用会导致不可预测行为。

| 阶段 | 协作模式 | 原因 |
|------|----------|------|
| Design | `mode=TeamMode.coordinate` | Maestro 分解+综合，成员串行/条件执行 |
| Plan Review | `mode=TeamMode.broadcast` | 所有审查者同时收到同一文档 |
| Design Review | `mode=TeamMode.broadcast` | 所有专家并行审查 |
| Implementation | `mode=TeamMode.route` | 按 task 类型路由到最合适的 specialist |
| PR Review | 独立 Agent 串行 | Workflow Step 串行调用独立 Agent + post-review Decision Gate（见 10.4） |

### 10.3 Session 隔离

来源：Agno F5 修正

```python
# 每个 task 独立 session，天然隔离
for task in tasks:
    impl_team.run(
        task.description,
        session_id=f"task_{task.id}",
        user_id="orchestra",
    )
```

### 10.4 HITL 审批与 PR Merge

来源：Agno F6 修正 + Stage 5 PR Lifecycle 设计 + **Gate 0 验证结果**

> **Gate 0 更新（AC-04）**：Agno 原生 HITL 在 Workflow Step 层已验证可用。
> `Step(requires_confirmation=True)` 在 Step 级正常工作，但在 **Loop 内被静默忽略**（G0-B DELTA-05）。
> 因此 Orchestra 采用 **Post-Loop Decision Gate 模式**：所有需要人工确认的 gate
> 放在 Loop 之后作为独立 Step，使用 Agno 原生 `requires_confirmation` + `on_reject`。

**Decision Gate 模式**（取代原 `WorkflowPauseRequest`）：

```python
# AC-04: Post-Loop Decision Gate 标准模式
Loop(
    name="review_loop",
    steps=[review_step, check_gate_step],
    end_condition=lambda ctx: ctx.get("gate_passed", False),  # AC-01
    max_iterations=3,
),
Step(executor=check_review_result, name="review_result",
     on_error=OnError.fail),  # AC-02
Step(executor=decision_gate, name="decision_gate",
     requires_confirmation=True,      # Agno 原生 HITL
     on_reject=OnReject.cancel,       # 拒绝时终止 workflow
     on_error=OnError.fail),          # AC-02
```

**PR Merge 流程**（详见 2.7 Stage 5）：
- Critic/Challenger 各返回 GateVerdict → `check_pr_review_gate` 程序化消费
- 审查不通过 → post-review Decision Gate `Step(requires_confirmation=True, on_reject=OnReject.cancel)`
- 最终合并 → `execute_merge()` 前置 Decision Gate 等待人工确认
- 高信任项目可配 `auto_merge: true` 跳过最终确认

**Team verdict 检查（AC-06）**：Team leader 产出 synthesis 不等于所有成员成功执行。
Agno 采用 error-as-content 策略——成员异常被捕获并作为字符串传给 leader。verdict 解析
必须显式检查 member error signals，不能仅凭 leader synthesis 判断全员通过。Phase 0 脚手架
将提供 `check_team_member_errors()` 工具函数降低遗漏风险。

**TeamMode 配置（AC-07）**：`Team(mode=TeamMode.broadcast)` 中 `mode=` 参数优先于布尔标志位
（如 `delegate_to_all_members`）。始终使用 `mode=` 显式指定，避免布尔标志冲突。

### 10.5 WorkflowAgent 对话入口

来源：Agno F10 修正

```python
from agno.workflow import WorkflowAgent

orchestra_agent = WorkflowAgent(
    model=Claude(id="claude-sonnet-4-6"),
    num_history_runs=4,
    instructions="""You are the Orchestra conductor.
    - If the user asks about previous results, answer from history.
    - If the user requests new work, run the workflow.
    - If the user asks to modify, adjust and re-run.
    """,
)

dev_workflow = Workflow(
    name="Orchestra Pipeline",
    agent=orchestra_agent,  # 对话式入口
    steps=[...],
    db=traces_db,
)

# 交互模式
dev_workflow.print_response("Design a cascade redistribution algorithm")
# → 触发完整 workflow

dev_workflow.print_response("What issues did Critic find?")
# → 从 history 回答，不重新运行
```

---

## 11. 实施路径

### Phase 0: Foundation（基础设施）

- [ ] Agno 项目脚手架 + `setup_tracing(db=traces_db)` + 5 DB 初始化
- [ ] SQLite 持久化层（5 DB：traces / events / mail / metrics / merge-queue）
- [ ] Decision Gate 协议（events.db 中 decision_gates 表 + REST API）
- [ ] 6 级模型解析链（`orchestra.yaml` 解析 + `resolve_model()` 实现）
- [ ] 通用工具词汇表 + provider 翻译层（参考 CAO `tool_mapping.py`）
- [ ] WorkUnit 数据模型（id / dod / file_scope / dependencies）
- [ ] 统一任务 Schema（适配 CCW `task-schema.json`）
- [ ] 知识库 JSONL Schema + Pydantic model（采自 Metaswarm）
- [ ] stage1_outputs 表（Memory Consolidation Phase 1 存储）
- [ ] 多格式日志系统（session.log + events.ndjson + errors.log）
- [ ] Secret 脱敏 logger（参考 Overstory）
- [ ] HITL Workflow 兼容性验证（Phase 0.5，Agno F8 修正）

### Phase 1: Core Workflow（核心工作流）

- [ ] Agent 定义：Architect / Specialist / Critic / Challenger / Implementer
- [ ] Callable Factory 动态角色加载
- [ ] Design Team（TeamMode.coordinate）
- [ ] Plan Review Gate（TeamMode.broadcast，3 对抗审查者，ALL PASS，3 轮 + Decision Gate 升级）
- [ ] Design Review Gate（TeamMode.broadcast，5+ 专家，ALL APPROVE，3 轮 + Decision Gate 升级）
- [ ] Cross-Model Review：审查矩阵实现 + Fresh Reviewer 规则
- [ ] **Work Unit Decomposition**：设计 → WorkUnit DAG（DoD / file_scope / dependencies）
- [ ] **Per-Unit 4-Phase Loop**：IMPL → VALIDATE → ADV.REVIEW → COMMIT（DAG 调度，独立 unit 并行）
- [ ] **Final Integration Review**：跨 work unit 集成审查（接口/状态/回归检查）
- [ ] Quality Gates：test + lint + typecheck 阻塞门控
- [ ] File Scoping 验证（work unit 文件范围不重叠）
- [ ] Watchdog Tier 0（进程监控 30s + warn/nudge + Decision Gate 感知）
- [ ] WorkflowAgent 对话入口

### Phase 2: PR Automation + Observability（PR 自动化 + 可观测性）

- [ ] PR Shepherd 状态机（MONITORING → FIXING → HANDLING_REVIEWS → DONE）
- [ ] Reaction Engine：9 种事件响应（覆盖全部 EventType）+ 指纹去重 + 冷却 + 升级
- [ ] Merge Queue：4 层解冲突管道 + 历史模式学习
- [ ] EventStore：16 事件类型 + 7 索引（含 decision_gate_* / wu_blocked/unblocked / correlation_id）
- [ ] Correlation ID 中间件（REST / Webhook / CLI 入口生成，贯穿所有事件）
- [ ] Activity State 衰减模型（6 状态 + 时间衰减 → Watchdog monitor_loop 直接调用 get_agent_activity_state）
- [ ] Decision Gate Dashboard（列出 pending gates + 审批操作）
- [ ] OTel 兼容 trace 导出（Jaeger / Grafana Tempo 集成）
- [ ] TUI Dashboard 或 Web Dashboard
- [ ] Watchdog Tier 1-2（AI triage + 持久监控）

### Phase 3: Learning + Advanced（学习 + 高级功能）

- [ ] Self-Reflect 管道（5 阶段：PR 分析 → 会话挖掘 → 配置反思 → 整合 → 注入）
- [ ] **Memory Consolidation Phase 1**（per-run 提取 → stage1_outputs 表，run_id 主键）
- [ ] **Memory Consolidation Phase 2**（全局整合 → knowledge.jsonl 更新 + MEMORY.md 物化）
- [ ] 整合触发机制（每 5 个 workflow run 自动 / 手动 / cron 每日）
- [ ] 知识库选择性注入（按 affectedFiles/tags 过滤）
- [ ] Agent 身份/CV 持久化
- [ ] 预算断路器（per_task + per_session）
- [ ] 成本优先路由 + 可用性感知模型选择
- [ ] PluginEval 集成（agent/skill 质量评估）
- [ ] 递归嵌套编排（Sub-Orchestrator）
- [ ] Research Stage（Scout Agent）

---

## 12. 风险与开放问题

### 风险矩阵

| 风险 | 影响 | 概率 | 缓解 |
|------|------|------|------|
| ~~Agno HITL 在 Workflow 层不完整~~ | ~~PR review 需降级实现~~ | — | **Gate 0 已验证**：Step 级 `requires_confirmation` 可用；Loop 内静默忽略已通过 AC-04 Post-Loop Decision Gate 模式解决 |
| 多模型 API 不稳定 | 跨模型审查中断 | 中 | fallback 链 + 预算断路器 + 可用性感知路由 |
| Context Window 膨胀 | agent 性能退化 | 高 | 选择性知识注入 + 检查点恢复 + Fresh Reviewer 规则 |
| Quality Gate 过严 | 工作流卡死 | 中 | 3 轮限制 + 人工升级 + Decision Gate |
| SQLite 并发瓶颈 | 高并发 agent 受阻 | 低 | WAL 模式 + 5 DB 分库（参考 Overstory） |
| 单人维护参考项目消亡 | 模式参考失效 | 中 | 仅采纳模式而非依赖上游代码 |

### Silent Failure Modes（Gate 0 发现）

> 以下风险的共同特征：**系统表面正常运行，但语义已失真**。实现阶段必须特别警惕。

| 风险 | 表现 | 根因 | 缓解 |
|------|------|------|------|
| **Workflow 缺少 db 参数（AC-05）** | Workflow 正常执行完成，但 `resume()` 完全失效——无 checkpoint 可恢复，无报错 | Agno 将 `db` 视为可选参数，不传时 silently 跳过持久化 | Phase 0 脚手架强制所有 Workflow 构造器传 `db`（生产 `SqliteDb`，测试 `InMemoryDb`）。Lint 规则检查 |
| **Team leader synthesis 掩盖成员失败（AC-06）** | Leader 正常产出综合 verdict，但某成员已抛异常。Leader synthesis 包含 error string 但格式不固定 | Agno error-as-content 策略：成员异常被捕获为字符串传给 leader，不中断 Team 执行 | Phase 0 提供 `check_team_member_errors()` 工具函数，verdict 解析必须调用。Review checklist 检查项 |
| **Loop 内 requires_confirmation 被静默忽略（AC-04）** | Loop 正常执行完毕，HITL 暂停从未触发，无报错 | Agno Loop 执行引擎不检查 Step 的 `requires_confirmation` 标志 | AC-04 架构约束：所有 Decision Gate 放在 Loop 之后。Phase 0 `create_gate_step()` 工厂函数强制此模式 |
| **StepOutput(stop=True) 终止整个 Workflow（AC-01）** | 期望退出 Loop，实际终止整个 Workflow。后续 Stage 全部被跳过，无报错 | `stop=True` 是 Workflow 级终止信号，不是 Loop 级 | AC-01 架构约束：Loop 退出只用 `end_condition`。代码审查 checklist 检查 `stop=True` 不出现在 Loop 内 |

### 开放问题

1. **通信机制选择**：Agno 原生共享内存 vs SQLite 邮件系统 vs JSONL 消息总线？建议 Phase 0 验证 Agno 原生 `session_state` 是否足以满足 agent 间通信需求。

2. **OTel 集成深度**：仅 trace 导出 vs 完整 metrics/logs/traces 三支柱？建议 Phase 2 先实现 trace 导出，按需扩展。

3. **PR Lifecycle 自治程度**：建议默认 `auto_merge: false`，由 Decision Gate（`gate_type="pr_merge"`）控制最终合并。高信任项目可配置 `auto_merge: true` 跳过确认。

4. **Learning 冷启动**：建议预装 Metaswarm 审查 rubrics（9 个）作为初始知识库，社区后续可共享 knowledge.jsonl。

5. **跨 Repo 编排**：V1 不需要。V2 可参考 Metaswarm Swarm Coordinator + Overstory Orchestrator。

---

## 附录

### A. 参考项目来源映射

| Orchestra 组件 | 主要参考 | 次要参考 |
|---------------|---------|---------|
| 5 阶段工作流 | Metaswarm 9-phase | Composio lifecycle SM |
| 跨模型审查矩阵 | Metaswarm external-tools | — |
| Plan Review Gate | Metaswarm plan-review-gate | — |
| Design Review Gate | Metaswarm design-review-gate | CCW team-supervisor |
| Reaction Engine | Composio lifecycle-manager | Metaswarm PR Shepherd |
| Merge Queue | Overstory merge/resolver | — |
| Watchdog | Overstory watchdog/ | — |
| EventStore | Overstory events/store | Composio observability |
| 多格式日志 | Overstory logging/ | — |
| 知识库 Schema | Metaswarm knowledge/ | CCW core-memory-store |
| Self-Reflect | Metaswarm self-reflect | CCW memory-consolidation |
| 通用工具词汇表 | CAO tool_mapping.py | — |
| 多模型配置 | Composio agent-selection | Overstory runtimes/registry |
| PluginEval | Conductor plugin-eval | — |
| Callable Factory | Agno 原生 | — |
| OTel Tracing | Agno 原生 setup_tracing | — |
| HITL | Decision Gate + Agno 原生 `requires_confirmation` / `on_reject`（Gate 0 AC-04 验证） | — |

### B. 相关文档

- [Agno 文档审查修正](design-revision-agno-review.md)
- [调研综合报告](research/final_research_report.md)
- [架构分析](research/01_architecture_analysis.md)
- [多模型集成](research/02_multi_model_integration.md)
- [可观测性分析](research/03_observability_analysis.md)
- [工作流模式](research/04_workflow_patterns.md)
- [集成可行性](research/05_integration_feasibility.md)
