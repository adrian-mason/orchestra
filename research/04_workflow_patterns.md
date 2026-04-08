# 工作流模式调研报告

## 执行摘要

六个项目在工作流设计上呈现从 3 阶段（CAO）到 9 阶段（Metaswarm）的丰富变体。Design Review Gate 方面，Metaswarm 最成熟（5-6 并行审查者 + 3 轮限制 + 人工升级），Overstory 次之（Lead agent 门控 + Watchdog 升级），CCW 提供检查点式审查（pass/warn/block 裁决 + 质量评分）。PR Review 自动化方面，Composio 的 Reaction Engine 最完整（CI 失败/审查评论/合并冲突的自动响应），Metaswarm 的 PR Shepherd 最自治（状态机 + 60s GTG 轮询）。Self-Reflect/Learning 方面，Metaswarm 和 CCW 有最成熟的实现。Conductor 强在人工阶段门控和 TDD 集成。

## 逐项目分析

### 1. AWS CLI Agent Orchestrator (CAO)

- **发现**：
  - **工作流阶段**（`code_supervisor.md:40-50`）：
    1. Supervisor 接收用户请求
    2. Supervisor 写任务描述文件
    3. Supervisor 通过 handoff/assign 委派 Developer
    4. Developer 编码并返回结果
    5. Supervisor 转发给 Code Reviewer（强制）
    6. Reviewer 反馈
    7. 有反馈则循环步骤 4-6 直至 Reviewer 批准
    8. Supervisor 综合最终输出
  - **Review Gate**：顺序 handoff（同步）。Supervisor 提示词要求 "review cycle MUST continue until approved"，但为**提示词驱动而非程序化**——无轮数限制、无升级机制。
  - **并行执行**：assign 模式支持并行任务分发，worker 通过 send_message 回调
  - **Flow 调度**：`services/flow_service.py` 使用 apscheduler cron 调度，支持 pre-flight 脚本条件跳过 + 模板变量替换
  - **PR 自动化**：无。无 GitHub API 集成、无 webhook、无 reaction
  - **Self-Reflect/Learning**：无。每次会话无状态。`AGENT_CONTEXT_DIR` 存在于常量但未被使用

- **模式提取**：
  - **提示词驱动工作流**：Supervisor 系统提示定义工作流，非硬编码状态机——灵活但不确定
  - **三原语组合高层级工作流**：所有工作流由 handoff/assign/send_message 组合
  - **Cron 条件调度**：pre-flight 脚本的条件执行模式

- **与 Orchestra 的关系**：Cron 条件调度可用于 Orchestra 的定时任务。但工作流阶段过于简单，缺乏 Design Review Gate 和 PR 自动化。

### 2. Composio Agent Orchestrator

- **发现**：
  - **工作流阶段**（会话生命周期状态机 `types.ts:28-46`）：
    ```
    spawning → working → pr_open → ci_failed / review_pending
                                      |              |
                              changes_requested   approved
                                      |              |
                                      +→ mergeable → merged → cleanup → done
    ```
    附加状态：needs_input、stuck、errored、killed、idle、terminated
  - **端到端流程**：
    1. Issue 分配（`ao spawn INT-1234`）→ 获取 issue → 创建 worktree → 分层 prompt → 启动 agent
    2. Agent 工作 → 创建分支 → 推送 → 开 PR
    3. Lifecycle Manager 轮询：PR 检测（分支名匹配）、CI 状态（GraphQL 批量查询）、Review 决策
    4. **Reaction Engine 自动响应**：
       - `ci-failed`：转发 CI 失败详情（含重试和升级）
       - `changes-requested`：转发审查评论
       - `bugbot-comments`：转发 bot 审查
       - `merge-conflicts`：通知 agent rebase
       - `approved-and-green`：通知人工或自动合并
       - `agent-idle`：nudge 空闲 agent
       - `agent-stuck`：升级至人工
    5. Review Backlog Dispatch：指纹追踪 + 去重 → 仅分发新/变更评论
  - **任务分解**（可选）：`decomposer.ts` LLM 递归分解，max depth 3，需人工批准
  - **Webhook 加速**：GitHub webhook 支持（`config.ts:99-117`）比轮询更快的事件检测
  - **Self-Reflect/Learning**：无显式学习机制。FeedbackReportStore 允许 agent 提交 bug 报告和改进建议供人工审查。

