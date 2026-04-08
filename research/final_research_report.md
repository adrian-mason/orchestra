# Orchestra Design: Reference Project Research Report

> 基于 6 个开源参考项目的深度调研，为 Orchestra 多 agent 开发编排系统提供设计决策支撑。
>
> 调研日期：2026-04-06 | 调研项目：CAO / Composio / Metaswarm / Overstory / CCW / Conductor

---

## 1. 关键发现总结（Top 10 Takeaways）

1. **层级化编排是共识**：6/6 项目选择层级拓扑（2-4 层深度）。无项目采用纯 mesh 或 peer-to-peer。Orchestra 应坚持 hierarchical 设计，但支持递归嵌套（参考 Metaswarm 的 Sub-Orchestrator 模式）。

2. **跨模型对抗审查是未充分探索的高价值模式**：仅 Metaswarm 有成熟实现（写者与审查者永远使用不同模型）。这是 Orchestra 的核心差异化能力之一。

3. **无项目使用 OpenTelemetry**：所有 6 个项目都自建可观测性。这是 Orchestra 的差异化机会——实现 OTel 兼容 trace 导出可以让 Orchestra 集成到企业级监控栈。

4. **质量门控必须是阻塞性的，而非建议性的**：Metaswarm 和 Overstory 的经验表明，advisory review gates 不足以保证质量。FAIL 必须意味着重试或升级，绝不跳过。

5. **PR Review 自动化是一个完整的子系统**：Composio 的 Reaction Engine（CI 失败/审查/冲突/空闲的自动响应）和 Metaswarm 的 PR Shepherd（自治状态机）表明，PR 自动化远比简单的 webhook 监听复杂。

6. **Learning/Wisdom 系统是 V2 功能，但架构应从 V1 预留接口**：Metaswarm 的 Self-Reflect 和 CCW 的 Memory Consolidation 表明，从行为中提取可操作洞察并累积为知识是成熟系统的标志。

7. **Agent 间通信有三种可行模式**：(a) SQLite 邮件系统（Overstory，最工程化）、(b) JSONL 消息总线（CCW/Metaswarm，最轻量）、(c) HTTP API（CAO，最解耦）。Orchestra 应根据部署场景选择。

8. **合并冲突解决需要多层策略**：Overstory 的 4 层管道（clean → auto-resolve → AI-resolve → re-imagine）+ 历史模式学习是目前最完善的设计。

9. **所有项目许可证兼容**：5 MIT + 1 Apache-2.0，无 copyleft 障碍。模式和格式可自由采纳。

10. **代码直接复用有限，模式复用价值极高**：多数项目绑定特定运行时（tmux/Bun/Claude Code），但设计模式（质量门控状态机、Reaction Engine、跨模型审查矩阵）具有框架无关的高复用价值。

---

## 2. 推荐架构变更

以下变更基于调研发现，相对于 Orchestra 当前三阶段设计（Design → Cross-Model Review → Implementation）的推荐调整：

### 2.1 扩展为 5 阶段工作流

当前三阶段设计覆盖了核心流程，但调研表明需要扩展前后端：

```
Research → Design → Cross-Model Review → Implementation → PR Lifecycle
    │                                         │               │
    │  (参考 Metaswarm Phase 1)               │   (参考 Metaswarm Phase 6)
    │                                         │               │
    └── 可选，按需激活                         │   ┌── PR Shepherd (SM)
                                              │   ├── Reaction Engine
                                              │   ├── Merge Queue
                                              │   └── Closure & Learning
                                              │
                                    ┌── 4 阶段循环 ──┐
                                    │ IMPLEMENT      │
                                    │ VALIDATE       │
                                    │ ADV. REVIEW    │
                                    │ COMMIT         │
                                    └────────────────┘
```

**变更理由**：
- **Research 阶段**：Metaswarm 的 Researcher Agent 显著提高了 Design 阶段的输入质量
- **PR Lifecycle 阶段**：Composio Reaction Engine + Metaswarm PR Shepherd 表明 PR 管理需要独立阶段

### 2.2 采用双层 Review Gate

