# 可观测性调研报告

## 执行摘要

六个项目的可观测性成熟度差异极大。Overstory 拥有最完整的自建可观测性栈（SQLite EventStore + 多格式日志 + TUI Dashboard + 12 种事件类型 + 6 个观测命令）；Composio 构建了自定义 ProjectObserver 模式 + 关联 ID 追踪 + Next.js Dashboard；CCW 有 JSONL 审计日志 + WebSocket 实时广播 + React Dashboard；Metaswarm 依赖 BEADS + JSONL 会话日志 + CLI 诊断命令。CAO 和 Conductor 的可观测性最弱。**无任何项目使用 OpenTelemetry**——这是 Orchestra 的差异化机会。

## 逐项目分析

### 1. AWS CLI Agent Orchestrator (CAO)

- **发现**：
  - **Trace 采集**：仅标准 Python `logging.basicConfig`（`utils/logging.py:1-24`），文件处理器输出到 `~/.aws/cli-agent-orchestrator/logs/cao_{timestamp}.log`。日志级别通过 `CAO_LOG_LEVEL` 环境变量控制。
  - **终端日志**：每个 agent 终端通过 tmux `pipe-pane` 捕获原始输出到 `logs/terminal/{terminal_id}.log`（`services/terminal_service.py:145-147`）——纯 ANSI 终端文本，非结构化。
  - **存储格式**：纯文本日志文件 + SQLite（仅用于 inbox 消息和终端元数据，非 trace）
  - **Dashboard/UI**：React + Vite + Tailwind Web 应用，组件包括 DashboardHome、TerminalView（WebSocket 实时流）、AgentPanel、FlowsPanel、InboxPanel、OutputViewer、StatusBadge、SettingsPanel
  - **会话隔离**：8 字符十六进制 terminal ID（`utils/terminal.py:27`），通过 `CAO_TERMINAL_ID` 环境变量注入 tmux 窗口
  - **无 OpenTelemetry、无结构化追踪、无 trace ID、无 span 关联**

- **模式提取**：
  - **终端输出作为 trace 代理**：pipe-pane 日志是最接近 trace 的东西，但内容是原始终端 ANSI 输出
  - **Web UI 用于手动观察**：提供实时可见性但无历史分析

- **与 Orchestra 的关系**：CAO 的 Web Dashboard 组件设计（特别是 WebSocket 终端流和状态徽章）可参考，但其可观测性基础设施不值得借鉴。

### 2. Composio Agent Orchestrator

- **发现**：
  - **Trace 采集**：自定义 `ProjectObserver` 模式（`observability.ts:302-447`）。组件通过 `createProjectObserver(config, componentName)` 创建观察者，调用 `recordOperation()` 发射 trace。覆盖：Lifecycle Manager（轮询周期、状态转换）、Web API 路由、SSE 流、WebSocket 终端。
  - **结构化日志**：stderr JSON Lines，受 `AO_LOG_LEVEL` 控制（`observability.ts:164-167`）
  - **关联 ID**：`createCorrelationId()` per-request 生成（`observability.ts:298`）。API 路由接受 `x-correlation-id` header，响应包含。SSE 快照包含 `correlationId` 和 `emittedAt`。
  - **存储格式**：进程本地 JSON 快照文件（`~/.agent-orchestrator/{hash}-observability/processes/{component}-{pid}.json`），含指标计数器、最近 trace（上限 80）、会话状态记录（上限 200）。查询时聚合。
  - **指标追踪**：15 种操作类型（`api_request`、`lifecycle_poll`、`spawn`、`sse_connect`、`websocket_connect` 等）（`observability.ts:17-32`）
  - **活动检测**：双路径——原生 JSONL（Claude Code/Codex）+ AO Activity JSONL（Aider/OpenCode）。6 种活动状态（active/ready/idle/waiting_input/blocked/exited）+ 时间衰减（active → ready 30s，ready → idle 5min）
  - **Dashboard**：Next.js 15 Web UI，含可观测性横幅（总体状态、SSE 流状态、最新关联 ID、失败原因）、`/api/observability` 聚合端点

- **模式提取**：
  - **ProjectObserver 模式**：组件注册 + 操作记录 + 聚合查询
  - **关联 ID 跨 API/SSE/WebSocket**：统一请求追踪
  - **活动状态 + 时间衰减**：6 状态活动检测，智能识别空闲/卡住

- **与 Orchestra 的关系**：ProjectObserver 模式和关联 ID 追踪是 Orchestra 自定义可观测性层的良好参考。活动状态时间衰减机制可用于 agent 健康监控。

### 3. Metaswarm

