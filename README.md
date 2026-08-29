# Coding Agent

Python coding agent。核心 Loop 负责模型请求、工具调用和停止条件；Harness 负责装配工作区、工具、权限、Hook 和会话存储。

## 快速开始

需要 Python 3.12+。在 .env 中配置 OpenAI 兼容接口：

    API_KEY=...
    BASE_URL=https://...
    MODEL=deepseek-v4-flash

单次任务：

    python -m cli.main --cwd . "在工作目录创建 demo.txt 并写入 Hello World"

REPL：

    python -m cli.main --cwd .

可用参数：

- --cwd：工作目录。
- --max-turns：单次 prompt 的最大 Loop 轮数，默认 24。
- --session-file：会话 JSONL 文件；省略时使用工作区 .agent/sessions/ 下按工作区哈希生成的默认路径。

## 架构

    CLI/TUI
      -> Harness
          -> AgentLoop
              -> ContextBuilder
              -> ModelProvider
              -> ToolExecutor
                  -> ToolRegistry
                  -> Tool
                  -> ExecutionEnv
              -> LoopState
          -> JsonlSessionStore
          -> AgentEvent stream

Loop 按以下五个阶段运行：

    prepare_context
      -> stream_model
      -> finalize_assistant
      -> execute_tools
      -> commit_turn_and_decide

核心模块不依赖 TUI、GUI、RPC、ACP 或具体模型 SDK。未来界面通过 Harness 和 AgentEvent 接入。

## 内置工具

- read_file：读取工作区内的 UTF-8 文本文件。
- write：创建或覆盖文件。
- edit：替换文件中的精确文本片段。
- list_dir：列出工作区目录。
- search：按 glob 搜索工作区路径。
- exe：在固定工作目录执行 Shell 命令。

路径校验和工作区边界由 ExecutionEnv 负责。当前权限实现仍为 AllowAllPermissions，真实询问式权限和 OS 级沙箱是后续扩展。

## 工具输出截断

ToolExecutor 统一限制工具结果，默认上限为 2,000 行或 50 KiB，先达到的限制生效：

- read_file、search、list_dir 使用 head 截断，保留开头。
- exe 使用 tail 截断，保留末尾错误和最终输出。
- 截断按 UTF-8 字节边界处理，不返回超过限制的半个字符。
- ToolResult 记录 truncated、truncated_by、总量和输出量等元数据。
- 返回内容附带截断提示，模型可以通过更窄的查询继续读取遗漏部分。

截断只限制进入上下文的结果，不修改原文件。

## 上下文和会话

每轮模型请求前，ContextBuilder 组装：

- 系统提示词；
- 工作区根目录、Git 状态、平台、Shell、模型和日期；
- 根目录 AGENTS.md；
- 当前会话历史；
- 当前工具 Schema。

Harness 启动时从 JsonlSessionStore 恢复历史；运行过程中按消息追加持久化。JSONL 位于工作区 .agent/sessions/ 下，记录包含 session_id、message_id、parent_id 和 operation_id，便于后续加入分支和操作追踪。

当前已实现完整历史重放，但尚未实现历史裁剪、Token 预算、自动 Compact、CustomMessage、多分支会话和多会话管理。

## 测试

使用 FakeProvider 和临时工作区运行测试：

    pytest -q

当前测试覆盖文本流、工具往返、工作区边界、Hook、停止条件、head/tail 截断、UTF-8 字节边界和 JSONL 会话恢复。真实模型调用不是测试前置条件。

## 项目文件

- src/core：消息、Loop、上下文、状态和错误。
- src/tools：工具契约、注册表、执行器、内置工具和截断器。
- src/runtime：ExecutionEnv、权限和 SessionStore。
- src/harness：长生命周期入口和工具 Hook 装配。
- src/providers：模型协议适配。
- src/cli：命令行入口和事件渲染。
- docs/architecture-plan.md：整体架构计划。