```
Plan Review Gate          Design Review Gate
┌─────────────┐          ┌──────────────────┐
│ 3 对抗审查者  │          │ 5-6 领域专家       │
│ (可行性/完整/  │   →      │ (PM/Arch/Design/ │
│  范围对齐)    │          │  Security/UX/CTO) │
│ ALL PASS     │          │ ALL APPROVE       │
│ Max 3 轮     │          │ Max 3 轮          │
│ 升级至人工    │          │ 升级至人工         │
└─────────────┘          └──────────────────┘
```

**变更理由**：Metaswarm 的实践证明，Plan Review 和 Design Review 有不同的审查关注点，应分别处理。

### 2.3 引入 Watchdog 子系统

```
Tier 0: 进程监控 (30s 间隔)
  └── tmux/PID 存活检查 → warn → nudge
Tier 1: AI 辅助分类
  └── 读取 agent 日志 → 分类 retry/terminate/extend
Tier 2: 持久巡逻 agent
  └── 全局 fleet 监控 → 异常模式检测
```

**变更理由**：Overstory 的 3 层 Watchdog 是唯一实现了渐进升级 + Decision Gate 感知的项目，是 agent fleet 管理的必备子系统。

### 2.4 多模型配置层级

```yaml
# Orchestra config.yaml
models:
  global_default: claude-sonnet-4-6
  roles:
    designer: claude-opus-4-6           # 高价值决策
    reviewer: gemini-pro                # 跨模型审查
    implementer: codex-gpt-5.3          # 最低成本
    scout: claude-haiku-4-5             # 轻量探索
  projects:
    my-app:
      implementer: claude-sonnet-4-6    # 项目级覆盖
  routing:
    strategy: cheapest-available        # 或 fastest / highest-quality
    escalation: [codex, gemini, claude] # 失败升级链
  budget:
    per_task_usd: 2.00
    per_session_usd: 20.00
```

**变更理由**：综合 Composio 的 6 级解析链、Metaswarm 的成本优先路由、和 Overstory 的 per-capability 分配。

---

## 3. 推荐新增能力

### 3.1 Self-Reflect / Learning 系统

**设计来源**：Metaswarm Self-Reflect + CCW Memory Consolidation

```
┌── Session 结束 ──────────────────────────────────┐
│                                                   │
│  Phase A: PR 评论分析                              │
│    └── 获取 PR 评论 → 质量过滤 → ACCEPT/REJECT    │
│                                                   │
│  Phase B: 对话 & 会话挖掘                          │
│    └── 战略模式提取 ("问题是..."、"我们决定...")     │
│                                                   │
│  Phase C: 配置反思                                 │
│    └── 审查 agent 指令改进机会                     │
│                                                   │
│  Phase D: 整合 & 存储                              │
│    ├── 规范化：去除引用 / 泛化路径 / 包含 WHY      │
│    ├── 去重 & 冲突解决                             │
│    └── 写入知识库（JSONL Schema from Metaswarm）   │
│                                                   │
│  Phase E: 选择性注入                               │
│    └── 下次会话按 affectedFiles/tags 过滤注入      │
└───────────────────────────────────────────────────┘
```

**知识库 Schema**（采自 Metaswarm `knowledge/README.md`）：
```jsonl
{
  "id": "k-20260406-001",
  "type": "pattern",        // pattern | gotcha | decision | anti-pattern | convention | dependency | environment
  "fact": "...",
  "recommendation": "...",
  "confidence": 0.85,
  "provenance": "pr-123-review",
  "tags": ["auth", "security"],
  "affectedFiles": ["src/auth/*.py"],
  "usageCount": 0,
  "helpfulCount": 0,
  "outdatedReports": 0
}
```

### 3.2 Reactions 事件驱动系统

**设计来源**：Composio Reaction Engine + Metaswarm PR Shepherd