- **发现**：
  - **Trace 采集**：Hook 驱动——`hooks/hooks.json` 配置 SessionStart/PreCompact 事件触发 `session-start.sh`，通过 `bd prime` 注入知识库上下文
  - **外部工具会话日志**（`_common.sh:log_session()` L573）：每次适配器调用追加结构化 JSONL 到 `~/.claude/sessions/external-tools.jsonl`，字段：timestamp、tool、command、model、attempt、exit_code、files_changed、diff_stats、cost（input/output tokens）、raw_log_path
  - **存储格式**：JSONL（知识库 + 会话日志）+ SQLite（BEADS 数据库 `.beads/beads.db`）
  - **Dashboard/UI**：无内置 dashboard。CLI 诊断：`bd stats`（项目统计）、`bd doctor`（系统健康检查）、`bd swarm status/worktrees/conflicts`（swarm 监控）、`/status`（9 项诊断检查）
  - **自我反思作为可观测性**：`/self-reflect` 命令分析 PR 评论、对话模式、会话历史提取学习
  - **知识库作为累积可观测性**：patterns、gotchas、decisions、anti-patterns 随时间累积，通过 `bd prime` 选择性加载

- **模式提取**：
  - **会话日志结构化 JSONL**：每次外部工具调用的完整审计记录
  - **Self-Reflect 即可观测性**：从行为模式中提取可操作洞察
  - **选择性知识注入**：`bd prime` 按影响文件、关键词和工作类型过滤

- **与 Orchestra 的关系**：JSONL 会话日志的字段设计（tool/command/model/attempt/cost/diff_stats）是 Orchestra trace 记录的参考模板。Self-Reflect 模式可集成为 Orchestra 的 post-session 分析管道。

### 4. Overstory

- **发现**：
  - **Trace 采集**：Hook 驱动事件日志。Claude Code hooks（SessionStart/UserPromptSubmit/ToolStart/ToolEnd）触发 `ov log` 命令写入 EventStore。Headless 运行时通过 NDJSON 事件 tailer（`src/events/tailer.ts`）解析 stdout。
  - **EventStore**（`src/events/store.ts`）：SQLite 支撑，12 种事件类型（tool_start/tool_end/session_start/session_end/mail_sent/mail_received/spawn/error/custom/turn_start/turn_end/progress/result）。5 个索引：(agent_name, created_at)、(run_id, created_at)、(event_type, created_at)、(tool_name, agent_name)、error-level 事件。
  - **多格式日志**（`src/logging/logger.ts`）：每个 agent 会话 4 个文件：
    - `session.log`：人类可读 `[TIMESTAMP] LEVEL EVENT key=value`
    - `events.ndjson`：机器可解析 NDJSON
    - `tools.ndjson`：工具使用日志
    - `errors.log`：带上下文的堆栈追踪
  - **Secret 脱敏**（`src/logging/sanitizer.ts`）：正则匹配脱敏 API key（sk-ant-*、github_pat_*、Bearer、ghp_*）
  - **TUI Dashboard**（`src/commands/dashboard.ts`）：原始 ANSI 转义码（零依赖）。多面板：agents 面板(60% 宽度)、任务面板、事件流、邮件活动、合并队列、指标。可配轮询间隔（默认 2s）。
  - **6 个观测命令**：`ov trace`（时间线）、`ov errors`（聚合错误）、`ov replay`（多 agent 回放）、`ov feed`（实时流 --follow）、`ov logs`（NDJSON 查询）、`ov inspect`（深度 agent 视图 + tmux 面板捕获）、`ov costs`（token/成本分析 --live --by-capability）
  - **Insight 分析**（`src/insights/analyzer.ts`）：后处理 EventStore 数据提取工具使用、文件编辑频率、错误模式。输出 SessionInsight 对象记录到 mulch。
  - **无 OpenTelemetry**

- **模式提取**：
  - **File-per-format 日志**：人类可读 + 机器可解析共存
  - **SQLite EventStore + 5 索引**：高性能事件查询
  - **Secret 脱敏在 logger 层**：源头防泄漏
  - **Run-scoped 事件隔离**：通过 run_id 字段分区

- **与 Orchestra 的关系**：Overstory 是可观测性最完整的项目。EventStore 设计（12 事件类型 + 5 索引）、多格式日志、TUI Dashboard、和 6 个观测命令是 Orchestra 可观测性层的首选参考。Secret 脱敏应作为 Orchestra 日志层的标配。

### 5. Claude-Code-Workflow (CCW)

- **发现**：
  - **Trace 采集**：
    - CLI 会话审计（`cli-session-audit.ts`）：JSONL 追加到 `.workflow/audit/cli-sessions.jsonl`，10 种事件（session_created/closed/paused/resumed/send/execute/resize/share_created/share_revoked/idle_reaped）
    - Claude Code Hooks：context-limit-detector、user-abort-detector、keyword-detector、stop-handler、recovery-handler
    - WebSocket 广播（`websocket.ts`）：10 种实时状态更新类型，100 连接上限、10 msg/sec 限流
  - **存储格式**：JSONL（审计 + 消息总线）+ JSON（会话状态）+ SQLite（内存/实体追踪 + CLI 历史）
  - **Dashboard/UI**：
    - React 终端 Dashboard：多终端网格、可调面板、执行监视器、文件侧栏、全屏模式
    - Orchestrator Editor：React Flow 可视化 DAG 编辑器
    - A2UI（Agent-to-User Interface）：WebSocket 交互问答协议，Zod 验证 schema（confirm/select/input/multi-select）
  - **广播指标**（`websocket.ts:77-80`）：追踪 sent/throttled/deduped 计数
  - **会话隔离**：`.workflow/active/{session-id}/` 路径隔离，`validateSessionId()` 防路径穿越

