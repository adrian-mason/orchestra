# 架构分析调研报告

## 执行摘要

六个参考项目在编排架构上呈现出高度一致的层级化拓扑选择，但在 agent 通信、状态持久化和容错机制上存在显著差异。CAO 和 Composio 采用 tmux 进程隔离 + HTTP/文件系统通信；Overstory 和 Metaswarm 构建了 SQLite/BEADS 支撑的消息系统；CCW 使用 JSONL 消息总线；Conductor 完全依赖 Claude Code 原生工具。最有价值的架构模式包括：CAO 的三原语编排模型、Overstory 的四层合并解冲突管道、Metaswarm 的质量门控状态机、以及 Composio 的插件槽位体系。

## 逐项目分析

### 1. AWS CLI Agent Orchestrator (CAO)

- **发现**：
  - **编排拓扑**：严格的层级化 Supervisor/Worker 架构。单一 `code_supervisor` 协调 `developer` 和 `reviewer` 两类 worker。Supervisor 从不直接写代码，仅通过三个编排原语委派任务。架构图定义于 `CODEBASE.md:5-55`。
  - **Agent 通信**：三个 MCP 工具通过 FastAPI HTTP（localhost:9889）暴露：
    - **Handoff**（同步阻塞）：创建 terminal → 发送任务 → 轮询至 COMPLETED → 获取输出 → 退出 worker（`mcp_server/server.py:246-321`）
    - **Assign**（异步非阻塞）：创建 terminal → 发送任务 → 立即返回 terminal_id（`server.py:430-448`）
    - **Send Message**（邮箱）：SQLite 消息队列，watchdog 文件系统观察器触发投递（`services/inbox_service.py:120-150`）
  - **状态管理**：SQLAlchemy 三模型（Terminal、Inbox、Flow）存储于 SQLite（`clients/database.py:19-59`）。终端状态不存储于数据库，而是通过各 provider 实时解析 tmux `capture-pane` 输出计算。
  - **容错**：Handoff 超时可配（默认 600s，最大 3600s）；Inbox 投递失败标记为 FAILED；14 天自动数据清理；Provider 级别重试（如 Gemini CLI 提取重试 3 次）。

- **模式提取**：
  - **tmux 作为隔离边界**：每个 agent 运行于独立 tmux 窗口，通过 `CAO_TERMINAL_ID` 环境变量标识
  - **三原语编排模型**：handoff（同步）/ assign（异步）/ send_message（邮箱）——所有高层级工作流由这三个原语组合而成
  - **Provider 适配器模式**：`BaseProvider` ABC + 7 个具体实现，每个 provider 约 200-400 行正则终端解析

- **与 Orchestra 的关系**：三原语模型可直接映射为 Agno Workflow 的同步步骤、并行分发和异步消息传递。Provider 适配器模式为 Orchestra 的多模型支持提供了参考架构。

### 2. Composio Agent Orchestrator

- **发现**：
  - **编排拓扑**：两层层级——Orchestrator agent（只读协调，不写代码）+ Worker agent（独立 worktree + 独立 PR）。角色通过 ID 模式匹配区分（`agent-selection.ts:21-27`）。
  - **Agent 通信**：运行时插件中介的消息传递。`sendMessage()` 通过 tmux pane 向 agent session 发送文本。无 agent 间直连，所有协调通过 orchestrator 流转。
  - **状态管理**：**无数据库**，全部使用扁平文件。配置为 Zod 验证的 YAML，会话元数据为 key=value 对，使用 SHA-256 哈希路径命名防冲突（`~/.agent-orchestrator/{hash}-{projectId}/`）。
  - **容错**：Reaction Engine 支持可配重试次数和时间升级（`escalateAfter: "30m"`, `retries: 2`）；空闲检测（默认 10 分钟阈值）；SCM/Agent 探测失败优雅降级。
  - **插件体系**：8 个插件槽位（Runtime / Agent / Workspace / Tracker / SCM / Notifier / Terminal / Lifecycle），21 个内置插件包，支持 npm 包和本地路径加载。

