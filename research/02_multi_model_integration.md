# 多模型集成调研报告

## 执行摘要

六个项目在多模型支持上呈现三种模式：CLI 包装器（CAO/Overstory/Composio 通过 tmux 启动不同 CLI 工具）、Shell 适配器（Metaswarm 通过 bash 脚本封装 Codex/Gemini）、以及子进程执行器（CCW 通过 Node.js 子进程调用多 CLI）。跨模型对抗审查仅 Metaswarm 有成熟实现（写者与审查者永远使用不同模型）。Token 成本优化普遍薄弱，仅 Metaswarm 和 Composio 有预算断路器。Conductor 仅支持 Claude 单一生态。Orchestra 应重点采纳 Metaswarm 的跨模型审查矩阵、CAO 的通用工具词汇表、和 Composio 的 6 级模型解析链。

## 逐项目分析

### 1. AWS CLI Agent Orchestrator (CAO)

- **发现**：
  - **7 个 Provider 支持**（`models/provider.py:1-12`）：kiro_cli（默认，AWS Bedrock）、claude_code（Anthropic）、codex（OpenAI）、gemini_cli（Google）、kimi_cli（Moonshot）、copilot_cli（GitHub）、q_cli（Amazon Q）
  - **模型切换**：per-agent profile YAML frontmatter `provider` 字段 + CLI `--provider` flag + 父终端继承。解析优先级：CLI flag > profile frontmatter > 父终端继承（`utils/agent_profiles.py:206-242`）
  - **通用工具词汇表**：`utils/tool_mapping.py:16-45` 定义 provider 无关的工具名称（`execute_bash`、`fs_read`、`fs_write` 等），并翻译为各 provider 原生工具名
  - **角色工具限制**：5/7 provider 支持硬性工具限制执行。三个内置角色（supervisor/developer/reviewer）于 `constants.py:123-127`
  - **无跨模型审查**：审查循环是 "developer 写 → reviewer 审"，无模型多样性强制
  - **无成本追踪**：无 token 计数或成本跟踪

- **模式提取**：
  - **Provider 无关 agent profile**：单一 `.md` profile 格式跨 7 个 provider 工作
  - **通用工具词汇表 + 翻译层**：将抽象工具名映射为各 CLI 原生工具名

- **与 Orchestra 的关系**：通用工具词汇表模式可直接用于 Agno 的多模型环境。Agent profile 支持 provider 声明是 Orchestra 应采纳的配置模式。

### 2. Composio Agent Orchestrator

- **发现**：
  - **4 个 Agent 插件**：claude-code、codex、aider、opencode（各为独立 npm 包）
  - **模型切换**：6 级解析链（`agent-selection.ts:29-51`）：持久化 agent > 生成时 override > 角色级项目配置 > 项目级 agent > 角色级默认 > 全局默认。支持 orchestrator 和 worker 使用不同 agent/model。
  - **Per-Role 配置**：YAML 支持 `defaults.orchestrator.agent` vs `defaults.worker.agent` 分别配置
  - **LLM 任务分解**：`decomposer.ts` 直接使用 Anthropic SDK（`claude-sonnet-4-20250514`）进行递归任务分解，与 agent 插件分离
  - **成本追踪**：每个 agent 插件实现 `getSessionInfo()` 返回 `CostEstimate`（inputTokens/outputTokens/estimatedCostUsd），从 agent 原生 JSONL 提取
  - **无对抗审查**：无显式跨模型审查机制，但架构天然支持（orchestrator 为 Claude + workers 为 Codex）

- **模式提取**：
  - **6 级模型解析链**：从全局到会话级的渐进覆盖
  - **Agent 无关架构**：每个 agent 是实现相同接口的插件
  - **成本追踪 per-session**：通过 agent 原生 JSONL 解析

- **与 Orchestra 的关系**：6 级模型解析链为 Orchestra 的模型选择策略提供了完整参考。成本追踪接口定义值得直接采纳。

### 3. Metaswarm

