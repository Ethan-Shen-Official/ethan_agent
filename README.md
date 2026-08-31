# Coding Agent

Python coding agent。核心 Loop 负责模型请求、工具调用和停止条件；Harness 负责装配工作区、工具、权限、Hook 和会话存储。

## 快速开始

需要 Python 3.12+。在 .env 中配置 OpenAI 兼容接口：

    API_KEY=...
    BASE_URL=https://...
    MODEL=deepseek-v4-flash

单次任务：

    python -m cli.main --cwd . "在工作目录创建 demo.txt 并写入 Hello World"

交互式 TUI：

    python -m cli.main --cwd .

可用参数：

- --cwd：工作目录。
- --max-turns：单次 prompt 的最大 Loop 轮数，默认 24。
- --session-file：会话 JSONL 文件；指定后打开该文件。
- --continue：恢复工作区 `.agent/sessions/` 下最近修改的会话；不指定时每次启动创建新的时间戳 + 12 位随机 ID 会话文件。

REPL 中的会话命令：

- `/help [command]`：显示所有命令，或查看单个命令的用法；简写为 `/h`、`/?`。
- `/new`：创建并切换到新的空会话；简写为 `/n`。
- `/name [name]`：查看、设置或清除当前会话显示名称（使用 `/name -` 清除）；简写为 `/nm`。
- `/resume [session-id]`：带参数时按会话 ID、文件名或唯一前缀切换；不带参数时列出会话并按编号或短 ID 选择；简写为 `/res`。
- `/drop <session-id>`：经确认后永久删除指定的非当前会话；当前会话不能删除。
- `/tree`：显示当前会话的树状节点、短 ID、角色预览和活动路径；简写为 `/t`。
- `/checkout <message-id>`：切换到指定消息节点；支持唯一 ID 前缀。
- `/rollback [message-id]`：带 ID 时切换到指定节点；不带 ID 时回退当前用户任务之前的安全边界。
- `/show_context`：显示最近一次实际发送给模型的完整上下文快照，敏感值默认脱敏。
- `/show_context --raw`：在本地调试时显示未脱敏快照。
- `/compact`：立即压缩当前会话的旧消息，并将摘要写入 Session Tree。
- `/abort`：中断当前正在运行的 Agent 任务；简写为 `/stop`。
- `/permission_mode`：显示当前权限模式。
- `/permission_mode <mode>`：切换后续工具调用的权限模式，可选 `default`、`accept_edits` 或 `bypass_permissions`。
- `/perm [d|e|b]`：权限模式简写；`d=default`、`e=accept_edits`、`b=bypass_permissions`。
- `/exit`：退出 REPL；简写为 `/quit`、`/q`。

这些命令只回滚消息上下文，不撤销工具已经产生的文件或 Shell 副作用。Compact 同样不会删除原始 Session 记录。

`/show_context` 是 Harness 层的只读诊断能力。它通过 Provider 观察器捕获最终 `ModelRequest`，包含 system prompt、消息和工具 Schema，不修改 Agent Loop，也不会写入 SessionStore。

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

权限策略由 Harness 装配到 before_tool 预执行链：默认允许读取，写入、编辑和 Shell 命令需要确认；`--permission-mode` 可选择 `default`、`accept_edits` 或 `bypass_permissions`。路径校验和工作区边界仍由 ExecutionEnv 负责，不能被权限模式绕过；包含 `.agent` 或 `.git` 元数据路径的文件操作和 Shell 命令始终被拒绝。ExecutionEnv 还会再次执行这一安全检查，因此直接使用 `AllowAllPermissions` 或绕过 Harness 的调用也不能修改这些目录。递归删除工作区根目录、全量通配符删除、批处理 `for` 动态删除循环、`find . -delete` 和 `git clean -f` 等命令属于不可逆操作，在所有模式下直接拒绝。命令文本检查无法替代操作系统 ACL 或容器隔离，对编码、外部脚本和系统漏洞只能提供尽力而为的防护。

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

Harness 启动时从 JsonlSessionStore 恢复当前活动分支；运行过程中按消息追加持久化。默认每次启动创建 `.agent/sessions/<timestamp>_<12位随机ID>.jsonl`，`--continue` 才恢复最近会话，`--session-file` 可打开指定会话。每个 JSONL 记录包含 session_id、message_id、parent_id 和 operation_id；Compact 会追加 `type=compaction` 摘要记录，会话重命名会追加 `type=session_info` 元数据记录。旁边的 `.head` 文件记录当前叶节点。通过 SessionStore 的 `checkout`/`rollback` 可以切换到已有历史节点，不删除旧分支；交互式 `/fork` 命令暂未加入。

当前已实现活动分支历史重放和不删除历史的 checkout/rollback API、REPL 命令注册表、`/help`、`/new`、`/name`、交互式或精确参数 `/resume`、带确认且仅针对非当前会话的 `/drop`、`/tree`、`/checkout` 和 `/rollback`，以及手动 `/compact` 和阈值触发的轻量 Compact；尚未实现历史裁剪、精确 Token 预算、完整 turn-prefix Compact、CustomMessage、交互式 `/fork` 命令和跨工作区会话管理。

## 测试

使用 FakeProvider 和临时工作区运行测试：

    pytest -q

当前测试覆盖文本流、工具往返、工作区边界、Hook、停止条件、head/tail 截断、UTF-8 字节边界和 JSONL 会话恢复。真实模型调用不是测试前置条件。

## 项目文件

- src/core：消息、Loop、上下文、状态和错误。
- src/tools：工具契约、注册表、执行器、内置工具和截断器。
- src/runtime：ExecutionEnv、权限和运行时策略；`runtime/session/` 内按 `types.py`（契约）、`paths.py`（路径）、`codec.py`（编解码）、`tree.py`（分支树）和 `store.py`（JSONL 存储）拆分，`runtime.session` 保留兼容导出。
- src/harness：长生命周期入口和工具 Hook 装配。
- src/providers：模型协议适配。
- src/cli：命令行入口、TUI 生命周期和事件渲染。交互模式使用状态化终端渲染；工具调用只在运行期间显示，收到结果后从临时区域清除。
- docs/architecture-plan.md：整体架构计划。
