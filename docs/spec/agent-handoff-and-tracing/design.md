# Agent Handoff & 链路追踪 — 技术设计

## 架构定位

三项能力均为 Agent 抽象层（domain/agent + infrastructure/agent）的**增量扩展**，
不引入新分层、不修改 ReAct Loop 主控流的 4 个执行入口外部契约。增量边界：

```
domain/agent/
  ├─ ports.py           ← DelegationPort 增 handoff / delegate_parallel
  ├─ value_objects.py   ← 新增 HandoffResult / DelegationRequest
  └─ exceptions.py      ← 新增 HandoffPerformed 信号异常

infrastructure/agent/
  ├─ delegation_adapter.py  ← 实现 handoff / delegate_parallel
  ├─ handoff_to_agent_tool.py     ← 新增（继承 Tool）
  ├─ delegate_parallel_tool.py    ← 新增（继承 Tool）
  ├─ react_agent_adapter.py       ← _iter_rounds 加每轮 span + handoff 短路
  └─ round_outcome.py             ← RoundOutcomeKind 增 "handoff"

application/container_config.py  ← _register_delegate_tool 追加 2 个新工具
```

## 组件设计

### 1. 领域值对象

**`DelegationRequest`**（`domain/agent/value_objects.py`）：
```python
@dataclass(frozen=True)
class DelegationRequest:
    """单条委派请求（用于 delegate_parallel）。"""
    agent_name: str
    task_goal: str
    input_data: dict[str, Any] = field(default_factory=dict)
```

**`HandoffResult`**（`domain/agent/value_objects.py`）：
```python
@dataclass(frozen=True)
class HandoffResult:
    """Handoff 结果值对象。

    与 DelegationResult 的关键差异：携带 target_agent 标识与 usage，
    用于父 Agent Loop 直接采纳为 AgentResult。
    """
    target_agent: str
    content: str
    success: bool
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
```

### 2. 信号异常

**`HandoffPerformed`**（`domain/agent/exceptions.py`）：
```python
class HandoffPerformed(Exception):
    """Handoff 控制转移成功信号。

    HandoffToAgentTool.execute 在目标 Agent 执行成功后**抛出**此异常。
    ReActAgentAdapter._execute_tool_call 捕获后写入 ToolMessage 并打标
    handoff_target，使 _iter_rounds 在后续检测到 handoff 标记并终止循环。

    使用异常做信号的理由：Tool.execute 仅能返回 str，无法在不污染字符串
    协议的情况下传递结构化控制信号；Python 标准库已有 StopIteration /
    GeneratorExit 等"以异常承载控制流"的先例，命名 HandoffPerformed
    （而非 HandoffError）以彰显"成功信号"语义。

    与 ToolExecutionError 区分：HandoffPerformed 不继承 ToolExecutionError，
    不视为工具失败；ToolMessage.metadata["error"] 不会被设置。
    """
    def __init__(self, target_agent: str, content: str, usage: dict[str, int], model: str):
        super().__init__(f"Handoff to '{target_agent}'")
        self.target_agent = target_agent
        self.content = content
        self.usage = usage
        self.model = model
```

### 3. DelegationPort 协议扩展

```python
# domain/agent/ports.py — DelegationPort
class DelegationPort(Protocol):
    async def delegate(...) -> DelegationResult: ...   # 既有，签名不变

    async def delegate_parallel(                       # 新增
        self,
        requests: list[DelegationRequest],
        delegation_depth: int = 0,
        max_delegation_depth: int = 3,
    ) -> list[DelegationResult]: ...

    async def handoff(                                 # 新增
        self,
        agent_name: str,
        context_messages: list[BaseMessage],
        delegation_depth: int = 0,
        max_delegation_depth: int = 3,
    ) -> HandoffResult: ...
```

### 4. DelegationAdapter 实现

`infrastructure/agent/delegation_adapter.py` 新增方法：

