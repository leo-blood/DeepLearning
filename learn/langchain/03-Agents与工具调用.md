# 课件 03｜Agents 与工具调用

---

## 课程目标

学完本课件后，你能够：

1. 理解 Agent 的推理循环（ReAct、Thought/Action/Observation）
2. 使用 `@tool` 装饰器和 `StructuredTool` 定义自定义工具
3. 理解 Function Calling 与 ReAct 的区别和适用场景
4. 配置 `AgentExecutor` 的超时、重试、错误处理
5. 构建多工具协作的实用 Agent

---

## 一、Agent 的核心思想

Chain 是固定的处理流程，Agent 是**能自主决策下一步做什么**的系统。

```
┌──────────────────────────────────────────────────────┐
│               ReAct 推理循环                           │
│                                                      │
│  用户问题：北京明天天气如何？如果下雨，推荐室内活动       │
│                                                      │
│  Thought：我需要先查天气，再根据结果决定是否推荐室内活动  │
│  Action：weather_tool(location="北京", date="明天")   │
│  Observation：明天北京有雨，气温12°C                   │
│                                                      │
│  Thought：已知明天下雨，现在需要推荐室内活动             │
│  Action：search_tool(query="北京室内活动推荐")          │
│  Observation：[博物馆、电影院、室内攀岩...]             │
│                                                      │
│  Thought：信息足够了，可以生成最终回答                  │
│  Final Answer：明天北京有雨，推荐...                   │
└──────────────────────────────────────────────────────┘
```

---

## 二、定义工具（Tools）

### 2.1 `@tool` 装饰器（最简写法）

```python
from langchain_core.tools import tool

@tool
def get_weather(location: str, date: str = "today") -> str:
    """获取指定城市的天气信息。
    
    Args:
        location: 城市名称，如"北京"、"上海"
        date: 日期，"today"表示今天，"tomorrow"表示明天
    """
    # 实际接入天气 API
    return f"{location} {date}：晴，25°C，湿度60%"

# 工具的元信息（LLM 依赖这些来决定何时调用）
print(get_weather.name)         # "get_weather"
print(get_weather.description)  # 函数 docstring
print(get_weather.args_schema)  # Pydantic schema，自动生成
```

**关键**：docstring 就是 LLM 理解工具用途的说明，写清楚很重要。

### 2.2 StructuredTool（复杂参数）

```python
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

class SearchInput(BaseModel):
    query: str = Field(description="搜索关键词")
    max_results: int = Field(default=5, description="返回结果数量，1-10", ge=1, le=10)
    language: str = Field(default="zh", description="语言代码，zh/en")

def search_web(query: str, max_results: int = 5, language: str = "zh") -> str:
    """在互联网上搜索信息"""
    # 实际接入搜索 API
    return f"搜索 '{query}' 的结果：[结果1, 结果2, ...]"

search_tool = StructuredTool.from_function(
    func=search_web,
    name="web_search",
    description="搜索互联网获取实时信息。当需要最新数据、新闻或不在知识库中的信息时使用。",
    args_schema=SearchInput,
    return_direct=False,   # True 表示工具返回值直接作为最终答案
)
```

### 2.3 异步工具

```python
@tool
async def fetch_user_data(user_id: str) -> str:
    """从数据库获取用户信息"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"/api/users/{user_id}") as resp:
            data = await resp.json()
            return str(data)
```

---

## 三、创建 Agent

### 3.1 OpenAI Tools Agent（Function Calling，推荐）

利用 OpenAI 的 Function Calling 能力，LLM 直接输出结构化的工具调用请求。

```python
from langchain_openai import ChatOpenAI
from langchain import hub
from langchain.agents import create_tool_calling_agent, AgentExecutor

llm = ChatOpenAI(model="gpt-4o", temperature=0)

tools = [get_weather, search_tool]

# 从 hub 拉取官方 prompt 模板
prompt = hub.pull("hwchase17/openai-tools-agent")

# 创建 agent（注意：这只是 Runnable，不是 AgentExecutor）
agent = create_tool_calling_agent(llm, tools, prompt)

# AgentExecutor 负责循环调用直到完成
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,          # 打印每步推理，调试必开
    max_iterations=10,     # 防止无限循环
    handle_parsing_errors=True,  # 解析失败时重试而非崩溃
)

result = agent_executor.invoke({
    "input": "北京明天天气如何？如果下雨推荐什么室内活动？"
})
print(result["output"])
```

### 3.2 ReAct Agent

适用于不支持 Function Calling 的模型。

