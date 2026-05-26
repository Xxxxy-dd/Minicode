# MiniCode Agent

MiniCode Agent 是一个本地 Coding Agent Runtime。它把模型规划、工具调用、权限审批、技能路由、记忆、上下文压缩、子 Agent 和评测 Harness 放在同一个可运行的本地框架里，适合用来学习、验证和迭代 Claude Code 风格的代码 Agent。

V1 的目标不是做一个“聊天壳”，而是提供一条完整闭环：

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
/help   查看可用快捷命令
/clear  清空当前会话显示
/exit   退出 MiniCode
```

写文件、删除文件、运行命令等动作会触发确认。交互模式里只需要按提示输入 `y` 或 `n`。

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
- [V1.1 Iteration Plan](docs/V1.1迭代优化计划.md)
- [Core Concepts](docs/核心概念说明.md)
- [Interview Q&A](docs/面试问答.md)
- [Design Spec](docs/superpowers/specs/2026-05-20-minicode-agent-design.md)

## 当前定位

V1 已经具备完整的本地 Agent Runtime 骨架，适合继续做两类迭代：

- 稳定性：继续收敛交互 UI、意图识别、工具选择、异常恢复和测试覆盖
- 能力增强：扩展文件操作、命令执行策略、技能生态、记忆质量和 benchmark 任务