**`delegate_parallel`**：
```python
async def delegate_parallel(self, requests, delegation_depth=0, max_delegation_depth=3):
    async def _one(req: DelegationRequest) -> DelegationResult:
        try:
            return await self.delegate(
                req.agent_name, req.task_goal, req.input_data,
                delegation_depth=delegation_depth,
                max_delegation_depth=max_delegation_depth,
            )
        except AgentNotFoundError as exc:
            return DelegationResult(content=str(exc), success=False)
        except Exception as exc:
            logger.warning("并行委派单条失败 agent=%s err=%s", req.agent_name, exc)
            return DelegationResult(content=str(exc), success=False)

    return list(await asyncio.gather(*[_one(r) for r in requests]))
```
错误隔离通过逐条 `try/except + asyncio.gather(return_exceptions=False)` 实现：因为
`_one` 自身吞掉异常并返回失败 `DelegationResult`，gather 不会因单条失败而短路。

**`handoff`**：
```python
async def handoff(self, agent_name, context_messages, delegation_depth=0, max_delegation_depth=3):
    config = self._agent_registry.get(agent_name)
    if config is None:
        raise AgentNotFoundError(agent_name, self._agent_registry.list_names())

    # 把父侧消息列表 + 子 Agent 自己的 system_prompt 作为目标 Agent 的初始上下文。
    # 创建独立 ConversationContext（不复用父 context，以保证父侧消息不被子 Agent 污染）。
    sub_context = ConversationContext()
    for msg in context_messages:
        sub_context.append_message(msg)  # 浅拷贝引用即可（消息为不可变值对象）

    task = Task(
        goal="(handoff)",  # goal 不再使用，由父侧 user message 驱动
        input_data={},
        tool_names=config.tool_names,
        model=config.model,
        delegation_depth=delegation_depth,
        session_id=None,
    )
    # TaskAgentAdapter.execute 内部会再次 add_user_message(task.goal) — 我们需要避免
    # 这次额外 user message。简化做法：跳过 TaskAgentAdapter，直接通过 AgentPort.run
    # 驱动子 Agent。但 DelegationAdapter 当前只持有 TaskAgentPort。
    # 最终方案：保留 TaskAgentPort，扩展 Task 增加 skip_default_user_message 字段，
    # 或在 DelegationAdapter 直接持有 AgentPort + ModelRegistryPort 组装子 Agent。
    ...
```

**Handoff 上下文转移的两条路径权衡**：

| 路径 | 复杂度 | 隔离性 | 选定 |
|---|---|---|---|
| A) 通过 TaskAgentPort + Task 构造（要新增 Task 字段控制 user message 注入） | 中 | 高 | × |
| B) DelegationAdapter 直接持有 AgentPort + ModelRegistryPort + ContextCompactionPort | 高 | 高 | × |
| C) 父侧把消息克隆到新 ConversationContext，调用 TaskAgentPort，但通过 `Task.goal=""` + 新增 `Task.skip_initial_user_message=False` 的方式跳过追加 | 低 | 高 | ✓ |

**最终选定**：保持 DelegationAdapter 现有依赖（仅 AgentRegistryPort + TaskAgentPort）不变，
**`Task` 增加可选字段 `skip_initial_user_message: bool = False`**，`TaskAgentAdapter.execute`
检查该字段决定是否追加 `goal` 为 user message。父 Agent 在 handoff 路径设
`skip_initial_user_message=True`，并预先把消息列表克隆到 ConversationContext —
但 ConversationContext 不能直接通过构造器塞入消息列表，需要扩展 `from_messages` 工厂或
通过 `for msg in ...: ctx.append_message(msg)`。

**简化收敛**：实际实现采用更直接的方案——`DelegationAdapter.handoff` 直接构造一个内嵌
`ConversationContext`，复制父侧消息（保留 `system / user / assistant / tool` 不区分），
然后调用 `TaskAgentPort.execute(task)`。但 `TaskAgentAdapter` 当前会
`context.add_system_message + context.add_user_message`，会污染上下文。

**最简实现路径**（决策）：在 `DelegationAdapter.handoff` 中**绕过 TaskAgentPort，直接调用
AgentPort.run**。为此 `DelegationAdapter.__init__` 追加注入 `AgentPort` 与
`ModelRegistryPort`，把"组 ConversationContext + 组 AgentConfig + 调 run + 拆 AgentResult"
四步内联完成。该改动隔离在适配器内部，不影响其它 Adapter。