- **发现**：
  - **3 个 AI CLI 支持**：Claude Code（主编排器）、Codex CLI（`adapters/codex.sh`）、Gemini CLI（`adapters/gemini.sh`）
  - **可用性感知路由 + 最低成本优先**（`skills/external-tools/SKILL.md:237-260`）：每次任务分发时运行健康检查，选择最便宜的可用工具。选择标准：(1) 最低估算成本 (2) 平局则最高历史成功率 (3) 无数据则偏好 Gemini（免费层）
  - **跨模型对抗审查**（`skills/external-tools/SKILL.md:148-158`）：**写者永远由不同模型审查**：

    | 实现者 | 审查者 1 | 审查者 2 |
    |--------|---------|---------|
    | Codex | Gemini | Claude |
    | Gemini | Codex | Claude |
    | Claude | Codex | Gemini |

  - **预算断路器**：`per_task_usd`（默认 $2）和 `per_session_usd`（默认 $20）
  - **成本追踪**：从适配器 JSON 输出提取 token 数（`_common.sh:extract_cost_codex()` L360、`extract_cost_gemini()` L399），chars/4 启发式 token 预算
  - **最小环境隔离**：`env -i` 仅传递 HOME、PATH 和工具 API key（`codex.sh:115-120`）
  - **事实而非裁决**：适配器返回原始事实，编排器判定 pass/fail

- **模式提取**：
  - **跨模型审查矩阵**：写者与审查者永远不同模型——这是最成熟的对抗审查实现
  - **Shell 适配器模式**：health/implement/review 三命令接口，输出结构化 JSON
  - **可用性感知 + 成本优先路由**：实时健康检查 + 成本估算的路由决策

- **与 Orchestra 的关系**：跨模型审查矩阵是 Orchestra Cross-Model Review 的直接设计来源。适配器模式（health/implement/review + JSON 输出）可适配为 Agno 工具定义。预算断路器是成本控制的必备功能。

### 4. Overstory

- **发现**：
  - **11 个 Runtime Adapter**（`src/runtimes/`）：Claude Code（稳定）、Sapling（稳定）、Pi/Copilot/Codex/Gemini/Cursor/OpenCode/Aider/Goose/Amp（实验性）
  - **Runtime 接口**（`src/runtimes/types.ts`）：`AgentRuntime` 接口要求实现 `buildSpawnCommand()`、`buildPrintCommand()`、`deployConfig()`、`detectReady()`、`parseTranscript()`、`buildEnv()`
  - **模型切换**：4 级解析（`src/runtimes/registry.ts:76-86`）：CLI `--runtime` flag > per-capability config > config default > 硬编码 fallback("claude")
  - **Per-Capability 模型分配**：`config.yaml` 中 `models.builder: zai/claude-sonnet-4-6`、`models.scout: openrouter/openai/gpt-4o`
  - **Gateway Provider 支持**：通过环境变量（`ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`）支持 z.ai、OpenRouter 或自托管代理
  - **跨模型成本追踪**：`src/metrics/pricing.ts` 包含 Claude/OpenAI/Google 定价表，`estimateCost()` 跨 provider 估算

- **模式提取**：
  - **Runtime 适配器接口**：清晰的 6 方法抽象（spawn/print/config/ready/transcript/env）
  - **Gateway Provider 抽象**：环境变量操控而非 SDK 直接集成
  - **Per-Capability 模型分配**：不同能力（builder/scout/reviewer）可配不同模型/provider

- **与 Orchestra 的关系**：Runtime 适配器接口是最全面的多 CLI 抽象（11 个适配器）。Per-Capability 模型分配直接映射到 Agno Team 成员的模型配置。Gateway Provider 模式简化了 API 代理集成。

### 5. Claude-Code-Workflow (CCW)

- **发现**：
  - **5+ CLI 工具**：配置于 `~/.claude/cli-tools.json`——Claude、Gemini、Codex、Qwen、OpenCode、LiteLLM（API 端点）
  - **三种工具类型**：`builtin`（原生 CLI）、`cli-wrapper`（包装器）、`api-endpoint`（API 调用）
  - **模型切换**：
    - Per-task 在统一任务 schema 中指定（`task-schema.json:376-378`）：`meta.execution_config.cli_tool: codex | gemini | qwen | auto`
    - 关键词检测路由（`keyword-detector.ts`）：检测 "ask codex"、"use gemini"、"delegate to gemini" 等
    - 模型别名解析：`PRIMARY_MODEL`/`SECONDARY_MODEL` 别名（`cli-executor-core.ts:91-106`）
  - **跨模型验证**：`workflow-multi-cli-plan` skill 实现多 CLI 协作分析。专用提示模板：`verification-cross-validation.txt`、`verification-codex-technical.txt`、`verification-gemini-strategic.txt`
  - **成本优化**：上下文缓存（`context-cache.ts`）；LiteLLM 执行器支持 `enableCache`；内存整合管道默认使用 gemini 以降低成本

