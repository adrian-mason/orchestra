# Multi-Agent Research Brief: Reference Project Deep Dive

## Context

我正在设计一个基于 Agno 的 multi-agent 开发编排系统（代号 Orchestra），用于自动化 Design → Cross-Model Review → Implementation → PR Review 的完整开发工作流。前期调研识别了 6 个高度相关的开源参考项目，需要对每个项目进行深度调研，提取可复用的架构模式和设计决策。

## 任务

创建一个 agent team 来并行调研以下 6 个项目，最终生成 `research/final_research_report.md`。

### Agent Team 定义

创建以下 5 个 sub-agent，每个负责特定维度的调研：

**Agent 1: Arch-Analyst（架构分析师）**
- 职责：分析每个项目的核心架构、编排模式、agent 通信机制
- 重点关注：
  - 编排拓扑（hierarchical / flat / mesh）
  - Agent 间通信方式（MCP / message bus / shared filesystem / SQLite mail）
  - 状态管理和持久化机制
  - 错误处理和容错模式
- 输出：`research/01_architecture_analysis.md`

**Agent 2: Multi-Model-Scout（多模型集成调查员）**
- 职责：调研每个项目如何支持多模型（Claude / Codex / Gemini）混用
- 重点关注：
  - 模型切换机制（per-agent config / runtime routing / fallback）
  - 不同模型在不同角色中的实际使用模式
  - 跨模型 review/对抗审查的具体实现
  - Token 成本优化策略
- 输出：`research/02_multi_model_integration.md`

**Agent 3: Observability-Inspector（可观测性审查员）**
- 职责：调研每个项目的 trace、监控、timeline 可视化能力
- 重点关注：
  - Trace 采集方式（hook / decorator / middleware / OpenTelemetry）
  - Trace 存储格式（JSONL / SQLite / PostgreSQL / 自定义）
  - Dashboard/UI 的实现方式和技术栈
  - Session 隔离和上下文追踪的具体机制
- 输出：`research/03_observability_analysis.md`

**Agent 4: Workflow-Cartographer（工作流制图师）**
- 职责：绘制每个项目的完整工作流阶段，与我们的三阶段设计做对比
- 重点关注：
  - 工作流阶段定义（从需求到 PR merge 的完整路径）
  - Design Review Gate 的实现（并行审查 / 轮数上限 / 升级机制）
  - PR Review 自动化的实现（reactions / webhook / polling）
  - Self-reflect / Learning / Wisdom accumulation 机制
- 输出：`research/04_workflow_patterns.md`

**Agent 5: Integration-Surveyor（集成可行性评估员）**
- 职责：评估每个项目与我们现有生态（cadence / Agno / Claude Code）的集成可行性
- 重点关注：
  - 哪些组件可以直接复用（代码 / 配置 / 模式）
  - 哪些设计模式可以适配到 Agno 的 Team / Workflow / Hooks 概念
  - 许可证兼容性（我们目标是 Apache-2.0 或 MIT）
  - 依赖复杂度和维护状态（最近 commit 时间、contributor 数量、issue 活跃度）
- 输出：`research/05_integration_feasibility.md`

### 调研目标项目

| # | 项目 | GitHub URL | 优先级 |
|---|------|-----------|--------|
| 1 | AWS CLI Agent Orchestrator (CAO) | https://github.com/awslabs/cli-agent-orchestrator | 🔴 高 |
| 2 | Composio Agent Orchestrator | https://github.com/ComposioHQ/agent-orchestrator | 🔴 高 |
| 3 | Metaswarm | https://github.com/dsifry/metaswarm | 🔴 高 |
| 4 | Overstory | https://github.com/jayminwest/overstory | 🟡 中 |
| 5 | Claude-Code-Workflow (CCW) | https://github.com/catlog22/Claude-Code-Workflow | 🟡 中 |
| 6 | wshobson/agents (Conductor) | https://github.com/wshobson/agents | 🟢 低 |

### 调研方法

对每个项目，按以下步骤执行：

1. **Clone & Inventory**：clone repo，执行 `find . -name "*.md" -o -name "*.toml" -o -name "*.yaml" -o -name "*.json" | head -50` 了解项目结构
2. **Read Core Docs**：优先阅读 README.md、CLAUDE.md（如有）、ARCHITECTURE.md、docs/ 目录
3. **Source Code Analysis**：阅读核心编排逻辑的源码（通常在 src/ 或 packages/core/）
4. **Config Schema**：提取 agent 配置、workflow 定义、reactions 配置的 schema
5. **Trace/Observability**：查找 trace、logging、monitoring 相关的实现代码

### 输出要求

每个 Agent 的输出文件必须包含：

```markdown
# [维度名称] 调研报告

## 执行摘要
（3-5 句话概括关键发现）

## 逐项目分析

### 项目名称
- **发现**：（具体的技术细节，引用源码文件路径）
- **模式提取**：（可复用的设计模式）
- **与 Orchestra 的关系**：（如何映射到我们的设计）

## 横向对比矩阵
（表格形式对比所有项目在该维度上的表现）

## 推荐采纳清单
（按优先级排列，标注来源项目和具体文件/代码段）
```

### 最终综合报告

所有 Agent 完成后，综合 5 份报告生成 `research/final_research_report.md`，结构：

```markdown
# Orchestra Design: Reference Project Research Report

## 1. 关键发现总结（Top 10 Takeaways）
## 2. 推荐架构变更（相对于现有 design-revision-agno-review.md）
## 3. 推荐新增能力
   - 3.1 Self-Reflect / Learning 系统
   - 3.2 Reactions 事件驱动系统
   - 3.3 Merge Queue / 冲突解决
   - 3.4 Watchdog / 健康监控
## 4. 可直接复用的组件清单（含许可证确认）
## 5. 修订后的实施路径（Phase 0-3 更新）
## 6. 风险与开放问题
## 7. 附录：逐项目详细分析（链接到 01-05 报告）
```

### 约束

- 所有文件放在 `research/` 目录下
- 使用中文撰写报告
- 引用源码时标注具体文件路径（如 `src/orchestrator/supervisor.ts:L42-L68`）
- 如果某个项目的某些信息无法确认（如 repo 为 private 或文档不全），明确标注 `[未确认]` 而非猜测
- 优先深度而非广度——宁可 3 个项目分析透彻，也不要 6 个项目浮于表面