最终代码：
```python
async def handoff(self, agent_name, context_messages, delegation_depth=0, max_delegation_depth=3):
    if delegation_depth + 1 > max_delegation_depth:
        raise DelegationDepthExceededError(delegation_depth, max_delegation_depth, agent_name)

    config = self._agent_registry.get(agent_name)
    if config is None:
        raise AgentNotFoundError(agent_name, self._agent_registry.list_names())

    sub_context = ConversationContext()
    for msg in context_messages:
        sub_context.append_message(msg)  # 仅追加引用；所有消息都是 frozen dataclass

    model_name = config.model or self._model_registry.get_default_model()
    model_access = self._model_registry.get_adapter_for_model(model_name)
    tool_schemas = self._tool_registry.get_schemas(tool_names=config.tool_names)

    agent_config_obj = AgentConfig(
        system_prompt=config.system_prompt,
        tool_schemas=tool_schemas,
        model=model_name,
        max_rounds=self._handoff_max_rounds,  # 默认 10，与 TaskAgentAdapter 对齐
        prompt_id=config.prompt_id,
    )

    result = await self._agent.run(sub_context, agent_config_obj, model_access)
    return HandoffResult(
        target_agent=agent_name,
        content=result.content,
        success=result.status == "completed",
        usage=result.usage,
        model=result.model,
    )
```

**ConversationContext.append_message**：当前 `ConversationContext` 暴露
`add_system_message` / `add_user_message` 等专用 API，但**没有通用 `append_message(msg)`**。
我们在 `domain/chat/context.py` 增加 `append_message(msg: BaseMessage) -> int` 方法
（或暴露 `_messages.append` 的安全包装），保证 handoff 上下文克隆能复用消息引用。
这是对领域类的最小补丁，不破坏既有不变量。

### 5. HandoffToAgentTool

`infrastructure/agent/handoff_to_agent_tool.py`：
```python
class HandoffToAgentTool(Tool):
    """触发 Handoff 的工具。

    LLM 调用 handoff_to_agent 后：
    1. Tool 调用 DelegationPort.handoff(agent_name, parent_messages_snapshot)
    2. 目标 Agent 独立执行 ReAct Loop 至完成
    3. Tool 抛出 HandoffPerformed 信号异常，携带目标 Agent 最终回复内容
    4. ReActAgentAdapter._execute_tool_call 捕获异常，写入 ToolMessage 并打标
       metadata["handoff_target"]，让 _iter_rounds 终止当前 Agent Loop
    """

    def __init__(self, agent_registry, delegation, context_provider, ...):
        # context_provider: 一个无参可调用对象，运行时返回当前父 ConversationContext
        # 的消息快照。由 ReActAgentAdapter 在执行工具前注入。
        ...
```

**消息快照来源**：HandoffTool 需要拿到父 Agent 当前 `ConversationContext.get_messages()`，
但 Tool 接口是 `execute(**kwargs)`，没有传 context 的入口。**两种解法**：

| 解法 | 评估 |
|---|---|
| A) Tool.execute 增加可选 `_runtime_context` kwarg | 修改 Tool ABC，影响所有工具 |
| B) HandoffToAgentTool 持有 `context_var: ContextVar[ConversationContext]`，Adapter 在执行前 set | 隔离，仅影响该工具 |
| C) 在 ReActAgentAdapter 内部"特判"是否为 HandoffToAgentTool，特判时直接调用 DelegationPort.handoff，绕过 Tool.execute | 把 handoff 当作 Adapter 内置能力 |

**选定 B**：使用 `contextvars.ContextVar` 在 `_execute_tool_call` 进入前 `var.set(context)`，
HandoffToAgentTool 在 execute 内 `var.get()` 拿到消息快照。利用 contextvars 的 task-local 语义
天然适配并发工具调用。`ContextVar` 定义在 `infrastructure/agent/handoff_context.py`（模块级
单例）：

```python
# infrastructure/agent/handoff_context.py
from contextvars import ContextVar
_current_parent_context: ContextVar["ConversationContext | None"] = ContextVar(
    "handoff_parent_context", default=None,
)

def get_parent_context() -> "ConversationContext | None":
    return _current_parent_context.get()

def set_parent_context(ctx) -> object:  # token
    return _current_parent_context.set(ctx)

def reset_parent_context(token) -> None:
    _current_parent_context.reset(token)
```

ReActAgentAdapter 在 `_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` /
`_events_concurrent_tool_calls` 入口 `set_parent_context(context)`，return 前 `reset_parent_context(token)`。
设置一次即可，因为同一轮所有 tool calls 共享同一父 context。

