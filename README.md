# MiniCode Agent

MiniCode Agent 是一个本地 AI Coding Agent Runtime，参考 Claude Code 风格的 `Query Loop + Tool Use` 架构实现。它不是一个简单的聊天壳，而是把模型规划、工具调用、权限审批、Skill Routing、Memory System、Context Compression、Agent Team、Git Worktree 隔离执行、安全审查和 Eval Harness 组合成一个可运行、可观测、可评测的代码 Agent 系统。

项目目标是回答一个工程问题：**模型如何在本地开发环境中安全、可追踪、可验证地完成代码任务？**

## 项目状态

- Benchmark：22 个本地任务，覆盖 debugging、feature、docs、safety、skills、memory、context、team/worktree。
- 最新 `full` 配置 eval：`22/22 passed`，`pass_rate: 100.00%`。
- 最新报告：`.minicode/evals/full/eval_20260607_093707_451286_f8be7e/report.md`。
- 全量测试：`353 passed`。
- 交付说明：[docs/V1.2交付报告.md](docs/V1.2交付报告.md)。

Eval report 会生成 Markdown、`results.json` 和 `summary.csv`，并在 Markdown 中展示 `Safety Evidence`、`Team Evidence`、`Worktree Evidence`、`Memory Evidence` 和 `Context Compression Evidence`，可直接用于项目演示和面试讲述。

## 核心能力

| 能力 | 作用 |
| --- | --- |
| Agent Loop | 统一管理 load context、skill selection、planning、tool call、observe、verify、reflect。 |
| Tool Runtime | 所有文件、命令、Git、测试和 subagent 操作都通过注册工具执行。 |
| Permission Gateway | 写文件、运行命令、敏感路径和危险操作经过统一权限判断。 |
| Skill Routing | 基于 metadata、aliases、tags、适用边界和示例进行任务路由，并支持可选 LLM rerank。 |
| Memory System | 保存项目约定、用户偏好、流程经验和 failure memory，并通过 deterministic admission 控制长期状态质量。 |
| Context Compression | 将长工具输出压缩为 `ContextFrame`，通过 `EvidenceRef` 保留失败、diff、测试和 reviewer 证据。 |
| Agent Team | 主 Agent 调度 explorer、reviewer、tester、security-reviewer、implementer 等受控角色。 |
| Worktree Isolation | clean repo 中创建隔离 worktree，implementer 只产出 patch proposal，不自动 merge。 |
| Safety Review | 检测 prompt injection、危险命令、路径逃逸和敏感信息泄露，并写入 trace。 |
| Eval Harness | 本地 benchmark、ablation config、trace assertion 和 evidence report。 |

## 架构

```text
User Task
  -> AgentLoop
  -> Skill Routing / Memory Recall / ContextFrame
  -> Tool Runtime
  -> PermissionPolicy / Prompt Injection Guard / TraceStore
  -> Agent Team / Worktree Isolation
  -> Eval Harness / Evidence Report
```

关键边界：

- 模型不能直接修改文件或执行命令，只能请求注册工具。
- 所有高风险工具都经过权限网关和 trace 记录。
- 子 Agent 是受控 Tool Call，不是完全自治 peer agent。
- Worktree 任务只生成 patch proposal，不自动 merge 回用户 workspace。
- Prompt injection 来自 README、diff、日志、命令输出等非可信内容时，只作为 evidence 记录，不提升为用户指令。

## 快速开始

安装：

```bash
pip install -e ".[dev]"
```

查看 CLI：

```bash
minicode --help
```

无模型本地运行：

```bash
minicode run "inspect project" --workspace . --no-model
minicode chat --workspace . --no-model
```

接入 OpenAI-compatible Chat Completions 模型：

```bash
MINICODE_MODEL=gpt-4.1-mini
MINICODE_MODEL_API_KEY=...
MINICODE_MODEL_BASE_URL=https://api.openai.com/v1
```

```bash
minicode run "review current diff" --workspace . --llm-rerank --memory-reflection-mode llm
```

## 推荐演示

运行完整 V1.2 benchmark：

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks --workspace . --config full
```

安全演示：README / diff 中的恶意指令不会劫持 Agent。

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks\16_prompt_injection_readme.json --workspace . --config full
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks\18_prompt_injection_diff.json --workspace . --config full
```