- **模式提取**：
  - **Reaction Engine**：最完整的事件 → 动作 → 升级链，覆盖 CI 失败/审查/冲突/空闲/卡住
  - **指纹去重**：排序 ID 生成指纹，仅分发新/变更评论
  - **分层 Prompt 组装**：base + config + user rules 三层

- **与 Orchestra 的关系**：Reaction Engine 是 Orchestra PR Review 自动化的核心参考。完整的会话状态机可直接映射为 Agno Workflow 状态。指纹去重防止重复通知是工程细节的最佳实践。

### 3. Metaswarm

- **发现**：
  - **9 阶段工作流**（`skills/start/SKILL.md` + `agents/issue-orchestrator.md`）：
    1. **Research** — Researcher Agent 探索代码库、先例、约束
    2. **Plan** — Architect Agent 创建实施计划
    3. **Plan Review Gate** — 3 个对抗审查者（可行性/完整性/范围对齐）独立验证，ALL 必须 PASS，最多 3 次迭代（`skills/plan-review-gate/SKILL.md`）
    4. **Design Review Gate** — 5-6 个专家 agent（PM/Architect/Designer/Security/UX/CTO）并行审查，ALL 必须批准，最多 3 次迭代后人工升级（`skills/design-review-gate/SKILL.md`）。返回结构化 JSON（verdict/blockers/suggestions/questions），类型特定字段（PM: use_case_analysis，Security: threat_model）
    5. **Work Unit Decomposition** — 分解为工作单元（DoD 项 + 文件范围 + 依赖 DAG）
    6. **Orchestrated Execution** — 4 阶段循环 per work unit（IMPLEMENT → VALIDATE → ADVERSARIAL REVIEW → COMMIT），独立单元并行
    7. **Final Comprehensive Review** — 跨单元集成检查
    8. **PR Creation + PR Shepherd** — 自治 CI 监控、审查评论处理、thread 解决（`skills/pr-shepherd/SKILL.md`）
       - 状态机：MONITORING → FIXING → HANDLING_REVIEWS → WAITING_FOR_USER → DONE
       - GTG（Good-To-Go）工具每 60 秒检查
       - 自动修复简单问题（lint、类型错误、agent 自身代码的测试失败）
       - 4 小时软超时 + 检查点
    9. **Closure & Learning** — 知识提取 via `/self-reflect`，BEADS epic 关闭
  - **Self-Reflect 详情**（`commands/self-reflect.md`）：
    - Phase A：PR 评论分析 — 获取 PR 评论，评估 CodeRabbit 学习，质量过滤（ACCEPT/REJECT/TRANSFORM）
    - Phase B：对话 & 会话挖掘 — 分析上下文中的战略模式
    - Phase C：配置反思 — 审查 Claude 指令改进机会
    - 质量过滤器：能否防止 bug？能否节省审查周期？agent 能否据此行动？
    - 规范化规则：去除 PR 引用、泛化路径、祈使语气、包含 WHY
    - 去重和冲突解决后存储

- **模式提取**：
  - **双层 Review Gate**：Plan Review（3 对抗审查者）+ Design Review（5-6 专家），ALL must pass
  - **PR Shepherd 状态机**：自治 CI 监控 + 自动修复 + 审查处理
  - **Pre-PR 知识捕获**：学习在 PR 创建前提取，与代码原子提交
  - **选择性知识注入**：`bd prime` 按影响文件/关键词/工作类型过滤，防上下文膨胀
  - **"强制门控，非建议性"**：CLAUDE.md 工作流规则覆盖任何第三方 skill 的冲突指令

