# MiniCode Agent 设计文档

## 1. 项目目标

MiniCode Agent 是一个本地 Coding Agent Runtime，设计参考 Claude Code、SWE-agent、OpenHands 以及轻量级 Agent Harness 系统。它面向 AI 应用开发和 Agent 开发岗位，目标是成为一个可运行、可展示、可量化、可写进简历并经得住面试追问的高质量项目。

它的主要产品形态是一个 CLI Agent。用户可以在任意本地代码仓库中输入任务，MiniCode Agent 会理解任务、收集上下文、选择 Skill、调用工具、修改代码、运行验证命令、压缩长上下文、沉淀可复用记忆、调度受控子 Agent，并产出可回放的执行轨迹和评测指标。

项目优化三个结果：

- 做出一个真实可用的 Coding Agent，而不是只会聊天的 demo。
- 架构贴合主流 Agent 系统：工具调用、循环控制、记忆、上下文压缩、权限、安全、多 Agent 和评测。
- 产出适合面试展示的量化结果：任务通过率、token 成本、工具调用次数、运行耗时、上下文压缩率和消融实验报告。

## 2. 范围

### 2.1 MVP 范围内

- CLI 命令：执行一次性编码任务、查看 trace、运行 benchmark。
- 基于状态机的 Query Loop，用于控制编码任务执行。
- Tool Runtime：支持文件读取、代码搜索、文件编辑、shell 命令、测试运行和 git diff 查看。
- Skill 系统：支持元信息过滤、语义检索、LLM rerank 和动态注入。
- Memory 系统：沉淀项目事实、用户偏好、可复用流程和失败经验。
- Context Compression：压缩长工具输出、对话历史、文件片段和任务状态。
- Permission & Safety Gateway：风险分类、路径沙箱、命令策略和人工确认。
- 中心化多 Agent 协作：用于代码探索、代码审查、测试分析和受限实现。
- 轻量 Harness：支持 benchmark 任务、trace replay、指标统计和消融实验。

### 2.2 MVP 暂不包含

- VS Code 插件。
- Web Dashboard。
- 完整远程沙箱基础设施。
- 长时间后台自治任务。
- 企业级多用户权限管理。

这些能力可以在 CLI Runtime 和 Harness 稳定后再扩展。

## 3. 推荐技术栈

- Python 3.11+
- Typer：CLI 命令框架。
- Rich：终端 UI 和输出渲染。
- Pydantic：状态对象、工具 schema 和配置模型。
- SQLite：本地 trace、memory 和评测记录。
- SQLModel 或 SQLAlchemy：数据访问层。
- LiteLLM 或 OpenAI/Anthropic SDK：模型适配层。
- ripgrep，可选 tree-sitter：代码检索和结构化分析。
- pytest：样例任务验证和系统测试。

第一版应保持后端优先。这个项目的核心价值在 Agent Runtime 和评测体系，而不是 UI。

## 4. 系统架构

```text
MiniCode Agent
+-- CLI Layer
+-- Session Manager
+-- Context Engine
+-- Skill System
+-- Agent Core
+-- Tool Runtime
+-- Permission & Safety Gateway
+-- Multi-Agent Orchestrator
+-- Memory System
+-- Trace Store
+-- Harness
```

### 4.1 CLI Layer

建议提供以下命令：

```text
minicode run "<task>"
minicode chat
minicode eval <taskset>
minicode trace <run_id>
minicode memory list
minicode skills list
```

MVP 优先实现 `run`、`eval` 和 `trace`。

### 4.2 Session Manager

Session Manager 负责一次 Agent run 的生命周期：

- 创建 `run_id`。
- 绑定 workspace 路径。
- 加载配置。
- 初始化 `AgentState`。
- 持久化 trace 事件。
- 汇总最终指标。

### 4.3 Context Engine

Context Engine 负责为 Agent 构建和维护上下文。

核心职责：

- 扫描仓库结构。
- 读取 git branch、status 和 diff。
- 加载规则文件，例如 `CLAUDE.md`、`AGENTS.md` 和 `MINICODE.md`。
- 加载相关 memory。
- 检索相关文件和代码片段。
- 维护 hot、warm、cold 三层上下文。
- 在上下文超限时触发压缩。