- **模式提取**：
  - **插件槽位架构**：TypeScript 接口 + Zod 验证定义每个槽位合约
  - **Reaction Engine**：事件 → 动作 → 升级的可配响应链，指纹去重防重复分发
  - **扁平文件持久化 + 哈希命名空间**：轻量但有效的多实例隔离

- **与 Orchestra 的关系**：插件槽位架构可参考用于 Orchestra 的扩展点设计。Reaction Engine 模式（CI 失败 → 转发 agent → 重试 → 升级至人工）是 PR Review 自动化的核心参考。

### 3. Metaswarm

- **发现**：
  - **编排拓扑**：三层层级+递归嵌套——Swarm Coordinator（跨 repo 多 Issue 并行）→ Issue Orchestrator（单 Issue 全生命周期）→ 19 个 Specialist Agents。Issue Orchestrator 可递归生成 Sub-Orchestrator（`skills/start/SKILL.md:690-716`）。
  - **Agent 通信**：双模式，启动时检测（`guides/agent-coordination.md:9-10`）：
    - **Task Mode**（默认）：fire-and-forget `Task()` 子 agent，无跨 agent 通信
    - **Team Mode**（可用时）：持久化 teammate + `SendMessage` 直接消息
  - **状态管理**：BEADS CLI（`bd`）驱动，SQLite 数据库（`.beads/beads.db`）+ JSONL 导出。关键持久化：`active-plan.md`、`project-context.md`、`execution-state.md`。上下文恢复协议可从 `.beads/` 重建状态（`skills/orchestrated-execution/SKILL.md:504-579`）。
  - **容错**：四阶段执行循环 + 阻塞质量门控；每门最多 3 次重试后升级人工；结构化恢复协议（DIAGNOSE → CLASSIFY → RETRY → ESCALATE）；外部工具升级链（Model A × 2 → Model B × 2 → Claude × 1 → 用户）。

- **模式提取**：
  - **"Trust nothing, verify everything"**：编排器独立运行验证，从不信任子 agent 自报
  - **Fresh Reviewer 规则**：对抗审查者永远是全新 Task 实例，防止锚定偏差
  - **质量门控作为阻塞状态转换**：非建议性的——FAIL 意味着重试或升级，绝不跳过
  - **BEADS 作为单一事实来源**：agent 通过数据库协调，而非消息

- **与 Orchestra 的关系**：质量门控状态机、fresh reviewer 规则和上下文恢复协议是 Orchestra Design Review Gate 的核心参考。递归嵌套模式适用于大型 epic 分解。

### 4. Overstory

- **发现**：
  - **编排拓扑**：层级化，深度限制（默认 maxDepth=2）。四层：Orchestrator（跨 repo 协调）→ Coordinator（项目级持久编排器）→ Lead/Supervisor（团队长，可生成子 worker）→ Workers（Scout/Builder/Reviewer/Merger）。定义于 `src/types.ts:165-178`。
  - **Agent 通信**：自定义 SQLite 邮件系统（`src/mail/store.ts`、`src/mail/client.ts`）。WAL 模式支持多 agent 并发访问。13 种类型化消息涵盖语义和协议两个维度。广播地址支持（@all, @builders 等）。查询延迟约 1-5ms。
  - **状态管理**：五个 SQLite 数据库（`mail.db`、`sessions.db`、`events.db`、`metrics.db`、`merge-queue.db`），全部使用 WAL 模式 + busy_timeout=5000。
  - **容错**：三层 Watchdog 系统：
    - Tier 0：机械进程监控（30s 间隔），4 级渐进升级（warn → nudge → AI triage → terminate）
    - Tier 1：AI 辅助故障分类（读取最后 50 行日志，Claude `--print` 模式分类）
    - Tier 2：Monitor agent 持久巡逻
    - Decision Gate 感知：watchdog 跳过等待人工决策的 agent
  - **合并系统**：4 层升级（`src/merge/resolver.ts`）：clean merge → auto-resolve → AI-resolve → re-imagine。FIFO 队列 + SQLite 持久化。历史模式学习跳过失败策略。