- **模式提取**：
  - **JSONL 追加审计日志**：简单有效的事件记录
  - **WebSocket 广播 + 限流**：实时 UI 更新的工程实践
  - **A2UI 交互协议**：agent-to-user 结构化交互的创新模式
  - **React Flow DAG 编辑器**：可视化工作流编辑

- **与 Orchestra 的关系**：A2UI 交互协议是 Orchestra 人机交互的创新参考。React Flow DAG 编辑器可用于 Orchestra 工作流可视化编辑器。WebSocket 广播限流策略是实时 Dashboard 的工程参考。

### 6. wshobson/agents (Conductor)

- **发现**：
  - **Trace 采集**：**无**。插件市场和 agent 编排无任何追踪、日志或遥测基础设施。
  - **observability-monitoring 插件**：这是一个帮助用户为自己项目构建可观测性的知识插件，不是自我监控系统。包含 `observability-engineer.md` agent（覆盖 Prometheus/Grafana/Jaeger/OTel/ELK/Loki）、分布式追踪/Grafana/Prometheus/SLO 知识技能。
  - **Dashboard/UI**：无
  - **PluginEval**（`plugins/plugin-eval/src/`）：最接近可观测性的组件——三层质量评估（静态分析 + LLM 评委 + 蒙特卡洛），但评估的是内容质量而非运行时行为。

- **模式提取**：
  - **零运行时可观测性**
  - **PluginEval 的质量评估模式**：10 维加权评分 + 反模式检测 + Elo 排名——可用于 agent/skill 质量评估

- **与 Orchestra 的关系**：PluginEval 的质量评估框架可参考用于 Orchestra 的 agent 能力评估。可观测性知识技能可作为 Orchestra 构建可观测性层的参考材料。

## 横向对比矩阵

| 维度 | CAO | Composio | Metaswarm | Overstory | CCW | Conductor |
|------|-----|----------|-----------|-----------|-----|-----------|
| **Trace 采集** | 文件日志 | ProjectObserver | Hook + JSONL | Hook + EventStore | Hook + JSONL 审计 | 无 |
| **结构化程度** | 非结构化文本 | JSON Lines | 结构化 JSONL | SQLite + NDJSON | JSONL + JSON | 无 |
| **存储** | 文本文件 | 进程 JSON 快照 | JSONL + SQLite | 5 SQLite DB | JSONL + JSON + SQLite | 无 |
| **Dashboard** | React Web | Next.js Web | CLI 命令 | ANSI TUI | React + DAG Editor | 无 |
| **实时性** | WebSocket 终端 | SSE + 关联 ID | 无 | TUI 2s 轮询 | WebSocket 广播 | 无 |
| **事件类型数** | ~3 (状态) | 15 操作 | ~10 BEADS | 12 事件 | 10 审计 | 0 |
| **Secret 脱敏** | 无 | 无 | 无 | ✅ Logger 层 | 无 | 无 |
| **OpenTelemetry** | 无 | 无 | 无 | 无 | 无 | 无 |
| **学习分析** | 无 | 无 | Self-Reflect | Insight Analyzer | Memory Pipeline | 无 |

## 推荐采纳清单

1. **🔴 Overstory EventStore 设计**（`src/events/store.ts`）：12 事件类型 + 5 复合索引的 SQLite 事件存储。Orchestra 应以此为基础设计 trace 存储层。

2. **🔴 Overstory 多格式日志模式**（`src/logging/logger.ts`）：session.log + events.ndjson + tools.ndjson + errors.log 四文件共存。Orchestra 应同时支持人类可读和机器可解析两种格式。

3. **🔴 OpenTelemetry 集成**（无参考项目）：所有 6 个项目均未使用 OTel——这是 Orchestra 的差异化机会。建议实现 OTel 兼容的 trace 导出，同时保留 SQLite 本地存储。

4. **🟡 Overstory Secret 脱敏**（`src/logging/sanitizer.ts`）：Logger 层正则匹配脱敏 API key。Orchestra 日志层的安全标配。

5. **🟡 Composio 关联 ID + 活动状态时间衰减**（`observability.ts`）：跨 API/SSE/WebSocket 统一请求追踪 + 6 状态活动检测。Orchestra 需要此级别的请求关联。

6. **🟡 Overstory TUI Dashboard 及 6 个观测命令**：`ov trace/errors/replay/feed/logs/inspect/costs`。Orchestra 应提供类似的 CLI 优先可观测性体验。

7. **🟢 CCW A2UI 交互协议**：WebSocket 结构化问答（confirm/select/input/multi-select）。Orchestra 人机交互的创新参考。

8. **🟢 CCW React Flow DAG 编辑器**：可视化工作流编辑。Orchestra 可选的 Web UI 组件。

9. **🟢 Metaswarm Self-Reflect + Overstory Insight Analyzer**：从历史行为中提取可操作洞察。Orchestra 后分析管道的参考。