### 6. DelegateParallelTool

`infrastructure/agent/delegate_parallel_tool.py`：
```python
class DelegateParallelTool(Tool):
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "requests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_name": {"type": "string"},
                            "task_goal": {"type": "string"},
                            "input_data": {"type": "object"},
                        },
                        "required": ["agent_name", "task_goal"],
                    },
                    "minItems": 1,
                    "maxItems": 8,  # 防止过度并发
                },
            },
            "required": ["requests"],
        }

    async def execute(self, **kwargs) -> str:
        raw = kwargs["requests"]
        delegation_requests = [
            DelegationRequest(
                agent_name=r["agent_name"],
                task_goal=r["task_goal"],
                input_data=r.get("input_data", {}) or {},
            )
            for r in raw
        ]
        results = await self._delegation.delegate_parallel(
            delegation_requests,
            delegation_depth=self._current_delegation_depth + 1,
            max_delegation_depth=self._max_delegation_depth,
        )
        # 聚合为可读文本
        sections = []
        for req, res in zip(delegation_requests, results):
            tag = "✓" if res.success else "✗"
            sections.append(f"[{tag}] {req.agent_name}\n{res.content}")
        return "\n\n".join(sections)
```

### 7. ReActAgentAdapter 改造

#### 7.1 模块级 tracer

```python
# react_agent_adapter.py 顶部
from opentelemetry import trace as _otel_trace
tracer = _otel_trace.get_tracer(__name__)
```

#### 7.2 `_iter_rounds` 每轮 span

```python
for round_num in range(start_round, effective_terminal + 1):
    if budget_exceeded_pending_after_tools:
        ...

    with tracer.start_as_current_span(
        "react_agent.round",
        attributes={"react.round_num": round_num},
    ) as span:
        try:
            builder_result = await self._context_builder.build(...)
            chat_request = ChatRequest(...)
            accumulator = _RoundStreamAccumulator(model=config.model or "")
            await accumulator.consume(model_access.stream(chat_request))
            response = accumulator.build_response()
            total_usage = merge_usage(total_usage, builder_result.usage, response.usage)
            last_response = response

            # span 属性写入
            span.set_attribute("react.tool_call_count", len(response.tool_calls))
            span.set_attribute("react.has_tool_calls", bool(response.tool_calls))
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if k in total_usage:
                    span.set_attribute(f"gen_ai.usage.{k}", total_usage[k])
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(_otel_trace.Status(_otel_trace.StatusCode.ERROR))
            raise

        if not response.tool_calls:
            yield RoundOutcome(kind="text", ...)
            return

        ... (现有 approval / tool_calls 分支)
```

`with tracer.start_as_current_span(...)` 在 `yield` 时会**保持 span 活跃**直到生成器
被 caller 继续推进；caller 在执行工具后 `__anext__` 时回到该上下文，span 才在 `finally`
中关闭。这是 Python 上下文管理器与异步生成器的良性配合。

**注意**：`yield` 到 caller 时，caller 可能再创建子 span（如 `_dispatch_concurrent_tool_calls`
内部为每个工具调用单独埋点），这些子 span 的父 span 自然就是当前 `react_agent.round`，
形成期望的嵌套结构。

#### 7.3 Handoff 短路

`_execute_tool_call` 增加 `HandoffPerformed` 捕获分支：
```python
async def _execute_tool_call(self, context, tool_call, config) -> tuple[str, bool]:
    is_error = False
    handoff_target: str | None = None
    timeout = self._resolve_tool_timeout(tool_call.name, config)
    try:
        self._ensure_tool_authorized(tool_call, config)
        if timeout is None:
            result = await self._tool_registry.execute(tool_call)
        else:
            result = await asyncio.wait_for(self._tool_registry.execute(tool_call), timeout=timeout)
    except HandoffPerformed as signal:
        result = signal.content
        handoff_target = signal.target_agent
    except ToolPermissionDeniedError as exc:
        ...
    except asyncio.TimeoutError as exc:
        ...
    except Exception as exc:
        ...

    msg_index = context.add_tool_result(tool_name=tool_call.name, result=result, tool_call_id=tool_call.id)
    msg = context.get_messages()[msg_index]
    assert isinstance(msg, ToolMessage)
    if is_error:
        msg.metadata["error"] = True
    if handoff_target is not None:
        msg.metadata["handoff_target"] = handoff_target
    self._stamp_event(context, msg_index)
    return result, is_error
```

