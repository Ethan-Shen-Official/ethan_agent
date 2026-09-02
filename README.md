# coding-agent

一个轻量、自包含的 coding agent。它参考 Pi 的交互模型，但不依赖 Agent
框架或 Agent SDK：模型只负责理解任务和提出文本/ToolCall，本地 Harness 负责
上下文、工具执行、权限、会话持久化、取消和停止条件。

项目支持两类使用方式：

- 在终端中使用 Pi 风格的 CLI/TUI；
- 通过 ACP（Agent Client Protocol）接入 VS Code 等 ACP 客户端。

当前实现使用 OpenAI-compatible Chat Completions 接口，可以连接 DeepSeek、
OpenAI 兼容网关、本地 vLLM 或其他提供相同协议的服务。

## 特性概览

- 流式模型输出：文本增量、ToolCall、工具结果和错误按事件顺序渲染。
- Pi 风格 TUI：用户消息、助手 Markdown、工具调用、Diff、权限确认和工作状态
  分块显示；工具输出默认折叠，可用 `Ctrl+O` 展开。
- 工作区安全：文件路径限制在当前工作区，`.agent`、`.git` 和工作区级递归删除
  始终受到保护。
- 专用只读工具：`ls`、`find`、`grep`、`read` 优先用于目录和代码分析，减少不必要
  的 Shell 权限确认。
- 会话树：JSONL 追加式历史、活动分支、`/resume`、`/tree`、`/checkout`、
  `/rollback` 和受保护的 `/drop`。
- Compact：摘要写入 Session Tree，原始记录保留；上下文达到阈值时可自动压缩。
- 取消与单任务约束：`Esc`、`Ctrl+C` 或 `/abort` 可中断当前任务；同一 Harness
  同时只允许一个活动 prompt。
- ACP v3 bridge：通过 JSON-RPC 2.0 stdio 转发 `session/new`、`session/prompt`、
  `session/cancel` 和权限请求。

## 环境要求

- Python 3.12 或更高版本；
- 一个 OpenAI-compatible 模型接口；
- Windows、Linux、macOS 均可运行。Windows 推荐使用 PowerShell 或 Conda。

项目运行时只使用 Python 标准库。开发测试额外需要 `pytest`，构建 Release
需要 `build` 和可选的 `pyinstaller`。

## 快速开始

### 从源码安装

```powershell
conda create -n coding-agent python=3.12
conda activate coding-agent
python -m pip install -e .
```

也可以使用标准虚拟环境：

