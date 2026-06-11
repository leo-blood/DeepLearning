# 课件 05｜LangGraph 多 Agent 系统

---

## 课程目标

学完本课件后，你能够：

1. 理解 LangGraph 的核心概念：State、Node、Edge
2. 构建带条件分支的有状态工作流
3. 实现 Supervisor（主控）+ Worker（执行者）的多 Agent 架构
4. 为 Agent 添加人工介入节点（Human-in-the-Loop）
5. 持久化图状态，实现可中断/恢复的长流程

---

## 一、为什么需要 LangGraph？

`AgentExecutor` 适合简单的工具调用循环，但有局限：

- 无法实现**分支逻辑**（根据结果走不同路径）
- 无法做到**多个专家 Agent 协作**
- 无法在执行中途**暂停等待人工确认**
- 无法可靠地持久化和恢复中断的任务

LangGraph 将 Agent 系统建模为**有向图（Graph）**，每个节点是一个处理步骤，边决定下一步走向。

---

## 二、核心概念

### 2.1 State（状态）

图的整体状态，所有节点共享并修改。

```python
from typing import TypedDict, Annotated, List
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # add_messages 是一个 reducer：新消息追加到列表，而非替换
    messages: Annotated[List[BaseMessage], add_messages]
    current_agent: str    # 当前执行的 Agent 名称
    task_complete: bool   # 任务是否完成
    error: str            # 错误信息
```

**Reducer 的重要性：**

```
初始状态：messages = [HumanMessage("查天气")]
节点A 返回：{"messages": [AIMessage("我需要调用天气工具")]}
add_messages reducer：messages = [HumanMessage("查天气"), AIMessage("...")]
# 不会覆盖，而是追加
```

### 2.2 Node（节点）

接收当前 State，返回对 State 的更新。

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o")

def agent_node(state: AgentState) -> dict:
    """LLM 推理节点"""
    response = llm.invoke(state["messages"])
    return {"messages": [response]}    # 返回更新，不是完整状态

def tool_node(state: AgentState) -> dict:
    """工具执行节点"""
    last_message = state["messages"][-1]
    # 执行 last_message 中的工具调用
    results = execute_tools(last_message.tool_calls)
    return {"messages": results}
```

### 2.3 Edge（边）

连接节点，决定执行顺序。

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(AgentState)

# 普通边：A 执行完之后总是执行 B
graph.add_edge("node_a", "node_b")

# 条件边：根据函数返回值决定下一节点
graph.add_conditional_edges(
    "agent",                           # 从 agent 节点出发
    should_continue,                   # 决策函数，返回下一节点名称
    {
        "call_tool": "tool_executor",  # 返回 "call_tool" → 去 tool_executor
        "done": END,                   # 返回 "done" → 结束
    }
)
```

---

## 三、构建基础 ReAct Agent

```python
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

# 工具定义
tools = [get_weather, search_web]
llm_with_tools = ChatOpenAI(model="gpt-4o").bind_tools(tools)

# 节点函数
def agent_node(state: AgentState) -> dict:
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}

# 构建图
graph = StateGraph(AgentState)

# 添加节点
graph.add_node("agent", agent_node)
graph.add_node("tools", ToolNode(tools))  # 内置工具执行节点

# 设置入口
graph.set_entry_point("agent")

# 添加边
graph.add_conditional_edges(
    "agent",
    tools_condition,    # 内置：有工具调用 → tools，无 → END
)
graph.add_edge("tools", "agent")   # 工具执行完回到 agent 继续推理

# 编译
app = graph.compile()

# 运行
result = app.invoke({
    "messages": [HumanMessage("北京今天天气如何？")]
})
print(result["messages"][-1].content)
```

---

## 四、多 Agent 架构：Supervisor 模式

一个 Supervisor（主控 Agent）负责分解任务、分配给专业 Worker Agent 执行。

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
import json

# ── 定义专业 Worker Agent ──────────────────────────────────

def research_agent(state: AgentState) -> dict:
    """负责信息检索和研究"""
    prompt = "你是研究专家。请搜索并整理用户问题的相关信息。"
    response = ChatOpenAI(model="gpt-4o").invoke([
        SystemMessage(prompt),
        *state["messages"]
    ])
    return {"messages": [response], "current_agent": "research"}

def code_agent(state: AgentState) -> dict:
    """负责代码生成和调试"""
    prompt = "你是编程专家。请根据需求编写或调试代码。"
    response = ChatOpenAI(model="gpt-4o").invoke([
        SystemMessage(prompt),
        *state["messages"]
    ])
    return {"messages": [response], "current_agent": "code"}

def writer_agent(state: AgentState) -> dict:
    """负责文案撰写和整理"""
    prompt = "你是写作专家。请整理信息并撰写清晰的文档。"
    response = ChatOpenAI(model="gpt-4o").invoke([
        SystemMessage(prompt),
        *state["messages"]
    ])
    return {"messages": [response], "current_agent": "writer"}

# ── Supervisor 决策 ────────────────────────────────────────