并在 `_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` /
`_events_concurrent_tool_calls` 三个调用入口前后管理 ContextVar：
```python
async def _dispatch_concurrent_tool_calls(self, context, tool_calls, config):
    from infrastructure.agent.handoff_context import set_parent_context, reset_parent_context
    token = set_parent_context(context)
    try:
        if len(tool_calls) == 1:
            await self._execute_tool_call(context, tool_calls[0], config)
            return
        await asyncio.gather(*(self._execute_tool_call(context, tc, config) for tc in tool_calls))
    finally:
        reset_parent_context(token)
```

#### 7.4 `_iter_rounds` 检测 handoff

在每轮 `yield RoundOutcome(kind="tool_calls", ...)` 之后，caller 执行工具回写 ToolMessage。
进入下一轮入口前，检查最近的 ToolMessage 是否带 `handoff_target` metadata：

```python
# 在 for round_num 循环顶端，检查 handoff 短路
if round_num > start_round:
    # 检查上一轮工具执行结果中是否有 handoff
    handoff_target = self._detect_handoff(context)
    if handoff_target is not None:
        # 取出最近的 ToolMessage 内容作为 final
        final_content = context.get_messages()[-1].content
        yield RoundOutcome(
            kind="handoff",
            round_num=round_num - 1,
            response=last_response,
            tool_calls=(),
            total_usage=dict(total_usage),
            handoff_target=handoff_target,
            handoff_content=final_content,
        )
        return

@staticmethod
def _detect_handoff(context: ConversationContext) -> str | None:
    """扫描最近一组 tool messages，返回 handoff_target，否则 None。"""
    messages = context.get_messages()
    # 从尾部往前扫，跳过最近一组连续 ToolMessage
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            break
        target = msg.metadata.get("handoff_target")
        if target:
            return target
    return None
```

#### 7.5 `RoundOutcome` 与 `AgentResult` 处理

`round_outcome.py` 增加：
```python
RoundOutcomeKind = Literal["text", "tool_calls", "approval", "final", "handoff"]

@dataclass(frozen=True)
class RoundOutcome:
    ...
    handoff_target: str | None = None
    handoff_content: str = ""
```

`_outcome_to_agent_result` 增加分支：
```python
if outcome.kind == "handoff":
    return AgentResult(
        content=outcome.handoff_content,
        model=outcome.response.model if outcome.response else "",
        usage=outcome.total_usage,
        latency_ms=0.0,
        terminated_reason="completed",
    )
```

`run_streaming` / `run_events` 也需在 outcome 循环增加 `kind == "handoff"` 分支：
- `run_streaming`：yield 一个 `StreamingChunk(delta_content=outcome.handoff_content,
  finished=True, usage=outcome.total_usage, metadata={"handoff_target": ...})` 后 return。
- `run_events`：yield `assistant_delta(handoff_content)` + `assistant_done(usage=...,
  metadata={"handoff_target": ...})` 后 return。

### 8. 装配（container_config.py）

`_create_delegation_adapter` 改造：现在需要额外注入 `AgentPort` / `ModelRegistryPort` /
`ToolRegistry`。但这会形成 `DelegationPort → AgentPort → ToolRegistry → DelegateToAgentTool
→ DelegationPort` 的循环。
**解决**：保持现有 `_register_delegate_tool` 延迟注册模式。`DelegationAdapter` 构造期不
解析 AgentPort/ToolRegistry，改在 `handoff` 调用时通过容器懒解析。

更简洁方案：把 `_create_delegation_adapter` 也升级为延迟解析——把 `agent_provider /
tool_registry_provider / model_registry` 通过工厂函数传入：
```python
async def _create_delegation_adapter():
    from infrastructure.agent.delegation_adapter import DelegationAdapter
    agent_registry = await container.resolve(AgentRegistryPort)
    task_agent = await container.resolve(TaskAgentPort)
    model_registry = await container.resolve(ModelRegistryPort)

    async def _agent_provider() -> AgentPort:
        return await container.resolve(AgentPort)

    async def _tool_registry_provider() -> ToolRegistry:
        return await container.resolve(ToolRegistry)

    return DelegationAdapter(
        agent_registry=agent_registry,
        task_agent=task_agent,
        model_registry=model_registry,
        agent_provider=_agent_provider,
        tool_registry_provider=_tool_registry_provider,
    )
```