上下文分层：

```text
Hot Context: 当前目标、相关代码片段、最近工具结果、已修改文件。
Warm Context: 压缩后的任务状态、相关 memory、早期决策。
Cold Context: 完整 trace、旧工具输出、完整文件快照、可检索归档。
```

### 4.4 Skill System

Skill 是能力包，不只是 prompt 模板。

建议目录结构：

```text
.minicode/skills/
  debugging/
    SKILL.md
    metadata.yaml
    examples/
  test-writing/
  code-review/
  refactor/
```

Skill 选择流程：

```text
任务意图识别
-> 元信息过滤
-> 关键词或语义检索
-> LLM rerank
-> 注入 top-k Skill
-> 记录执行效果
```

MVP 内置三个 Skill：

- `debugging`
- `test-writing`
- `code-review`

### 4.5 Agent Core

Agent Core 使用受控状态机，而不是无限开放式循环。

主流程：

```text
INIT
-> LOAD_CONTEXT
-> SELECT_SKILL
-> PLAN
-> ACT
-> OBSERVE
-> COMPRESS_CONTEXT
-> VERIFY
-> REFLECT
-> DONE
```

关键异常分支：

```text
ACT -> NEED_APPROVAL
ACT -> TOOL_FAILED
VERIFY -> REPLAN
VERIFY -> FAILED
COMPRESS_CONTEXT -> RETRIEVE_COLD_CONTEXT
```

这样可以明确控制失败处理、验证、权限审批和上下文压力，避免 Agent 在长任务中失控循环。

### 4.6 Tool Runtime

MVP 工具：

- `list_files`
- `read_file`
- `search_code`
- `edit_file`
- `run_shell`
- `run_tests`
- `git_status`
- `git_diff`
- `write_memory`
- `spawn_subagent`

每个工具都有结构化定义：

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict
    risk_level: RiskLevel
    permission: PermissionMode
    timeout_seconds: int
```

工具返回结构化 observation：

```python
class ToolObservation(BaseModel):
    tool_call_id: str
    ok: bool
    output: str
    error: str | None
    metadata: dict
    truncated: bool = False
```

### 4.7 Permission & Safety Gateway

所有工具调用在执行前都必须经过权限和安全网关。

风险等级：

```text
safe: 只读搜索和文件读取。
low: git diff、git status、测试命令。
medium: 文件编辑、安装依赖、格式化命令。
high: 删除、网络上传、git push、访问密钥。
blocked: 根目录破坏性操作、凭据外传、危险路径逃逸。
```

策略模式：

```text
allow: 不询问，直接执行。
ask: 解释风险并等待用户确认。
deny: 阻断执行，返回结构化安全错误。
```

安全检查：

- 路径沙箱。
- 命令风险分类。
- denylist / allowlist。
- prompt injection 模式检测。
- secret 文件和环境变量文件保护。
- 工具执行前后的审计事件。

### 4.8 Multi-Agent Orchestrator

子 Agent 是受控工具，不是自由自治的同级 Agent。

MVP 子 Agent：

- Explorer Agent：只读分析代码结构和相关文件。
- Reviewer Agent：审查 diff，发现风险和遗漏。
- Tester Agent：分析测试失败原因。
- Implementer Agent：后续阶段加入，用于限定范围内的代码修改。

子 Agent 约束：

- 明确 role prompt。
- 限定工具集合。
- 限定可访问路径。
- 限定最大轮数。
- 返回结构化结果。
- 不拥有最终决策权。

Main Agent 仍然负责规划、审批、质量控制和最终输出。

### 4.9 Memory System

Memory 分为四类：

```text
project_memory: 稳定项目事实，例如测试命令、应用目录结构。
user_memory: 用户偏好，例如回复语言、编码风格、常用工具。
procedure_memory: 可复用流程，例如如何定位 pytest 失败。
failure_memory: 失败尝试、错误假设和修复策略。
```

Memory 写入流程：

```text
Run 结束
-> Reflection Engine 提取候选记忆
-> admission rules 过滤候选
-> conflict detector 检查已有记忆
-> 写入带置信度和来源 run 的 memory
```

准入规则：

- 只存储可复用信息。
- 不存储密钥或敏感数据。
- 优先存储稳定事实，而不是一次性观察。
- 不确定的记忆标记低置信度。
- 保留来源 run 和时间戳。
- 冲突时依据新鲜度、置信度和用户明确指令处理。

### 4.10 Context Compression

上下文压缩不输出普通摘要，而是输出结构化任务状态。

```python
class TaskState(BaseModel):
    goal: str
    constraints: list[str]
    known_facts: list[str]
    decisions: list[str]
    failed_attempts: list[str]
    files_relevant: list[str]
    files_modified: list[str]
    next_actions: list[str]