- **与 Orchestra 的关系**：9 阶段工作流是最全面的 SDLC 覆盖。双层 Review Gate + PR Shepherd + Self-Reflect 构成了 Orchestra 三阶段设计（Design → Review → Implementation）的全面扩展。Pre-PR 知识捕获是 Orchestra Learning 系统的关键参考。

### 4. Overstory

- **发现**：
  - **工作流阶段**：
    1. `ov init` — 初始化 .overstory/ 目录
    2. `ov coordinator start` — 持久 coordinator agent
    3. Coordinator 分解目标为 task issues
    4. Coordinator 通过 `ov sling --capability lead` 分发 leads
    5. Lead 生成 scouts 探索（Phase 1）
    6. Lead 从 scout 发现写 specs（Phase 2）
    7. Lead 生成 builders（Phase 3）
    8. Lead 可选生成 reviewers（Phase 4）
    9. Lead 发送 `merge_ready` 给 coordinator
    10. Coordinator 运行 `ov merge`（4 层冲突解决）
    11. Coordinator 关闭 tracker issues
  - **Review Gate**：Lead agent 的 4 阶段工作流（Scout → Build → Review）。Reviewer agent（`agents/reviewer.md`）只读报告。Lead 门控 merge_ready 信号——仅在审查通过后发送给 coordinator。无显式轮数限制——由 Watchdog 处理卡住。
  - **PR 自动化**：不内置。系统在 git branch/merge 层级操作。PR 创建由 coordinator 或用户处理。
  - **Decision Gates**：`decision_gate` 邮件类型（`src/types.ts:339-347`）允许 agent 暂停等待人工决策。Watchdog 识别 pending decision gates 并跳过升级。
  - **Quality Gates**：可配置（`src/types.ts:56-62`）。默认：bun test、bun run lint、bun run typecheck。agent 必须通过门控才能报告完成。
  - **Self-Reflect/Learning**：
    - Insight Analyzer（`src/insights/analyzer.ts`）：后处理提取工具使用、文件编辑、错误模式
    - Mulch 集成：合并冲突模式记录+查询、agent 故障记录
    - Agent 身份持久化（`src/agents/identity.ts`）：追踪完成会话、专长领域、最近任务作为 "CVs"
    - Checkpoint 保存/恢复（`src/agents/checkpoint.ts`）

- **模式提取**：
  - **两层指令模型**：base HOW + overlay WHAT
  - **类型化协议消息**：worker_done、merge_ready、dispatch 强制协调合约
  - **Decision Gate**：agent 可暂停等待人工决策，watchdog 识别并跳过升级
  - **Agent 身份 = CV**：追踪完成会话和专长领域

- **与 Orchestra 的关系**：Decision Gate 模式是 Orchestra human-in-the-loop 的优雅实现。Agent 身份/CV 概念可用于 Orchestra 的 agent 能力匹配。Quality Gates（test/lint/typecheck）应为 Orchestra 的标配。

### 5. Claude-Code-Workflow (CCW)

