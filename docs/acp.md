# ACP bridge

The project includes a first-version Agent Client Protocol bridge at
bridge.acp. It speaks JSON-RPC 2.0 over stdin/stdout and adapts ACP sessions to
the existing Harness; the agent loop and core package are not modified.

## Run

Install the project in editable mode once:

    python -m pip install -e .

Then configure an ACP client (including the VS Code ACP client) to start:

    {
      "command": "python",
      "args": ["-m", "bridge.acp"],
      "env": {}
    }

For the VS Code ACP Client extension, use ACP: Add Agent Configuration and
enter:

- Agent name: coding-agent
- Agent command: the absolute path to the Python interpreter used by this
  project, for example D:\Developer\Conda\python.exe
- Arguments: -m bridge.acp

The equivalent settings.json entry is:

    {
      "acp.agents": {
        "coding-agent": {
          "command": "D:\\Developer\\Conda\\python.exe",
          "args": ["-m", "bridge.acp"],
          "env": {
            "CODING_AGENT_PERMISSION_MODE": "bypass_permissions"
          }
        }
      }
    }

Install the project with python -m pip install -e D:\NJU\codeagent before
using this form. If editable installation is not desired, add
"PYTHONPATH": "D:\\NJU\\codeagent\\src" to the agent's env object instead.
The provider environment variables can be placed in the same object:
CODING_AGENT_API_KEY, CODING_AGENT_BASE_URL, and CODING_AGENT_MODEL.
CODING_AGENT_PERMISSION_MODE accepts default, accept_edits, or
bypass_permissions; the last one suppresses normal write/shell approval
prompts. Protected .agent/.git paths and workspace-wide destructive commands
remain blocked.
Do not pass --cwd; the VS Code client supplies the workspace directory in
session/new.

The process must be started with its standard output connected directly to the
ACP client. Bridge diagnostics are written to standard error only. The model
provider uses the same CODING_AGENT_API_KEY, CODING_AGENT_BASE_URL, and
CODING_AGENT_MODEL environment variables as the CLI.

## Supported methods

- initialize
- session/new with a local cwd
- session/prompt with text content
- session/cancel notification
- session/update notifications for assistant text and tool calls
- session/request_permission requests from the bridge to the client

Images, audio, embedded context, MCP servers, session loading, and model/mode
configuration are intentionally advertised as unsupported in this first
bridge. A process-wide run gate allows at most one prompt to execute at once;
additional prompt requests receive a JSON-RPC error until the active prompt
finishes or is cancelled.
