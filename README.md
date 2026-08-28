# Coding Agent Workspace

这是自研 coding agent 的工作区，目前处于初版架构和实现准备阶段。

详细架构入口：[完整架构计划](docs/architecture-plan.md)

实现约束和 Codex 快速上下文见根目录 [AGENTS.md](AGENTS.md)。

## 目录结构

```text
.
├── src/
│   ├── core/                   # 消息、五阶段 agent loop、状态和上下文
│   ├── providers/              # 模型协议适配
│   ├── tools/                  # 文件、终端和工具执行器
│   ├── runtime/                # 权限、ExecutionEnv、会话和 Compact
│   ├── harness/                # 长生命周期运行门面
│   ├── cli/                    # 命令行入口和渲染
│   └── prompts/                # 系统提示词和可复用 prompt 模板
├── configs/                    # 本地、测试和生产配置
├── experiments/                # 可复现的评测和原型实验
├── tests/                      # 单元、集成和端到端测试
├── scripts/                    # 开发、构建、评测辅助脚本
└── docs/                       # 设计决策、架构和使用文档
```

## 开发约定

- 配置中的密钥使用环境变量或本地未跟踪文件，不要提交到版本库。
- 先为 `core/` 和 `tools/` 建立契约测试，再接入具体模型 provider。