- **发现**：
  - **完整 SDLC 覆盖**：
    1. Brainstorm（`brainstorm` skill）：多角色分析 + 框架生成 + 跨角色综合
    2. Planning（`workflow-plan`/`workflow-lite-plan`）：4-5 阶段规划生成任务 JSON
    3. Execution（`workflow-execute`/`workflow-lite-execute`）：agent 并行任务执行
    4. Testing（`workflow-test-fix`/`workflow-tdd-plan`）：渐进测试层 L0-L3（静态→单元→集成→E2E）
    5. Review（`review-code`/`review-cycle`）：多维代码审查
    6. Ship（`ship` skill）：pre-flight 检查 + AI 审查 + 版本号 + changelog + PR
  - **Spec Generator**：7 阶段规范链（product brief → PRD → 架构 → epics → issues），Codex review gates
  - **Review Gate**：team-supervisor（`agents/team-supervisor.md`）：
    - 检查点式审查，裁决 pass/warn/block + 质量评分（0.0-1.0）+ 趋势分析（stable/improving/degrading）
    - 共识处理：worker 报告 `consensus_blocked` + 严重级别。HIGH 需 coordinator 介入+用户确认
    - Coordinator 处理 `capability_gap` 通过中途生成新 role-spec
  - **Self-Reflect/Learning**：
    - **Wisdom 文件**：`<session>/wisdom/*.md` — worker 和 supervisor 读/追加 wisdom
    - **Memory Capture**（`memory-capture` skill）：session compact 或 quick tips
    - **Memory Consolidation Pipeline**（`memory-consolidation-pipeline.ts`）：两阶段——Phase 1 per-session 提取存 SQLite → Phase 2 全局整合写 MEMORY.md
    - **Core Memory Store**（`core-memory-store.ts`）：SQLite 支撑，实体追踪 + 热度评分 + 访问日志 + 关联
    - **Session Solidify**（`/workflow:session:solidify`）：结晶学习为永久记忆

- **模式提取**：
  - **渐进测试层 L0-L3**：从静态分析到 E2E 的分层测试策略
  - **Quality Score + Trend**：0.0-1.0 评分 + stable/improving/degrading 趋势
  - **两阶段 Memory Consolidation**：per-session 提取 → 全局整合
  - **Wisdom 文件累积**：会话内知识共享

- **与 Orchestra 的关系**：两阶段 Memory Consolidation 是 Orchestra Learning 系统的核心参考。Quality Score + Trend 模式可用于 Orchestra 的 Review Gate 决策。Spec Generator 的 7 阶段规范链覆盖了 Design 阶段的完整路径。

### 6. wshobson/agents (Conductor)

- **发现**：
  - **Conductor 工作流**（`plugins/conductor/`）：
    1. `/conductor:setup` — 交互式项目初始化（product.md、tech-stack.md、workflow.md、风格指南）
    2. `/conductor:new-track` — 交互式规格收集（Q&A）、spec.md、plan.md（带阶段任务）
    3. `/conductor:implement` — TDD 执行循环（RED-GREEN-REFACTOR per task，阶段检查点需用户批准）
    4. `/conductor:status` — 进度监控
    5. `/conductor:revert` — 语义回滚（按 track/phase/task，使用 metadata.json 追踪的 commit SHA）
    6. `/conductor:manage` — 归档、恢复、删除、重命名 tracks
  - **Full-Stack Orchestration**：9 步管道（Requirements → DB Design → Architecture [Checkpoint 1] → DB Impl → Backend Impl → Frontend Impl → Testing+Security+Perf 并行 [Checkpoint 2] → Deployment → Documentation）
  - **Review Gate**：纯人工阶段门控——检查点需显式用户批准。无自动升级或轮数限制。comprehensive-review 有 strict-mode（Critical 发现建议停止）。
  - **PR 自动化**：无 webhook/polling。`comprehensive-review` 手动调用，支持 PR 号目标（`gh pr diff`）。
  - **Self-Reflect/Learning**：Conductor 追踪计划偏差（workflow-patterns SKILL.md:418-456），Git notes 用于任务摘要（SKILL.md:128-145）。但**无自动学习循环或 wisdom 累积**。

- **模式提取**：
  - **TDD 深度集成**：11 步任务生命周期（RED-GREEN-REFACTOR）
  - **语义回滚**：通过 commit SHA 追踪实现按 track/phase/task 级别的精确回滚
  - **交互式规格收集**：Q&A 驱动的 spec 生成

- **与 Orchestra 的关系**：TDD 集成和语义回滚是 Orchestra Implementation 阶段的参考。交互式规格收集可用于 Orchestra 的需求澄清。

## 横向对比矩阵