```bash
python3.12 -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

安装后会提供两个命令：

```text
coding-agent       # CLI/TUI
coding-agent-acp   # ACP stdio bridge
```

如果不安装，也可以在仓库根目录直接运行：

```powershell
$env:PYTHONPATH = "src"
python -m cli.main --cwd .
```

### 配置模型接口

复制 `.env.example` 为 `.env`，然后填写自己的凭据：

```powershell
Copy-Item .env.example .env
```

最小配置：

```dotenv
CODING_AGENT_API_KEY=sk-your-key
CODING_AGENT_BASE_URL=https://api.deepseek.com/v1
CODING_AGENT_MODEL=deepseek-v4-flash
```

`BASE_URL` 如果没有以 `/chat/completions` 结尾，程序会自动补上该路径。
不要把真实 `.env` 提交到 Git；仓库的 `.gitignore` 已经忽略它。

变量优先级如下，左侧优先：

```text
CODING_AGENT_API_KEY > OPENAI_API_KEY > API_KEY
CODING_AGENT_BASE_URL > OPENAI_BASE_URL > BASE_URL
CODING_AGENT_MODEL > DEEPSEEK_MODEL > MODEL
```

操作系统环境变量优先于 `.env` 文件。`.env` 只会填充尚未设置的环境变量，不会
覆盖已有值。

可选变量：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `CODING_AGENT_TIMEOUT` | `120` | 单次 HTTP 请求超时（秒） |
| `CODING_AGENT_PERMISSION_MODE` | `default` | ACP 默认权限模式 |
| `CODING_AGENT_SESSION_DIR` | `<workspace>/.agent/sessions` | 会话 JSONL 存储目录 |

独立 exe 读取的是**进程当前工作目录**下的 `.env`，不是 exe 所在目录。最稳妥
的方式是启动前设置环境变量，或先切换到 `.env` 所在目录：

```powershell
$env:CODING_AGENT_API_KEY = "sk-your-key"
$env:CODING_AGENT_BASE_URL = "https://api.deepseek.com/v1"
$env:CODING_AGENT_MODEL = "deepseek-v4-flash"
.\coding-agent-windows.exe --cwd "D:\NJU\my-project"
```

### 运行 CLI/TUI

单次任务：

```bash
coding-agent --cwd ./my-project "分析当前目录并列出可能需要修改的文件"
```

交互式 TUI：

```bash
coding-agent --cwd ./my-project
```

常用启动参数：

| 参数 | 说明 |
| --- | --- |
| `--cwd PATH` | 工作区根目录，默认为当前目录 |
| `--continue` | 恢复该工作区最近修改的会话 |
| `--session-file PATH` | 打开指定 JSONL 会话文件 |
| `--max-turns N` | 单次 prompt 最大 Loop 轮数，默认 24 |
| `--permission-mode MODE` | `default`、`accept_edits` 或 `bypass_permissions` |

TUI 底部状态栏显示工作目录、累计输入/输出 token、缓存统计、当前上下文窗口
占用和模型。累计输入量与当前上下文占用是两个不同指标：前者跨请求累加，后者
表示最近一次模型请求或本地投影在 context window 中的占用。

## 交互命令

在 TUI 中输入 `/help` 可查看命令帮助。完整命令如下：

| 命令 | 作用 |
| --- | --- |
| `/help [command]` | 查看全部命令或某个命令详情；别名 `/h`、`/?` |
| `/new` | 创建并切换到新的空会话；别名 `/n` |
| `/name [name]` | 查看、设置或清除会话名称；别名 `/nm` |
| `/resume [session-id]` | 列出或切换会话；别名 `/res` |
| `/drop [session-id]` | 选择并删除非当前会话 |
| `/tree` | 查看当前会话树和活动路径；别名 `/t` |
| `/checkout <message-id>` | 切换到消息分支；别名 `/co` |
| `/rollback [message-id]` | 回退活动分支；别名 `/rb` |
| `/show_context [--raw]` | 查看最近一次模型请求，默认脱敏 |
| `/compact` | 手动压缩旧上下文；别名 `/cmp` |
| `/abort` | 中断当前任务；别名 `/stop` |
| `/permission_mode [mode]` | 查看或切换权限模式；别名 `/perm` |
| `/exit` | 退出 TUI；别名 `/quit`、`/q` |

会话切换和 checkout/rollback 只改变模型消息树，不撤销已经发生的文件写入或
Shell 副作用。`/drop` 会要求确认，当前正在使用的会话不会出现在可删除列表中。

## 工具与安全模型

### 内置工具

| 工具 | 用途 | 默认风险 |
| --- | --- | --- |
| `read` | 分段读取 UTF-8 文件，支持 `offset`/`limit` | 只读，默认允许 |
| `ls` | 列出目录，支持深度和隐藏文件选项 | 只读，默认允许 |
| `find` | 按 glob 搜索路径 | 只读，默认允许 |
| `grep` | 按正则或字面量搜索文件内容 | 只读，默认允许 |
| `write` | 创建或原子覆盖文件 | 需要确认 |
| `edit` | 对文件执行精确替换并记录 Diff | 需要确认 |
| `bash` | 在固定 cwd 执行 Shell 命令 | 需要确认 |
| `powershell` | 在固定 cwd 执行 PowerShell 命令 | 需要确认 |

模型进行目录/代码分析时应优先使用 `ls`、`find`、`grep`、`read`。Shell 是兜底
能力，不应重复实现这些专用只读操作。

### 权限模式

- `default`：读取允许，写入、编辑和 Shell 通常需要逐次确认；
- `accept_edits`：接受常规编辑，仍保留危险操作保护；
- `bypass_permissions`：跳过普通权限询问，但不绕过工作区边界和元数据保护。

无论权限模式如何，以下边界都由 `ExecutionEnv` 和权限 Hook 双重执行：

- 路径必须位于工作区根目录内；
- `.agent` 和 `.git` 始终不可写、不可删除；
- 工作区根目录递归删除、全量通配符删除、`git clean -f`、`find . -delete` 等
  常见不可逆命令会在 Shell 启动前拒绝；
- Shell 使用固定 cwd、超时、取消和输出大小限制。

这些保护是应用层防线，不等价于操作系统沙箱。对恶意二进制、系统漏洞或被授权
的外部程序，仍应使用操作系统 ACL、容器或独立沙箱。

### 工具输出截断与 Diff

工具结果默认限制为 2,000 行或 50 KiB：文件和搜索类工具保留头部，Shell 类工具
保留尾部。结果会带有截断元数据和继续读取提示，原文件不会被修改。`edit` 的
Diff、Patch 和完整输出详情保存在瞬态 `ToolDetailsStore` 中，TUI 可展开查看，
不会写入模型会话 JSONL。

## 会话与上下文

每个工作区的会话默认存放在：

```text
<workspace>/.agent/sessions/<timestamp>_<12-hex-id>.jsonl
```

JSONL 是追加式格式，每条记录包含 `session_id`、`message_id`、`parent_id` 和
`operation_id`。同名 `.head` 文件记录活动叶节点。Session Tree 保留旧分支，
checkout/rollback 只移动 head，不删除历史记录。

模型请求上下文由以下部分组成：

1. 系统提示词和运行时元数据；
2. 当前工作区根目录的 `AGENTS.md`（只从当前工作区查找）；
3. 活动分支上的消息历史；
4. 当前注册工具的 Schema。

Compact 使用轻量的 chars/4 token 估算和安全消息边界：

```text
context_window = 128000
reserve_tokens = 4096
keep_recent_tokens = 16000
```

自动压缩在投影上下文超过 `context_window - reserve_tokens` 时触发。手动 `/compact`
也要求存在达到压缩范围且可安全切分的旧消息；过短会话会返回
`Nothing to compact (session too small)`。Compact 只追加摘要检查点，原始消息仍可
通过会话树查看和回溯。

## ACP 接入 VS Code

ACP bridge 使用 JSON-RPC 2.0 over stdio，标准输出专用于协议消息，诊断信息写入
stderr。源码安装后可直接启动：

```powershell
python -m pip install -e .
coding-agent-acp
```

VS Code ACP 客户端配置示例：

```json
{
  "acp.agents": {
    "coding-agent": {
      "command": "D:\\NJU\\.conda\\envs\\coding-agent\\python.exe",
      "args": ["-m", "bridge.acp"],
      "env": {
        "CODING_AGENT_API_KEY": "sk-your-key",
        "CODING_AGENT_BASE_URL": "https://api.deepseek.com/v1",
        "CODING_AGENT_MODEL": "deepseek-v4-flash",
        "CODING_AGENT_PERMISSION_MODE": "default"
      }
    }
  }
}
```

如果没有安装项目，可以把项目的 `src` 放进 ACP 进程环境：

```json
{
  "command": "D:\\NJU\\.conda\\envs\\coding-agent\\python.exe",
  "args": ["-m", "bridge.acp"],
  "env": {
    "PYTHONPATH": "D:\\NJU\\codeagent\\src"
  }
}
```

不要在 ACP 参数中传 `--cwd`；ACP 客户端会在 `session/new` 中提供工作区目录。
当前 bridge 支持初始化、新会话、文本 prompt、取消、工具进度和权限请求；图片、
音频、MCP、会话加载和模型动态配置暂未支持。所有 ACP 会话共享一个进程级运行锁，
同一时间最多执行一个 prompt。

## 从 GitHub Release 安装

仓库的 `.github/workflows/release.yml` 在推送 `v*` tag 后执行测试、构建和发布：

```powershell
git add .
git commit -m "release: v0.2.0"
git tag v0.2.0
git push origin main
git push origin v0.2.0
```

Release 页面会包含：

- Windows、Linux、macOS 的 PyInstaller 独立程序；
- Python wheel；
- Python source tarball。

普通用户优先下载对应平台的独立程序。需要 ACP 或二次开发时下载 wheel/source
并安装到 Python 3.12 环境。独立程序和 Python 安装都需要用户自行提供模型 API
密钥，Release 不包含任何真实 `.env` 或凭据。

## 开发与测试

安装开发依赖并运行测试：

```bash
python -m pip install -U pytest
python -m pytest -q
python -m compileall -q src tests
```

测试使用 `FakeProvider` 和临时工作区，不要求真实模型 API。重点覆盖：

- Agent Loop 的 ToolCall 往返和停止条件；
- 工作区路径、`.agent`/`.git` 保护和权限 Hook；
- ToolResult 配对、输出截断、取消和 Compact；
- JSONL 会话恢复、分支树和 TUI 渲染。

## 架构

```text
CLI / TUI / ACP
       |
     Harness                 <- 长生命周期门面和依赖装配
       |
   AgentSession              <- 会话、取消、Compact 协调
       |
    AgentLoop                <- 核心五阶段循环
       |
  +----+-----------+----------------+
  | ContextBuilder | ModelProvider  |
  | ToolExecutor   | LoopState      |
  +----+-----------+----------------+
       |
  Permission + ExecutionEnv
       |
  SessionStore / AgentEvent
