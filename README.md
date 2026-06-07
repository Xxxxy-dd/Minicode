# MiniCode Agent

MiniCode Agent 是一个本地 AI Coding Agent Runtime，参考 Claude Code 风格的 Query Loop + Tool Use 架构实现。项目把模型规划、工具调用、权限审批、技能路由、记忆沉淀、上下文压缩、Agent Team、Git Worktree 隔离执行、安全审查和评测 Harness 放在同一个可运行的本地框架里，目标是构建一个可控、可观测、可评测的代码 Agent 系统，而不是简单的 prompt demo。

## V1.2 交付状态

- 22 个本地 benchmark task，覆盖 debugging、feature、docs、safety、skills、memory、context、team/worktree。
- 最新 `full` 配置 eval：`22/22 passed`，`pass_rate: 100.00%`。
- 最新报告：`.minicode/evals/full/eval_20260607_093707_451286_f8be7e/report.md`。
- 全量测试：`353 passed`。
- Eval report 支持 `Safety Evidence`、`Team Evidence`、`Worktree Evidence`、`Memory Evidence`、`Context Compression Evidence`，可以直接展示 prompt injection、权限拒绝、patch proposal、memory recall 和压缩证据。

## 核心亮点

- Skill 能力体系：基于 metadata、aliases、tags、适用边界和示例进行任务路由，并支持可选 LLM rerank。
- Memory System：支持项目记忆、用户偏好、流程经验和 failure memory，写入经过 deterministic admission、secret rejection、duplicate/conflict/stale 策略。
- Context Compression：用 `ContextFrame`、`EvidenceRef` 和分类 evidence refs 保留长上下文中的失败、diff、测试证据。
- Agent Team：主 Agent 统一调度 explorer、reviewer、tester、security-reviewer、implementer，子 Agent 通过受限 Tool Call 返回结构化 evidence。
- Git Worktree Isolation：clean repo 中创建隔离 worktree，implementer 只产出 patch proposal，不自动 merge；dirty repo 返回 blocker。
- Safety & Audit：统一权限网关、危险命令阻断、Prompt Injection 检测、trace redaction 和 eval report 证据展示。

## 架构速览

```text
User Task
-> AgentLoop
-> Skill Routing / Memory Recall / ContextFrame
-> Tool Runtime
-> PermissionPolicy / Prompt Injection Guard / TraceStore
-> Agent Team / Worktree Isolation
-> Eval Harness / Evidence Report
```

## 快速 Demo

```powershell
E:\conda\envs\minicode\python.exe -m pytest -q
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks --workspace . --config full
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks\19_worktree_clean_isolation.json --workspace . --config full
```

生成的 Markdown report 位于 `.minicode/evals/`，可直接用于展示 V1.2 的安全、协作、隔离执行、记忆和上下文压缩能力。

## 项目目标

V1.2 的目标不是做一个“聊天壳”，而是提供一条完整闭环：

- 理解用户意图，区分日常对话和需要操作工作区的编码任务
- 通过工具读取、搜索、修改、创建、追加、删除文件
- 对写文件、运行命令等高风险动作进行审批
- 记录 trace，便于复盘每一步决策和工具结果
- 使用技能、记忆和上下文压缩改善长任务表现
- 使用本地 benchmark 任务集评估 Agent 行为

## 安装

推荐使用 Python 3.11 或更高版本。

```bash
pip install -e ".[dev]"
```

安装后会提供 `minicode` 命令：

```bash
minicode --help
```

## 快速开始

不接模型时，可以先用确定性的规则规划器跑本地演示：

```bash
minicode chat --workspace . --no-model
minicode run "inspect project" --workspace . --no-model
```

接入 OpenAI-compatible Chat Completions 模型后，可以运行完整的模型规划流程：

```bash
minicode chat --workspace . --llm-rerank --memory-reflection-mode llm
minicode run "review current diff" --workspace . --llm-rerank --memory-reflection-mode llm
```

模型配置优先使用 MiniCode 专用环境变量：

```bash
MINICODE_MODEL=gpt-4.1-mini
MINICODE_MODEL_API_KEY=...
MINICODE_MODEL_BASE_URL=https://api.openai.com/v1
```

兼容 fallback：

```bash
OPENAI_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
```

完整模板见 `.env.example`。

## 交互模式

启动交互式会话：

```bash
minicode chat --workspace .
```

常用交互命令：