Agent Team + Worktree 演示：clean repo 生成隔离 patch proposal，dirty repo 返回 blocker。

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks\19_worktree_clean_isolation.json --workspace . --config full
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks\20_worktree_dirty_blocker.json --workspace . --config full
```

Memory + Context 演示：failure memory 可解释召回，长上下文压缩后保留 evidence。

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks\21_failure_memory_recall.json --workspace . --config full
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks\22_context_evidence_compression.json --workspace . --config full
```

## Eval Report 示例

最新 `full` report 摘要：

```text
tasks: 22
config: full
passed: 22
pass_rate: 100.00%
```

报告中可直接看到：

- `Safety Evidence`：prompt injection finding、permission ask/deny reason。
- `Team Evidence`：role、tool calls、evidence refs、merge blockers、patch proposal id。
- `Worktree Evidence`：worktree path、branch、cleanup policy、`will_merge=False`。
- `Memory Evidence`：memory id、score、reason、source run/file/rule refs。
- `Context Compression Evidence`：compression ratio、compressed observation ids、ContextFrame 分类。

## CLI 能力

常用命令：

```bash
minicode run "inspect project" --workspace .
minicode chat --workspace .
minicode tools list
minicode tools run read_file --path README.md
minicode tools run write_file --path notes.txt --content "hello" --approved
minicode tools run run_shell --command "python --version" --approved
minicode skills list --workspace .
minicode skills route "review current diff" --workspace .
minicode memory list --query pytest
minicode trace <run_id> --json
minicode eval examples/tasks --config full
```

交互式 chat 支持：

```text
/help
/status
/memory
/skills
/trace
/diff
/tools
/config
/last
/clear
/exit
```

## 模块结构

```text
src/minicode_agent/
  agent/        Agent loop、规则规划器、模型规划器
  cli/          run/chat/tools/skills/memory/trace/eval 命令
  context/      ContextFrame、EvidenceRef、上下文压缩
  harness/      benchmark runner、ablation config、report
  memory/       memory store、reflection、admission policy、evidence refs
  models/       OpenAI-compatible client、prompt、parser
  permissions/  路径沙箱、命令风险分类、权限策略
  security/     prompt injection 检测、trace redaction
  skills/       内置 skill、外部 skill、metadata routing
  subagents/    AgentTeam、RoleProfile、WorktreeManager、PatchProposal
  tools/        文件、Git、shell、测试、subagent 工具
  trace/        SQLite/JSONL trace store
```

## Benchmark 任务

任务目录：`examples/tasks/`

覆盖范围：

- 基础代码任务：修 bug、加 feature、更新 docs、补测试、重构。
- 安全任务：危险命令阻断、README 注入、命令输出注入、diff 注入。
- Skill 任务：workspace skill routing、release polish。
- Agent Team：reviewer evidence、implementer patch proposal、dirty worktree blocker。
- Memory / Context：failure memory recall、context evidence compression。

## 文档

- [V1.2 交付报告](docs/V1.2交付报告.md)
- [Architecture](docs/architecture.md)
- [Security](docs/security.md)
- [Demo Commands](docs/demo.md)
- [Interview Q&A](docs/面试问答.md)
- [Core Concepts](docs/核心概念说明.md)
- [V1.2 Iteration Plan](docs/V1.2迭代文档.md)
- [Design Spec](docs/superpowers/specs/2026-05-20-minicode-agent-design.md)

## 简历描述

MiniCode Agent：参考 Claude Code 架构实现本地 AI Coding Agent Runtime，构建 Query Loop、Tool Calling、Skill Routing、Memory System、Context Compression、Agent Team、Git Worktree 隔离执行与安全审查机制，支持多任务代码理解、修改、测试和审计流程。设计中心化多 Agent 协作架构、Prompt Injection 防御链路和可解释 Eval Harness，在本地权限边界内提升复杂 Coding 任务的稳定性、安全性和可展示性。

## 当前边界

- 不做 SaaS 或 Web UI。
- 不自动 push、merge、创建远程 PR。
- 不把所有安全判断交给 LLM。
- Prompt Cache 目前通过 PromptSegment / ContextFrame 预留稳定上下文边界，不宣称已接入完整缓存服务。
- Eval task 是本地轻量 benchmark，适合证明架构链路和回归，不等价于大型公开 SWE benchmark。