```

压缩触发条件：

- 工具输出超过阈值。
- 对话历史超过 token budget。
- 文件片段过长。
- 验证失败，需要重新规划。

压缩必须保留：

- 用户目标。
- 硬性约束。
- 已读取和已修改文件。
- 已运行命令及结果。
- 失败尝试。
- 当前假设。
- 待验证步骤。

### 4.11 Trace Store

Trace Store 记录完整执行过程，用于调试、回放和评测。

事件类型：

- `run_started`
- `phase_changed`
- `model_request`
- `tool_requested`
- `permission_checked`
- `tool_finished`
- `context_compressed`
- `subagent_started`
- `subagent_finished`
- `memory_written`
- `verification_finished`
- `run_finished`

指标：

- pass / fail
- 输入和输出 token
- 工具调用次数
- 运行耗时
- 变更文件数量
- 权限阻断次数
- 上下文压缩率
- 重试次数
- 子 Agent 调用次数

### 4.12 Harness

任务格式：

```yaml
id: fix_pytest_failure_001
repo: samples/python_bug
prompt: "修复失败的测试。"
success:
  - command: "pytest"
    exit_code: 0
metrics:
  - pass_rate
  - tool_calls
  - token_cost
  - runtime
  - files_changed
```

Harness 能力：

- 运行单个任务或任务集。
- 每次运行前重置样例仓库。
- 执行 success commands。
- 存储 trace 和 metrics。
- 生成 Markdown 报告。
- 对比 baseline 和增强配置。

消融实验配置：

```text
baseline: 无 memory、无 skill、无 compression、无 subagents。
skill_only: 开启 skill routing。
memory_skill: 开启 memory 和 skill。
full: 开启 skill、memory、compression 和 reviewer subagent。
```

## 5. 数据模型草图

```python
class AgentState(BaseModel):
    run_id: str
    workspace: str
    user_goal: str
    current_phase: str
    selected_skills: list[str]
    task_state: TaskState
    tool_history: list[str]
    files_touched: list[str]
    approval_events: list[str]
    metrics: dict
```

```python
class MemoryRecord(BaseModel):
    id: str
    kind: str
    content: str
    confidence: float
    source_run_id: str
    created_at: str
    updated_at: str
    tags: list[str]
```

```python
class TraceEvent(BaseModel):
    id: str
    run_id: str
    event_type: str
    timestamp: str
    payload: dict