```

Agent Loop 的稳定阶段为：

```text
prepare_context
  -> stream_model
  -> finalize_assistant
  -> execute_tools
  -> commit_turn_and_decide
```

依赖方向从界面到 Harness，再到核心运行时；`core` 不导入 TUI、GUI、RPC、ACP 或
具体模型 SDK。未来界面通过 `Harness` 和 `AgentEvent` 接入，不需要改动核心 Loop
契约。

主要目录：

```text
src/
├── cli/                  CLI 参数、REPL、TUI 和会话视图
├── core/                 消息类型、Agent Loop、上下文和错误
├── providers/            OpenAI-compatible provider
├── tools/                工具契约、注册表、执行器和内置工具
├── runtime/              权限、执行环境、Compact、Session Store
├── harness/              长生命周期 AgentSession 和应用装配
└── bridge/acp/           ACP JSON-RPC stdio bridge
```

## 已知限制

- 当前 Provider 只实现 OpenAI-compatible streaming 接口；
- Compact 使用轻量 token 估算，不是厂商 tokenizer 的精确计数；
- ACP v3 暂不支持图像、音频、MCP、历史会话加载和动态模型配置；
- TUI 的工具详情是瞬态数据，完整详情不会持久化到会话文件；
- 应用层权限保护不能替代操作系统级沙箱。

更多设计细节请参阅：

- [`docs/architecture-plan.md`](docs/architecture-plan.md)
- [`docs/tools.md`](docs/tools.md)
- [`docs/commands.md`](docs/commands.md)
- [`docs/compact.md`](docs/compact.md)
- [`docs/acp.md`](docs/acp.md)

## 安全提示

API key 只应通过环境变量或本地未入库的 `.env` 提供。不要把密钥、Authorization
header 或包含凭据的日志提交到 GitHub。执行具有破坏性的任务前，请确认
`--cwd` 指向正确的工作区，并保留重要项目的独立备份。
