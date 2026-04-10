# Orchestra Roadmap

> GitHub Project 规划 — 参照 [wperf](https://github.com/nicholasgasior/wperf) roadmap 模式
>
> 更新日期：2026-04-08 | 设计文档：[DESIGN.md](DESIGN.md) v4.4

---

## GitHub Project 配置

### Project Board

| 字段 | 类型 | 值 |
|------|------|-----|
| Title | Text | — |
| Status | Single Select | `Todo`, `In Progress`, `Done` |
| Milestone | Iteration | 见下方 Milestones |
| Labels | Labels | 见下方 Label Taxonomy |
| Assignees | People | — |
| Linked PRs | PR | — |
| Parent issue | Issue | 层级关联 |
| Sub-issues progress | Progress | 自动 |

### Label Taxonomy

**Priority（4 级）**

| Label | 含义 |
|-------|------|
| `P0-critical` | 阻塞后续阶段的核心依赖 |
| `P1-high` | 当前阶段必须完成 |
| `P2-medium` | 当前阶段应完成，可推迟 |
| `P3-low` | 增强项，不阻塞里程碑 |

**Area（10 个子系统）**

| Label | 对应 DESIGN.md |
|-------|---------------|
| `area/workflow` | §2 五阶段工作流 + Workflow Engine |
| `area/agents` | §3 Agent 定义 + Callable Factory |
| `area/review-gate` | §4 Plan/Design Review Gate |
| `area/persistence` | §10 SQLite 5-DB + session_state |
| `area/pr-lifecycle` | §2.7 PR Shepherd + Merge |
| `area/reaction-engine` | §5 Reaction Engine 事件驱动 |
| `area/merge-queue` | §6 Merge Queue 冲突解决 |
| `area/watchdog` | §7 Watchdog 健康监控 |
| `area/observability` | §8 OTel + EventStore + Dashboard |
| `area/learning` | §9 Self-Reflect + Knowledge |

**Phase（6 个阶段）**

| Label | 对应 Milestone |
|-------|---------------|
| `phase/gate-0` | Gate 0: Agno Feasibility |
| `phase/0-foundation` | Phase 0: Foundation |
| `phase/1-core-workflow` | Phase 1: Core Workflow |
| `phase/2-pr-observability` | Phase 2: PR Automation + Observability |
| `phase/3-learning` | Phase 3: Learning + Advanced |
| `phase/release` | v0.1.0-alpha |

**Type（4 种）**

| Label | 含义 |
|-------|------|
| `type/feature` | 功能实现 |
| `type/infra` | 基础设施 / 脚手架 |
| `type/ci` | CI/CD 配置 |
| `type/docs` | 文档 |

---

## Milestones

```
Gate 0 ──→ Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ v0.1.0-alpha
 验证        基础        核心        PR+可观测     学习         发布
```

| # | Milestone | 目标 | Exit Criteria |
|---|-----------|------|---------------|
| 0 | **Gate 0: Agno Feasibility** | 验证 Agno 框架假设，消除架构风险 | 5 个 PoC 全部通过 |
| 1 | **Phase 0: Foundation** | 持久化层 + Schema + 配置基础 | 5-DB 初始化 + `resolve_model()` 可调用 + CI green |
| 2 | **Phase 1: Core Workflow** | 端到端 Research→Design→Review→Impl 可运行 | 单任务 e2e 流程通过 + Watchdog Tier 0 在线 |
| 3 | **Phase 2: PR Automation + Observability** | PR 全生命周期自动化 + 可观测性上线 | PR Shepherd 创建/监控/修复 PR + trace 可导出到 Jaeger |
| 4 | **Phase 3: Learning + Advanced** | 知识积累闭环 + 高级功能 | Self-Reflect 跑通 + knowledge.jsonl 可注入 |
| 5 | **v0.1.0-alpha** | 首个可用版本 | 全部 P0/P1 issue 关闭 + README + 安装文档 |

---

## Issue Hierarchy

每个 Gate/Phase 有一个 **Parent Issue**（顶层 epic），包含带 exit criteria 的 sub-issue checklist。Sub-issue 使用 **phase-prefixed ID** 命名。

---

### Gate 0: Agno Feasibility

> **目标**：用最小 PoC 验证 DESIGN.md 的 5 个核心 Agno 假设，失败则调整设计。
>
> 对应 DESIGN.md §12 风险矩阵中的高概率风险。

| ID | Title | Priority | Area | Exit Criteria |
|----|-------|----------|------|---------------|
| **G0-A** | Workflow/Step/Loop 原语验证 | P0-critical | area/workflow | 3-step workflow 含 Loop(max=2) 正确执行，step 间 `previous_step_content` 传递正确 |
| **G0-B** | HITL 兼容性验证 | P0-critical | area/workflow | `WorkflowPauseRequest` 暂停 workflow → checkpoint 持久化 → `resume()` 恢复执行成功 |
| **G0-C** | session_state 跨步骤传递 | P0-critical | area/persistence | 5 个 step 间通过 `session_state` 传递 dict，Loop 内修改后可在 Loop 外读取 |
| **G0-D** | TeamMode.broadcast + coordinate | P1-high | area/agents | broadcast: 3 agent 并行收到同一输入；coordinate: leader 分配任务给 members |
| **G0-E** | Multi-model Agent 实例化 | P1-high | area/agents | 同一 Team 内混合 Claude + Gemini + OpenAI agent，全部正常响应 |

```
Parent Issue: "Gate 0: Agno Feasibility Validation"
  - [ ] #G0-A Workflow/Step/Loop 原语验证
  - [ ] #G0-B HITL 兼容性验证 (WorkflowPauseRequest + resume)
  - [ ] #G0-C session_state 跨步骤传递
  - [ ] #G0-D TeamMode.broadcast + coordinate 验证
  - [ ] #G0-E Multi-model Agent 实例化验证
  Exit: 5/5 通过 → 进入 Phase 0；任一 P0 失败 → 修订 DESIGN.md 对应章节
```

---

### Phase 0: Foundation

> **目标**：搭建项目脚手架、持久化层、配置系统，为 Phase 1 提供可用基础。
>
> 对应 DESIGN.md §11 Phase 0。

| ID | Title | Priority | Area | Exit Criteria |
|----|-------|----------|------|---------------|
| **P0-01** | 项目脚手架 + pyproject.toml + CI | P0-critical | area/workflow | `uv sync` 成功 + GitHub Actions green |
| **P0-02** | SQLite 5-DB 持久化层 | P0-critical | area/persistence | traces / events / mail / metrics / merge-queue 5 个 DB 初始化 + WAL 模式 |
| **P0-03** | Decision Gate 协议 | P0-critical | area/persistence | `decision_gates` 表 + REST API (`POST /decision-gates`, `POST /decision-gates/{id}/resolve`) |
| **P0-04** | 6 级模型解析链 | P0-critical | area/agents | `orchestra.yaml` 解析 + `resolve_model()` 6 级优先级正确 |
| **P0-05** | 通用工具词汇表 + provider 翻译层 | P1-high | area/agents | 参考 CAO `tool_mapping.py`，至少覆盖 GitHub + Shell + FileSystem |
| **P0-06** | WorkUnit 数据模型 | P0-critical | area/workflow | `WorkUnit` dataclass + DAG 验证（无环 + file_scope 不重叠） |
| **P0-07** | 统一任务 Schema | P2-medium | area/workflow | 适配 CCW `task-schema.json` |
| **P0-08** | 知识库 JSONL Schema + Pydantic | P1-high | area/learning | `KnowledgeEntry` model + 读写 `knowledge.jsonl` |
| **P0-09** | stage1_outputs 表 | P2-medium | area/learning | Memory Consolidation Phase 1 存储表，run_id 主键 |
| **P0-10** | 多格式日志系统 | P1-high | area/observability | session.log + events.ndjson + errors.log 三路输出 |
| **P0-11** | Secret 脱敏 logger | P1-high | area/observability | 参考 Overstory，API key / token 自动脱敏 |

```
Parent Issue: "Phase 0: Foundation Infrastructure"
  - [ ] #P0-01 项目脚手架 + pyproject.toml + CI
  - [ ] #P0-02 SQLite 5-DB 持久化层 (WAL mode)
  - [ ] #P0-03 Decision Gate 协议 (events.db + REST API)
  - [ ] #P0-04 6 级模型解析链 (orchestra.yaml + resolve_model)
  - [ ] #P0-05 通用工具词汇表 + provider 翻译层
  - [ ] #P0-06 WorkUnit 数据模型 (DAG 验证)
  - [ ] #P0-07 统一任务 Schema
  - [ ] #P0-08 知识库 JSONL Schema + Pydantic model
  - [ ] #P0-09 stage1_outputs 表
  - [ ] #P0-10 多格式日志系统
  - [ ] #P0-11 Secret 脱敏 logger
  Exit: 全部 P0 + P1 完成 → Phase 1 可开始
```

---

### Phase 1: Core Workflow

> **目标**：实现从 Design 到 Implementation 的完整工作流，包含跨模型审查和质量门控。
>
> 对应 DESIGN.md §11 Phase 1 + §2-4。

| ID | Title | Priority | Area | Exit Criteria |
|----|-------|----------|------|---------------|
| **P1-01** | Agent 定义（5 核心角色） | P0-critical | area/agents | Scout / Architect / Specialist / Plan Critic / Design Expert / Implementer 可实例化并响应 |
| **P1-02** | Callable Factory 动态角色加载 | P1-high | area/agents | `resolve_design_members()` 按 project_tags 动态加载 Specialist |
| **P1-03** | Design Team (coordinate) | P0-critical | area/agents | TeamMode.coordinate 运行，Architect 分配子任务给 Specialist，产出 `final_design.md` |
| **P1-04** | Plan Review Gate | P0-critical | area/review-gate | broadcast 3 对抗审查者，ALL PASS，Max 3 轮 + Decision Gate 升级 |
| **P1-05** | Design Review Gate | P0-critical | area/review-gate | broadcast 5+ 专家，ALL APPROVE，Max 3 轮 + Decision Gate 升级 |
| **P1-06** | 跨模型审查矩阵 | P0-critical | area/review-gate | `resolve_adversarial_reviewer()` 保证写者/审查者不同 provider + Fresh Reviewer 规则 |
| **P1-07** | Work Unit Decomposition | P0-critical | area/workflow | 设计 → WorkUnit DAG（DoD / file_scope / dependencies），DAG 合法性验证 |
| **P1-08** | Per-Unit 4-Phase Loop | P0-critical | area/workflow | IMPL → VALIDATE → ADV.REVIEW → COMMIT 循环，DAG 调度独立 unit 并行 |
| **P1-09** | Final Integration Review | P0-critical | area/workflow | 跨 work unit 集成审查（接口/状态/回归），失败 → Decision Gate 暂停 |
| **P1-10** | Quality Gates | P0-critical | area/workflow | test + lint + typecheck 阻塞门控，`run_quality_gates()` 返回 bool |
| **P1-11** | File Scoping 验证 | P1-high | area/workflow | work unit file_scope 不重叠检测 + 告警 |
| **P1-12** | Watchdog Tier 0 | P1-high | area/watchdog | 进程监控 30s + warn/nudge + Decision Gate 感知 |
| **P1-13** | WorkflowAgent 对话入口 | P1-high | area/workflow | `/orchestra` CLI 入口 + 参数解析 |

```
Parent Issue: "Phase 1: Core Workflow Engine"
  - [ ] #P1-01 Agent 定义（5 核心角色）
  - [ ] #P1-02 Callable Factory 动态角色加载
  - [ ] #P1-03 Design Team (TeamMode.coordinate)
  - [ ] #P1-04 Plan Review Gate (broadcast, 3 critics, ALL PASS)
  - [ ] #P1-05 Design Review Gate (broadcast, 5+ experts, ALL APPROVE)
  - [ ] #P1-06 跨模型审查矩阵 + Fresh Reviewer 规则
  - [ ] #P1-07 Work Unit Decomposition (设计 → DAG)
  - [ ] #P1-08 Per-Unit 4-Phase Loop (IMPL→VALIDATE→REVIEW→COMMIT)
  - [ ] #P1-09 Final Integration Review (阻塞性 gate)
  - [ ] #P1-10 Quality Gates (test + lint + typecheck)
  - [ ] #P1-11 File Scoping 验证 (无重叠)
  - [ ] #P1-12 Watchdog Tier 0 (30s 进程监控)
  - [ ] #P1-13 WorkflowAgent 对话入口 (/orchestra CLI)
  Exit: 单任务 e2e 流程（Design→Review→Impl→Integration）通过
```

---

### Phase 2: PR Automation + Observability

> **目标**：实现 PR 全生命周期自动化和系统可观测性。
>
> 对应 DESIGN.md §11 Phase 2 + §5-8。

| ID | Title | Priority | Area | Exit Criteria |
|----|-------|----------|------|---------------|
| **P2-01** | PR Shepherd 状态机 | P0-critical | area/pr-lifecycle | MONITORING → FIXING → HANDLING_REVIEWS → DONE 状态流转正确 |
| **P2-02** | Reaction Engine（9 事件类型） | P0-critical | area/reaction-engine | 覆盖全部 EventType + 指纹去重 + 冷却期 + 升级链 |
| **P2-03** | Merge Queue（4 层解冲突） | P1-high | area/merge-queue | 4 层冲突解决管道 + 历史模式学习 |
| **P2-04** | EventStore（16 事件类型） | P0-critical | area/observability | 16 事件类型 + 7 索引（含 decision_gate_* / wu_blocked / correlation_id） |
| **P2-05** | Correlation ID 中间件 | P1-high | area/observability | REST / Webhook / CLI 入口生成 correlation_id，贯穿所有事件 |
| **P2-06** | Activity State 衰减模型 | P2-medium | area/watchdog | 6 状态 + 时间衰减 → `get_agent_activity_state()` |
| **P2-07** | Decision Gate Dashboard | P1-high | area/observability | 列出 pending gates + 审批操作 + 历史记录 |
| **P2-08** | OTel trace 导出 | P1-high | area/observability | Jaeger / Grafana Tempo 集成，`setup_tracing()` 输出可查看 |
| **P2-09** | TUI / Web Dashboard | P2-medium | area/observability | 实时展示 workflow 状态、agent 活动、质量门控结果 |
| **P2-10** | Watchdog Tier 1-2 | P1-high | area/watchdog | AI triage（Tier 1）+ 持久监控（Tier 2） |

```
Parent Issue: "Phase 2: PR Automation + Observability"
  - [ ] #P2-01 PR Shepherd 状态机
  - [ ] #P2-02 Reaction Engine (9 事件类型 + 去重 + 冷却)
  - [ ] #P2-03 Merge Queue (4 层解冲突管道)
  - [ ] #P2-04 EventStore (16 事件类型 + 7 索引)
  - [ ] #P2-05 Correlation ID 中间件
  - [ ] #P2-06 Activity State 衰减模型
  - [ ] #P2-07 Decision Gate Dashboard
  - [ ] #P2-08 OTel trace 导出 (Jaeger/Tempo)
  - [ ] #P2-09 TUI / Web Dashboard
  - [ ] #P2-10 Watchdog Tier 1-2 (AI triage + 持久监控)
  Exit: PR Shepherd 完成创建→监控→修复→合并 + trace 可导出
```

---

### Phase 3: Learning + Advanced

> **目标**：实现知识积累闭环和高级编排功能。
>
> 对应 DESIGN.md §11 Phase 3 + §9。

| ID | Title | Priority | Area | Exit Criteria |
|----|-------|----------|------|---------------|
| **P3-01** | Self-Reflect 管道 | P0-critical | area/learning | 5 阶段跑通：PR 分析 → 会话挖掘 → 配置反思 → 整合 → 注入 |
| **P3-02** | Memory Consolidation Phase 1 | P0-critical | area/learning | per-run 提取 → stage1_outputs 表，run_id 主键 |
| **P3-03** | Memory Consolidation Phase 2 | P0-critical | area/learning | 全局整合 → knowledge.jsonl 更新 + MEMORY.md 物化 |
| **P3-04** | 整合触发机制 | P1-high | area/learning | 每 5 个 workflow run 自动 / 手动 / cron 每日 |
| **P3-05** | 知识库选择性注入 | P1-high | area/learning | 按 affectedFiles / tags 过滤，仅注入相关知识 |
| **P3-06** | Agent 身份/CV 持久化 | P2-medium | area/agents | agent 积累经验和偏好跨 session 保持 |
| **P3-07** | 预算断路器 | P1-high | area/observability | per_task + per_session 成本限制，80% 告警 |
| **P3-08** | 成本优先路由 + 可用性感知 | P2-medium | area/agents | cheapest-available / fastest / highest-quality 策略切换 |
| **P3-09** | PluginEval 集成 | P3-low | area/agents | agent/skill 质量评估（参考 Conductor） |
| **P3-10** | 递归嵌套编排 | P3-low | area/workflow | Sub-Orchestrator 支持 |
| **P3-11** | Research Stage（Scout Agent） | P2-medium | area/workflow | 可选 Stage 1 代码库探索 + 先例研究 |

```
Parent Issue: "Phase 3: Learning + Advanced"
  - [ ] #P3-01 Self-Reflect 管道 (5 阶段)
  - [ ] #P3-02 Memory Consolidation Phase 1 (per-run)
  - [ ] #P3-03 Memory Consolidation Phase 2 (global)
  - [ ] #P3-04 整合触发机制
  - [ ] #P3-05 知识库选择性注入
  - [ ] #P3-06 Agent 身份/CV 持久化
  - [ ] #P3-07 预算断路器
  - [ ] #P3-08 成本优先路由 + 可用性感知
  - [ ] #P3-09 PluginEval 集成
  - [ ] #P3-10 递归嵌套编排 (Sub-Orchestrator)
  - [ ] #P3-11 Research Stage (Scout Agent)
  Exit: Self-Reflect 跑通 + knowledge.jsonl 可注入下次 session
```

---

### v0.1.0-alpha

> **目标**：首个可对外使用的版本。

```
Parent Issue: "v0.1.0-alpha Release"
  - [ ] 全部 P0-critical + P1-high issue 关闭
  - [ ] README.md 安装/使用文档
  - [ ] orchestra.yaml 示例配置
  - [ ] e2e 集成测试通过
  - [ ] pypi / GitHub Release 发布
```

---

## Issue Dependencies（跨阶段）

```
G0-A ──→ P0-01 (脚手架依赖框架验证)
G0-B ──→ P0-03 (Decision Gate 依赖 HITL 验证)
G0-C ──→ P0-02 (持久化层依赖 session_state 验证)
G0-D ──→ P1-03, P1-04 (Team 依赖 TeamMode 验证)
G0-E ──→ P0-04 (模型解析依赖多模型验证)

P0-02 ──→ P0-03 (Decision Gate 依赖 events.db)
P0-04 ──→ P1-01 (Agent 定义依赖模型解析)
P0-06 ──→ P1-07 (Decomposition 依赖 WorkUnit 模型)

P1-01 ──→ P1-03, P1-04, P1-05 (Team 依赖 Agent 定义)
P1-06 ──→ P1-08 (4-Phase Loop 的 ADV.REVIEW 依赖审查矩阵)
P1-07 ──→ P1-08 (Per-Unit 执行依赖 Decomposition)
P1-08 ──→ P1-09 (Integration Review 依赖所有 unit 完成)

P1-09 ──→ P2-01 (PR Shepherd 依赖 Integration 通过)
P2-04 ──→ P2-02 (Reaction Engine 依赖 EventStore)
P2-04 ──→ P2-05 (Correlation ID 依赖 EventStore)

P3-02 ──→ P3-03 (Phase 2 整合依赖 Phase 1 提取)
P3-03 ──→ P3-05 (注入依赖整合后的 knowledge.jsonl)
```

---

## Summary

| Milestone | Issues | P0 | P1 | P2 | P3 |
|-----------|--------|----|----|----|----|
| Gate 0 | 5 | 3 | 2 | 0 | 0 |
| Phase 0 | 11 | 4 | 4 | 2 | 0 |
| Phase 1 | 13 | 9 | 4 | 0 | 0 |
| Phase 2 | 10 | 3 | 4 | 2 | 0 |
| Phase 3 | 11 | 3 | 3 | 3 | 2 |
| **Total** | **50** | **22** | **17** | **7** | **2** |