supervisor_prompt = ChatPromptTemplate.from_messages([
    ("system", """你是任务调度器。根据当前任务状态，决定下一步应该由哪个专家处理：
- research：需要搜索信息、查找资料
- code：需要编写代码、技术实现
- writer：需要整理信息、撰写文档
- FINISH：任务已完成

只返回 JSON：{{"next": "research|code|writer|FINISH"}}"""),
    ("human", "当前任务：{task}\n已完成步骤：{history}"),
])

def supervisor_node(state: AgentState) -> dict:
    # 提取对话摘要
    history = "\n".join([
        f"{m.__class__.__name__}: {m.content[:100]}"
        for m in state["messages"][-6:]  # 最近6条
    ])
    
    task = state["messages"][0].content  # 初始任务
    
    response = ChatOpenAI(model="gpt-4o").invoke(
        supervisor_prompt.format_messages(task=task, history=history)
    )
    
    decision = json.loads(response.content)
    
    if decision["next"] == "FINISH":
        return {"task_complete": True}
    
    return {"current_agent": decision["next"]}

def route_to_agent(state: AgentState) -> str:
    """根据 supervisor 决策路由"""
    if state.get("task_complete"):
        return END
    return state.get("current_agent", "research")

# ── 构建多 Agent 图 ─────────────────────────────────────────

graph = StateGraph(AgentState)

graph.add_node("supervisor", supervisor_node)
graph.add_node("research", research_agent)
graph.add_node("code", code_agent)
graph.add_node("writer", writer_agent)

graph.set_entry_point("supervisor")

# Supervisor 根据决策路由
graph.add_conditional_edges("supervisor", route_to_agent)

# 所有 Worker 完成后回到 Supervisor
graph.add_edge("research", "supervisor")
graph.add_edge("code", "supervisor")
graph.add_edge("writer", "supervisor")

app = graph.compile()
```

---

## 五、Human-in-the-Loop（人工介入）

在执行危险操作（如写数据库、发邮件）前暂停，等待人工确认。

```python
from langgraph.checkpoint.memory import MemorySaver

# 使用 checkpointer 才能暂停/恢复
checkpointer = MemorySaver()
app = graph.compile(
    checkpointer=checkpointer,
    interrupt_before=["dangerous_operation"],  # 在此节点前暂停
)

config = {"configurable": {"thread_id": "task_001"}}

# 第一次执行：到达 interrupt 点时暂停
for event in app.stream(
    {"messages": [HumanMessage("删除所有测试数据")]},
    config=config
):
    print(event)
# 输出：图已暂停在 dangerous_operation 节点前

# 获取当前状态供人工审核
state = app.get_state(config)
print("待执行操作：", state.values["messages"][-1].content)

# 人工确认后继续
human_input = input("确认执行？(yes/no): ")
if human_input == "yes":
    for event in app.stream(None, config=config):  # None 表示继续
        print(event)
```

---

## 六、状态持久化（Checkpointer）

```python
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

# SQLite 持久化（生产用 PostgreSQL）
conn = sqlite3.connect("checkpoints.db")
checkpointer = SqliteSaver(conn)

app = graph.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": "long_task_001"}}

# 执行（中途可以中断）
app.invoke({"messages": [...]}, config=config)

# 重启后恢复（相同 thread_id）
app.invoke(None, config=config)  # 从断点继续

# 查看完整历史
for state in app.get_state_history(config):
    print(state.created_at, state.values["current_agent"])
```

---

## 七、图可视化

```python
# 生成 Mermaid 格式的图结构描述
print(app.get_graph().draw_mermaid())

# 输出：
# %%{init: {'flowchart': {'curve': 'linear'}}}%%
# graph TD;
#     __start__ --> supervisor;
#     supervisor --> research;
#     supervisor --> code;
#     supervisor --> writer;
#     research --> supervisor;
#     ...
```

---

## 八、常见多 Agent 模式总结

```
模式 1：Supervisor + Workers（本课示例）
  supervisor ─► research / code / writer ─► supervisor ─► END
  适用：任务需要多个专家协作，有明确的调度逻辑

模式 2：Sequential Pipeline（流水线）
  planner ─► executor ─► reviewer ─► END
  适用：任务有固定的处理阶段，如写作（大纲→草稿→润色）

模式 3：Debate（辩论/交叉验证）
  agent_a ─► agent_b ─► agent_a ─►（轮数限制）─► judge ─► END
  适用：需要多角度验证，减少单点 LLM 错误

模式 4：Parallel（并行专家）
  dispatcher ─►┬─ expert_a ─┬─► aggregator ─► END
               ├─ expert_b ─┤
               └─ expert_c ─┘
  适用：任务可分解为独立子任务，并行加速
```

---

## 本课小结

| 概念 | 要点 |
|------|------|
| State | TypedDict + Reducer，所有节点共享 |
| Node | 接收 State，返回部分更新（不是完整 State）|
| 条件边 | 决策函数返回节点名称，实现动态路由 |
| Supervisor 模式 | 主控分配任务，Worker 专注执行 |
| Human-in-the-Loop | `interrupt_before` + `stream(None)` 继续 |
| 持久化 | `MemorySaver`（测试）/ `SqliteSaver`（生产）|