```
┌── 事件源 ─────────────────────────────────────────┐
│                                                   │
│  GitHub Webhook / Polling                         │
│    ├── ci-failed         → 转发详情 + 重试(2x)    │
│    ├── changes-requested → 指纹去重 → 转发 agent  │
│    ├── bugbot-comments   → 转发 bot 审查           │
│    ├── merge-conflicts   → 通知 rebase            │
│    ├── approved-and-green → 自动合并 / 通知人工    │
│    └── stale-pr          → nudge agent            │
│                                                   │
│  Agent 状态                                        │
│    ├── agent-idle (>10m) → nudge                  │
│    ├── agent-stuck (>30m) → 升级至人工            │
│    └── agent-errored     → AI triage → 重试/终止  │
│                                                   │
│  每个 Reaction:                                    │
│    - retries: 可配（默认 2）                       │
│    - escalateAfter: 可配（默认 30m）               │
│    - fingerprint: 排序 ID 去重                     │
│    - cooldown: 防重复触发                          │
└───────────────────────────────────────────────────┘
```

### 3.3 Merge Queue / 冲突解决

**设计来源**：Overstory 4 层合并管道

```
┌── Merge Queue (FIFO, SQLite 持久化) ──────────────┐
│                                                   │
│  入队：agent 发送 merge_ready 信号                 │
│                                                   │
│  处理：per item, 4 层升级：                        │
│    Tier 1: git merge --no-edit (clean)            │
│    Tier 2: 解析冲突标记 → 保留 incoming            │
│            (skip if canonical side has content)    │
│    Tier 3: AI 解决 → per conflicted file           │
│            → 验证输出是代码非散文                   │
│    Tier 4: Re-imagine → abort merge               │
│            → 从头重新实现变更                       │
│                                                   │
│  历史学习：                                        │
│    - 记录每个文件的冲突解决模式                     │
│    - 跳过历史失败的 tier                           │
│                                                   │
│  File Scoping:                                    │
│    - 每个 agent 的文件范围互不重叠                  │
│    - 减少冲突概率                                  │
└───────────────────────────────────────────────────┘
```

### 3.4 Watchdog / 健康监控

**设计来源**：Overstory 3 层 Watchdog

```
┌── Watchdog 子系统 ────────────────────────────────┐
│                                                   │
│  Tier 0: 机械进程监控 (30s 间隔)                   │
│    ├── 检查：进程存活 / PID 存在 / RPC getState()  │
│    ├── 升级 L0: warn (日志)                        │
│    ├── 升级 L1: nudge (发送提醒消息)               │
│    └── 升级 L2: → Tier 1                          │
│                                                   │
│  Tier 1: AI 辅助故障分类                           │
│    ├── 读取 agent 最后 50 行日志                   │
│    ├── AI 分类：retry / terminate / extend         │
│    └── 升级 L3: terminate + 通知人工               │
│                                                   │
│  Tier 2: 持久监控 agent                            │
│    └── 全局 fleet 巡逻 → 异常模式检测              │
│                                                   │
│  特殊处理：                                        │
│    - Decision Gate 感知：跳过等待人工决策的 agent   │
│    - 故障记录：写入知识库供未来学习                 │
│                                                   │
│  ZFC 原则：                                        │
│    可观测状态（进程存活）是事实来源，非记录状态     │
└───────────────────────────────────────────────────┘
```

---

## 4. 可直接复用的组件清单（含许可证确认）

### 4.1 可直接采用的格式/Schema

| # | 组件 | 来源 | 许可证 | 复用方式 |
|---|------|------|--------|---------|
| 1 | 知识库 JSONL Schema | Metaswarm `knowledge/README.md:30-52` | MIT | 直接采用格式 |
| 2 | 审查 Rubrics (9 个) | Metaswarm `rubrics/` | MIT | 直接使用为 Review Gate 评估标准 |
| 3 | 统一任务 Schema | CCW `task-schema.json` (564 行) | MIT | 直接采用/适配为任务定义格式 |
| 4 | Agent 人格定义模板 | Metaswarm `agents/` (19 个) | MIT | 格式参考，内容需定制 |
| 5 | 通用工具词汇表 | CAO `utils/tool_mapping.py` | Apache-2.0 | 适配为 Agno 多模型工具映射 |

### 4.2 可直接使用的代码