`DelegationAdapter.handoff` 内部：
```python
async def handoff(self, ...):
    agent = await self._agent_provider()
    tool_registry = await self._tool_registry_provider()
    ...
```

`_register_delegate_tool` 增加注册 `HandoffToAgentTool` + `DelegateParallelTool`：
```python
async def _register_delegate_tool():
    if not agent_config.delegate_tool_enabled:
        return
    ...
    tool_registry.register(DelegateToAgentTool(...))
    tool_registry.register(HandoffToAgentTool(
        agent_registry=agent_registry,
        delegation=delegation,
        max_delegation_depth=agent_config.max_delegation_depth,
    ))
    tool_registry.register(DelegateParallelTool(
        agent_registry=agent_registry,
        delegation=delegation,
        max_delegation_depth=agent_config.max_delegation_depth,
    ))
```

## 关键决策

1. **Handoff 信号用异常而非返回值**：`Tool.execute -> str` 协议无法承载结构化控制信号；
   异常路径已被 `HandoffPerformed`（`Exception` 子类，但语义为"成功信号"）显式刻画，
   命名上以 `Performed`（已发生）替代 `Error`（出错），降低误读概率。
2. **不复用 TaskAgentPort 路径做 Handoff**：`TaskAgentAdapter` 会硬编码追加
   `add_user_message(task.goal)`，与 handoff "保留父侧消息原样转交" 语义冲突；
   选择 `DelegationAdapter.handoff` 直接调用 `AgentPort.run` 路径。
3. **ContextVar 传递父消息快照**：避免修改 Tool ABC 接口或对 HandoffTool 做 isinstance
   特判，使用标准库 `contextvars` 在 task-local 范围内传递；天然适配并发工具调用。
4. **OTel span 在 `_iter_rounds` 内 with 块包裹整轮**：`yield` 期间 span 保持活跃，
   覆盖 caller 工具执行时间，因此工具调用的子 span（如 httpx 自动埋点）自动嵌套
   到 `react_agent.round` 下，形成完整 "round → tool → http" 三层结构。
5. **OTel 禁用零开销**：依赖 OTel SDK 默认 `NoOpTracer`；不引入 `if otel_config.enabled`
   分支，保持代码简洁。
6. **并行委派错误隔离**：通过 `_one()` 内吞掉异常 + `asyncio.gather(return_exceptions=False)`
   实现，因为 gather 仅在协程**未捕获**异常抛出时短路；错误 result 对象语义上等价于
   "成功的失败信号"。
7. **ConversationContext 新增 `append_message`**：domain 类的最小补丁，仅暴露已有
   `_messages.append` 的安全包装；不破坏既有不变量（消息类型仍为 `BaseMessage` 子类）。

## 测试策略

- **单测**（`test/infrastructure/agent/`）：
  - `test_handoff_tool_unit.py` — HandoffToAgentTool 调用路径、HandoffPerformed 信号、
    深度超限错误、Agent 不存在错误。
  - `test_delegation_adapter_handoff_unit.py` — DelegationAdapter.handoff 上下文克隆、
    AgentPort.run 调用契约、HandoffResult 字段映射。
  - `test_delegation_adapter_parallel_unit.py` — delegate_parallel 顺序保持、错误隔离、
    深度校验逐条执行。
  - `test_delegate_parallel_tool_unit.py` — 工具参数 schema、聚合输出格式、空数组校验。
  - `test_react_agent_handoff_unit.py` — `_iter_rounds` 检测 handoff metadata 后短路、
    `run_streaming` / `run_events` handoff 分支。
  - `test_react_agent_otel_span_unit.py` — 使用 `InMemorySpanExporter` 断言每轮 span
    名称 / 属性 / 嵌套关系；异常路径 span 状态为 ERROR。
- **属性测试**：`test_delegation_parallel_property.py` 用 hypothesis 生成多条
  DelegationRequest 组合，验证返回顺序与输入一致、错误条目独立。
- **回归覆盖**：执行 `uv run --frozen pytest test/infrastructure/agent test/infrastructure/telemetry`
  与 `uv run --frozen pytest test/infrastructure/task test/infrastructure/chat`，
  确保不破坏既有测试。
