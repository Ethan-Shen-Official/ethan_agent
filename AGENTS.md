# Coding Agent Implementation Context

本文件是本项目的快速开发上下文。实现或修改代码前必须遵守这些边界；完整设计说明见 `docs/architecture-plan.md`。

## 项目目标

实现一个独立、轻量优先的 coding agent。模型负责理解任务和提出 ToolCall；本项目自行负责 Agent Loop、工具定义与执行、上下文、权限、会话、错误处理和终止条件。

不得使用 Agent 框架或 Agent SDK。允许使用普通 HTTP/API 客户端、终端库和测试工具，但不得使用服务端托管的代码执行或文件工具。

## 当前架构

```text
TUI -> Harness -> Agent Loop
                    |
       Context / Provider / ToolExecutor
                              |
                   Permission + ExecutionEnv
                              |
                         SessionStore
                              |
                       AgentEvent stream
```

建议的 P0 目录：

```text
src/
├── main.py
├── cli/{main.py,renderer.py}
├── core/{types.py,events.py,state.py,loop.py,context.py,errors.py}
├── providers/{base.py,openai_compatible.py}
├── tools/{base.py,registry.py,executor.py,filesystem.py,shell.py}
├── runtime/{permissions.py,execution.py,session.py,compact.py}
└── harness/app.py
```

首版默认单会话、单活动运行、串行工具；`extensions/`、`bridges/`、多 Agent、多 Lane 和远程执行只保留后续扩展位置，不进入 P0。

## Agent Loop 五阶段

```text
  prepare_context
  -> stream_model
  -> finalize_assistant
  -> execute_tools
  -> commit_turn_and_decide
```

### `prepare_context`

- 根据 Query 和 LoopState 装配 system prompt、历史和工具 Schema；
- 截断超大工具结果并估算上下文；
- 必要时执行一次轻量 Compact，然后继续当前 Loop；
- 请求前检查取消、最大轮数和预算。

### `stream_model`

- Provider 只负责模型协议转换和流式响应；
- 立即发出 `text_delta` 等事件；
- 流结束后才物化完整 AssistantMessage；
- 不在半截 ToolCall 参数上执行工具。

### `finalize_assistant`

- 校验助手消息和 ToolCall JSON；
- 以实际解析出的 ToolCall 判断是否继续，不依赖 stop reason；
- 没有 ToolCall 时提交最终回答并结束；
- 响应被截断或不可解析时返回结构化错误或停止。

### `execute_tools`

- 查找工具、校验 Schema、执行语义检查和权限判断；
- 通过 `ExecutionEnv` 执行文件和命令；
- 默认串行；每个 ToolCall 必须得到且只有一个 ToolResult；
- 拒绝、异常、超时和取消都生成 `is_error=true` 的结果；
- 取消时补齐已经产生但尚未完成的 ToolCall。

### `commit_turn_and_decide`

- 按源顺序提交 `assistant message -> tool results`；
- 更新消息、轮数、Token、错误和恢复计数；
- 检查 completed、cancelled、max_turns、budget_exhausted、provider_error 和 recovery_exhausted；
- 需要继续时回到 `prepare_context`。

## 稳定契约

- `ModelProvider.stream(request)`：输出内部 ProviderEvent，不泄漏厂商消息类型；
- `Tool`：名称、描述、输入校验、风险分类、执行和结果映射；
- `SessionStore`：追加/读取消息，保留 `session_id`、`message_id`、`parent_id`、`operation_id`；
- `AgentEvent`：TUI、测试和未来 GUI/RPC/ACP 的只读事件流；
- `ExecutionEnv`：抽象文件系统、Shell、cwd、环境策略和资源限制。

核心模块不得导入 TUI、GUI、RPC、ACP 或具体模型 SDK。Harness 是所有界面的长生命周期入口。

## 执行安全默认值

默认执行模式为 `workspace`：

- 路径必须位于工作区根目录；拒绝路径穿越和不允许的符号链接；
- Shell 固定 cwd，限制环境变量、超时、输出大小和子进程树；
- `read` 默认 allow；`write`、`edit`、删除、覆盖和高风险命令默认 ask；
- API key、Authorization 和敏感环境变量不得进入消息、日志或事件；
- OS 级隔离通过未来的 `IsolatedExecutionEnv` 接入，不修改 Loop 或 Tool 契约。

## 测试要求

优先为 `core`、权限、路径校验、工具结果配对、停止条件、取消和 Compact 编写单元测试；使用 FakeProvider + 临时工作区做集成测试。真实模型调用不得成为测试前置条件。


## Current P0 implementation

- Harness owns one LoopState and restores the active branch transcript from JsonlSessionStore at startup.
- The default session file is stored at .agent/sessions/<workspace-hash>.jsonl under the workspace root. Pass --session-file or session_path to choose an explicit location.
- Each JSONL record contains session_id, message_id, parent_id, operation_id, and a serialized Message. The persisted format is append-only and versioned. A sibling `.head` file stores the active leaf; checkout/rollback moves that pointer without deleting historical records.
- Tool output is capped centrally by ToolExecutor at 2,000 lines or 50 KiB by default. File/search/list results use head truncation; exe uses tail truncation. Truncation is UTF-8 byte-safe and records metadata on ToolResult.
- A truncated result contains an actionable notice for the model. The full source files remain unchanged; callers can use a narrower read/search request to retrieve omitted data.

## Context and persistence boundaries

The provider receives only standard Message values plus ModelRequest.system_prompt and tool schemas. Session persistence is a Harness/runtime concern and does not change the Provider contract. AgentEvent remains a transient observation stream and is not written to the transcript.

The current context builder includes runtime metadata, root AGENTS.md, the active branch history, and tool schemas. History pruning, token estimation/enforcement, automatic Compact, CustomMessage projection, and interactive fork commands remain extension points rather than P0 features.
