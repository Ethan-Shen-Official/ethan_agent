# Compact 机制

本文描述当前 coding agent 的上下文压缩实现，以及 P1 阶段加入的三项轻量优化：

- `ActivePathSnapshot`：一次构建活动分支，同时提供普通消息和 Compact 记录视图。
- `TokenLedger`：使用前缀和增量维护消息 token 估算。
- 单次扫描 `find_cut_point()`：在一次反向遍历中选择安全截断点。

## 1. 设计目标

Compact 只压缩“模型请求上下文”，不删除 Session 中的原始消息。Session Tree 保存完整历史和 Compact 检查点；Context Builder 在下一次模型请求前生成一个较短的投影视图。

```text
完整 Session Tree
    |
    |  原始消息仍然保留
    v
Compact metadata + active branch
    |
    |  请求前投影
    v
模型上下文 = summary + 保留的最近消息 + 当前用户输入
```

核心 Loop 不负责持久化和摘要，只负责正常的五阶段循环：

```text
prepare_context
    -> stream_model
    -> finalize_assistant
    -> execute_tools
    -> commit_turn_and_decide
```

Compact 由 Harness/runtime 在一次 prompt 完成后处理，因此不改变 Provider 或 Loop 契约。

## 2. 记录模型

普通消息节点和 Compact 节点都存储为 `SessionRecord`，共享树结构字段：

```text
version
session_id
message_id
parent_id
operation_id
```

记录类型由 `RecordType` 限定：

```python
RecordType = Literal["message", "compaction"]
```

```text
SessionRecord
├── record_type = "message"
│   └── message = Message(...)
│
└── record_type = "compaction"
    └── message = None
        metadata = {
            "summary": ...,
            "tokens_before": ...,
            "summarized_count": ...,
            "kept_count": ...,
            "details": ...
        }
```

`SessionRecord.__post_init__()` 会保证：

- `message` 记录必须有 `message`；
- `compaction` 记录不能有 `message`；
- 未知的 `record_type` 被拒绝。

## 3. Compact 触发

### 手动触发

```text
/compact
```

CLI 调用：

```python
harness.compact(force=True)
```

手动 Compact 会跳过上下文阈值判断，但仍要求存在可压缩的完整历史。

### 自动触发

普通 prompt 完成后，Harness 执行：

```text
prompt completed
    -> _auto_compact()
    -> _projected_token_count()
    -> should_compact()
    -> compact(force=False)
```

默认配置：

```text
context_window    = 128000
reserve_tokens    = 4096
keep_recent_tokens = 16000
```

触发条件为：

```text
projected_tokens > context_window - reserve_tokens
```

自动 Compact 目前只在 `completed` 后触发。Provider 错误、取消或达到最大轮数时不会自动压缩。

## 4. 活动路径快照

### 为什么需要快照

Session Store 的活动分支由 `.head` 和 `parent_id` 决定。旧实现中：

```text
read()
    -> current_path()

compactions()
    -> current_path()
```

同一个恢复流程会重复回溯树路径。

### `ActivePathSnapshot`

现在 `JsonlSessionStore` 缓存一次活动路径，并派生出所有读取视图：

```python
ActivePathSnapshot(
    path=...,              # 活动分支的全部节点
    messages=...,          # 过滤后的普通 Message
    compactions=...,       # 活动分支上的 Compact 节点
    latest_compaction=..., # 最新 Compact 节点
)
```

```text
SessionTree.current_path()
             |
             v
    +-----------------------+
    | ActivePathSnapshot    |
    +-----------------------+
       |          |       |
       v          v       v
     path     messages  compactions
                              |
                              v
                    latest_compaction
```

以下操作会使快照失效并在下次读取时重建：

```text
append(message)
append_compaction(...)
checkout(...)
rollback(...)
```

因此同一活动叶节点下的连续 `read()`、`compactions()` 和 Harness 恢复逻辑只需要一次树路径构建。

## 5. 如何选择压缩范围

设当前普通消息为：

```text
m1 -> m2 -> m3 -> m4 -> m5 -> m6 -> m7
```

如果之前已经压缩了前两条：

```text
summarized_count = 2
```

本次只在未压缩部分寻找新的范围：

```text
working_messages = [m3, m4, m5, m6, m7]
```

### 合法截断点

截断点只能位于：

```text
user
assistant
```

不能从 `tool` 结果开始保留，以免模型看到没有对应 ToolCall 的工具结果。

```text
m3 user              可作为截断点
m4 assistant         可作为截断点
m5 tool result       不可作为截断点
m6 assistant         可作为截断点
```

### 单次反向扫描

`find_cut_point()` 从最新消息向前累加估算 token，同时记录安全边界：

```text
从后向前：

index 6   m7   累计  ...
index 5   m6   累计  ...  合法边界
index 4   m5   累计  ...  工具结果，跳过
index 3   m4   累计  ...  合法边界
```

当累计大小达到 `keep_recent_tokens` 时，选择满足以下条件的边界：

```text
边界位于当前扫描位置之后
边界不是 tool 结果
保留区尽量接近 keep_recent_tokens
```

如果整个列表都没有达到预算，则使用最早的合法边界；如果不存在任何合法边界，则返回 `None`。