| # | 组件 | 来源 | 许可证 | 语言 | 复用方式 |
|---|------|------|--------|------|---------|
| 6 | PluginEval 框架 | Conductor `plugins/plugin-eval/src/` | MIT | Python | 直接用于 agent/skill 质量评估 |
| 7 | 提示模板库 (80+) | CCW `.ccw/workflows/cli-templates/prompts/` | MIT | Markdown | 直接用于多模型验证 |
| 8 | 定价模块 | Overstory `src/metrics/pricing.ts` | MIT | TypeScript | 需移植为 Python |

### 4.3 需要重新实现的设计模式

| # | 模式 | 来源 | 参考实现 | 推荐用于 |
|---|------|------|---------|---------|
| 9 | 4 层合并解冲突 | Overstory `src/merge/resolver.ts` | TypeScript/Bun | Merge Queue |
| 10 | 3 层 Watchdog | Overstory `src/watchdog/` | TypeScript/Bun | 健康监控 |
| 11 | Reaction Engine | Composio `lifecycle-manager.ts` | TypeScript | PR 自动化 |
| 12 | 跨模型审查矩阵 | Metaswarm `skills/external-tools/SKILL.md:148-158` | Markdown | Cross-Model Review |
| 13 | SQLite 邮件系统 | Overstory `src/mail/` | TypeScript/Bun | Agent 通信 |
| 14 | Beat/Cadence 模型 | CCW 设计模式 | TypeScript | Coordinator 唤醒 |
| 15 | 两阶段 Memory Consolidation | CCW `memory-consolidation-pipeline.ts` | TypeScript | Learning 系统 |
| 16 | EventStore (12 类型 + 5 索引) | Overstory `src/events/store.ts` | TypeScript/Bun | 可观测性 |

---

## 5. 修订后的实施路径（Phase 0-3 更新）

### Phase 0: Foundation（基础设施）
- [ ] Agno 项目脚手架 + 基础 Agent/Team/Workflow 配置
- [ ] SQLite 持久化层（邮件 + 事件 + 指标 + 合并队列）
- [ ] 通用工具词汇表 + 多模型配置层级（参考 CAO + Composio）
- [ ] 统一任务 Schema（采自 CCW）
- [ ] 知识库 JSONL Schema（采自 Metaswarm）
- [ ] 基础日志系统（多格式：session.log + events.ndjson，参考 Overstory）

### Phase 1: Core Workflow（核心工作流）
- [ ] Design Stage：Researcher Agent + Architect Agent + Spec Generator
- [ ] Plan Review Gate：3 对抗审查者，ALL PASS，3 轮限制（参考 Metaswarm）
- [ ] Design Review Gate：5+ 并行专家，ALL APPROVE，3 轮 + 人工升级（参考 Metaswarm）
- [ ] Cross-Model Review：跨模型审查矩阵实现（参考 Metaswarm）
- [ ] Implementation Stage：4 阶段循环（IMPLEMENT → VALIDATE → ADVERSARIAL REVIEW → COMMIT）
- [ ] Quality Gates：test + lint + typecheck 阻塞门控（参考 Overstory）
- [ ] 基础 Watchdog Tier 0（进程监控 + warn/nudge）

### Phase 2: PR Automation + Observability（PR 自动化 + 可观测性）
- [ ] PR Lifecycle Stage：PR Shepherd 状态机（参考 Metaswarm）
- [ ] Reaction Engine：CI 失败/审查/冲突/空闲的自动响应（参考 Composio）
- [ ] Merge Queue：4 层冲突解决管道（参考 Overstory）
- [ ] EventStore：SQLite 12 事件类型 + 5 索引（参考 Overstory）
- [ ] OpenTelemetry 兼容 trace 导出（差异化能力）
- [ ] TUI Dashboard（参考 Overstory）或 Web Dashboard（参考 CAO/Composio）
- [ ] Watchdog Tier 1-2（AI 辅助分类 + 持久监控）
- [ ] Secret 脱敏（参考 Overstory）

### Phase 3: Learning + Advanced Features（学习 + 高级功能）
- [ ] Self-Reflect 管道（参考 Metaswarm 3 阶段分析）
- [ ] Memory Consolidation（参考 CCW 两阶段提取→整合）
- [ ] 知识库选择性注入（`bd prime` 模式，按 affectedFiles/tags 过滤）
- [ ] Agent 身份/CV 持久化（参考 Overstory）
- [ ] 预算断路器（per_task + per_session，参考 Metaswarm）
- [ ] 可用性感知路由 + 成本优先模型选择（参考 Metaswarm）
- [ ] PluginEval 集成（参考 Conductor，用于 agent/skill 质量评估）
- [ ] 递归嵌套编排（Sub-Orchestrator，参考 Metaswarm）