- **模式提取**：
  - **SQLite 邮件系统**：类型化协议消息（worker_done、merge_ready、dispatch）强制协调合约
  - **4 层合并解冲突管道**：渐进升级 + 历史学习，首创性设计
  - **3 层 Watchdog**：ZFC 原则——可观测状态（tmux/PID 存活）是事实来源，而非记录状态
  - **"Instruction Overlay" 模型**：Layer 1（base .md 定义 HOW）+ Layer 2（动态 CLAUDE.md 定义 WHAT）

- **与 Orchestra 的关系**：SQLite 邮件系统和 Watchdog 是最工程化的实现，可直接参考。4 层合并管道是 Orchestra Merge Queue 的核心设计来源。Instruction Overlay 模型适用于 agent profile 设计。

### 5. Claude-Code-Workflow (CCW)

- **发现**：
  - **编排拓扑**：三层层级——Skill 层（SKILL.md 工作流定义）→ Coordinator（team-coordinate 协调器）→ team-worker agents。`team-coordinate` 的 coordinator 动态生成 worker role-spec 文件，再通过 Claude Code 的 `Agent()` 工具生成 worker（`skills/team-coordinate/SKILL.md:1-150`）。每管道最多 5 个 worker 角色。
  - **Agent 通信**：JSONL 消息总线（`ccw/src/tools/team-msg.ts`）+ Claude Code 原生 `SendMessage`。Beat/Cadence 模型：coordinator 仅在回调/恢复时唤醒，worker 可 "fast-advance" 跳过 coordinator 往返。
  - **状态管理**：混合——会话 JSON（`.workflow/active/`）、任务 JSON（`.task/`）、检查点 JSON（`.workflow/checkpoints/`）、内存 SQLite（`memory-store.ts`）、CLI 历史 SQLite（`cli-history-store.ts`）、消息 JSONL。**队列调度器仅内存**（`queue-scheduler-service.ts:14`明确声明无持久化）。
  - **容错**：Recovery Handler 处理 PreCompact 事件 + 互斥锁防竞态；Stop Handler 软执行（永远返回 `continue: true`）；team-supervisor 崩溃恢复协议从 checkpoint 重建上下文。

- **模式提取**：
  - **Beat/Cadence 事件驱动模型**：coordinator 仅在回调时唤醒，减少不必要轮询
  - **动态角色生成**：运行时从任务分析生成 agent 配置
  - **统一任务 Schema**：564 行 JSON Schema 涵盖身份、分类、范围、依赖、收敛、文件、执行配置

- **与 Orchestra 的关系**：Beat/Cadence 模型适用于 Orchestra 的 coordinator 设计。统一任务 Schema 是任务定义的直接参考。动态角色生成模式可适配为 Agno 运行时 agent 配置。

### 6. wshobson/agents (Conductor)

- **发现**：
  - **编排拓扑**：层级化，但完全基于提示词。无自定义运行时——所有编排通过 Claude Code 内置 `Task`/`Teammate`/`SendMessage` 工具。工作流定义为结构化 Markdown 命令文件（如 `full-stack-feature.md` 定义 9 步管道）。
  - **Agent 通信**：纯 Claude Code 原生工具。Team 通信依赖实验性 Agent Teams 功能（`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`）。
  - **状态管理**：文件 JSON 持久化（`conductor/setup_state.json`、`tracks/{trackId}/metadata.json`、`plan.md`）。支持跨会话恢复。
  - **容错**：显式 "halt-on-failure" 协议；阶段检查点需人工批准；Conductor 追踪每个 commit SHA 支持语义回滚。无自动重试、断路器或死信队列。