```python
from langchain.agents import create_react_agent

# ReAct 需要特定的 prompt 格式
prompt = hub.pull("hwchase17/react")

agent = create_react_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
```

### 3.3 Function Calling vs ReAct 对比

| 特性 | Function Calling | ReAct |
|------|-----------------|-------|
| 适用模型 | OpenAI / Anthropic | 任意模型 |
| 工具调用格式 | 结构化 JSON | 文本解析 |
| 稳定性 | ★★★★★ | ★★★☆☆ |
| 并行工具调用 | ✅ 支持 | ❌ 不支持 |
| 推荐场景 | 生产环境 | 兼容老模型 |

---

## 四、带记忆的 Agent

```python
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory

store = {}

def get_session_history(session_id: str):
    if session_id not in store:
        store[session_id] = ChatMessageHistory()
    return store[session_id]

# prompt 需要包含 chat_history 占位符
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个全能助手，可以调用工具解决问题。"),
    MessagesPlaceholder("chat_history"),   # 历史对话
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),  # Agent 的推理过程
])

agent = create_tool_calling_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools)

agent_with_history = RunnableWithMessageHistory(
    agent_executor,
    get_session_history,
    input_messages_key="input",
    history_messages_key="chat_history",
)

config = {"configurable": {"session_id": "user_001"}}
agent_with_history.invoke({"input": "帮我查一下北京天气"}, config=config)
agent_with_history.invoke({"input": "如果下雨我该怎么办？"}, config=config)
# 第二轮自动知道是在问北京下雨的情况
```

---

## 五、工具错误处理

### 5.1 工具内部捕获异常

```python
@tool
def query_database(sql: str) -> str:
    """执行 SQL 查询并返回结果"""
    try:
        result = db.execute(sql)
        return str(result)
    except Exception as e:
        # 告知 LLM 出错了，让它重新思考
        return f"查询失败：{str(e)}。请检查 SQL 语法是否正确。"
```

### 5.2 AgentExecutor 级别的错误处理

```python
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    handle_parsing_errors=True,      # 解析错误时让 LLM 重试
    max_iterations=10,                # 最大循环次数
    max_execution_time=30,            # 最大执行时间（秒）
    return_intermediate_steps=True,  # 返回每步的工具调用记录
)

result = agent_executor.invoke({"input": "..."})
for step in result["intermediate_steps"]:
    tool_call, tool_output = step
    print(f"调用工具：{tool_call.tool}")
    print(f"工具输出：{tool_output}")
```

---

## 六、实用工具集

### 6.1 Tavily Search（联网搜索）

```python
from langchain_community.tools.tavily_search import TavilySearchResults

search = TavilySearchResults(
    max_results=5,
    include_answer=True,       # 包含 AI 生成的摘要答案
    include_raw_content=False, # 不包含完整网页内容（节省 token）
)
```

### 6.2 Python REPL（代码执行）

```python
from langchain_experimental.tools import PythonREPLTool

python_repl = PythonREPLTool()
# 注意：⚠️ 生产环境需要在沙箱中运行！
```

### 6.3 文件读写工具

```python
from langchain_community.tools import ReadFileTool, WriteFileTool, ListDirectoryTool
from langchain_community.agent_toolkits import FileManagementToolkit

toolkit = FileManagementToolkit(
    root_dir="/tmp/workspace",    # 限制文件操作范围
    selected_tools=["read_file", "write_file", "list_directory"],
)
tools = toolkit.get_tools()
```

---

## 七、自定义 Agent 逻辑

有时候需要完全控制 Agent 的决策流程：

```python
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.runnables import RunnableLambda

def custom_agent_logic(input_dict: dict):
    """
    自定义 Agent：先强制调用一次权限检查工具，再正常推理
    """
    user_input = input_dict["input"]
    
    # 强制第一步：权限检查
    permission = check_permission_tool.invoke({"user_id": input_dict["user_id"]})
    if "denied" in permission:
        return AgentFinish(
            return_values={"output": "您没有权限执行此操作"},
            log="Permission denied",
        )
    
    # 后续正常走 LLM 决策
    return agent.invoke(input_dict)
```

---

## 本课小结

| 知识点 | 要点 |
|--------|------|
| Agent 本质 | LLM + 工具 + 推理循环，自主决定下一步 |
| 工具定义 | `@tool` + 清晰 docstring；复杂参数用 Pydantic schema |
| 推荐 Agent 类型 | `create_tool_calling_agent`（Function Calling）|
| 防护措施 | `max_iterations`、`max_execution_time`、`handle_parsing_errors` |
| 工具错误处理 | 工具内捕获异常并返回有意义的错误描述 |