- **模式提取**：
  - **关键词检测隐式路由**：自然语言中检测模型切换意图
  - **跨模型验证提示模板库**：80+ 模板覆盖分析、开发、文档、规划、验证
  - **LiteLLM 作为统一 API 网关**

- **与 Orchestra 的关系**：跨模型验证提示模板可直接复用。LiteLLM 集成模式为 Orchestra 的 API 端点模型支持提供参考。关键词检测路由可作为用户意图识别的补充。

### 6. wshobson/agents (Conductor)

- **发现**：
  - **仅 Claude 生态**：所有 182 个 agent 定义使用 Claude 模型（opus/sonnet/haiku/inherit 四层）
  - **静态模型分配**：frontmatter `model` 字段（`docs/agents.md:226-235`）——opus 42 个、inherit 42 个、sonnet 51 个、haiku 18 个
  - **混合编排模式**（概念性）：文档描述 "Opus(架构) → Sonnet(开发) → Haiku(部署)" 模式（`docs/architecture.md:238-250`），但未强制执行
  - **无跨 provider 支持**：无 Codex、Gemini 或其他非 Claude 模型支持
  - **Token 优化**：渐进式技能加载（3 层：元数据始终加载、指令激活时加载、资源按需加载）

- **模式提取**：
  - **4 层模型分层**：opus(高价值)/sonnet(通用)/haiku(简单)/inherit(用户选择) 的清晰分层策略
  - **渐进式加载减少 token**：按需加载减少上下文窗口占用

- **与 Orchestra 的关系**：4 层模型分层策略可参考用于 Orchestra 的成本优化。但缺乏多 provider 支持，参考价值有限。

## 横向对比矩阵

| 维度 | CAO | Composio | Metaswarm | Overstory | CCW | Conductor |
|------|-----|----------|-----------|-----------|-----|-----------|
| **支持模型数** | 7 CLI | 4 Agent 插件 | 3 CLI | 11 Runtime | 5+ CLI + LiteLLM | 仅 Claude (4 层) |
| **切换机制** | Profile + CLI flag | 6 级解析链 | 可用性路由 | 4 级解析 | Per-task + 关键词 | 静态 frontmatter |
| **跨模型审查** | 无 | 无（架构支持） | ✅ 审查矩阵 | 无（架构支持） | ✅ 验证模板 | 无 |
| **成本追踪** | 无 | ✅ per-session | ✅ 预算断路器 | ✅ 定价表 | ✅ 缓存 | 概念性 |
| **路由策略** | 继承 + 显式 | 配置解析链 | 最低成本优先 | Capability 映射 | 任务 + 关键词 | 固定 |
| **适配器抽象** | BaseProvider ABC | Agent 插件接口 | Shell 脚本 | AgentRuntime 接口 | CLI Tools JSON | 无 |

## 推荐采纳清单

1. **🔴 Metaswarm 跨模型审查矩阵**（`skills/external-tools/SKILL.md:148-158`）：写者与审查者永远使用不同模型。Orchestra 的 Cross-Model Review 应直接采纳此设计。

2. **🔴 CAO 通用工具词汇表**（`utils/tool_mapping.py`）：Provider 无关的工具名称 + per-provider 翻译层。Orchestra 需要此抽象层来支持多模型环境下的工具一致性。

3. **🔴 Overstory Per-Capability 模型分配**：不同角色（builder/scout/reviewer）可配不同模型。Orchestra Team 成员应支持此级别的模型灵活性。

4. **🟡 Composio 6 级模型解析链**（`agent-selection.ts:29-51`）：全局 → 角色默认 → 项目级 → 角色级项目 → 生成时 → 持久化。Orchestra 应实现类似的分级配置覆盖。

5. **🟡 Metaswarm 预算断路器**：`per_task_usd` 和 `per_session_usd` 限制。Orchestra 需要成本控制机制防止失控。

6. **🟡 Metaswarm Shell 适配器模式**（`adapters/*.sh`）：health/implement/review 三命令接口 + 结构化 JSON 输出。适用于 Orchestra 的外部 CLI 工具集成。

7. **🟢 CCW 跨模型验证提示模板**（`.ccw/workflows/cli-templates/prompts/`）：80+ 预构建模板。可直接复用于 Orchestra 的验证阶段。

8. **🟢 Overstory Gateway Provider 抽象**：环境变量操控支持 API 代理。Orchestra 可采用此模式支持 OpenRouter 等网关。