```text
/help    查看可用快捷命令
/status  查看最近一次运行的 phase、tool、skill 和 trace id
/memory  查看最近项目记忆摘要
/skills  查看最近任务的 skill 路由摘要
/trace   查看最近 run 的 trace 摘要
/diff    查看最近一次写入 preview/diff
/clear   清空当前会话显示
/exit    退出 MiniCode
```

写文件、删除文件、运行命令等动作会触发确认。交互模式会先展示 diff/stat preview，再按 `y/N` 等待确认；拒绝时会显示“未应用变更”，文件保持不变。

## 核心能力

### Agent Loop

MiniCode 的主循环会持续执行：

1. 根据用户输入和当前状态生成下一步计划
2. 选择合适工具
3. 执行权限检查
4. 写入 trace
5. 把工具结果反馈给模型
6. 判断是否继续或给出最终回答

它支持最大步数限制、失败重试限制、重复工具调用拦截，以及针对追加、覆盖、创建、删除等文件意图的工具选择约束。

### 文件工具

默认工具集包括：

- `list_files`：列出工作区文件
- `read_file`：读取文件
- `search_code`：搜索代码或文本
- `write_file`：覆盖写入文件；目标不存在时可创建
- `append_file`：追加内容，支持格式感知
- `create_file`：创建新文件；目标已存在时失败
- `delete_file`：删除工作区内文件
- `edit_file`：按精确文本替换内容

`append_file` 会根据文件类型和参数处理格式，包括自然语言、Markdown、代码、JSON、CSV、TOML 和 YAML。JSON 数组/对象、CSV 列数、TOML 语法等会在写入前校验，避免把文件追加坏。

### 命令和 Git 工具

- `run_shell`：运行 shell 命令，需要审批
- `run_tests`：运行测试命令，需要审批
- `git_status`：查看 Git 状态
- `git_diff`：查看 Git diff

命令工具会经过风险分类。危险命令会被阻止或要求用户显式批准。

### 权限和沙盒

MiniCode 默认把文件操作限制在当前 workspace 内。写入、删除和运行命令属于需要审批的操作。

工具权限由统一的 permission gateway 处理，而不是散落在单个工具里。重复调用策略、工具意图、状态影响和子 Agent 可用性也声明在工具规格中，方便统一审计和复用。

### Skills

MiniCode 可以加载内置技能、外部技能和工作区技能。加载顺序如下，后者可以覆盖前者：

1. 内置技能：`src/minicode_agent/skills/builtin`
2. 环境变量 `MINICODE_SKILL_PATHS` 指定的额外目录
3. 当前工作区的 `.minicode/skills`

当前内置技能包括 `debugging`、`test-writing`、`code-review`、`refactoring`、`release-polish`、`security-review` 和 `repo-onboarding`。

技能目录结构：

```text
.minicode/
  skills/
    code-style/
      metadata.yaml
      SKILL.md
```

常用命令：

```bash
minicode skills list --workspace .
minicode skills show debugging --workspace .
minicode skills route "review current diff" --workspace .
```

Windows PowerShell 示例：

```powershell
$env:MINICODE_SKILL_PATHS="examples/skills"
minicode skills list --workspace .
```

### Memory

MiniCode 支持本地记忆，用于保存项目习惯、用户偏好、流程经验和失败教训。

```bash
minicode memory add "Use python -m pytest tests for validation" --kind project_memory --confidence 0.9
minicode memory list
minicode memory list --query pytest
minicode memory delete <memory_id>
```

记忆写入会经过置信度、重复内容和敏感信息检查。模型反思模式可以先生成候选记忆，再交给确定性规则决定是否保存。

### Trace

每次运行都会记录工具请求、权限判断、工具结果、压缩事件、子 Agent 事件和评测指标。

```bash
minicode trace
minicode trace <run_id> --json
```

Trace 可用于调试“为什么 Agent 这样回答”“为什么工具没有执行”“是否重复调用了同一个工具”等问题。

### V1.1 CLI Observability

V1.1 的 chat 交互会在每次 run 后显示最近 phase、tool call、selected skill 和 trace id。`/status`、`/memory`、`/skills`、`/trace`、`/diff` 复用同一套短摘要 renderer，避免 chat、CLI 和 trace 展示分叉。

### Subagents

MiniCode V1 内置只读子 Agent：

- Explorer：用于快速探索项目结构和相关文件
- Reviewer：用于检查 diff、风险和测试建议

子 Agent 复用主运行时的工具、权限和 trace 机制，不绕过沙盒。

## 常用工具命令