- **模式提取**：
  - **Command-as-Orchestrator**：Markdown 文件即编排逻辑
  - **Phase-Gate Checkpoints**：阶段间人工审批门控
  - **PluginEval 框架**：三层质量评估（静态分析 + LLM 评委 + 蒙特卡洛），10 维加权评分

- **与 Orchestra 的关系**：Phase-Gate Checkpoint 模式和 PluginEval 框架可直接借鉴。但缺乏程序化编排代码，所有模式需要重新实现。

## 横向对比矩阵

| 维度 | CAO | Composio | Metaswarm | Overstory | CCW | Conductor |
|------|-----|----------|-----------|-----------|-----|-----------|
| **编排拓扑** | 2 层 Supervisor/Worker | 2 层 Orchestrator/Worker | 3 层 + 递归嵌套 | 4 层深度限制 | 3 层 Skill/Coord/Worker | 2 层提示词驱动 |
| **通信机制** | MCP over HTTP + SQLite 邮箱 | tmux sendMessage | Task()/SendMessage 双模式 | SQLite 邮件系统(13 类型) | JSONL 总线 + SendMessage | Claude Code 原生工具 |
| **状态持久化** | SQLite (3 模型) | 扁平文件 (无 DB) | BEADS SQLite + JSONL | 5 个 SQLite DB (WAL) | 混合 JSON/JSONL/SQLite | JSON 文件 |
| **进程隔离** | tmux 窗口 | tmux + git worktree | git worktree | tmux + git worktree | Claude Code 子进程 | Claude Code Task |
| **容错级别** | 中（超时+清理） | 中（重试+升级） | 高（3 重试+升级链） | 高（3 层 watchdog） | 中（recovery handler） | 低（halt-on-failure） |
| **运行时代码** | Python FastAPI | TypeScript Node.js | Bash + Markdown | TypeScript Bun | TypeScript Node.js | 纯 Markdown |
| **许可证** | Apache-2.0 | MIT | MIT | MIT | MIT | MIT |

## 推荐采纳清单

1. **🔴 CAO 三原语编排模型**（`mcp_server/server.py`）：handoff/assign/send_message 映射为 Agno Workflow 同步步骤、Team 并行分发、Agent 异步消息。这是最简洁的编排原语抽象。

2. **🔴 Overstory 4 层合并解冲突管道**（`src/merge/resolver.ts`）：clean → auto-resolve → AI-resolve → re-imagine，带历史模式学习。Orchestra 的 Merge Queue 应采用此设计。

3. **🔴 Overstory 3 层 Watchdog**（`src/watchdog/daemon.ts`）：warn → nudge → AI triage → terminate，ZFC 可观测状态原则。Orchestra 需要此级别的健康监控。

4. **🔴 Metaswarm 质量门控状态机**（`skills/orchestrated-execution/SKILL.md`）：IMPLEMENT → VALIDATE → ADVERSARIAL REVIEW → COMMIT，3 次重试限制 + 人工升级。Orchestra Design Review Gate 的核心参考。

5. **🟡 Composio 插件槽位架构**（`packages/core/src/types.ts`）：8 个插件槽位 + TypeScript 接口 + Zod 验证。为 Orchestra 扩展点设计提供参考。

6. **🟡 Composio Reaction Engine**（`packages/core/src/lifecycle-manager.ts`）：事件驱动响应 + 指纹去重 + 时间升级。PR Review 自动化的实现参考。

7. **🟡 Overstory SQLite 邮件系统**（`src/mail/`）：类型化协议消息 + WAL 并发 + 广播地址。如果 Orchestra 选择 SQLite 作为 agent 通信后端，这是最佳参考。

8. **🟢 CCW Beat/Cadence 模型**：事件驱动 coordinator 唤醒 + worker fast-advance。减少不必要的 coordinator 轮询开销。

9. **🟢 Metaswarm 上下文恢复协议**（`skills/orchestrated-execution/SKILL.md:504-579`）：从持久化状态重建 agent 上下文。长时任务的容错必备。