---

## 6. 风险与开放问题

### 风险

| 风险 | 影响 | 概率 | 缓解 |
|------|------|------|------|
| **单人维护项目消亡** | 上游参考失效 | 中 | 仅采纳模式而非依赖上游代码 |
| **Agno 框架限制** | 某些模式无法在 Agno 中高效实现 | 中 | 提前在 Phase 0 验证核心模式的 Agno 可行性 |
| **多模型 API 不稳定** | 跨模型审查依赖多个 API 的稳定性 | 中 | 实现 fallback 链 + 预算断路器 |
| **SQLite 并发瓶颈** | 高并发 agent fleet 下 SQLite 可能成为瓶颈 | 低 | WAL 模式 + 按功能分库（参考 Overstory 5 DB 模式） |
| **Quality Gate 过严** | 审查循环导致工作流卡住 | 中 | 3 轮限制 + 人工升级 + Decision Gate |
| **Context Window 膨胀** | 知识注入 + 审查历史导致上下文溢出 | 高 | 选择性知识注入（参考 Metaswarm `bd prime`）+ 检查点恢复（参考 CCW） |

### 开放问题

1. **Agno 原生支持程度**：Agno 的 Team/Workflow/Hooks 是否足以实现所有推荐模式（特别是 Reaction Engine 和 Watchdog），还是需要扩展 Agno？

2. **通信机制选择**：SQLite 邮件系统（Overstory）vs JSONL 消息总线（CCW）vs Agno 原生共享内存——哪种最适合 Orchestra 的部署场景？

3. **OpenTelemetry 集成深度**：仅 trace 导出，还是完整的 metrics/logs/traces 三支柱？

4. **PR Lifecycle 自治程度**：PR Shepherd 应自动合并 approved PR，还是始终需要人工最终确认？

5. **Learning 系统冷启动**：新项目首次使用 Orchestra 时，知识库为空。如何提供有意义的初始知识（预构建 rubrics 来自 Metaswarm？社区共享知识库？）

6. **跨 Repo 编排**：Metaswarm 的 Swarm Coordinator 和 Overstory 的 Orchestrator 支持跨 repo 工作。Orchestra 的 V1 是否需要此能力？

---

## 7. 附录：逐项目详细分析

详细分析请参阅以下报告：

- [01. 架构分析](01_architecture_analysis.md) — 编排拓扑、通信机制、状态管理、容错
- [02. 多模型集成](02_multi_model_integration.md) — 模型切换、跨模型审查、成本优化
- [03. 可观测性分析](03_observability_analysis.md) — Trace 采集、存储、Dashboard、Session 隔离
- [04. 工作流模式](04_workflow_patterns.md) — 工作流阶段、Review Gate、PR 自动化、Learning
- [05. 集成可行性](05_integration_feasibility.md) — 可复用组件、Agno 映射、许可证、维护状态

### 项目概览

| 项目 | 技术栈 | 许可证 | 核心价值 |
|------|--------|--------|---------|
| **CAO** | Python/FastAPI | Apache-2.0 | 三原语编排模型 + 通用工具词汇表 + 7 Provider |
| **Composio** | TypeScript/Next.js | MIT | 插件槽位架构 + Reaction Engine + 6 级模型解析 |
| **Metaswarm** | Markdown/Bash | MIT | 9 阶段 SDLC + 跨模型审查矩阵 + Self-Reflect + 知识库 |
| **Overstory** | TypeScript/Bun | MIT | SQLite 邮件 + 4 层合并 + 3 层 Watchdog + EventStore |
| **CCW** | TypeScript/Node.js | MIT | Beat/Cadence 模型 + 任务 Schema + Memory Consolidation |
| **Conductor** | Markdown/Python | MIT | 182 Agent 定义 + PluginEval + TDD 集成 |