```bash
minicode tools list
minicode tools run read_file --path README.md
minicode tools run write_file --path notes.txt --content "hello" --approved
minicode tools run write_file --path nested/notes.txt --content "hello" --create-parents --approved
minicode tools run append_file --path notes.txt --content "more text" --append-format text --approved
minicode tools run create_file --path new_notes.txt --content "hello" --approved
minicode tools run delete_file --path old_notes.txt --approved
minicode tools run edit_file --path notes.txt --old-text "hello" --new-text "hi" --approved
minicode tools run run_shell --command "python --version" --approved
minicode tools run run_tests --command "python -m pytest tests" --approved
minicode tools run inspect_repo --workspace .
minicode tools run run_formatter --command "python -m black src tests" --approved
minicode tools run run_linter --command "python -m ruff check src tests" --approved
minicode tools run apply_patch --patch "<unified diff>" --approved
minicode tools run apply_patch --patch-file change.diff --approved
```

## 评测

MiniCode 提供本地 benchmark runner。任务可以来自单个 JSON 文件，也可以来自目录。

```bash
minicode eval examples/tasks --config baseline
minicode eval examples/tasks --config all
minicode eval examples/tasks --list-configs
minicode eval examples/tasks --config-file custom_ablation.json
```

评测报告会写入 `.minicode/evals/`，包括 Markdown 报告、`results.json` 和 `summary.csv`。

V1.1 的评测任务支持 trace assertions、forbidden tool assertions、file diff assertions 和 team assertions。`analysis_only` 任务也可以要求出现指定 trace 事件、禁止某个工具被请求，或验证 reviewer role 是否产出 evidence 和 merge blocker。

### Agent Team

`spawn_subagent` 现在会通过轻量 `AgentTeam` 协议运行 bounded role worker。V1.1 只启用 explorer/reviewer 的只读能力；implementer role 仅保留协议接口，不获得独立写权限。Team trace 会记录 `team_started`、`team_role_completed` 和 `team_finished`，并附带 workspace isolation plan。该 plan 只探测 git/worktree 可用性、branch 和 dirty state，不会自动创建 worktree、fork 或 merge。

## V1.1 发布检查

- `python -m pytest -q`
- `minicode chat --workspace . --no-model --preview`
- `minicode chat "/status" --workspace . --no-model --preview`
- `minicode tools run write_file --workspace . --path scratch.txt --content "hello"`
- `minicode eval examples/tasks --config baseline`
- `minicode eval examples/tasks --config all`

已知限制：

- V1.1 不自动创建 worktree、fork 或 merge。
- chat 仍是轻量 CLI，不是复杂 TUI。
- `/diff` 展示最近一次 write preview；历史完整 diff 仍通过 trace 查看。

## 验证

推荐的 V1 检查：

```bash
python -m pytest -q
minicode chat --workspace . --no-model --preview
minicode skills list --workspace .
minicode eval examples/tasks --config baseline
```

如果要验证模型路径，先设置模型环境变量，再运行：

```bash
minicode run "inspect project" --workspace . --llm-rerank --memory-reflection-mode llm
```

## 项目结构

```text
src/minicode_agent/
  agent/       Agent loop 和规划执行逻辑
  cli/         CLI 与交互式 UI
  context/     上下文压缩
  harness/     benchmark runner
  memory/      本地记忆
  models/      模型客户端、prompt 和响应解析
  permissions/ 权限策略和命令安全
  skills/      技能加载与路由
  subagents/   子 Agent 运行器
  tools/       工具定义、注册表和执行器
  trace/       trace 存储
```

## 文档

- [Architecture](docs/architecture.md)
- [Security](docs/security.md)
- [Demo Commands](docs/demo.md)
- [V1.2 Delivery Report](docs/V1.2交付报告.md)
- [V1.2 Iteration Plan](docs/V1.2迭代文档.md)
- [V1.1 Iteration Plan](docs/V1.1迭代优化计划.md)
- [Core Concepts](docs/核心概念说明.md)
- [Interview Q&A](docs/面试问答.md)
- [Design Spec](docs/superpowers/specs/2026-05-20-minicode-agent-design.md)

## 当前定位

V1 已经具备完整的本地 Agent Runtime 骨架，适合继续做两类迭代：

- 稳定性：继续收敛交互 UI、意图识别、工具选择、异常恢复和测试覆盖
- 能力增强：扩展文件操作、命令执行策略、技能生态、记忆质量和 benchmark 任务