该实现与原先的结果保持一致，但不再先创建完整候选点列表，再进行第二次查找。

### 被压缩和保留的消息

```text
working_messages = [m3, m4, m5, m6, m7]
cut = 2

old_messages = [m3, m4]
kept_messages = [m5, m6, m7]

absolute_cut = summarized_count + cut
```

Compact 的摘要输入只包含 `old_messages`。

## 6. 增量 token 估算

当前估算规则仍然是轻量的字符数除以 4：

```python
estimate_tokens(message) = ceil(message_characters / 4)
```

`TokenLedger` 使用前缀和保存每条消息的累计估算：

```text
messages:       m1     m2     m3     m4
message tokens:  10     5      8      7
prefix:         0     10     15     23     30
```

于是任意连续区间可以 O(1) 得到：

```text
tokens(m3..m4) = prefix[4] - prefix[2]
                = 30 - 15
                = 15
```

消息追加时只需：

```python
ledger.append(message)
```

分支切换或恢复时重新建立一次 ledger：

```python
ledger.reset(active_messages)
```

当前投影上下文 token 估算为：

```text
summary token 数量
    + prefix[message_count] - prefix[summarized_count]
```

这样自动 Compact 不再每次重新遍历整个投影消息列表。

## 7. Compact 节点的保存

假设 Compact 前的树为：

```text
m1 -> m2 -> m3 -> m4 -> m5 -> m6
```

Compact 压缩 `m1..m3`，保留 `m4..m6`。Compact 节点会追加在当前叶节点之后：

```text
m1 -> m2 -> m3 -> m4 -> m5 -> m6 -> c1
```

注意：`c1` 不是插入在 `m3` 和 `m4` 之间，而是一个位于历史末端的检查点。

`c1` 保存：

```text
c1.parent_id = m6
c1.metadata.summary = summary(m1,m2,m3)
c1.metadata.summarized_count = 3
c1.metadata.kept_count = 3
```

原始消息仍然存在。后续新消息会继续挂在 `c1` 后面：

```text
m1 -> m2 -> m3 -> m4 -> m5 -> m6 -> c1 -> m7 -> m8
```

## 8. 退出后的恢复流程

恢复过程如下：

```text
启动
  |
  | --continue 或 --session-file
  v
选择 session.jsonl
  |
  v
读取全部 JSONL 记录
  |
  v
解析 RecordType
  |
  v
重建 SessionTree
  |
  v
读取 .head，得到活动叶节点
  |
  v
构建 ActivePathSnapshot
  |
  +--> messages -> LoopState.messages
  |
  +--> latest_compaction -> ContextBuilder.summary
```

`state.messages` 只包含普通消息：

```text
[m1, m2, m3, m4, m5, m6, m7, m8]
```

Compact 节点 `c1` 不会进入 `state.messages`，但它的 summary 和 `summarized_count` 会恢复到 `CompactionContextBuilder`。

下一次请求前：

```text
state.messages
    [m1, m2, m3, m4, m5, m6, m7, m8]

summary = summary(m1,m2,m3)
summarized_count = 3

ModelRequest.messages
    [summary(m1,m2,m3), m4, m5, m6, m7, m8, new_user_message]
```

Compact 摘要是请求时生成的合成 `user` 消息，不会重复追加到 Session JSONL。

## 9. checkout 到 Compact 节点

Compact 节点拥有普通的 `message_id`，因此可以作为 checkout 目标：

```text
/checkout <compact-node-id>
```

假设：

```text
m1 -> m2 -> m3 -> m4 -> m5 -> m6 -> c1 -> m7 -> m8
```

checkout 到 `c1` 后：

```text
活动路径：
m1 -> m2 -> m3 -> m4 -> m5 -> m6 -> c1
```

```text
state.messages：
m1, m2, m3, m4, m5, m6
```

```text
下一次模型请求：
summary(m1,m2,m3)
+ m4, m5, m6
+ 新的 user message
```

这恢复的是“Compact 创建时的上下文视图”，而不是只保留 summary。

## 10. 当前复杂度

### 恢复

当前仍需解析整个 JSONL 文件：

```text
解析文件记录       O(R)
构建树             O(R)
首次活动路径构建   O(H)
后续快照读取       O(1) 命中缓存
```

因此渐进复杂度仍约为 `O(R + H)`，但恢复流程不再重复构建活动路径。

### Compact

```text
消息追加后的 token 更新      O(1)
投影 token 区间查询          O(1)
截断点选择                   O(N)
摘要文本序列化               O(N)
文件操作提取                 O(N)
摘要模型调用                 取决于输入和网络
```

本次优化没有引入索引、快照文件或物理历史裁剪，因此不会改变 JSONL 文件规模的长期增长特性。这些属于后续大规模 session 优化方向。

## 11. 边界和扩展点

当前实现暂不包含：

- 基于 `first_kept_message_id` 的 Compact 定位；
- turn-prefix Compact；
- 精确 tokenizer；
- 上下文溢出后的自动 Compact 重试；
- JSONL 索引或 session snapshot；
- CustomMessage 投影。

这些扩展可以在不修改核心 Loop 的前提下，继续放在 `runtime/compact.py`、`runtime/session/` 和 Harness 层实现。