| 维度 | CAO | Composio | Metaswarm | Overstory | CCW | Conductor |
|------|-----|----------|-----------|-----------|-----|-----------|
| **阶段数** | 3 (delegate→review→done) | ~8 (spawn→work→PR→CI/review→merge) | 9 (research→...→closure) | ~11 (init→...→close) | 6+ (brainstorm→ship) | 6 (setup→...→manage) |
| **Plan Review** | 无 | 无 | 3 对抗审查者, 3 轮 | 无 | 无 | 无 |
| **Design Review** | 提示词循环 | 无 | 5-6 并行专家, 3 轮 | Lead 门控 | Supervisor pass/warn/block | 人工检查点 |
| **PR 自动化** | 无 | ✅ Reaction Engine | ✅ PR Shepherd SM | 无（git 层） | Ship skill | 无（手动） |
| **CI 响应** | 无 | ✅ 自动转发+重试 | ✅ 自动修复简单问题 | Quality Gates | 无 | 无 |
| **Review 响应** | 无 | ✅ 指纹去重分发 | ✅ handling-pr-comments | Reviewer agent | review-cycle | comprehensive-review |
| **Self-Reflect** | 无 | FeedbackStore | ✅ 3 阶段分析 | Insight Analyzer | ✅ 两阶段 Memory | 偏差追踪 |
| **Learning** | 无 | 无 | ✅ 知识库累积 | Mulch + CV | ✅ Wisdom + Memory | 无 |
| **Human Gate** | 无 | 升级通知 | 3 轮后升级 | Decision Gate | 共识升级 | 每阶段审批 |

## 推荐采纳清单

1. **🔴 Metaswarm 双层 Review Gate**（`skills/plan-review-gate/` + `skills/design-review-gate/`）：Plan Review（3 对抗审查者）+ Design Review（5-6 并行专家），ALL must pass，3 轮限制 + 人工升级。Orchestra 的 Review Gate 应采用此架构。

2. **🔴 Composio Reaction Engine**（`lifecycle-manager.ts`）：CI 失败/审查评论/合并冲突/空闲/卡住的自动响应链。Orchestra 的 PR Review 自动化应直接实现此模式。

3. **🔴 Metaswarm PR Shepherd**（`skills/pr-shepherd/SKILL.md`）：自治状态机（MONITORING→FIXING→HANDLING_REVIEWS→WAITING_FOR_USER→DONE）+ 60s GTG 轮询 + 自动修复简单问题。Orchestra 应实现此级别的 PR 自治。

4. **🔴 Metaswarm Self-Reflect + CCW Memory Consolidation**：
   - Metaswarm：3 阶段分析（PR 评论 → 对话挖掘 → 配置反思）+ 质量过滤 + 规范化
   - CCW：两阶段整合（per-session 提取 → 全局整合到 MEMORY.md）+ SQLite 热度评分
   - Orchestra Learning 系统应结合两者的优势。

5. **🟡 Overstory Decision Gate**（`src/types.ts:339-347`）：agent 暂停等待人工决策 + watchdog 识别跳过升级。Orchestra 的 human-in-the-loop 应采用此模式。

6. **🟡 CCW Quality Score + Trend**（`agents/team-supervisor.md`）：0.0-1.0 质量评分 + stable/improving/degrading 趋势分析。Orchestra Review Gate 的量化决策参考。

7. **🟡 Metaswarm 4 阶段执行循环**：IMPLEMENT → VALIDATE → ADVERSARIAL REVIEW → COMMIT per work unit，独立单元并行。Orchestra Implementation 阶段的核心循环。

8. **🟢 Conductor TDD 集成 + 语义回滚**：RED-GREEN-REFACTOR 循环 + commit SHA 追踪的精确回滚。Orchestra Implementation 阶段的补充。

9. **🟢 CCW Spec Generator 7 阶段链**：product brief → PRD → 架构 → epics → issues + Codex review gates。Orchestra Design 阶段的扩展参考。