```

## 6. 错误处理

工具失败：

- 返回结构化错误 observation。
- 记录 stdout、stderr、exit code 和 timeout。
- 在可配置 retry limit 内允许 Agent 重新规划。

权限失败：

- 返回 safety observation。
- 要求模型选择更安全路径，或向用户请求确认。

上下文压缩失败：

- 回退到确定性截断。
- 必须保留目标、约束、已修改文件和最新错误。

模型调用失败：

- 对瞬时 API 错误进行 backoff 重试。
- 多次失败后优雅停止。
- 持久化 partial trace，方便排查。

验证失败：

- 捕获失败命令和输出。
- 若 retry budget 未耗尽，进入 `REPLAN`。
- 若重试耗尽，以明确原因标记 run failed。

## 7. 测试策略

单元测试：

- Tool schema 校验。
- 权限策略判断。
- 路径沙箱行为。
- Context compressor 保真规则。
- Memory 准入和冲突处理。

集成测试：

- 在 toy repositories 上运行 Agent。
- 验证编辑和测试闭环。
- 验证 trace event 持久化。
- 验证危险命令被阻断。

Harness 测试：

- 运行样例任务。
- 断言报告生成。
- 对比消融实验配置。

手动 demo：

- 修复失败的 pytest 测试。
- 添加一个小功能。
- 重构重复代码。
- 审查一个有风险的 diff。
- 阻断危险 shell 命令。

## 8. 里程碑

### 第 1 周：项目骨架和 CLI

- 创建包结构。
- 添加 Typer CLI。
- 添加配置加载。
- 添加 SQLite trace store。
- 实现基础 `minicode run`。

### 第 2 周：Tool Runtime 和权限系统

- 实现 read、search、edit、shell、test 和 git 工具。
- 实现风险等级和 allow / ask / deny 策略。
- 添加路径沙箱。
- 添加结构化 trace event。

### 第 3 周：Agent Loop

- 实现状态机阶段。
- 连接模型适配器和工具调用。
- 添加 planner 和 verifier prompt。
- 支持任务完成和失败状态。

### 第 4 周：Skill System

- 添加 skill registry。
- 添加元信息过滤。
- 添加关键词检索和 LLM rerank。
- 添加 debugging、test-writing 和 code-review skills。

### 第 5 周：Memory 和 Reflection

- 添加 memory records。
- 添加 run 结束后的 reflection。
- 添加 memory admission rules。
- 将相关 memory 加载进上下文。

### 第 6 周：Context Compression

- 添加 task state compression。
- 添加工具输出摘要。
- 添加 hot、warm、cold context 管理。
- 记录 compression metrics。

### 第 7 周：Subagents

- 添加 Explorer Agent。
- 添加 Reviewer Agent。
- 添加结构化 subagent result schema。
- 添加最大轮数和工具限制。

### 第 8 周：Harness 和简历指标

- 添加 benchmark task 格式。
- 添加 sample repositories。
- 添加 eval runner。
- 添加 Markdown report。
- 运行消融实验。
- 准备最终 README 和简历 bullet。

## 9. 简历定位

项目简介建议：

MiniCode Agent 是一个参考 Claude Code、SWE-agent 和 OpenHands 架构自研的本地 Coding Agent Runtime。项目基于状态机 Query Loop 和类型化 Tool Calling 构建多轮代码任务执行闭环，实现 Skill 路由、自进化记忆、分层上下文压缩、受控多 Agent 协作、权限安全审查，并内置轻量 Harness 支持 trace replay 和消融评测。

简历 bullet 建议：

- 基于 Query Loop 和 Tool Calling 构建本地 Coding Agent CLI，支持代码检索、文件编辑、shell 执行、测试验证、git diff 审计和执行轨迹回放。
- 设计分层 Skill 路由系统，结合任务意图识别、元信息过滤、检索和 LLM rerank，动态注入 debugging、testing、review 等能力包。
- 实现自进化记忆机制，将经验拆分为 project、user、procedure、failure memory，并通过 reflection、置信度、冲突检测和准入规则控制记忆污染。
- 构建分层上下文压缩机制，将长工具输出、文件片段和执行历史压缩为结构化 task state，保留约束、决策、失败尝试和下一步动作。
- 设计中心化多 Agent 协作流程，将 Explorer、Reviewer、Tester 子 Agent 作为受控 Tool Call 执行，限制上下文、工具、路径和最大轮数。
- 实现权限与安全审查链路，支持工具风险等级、路径沙箱、命令分类、prompt injection 检测和人工确认。
- 内置轻量 Harness，支持 benchmark 任务、trace replay、pass rate / token / tool call / runtime 指标统计，以及 Skill、Memory、Compression、Subagent 消融实验。

## 10. 审核节点

这份设计文档可作为 MiniCode Agent 的正式规格说明。确认后，下一步是编写实现计划，将系统拆分为具体任务、文件结构、测试范围和里程碑顺序。
