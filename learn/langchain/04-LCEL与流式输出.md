# 课件 04｜LCEL 与流式输出

---

## 课程目标

学完本课件后，你能够：

1. 理解 LCEL 的核心抽象 `Runnable` 接口
2. 用 `|` 管道组合任意 Runnable 组件
3. 实现流式输出（`stream` / `astream`）和异步调用
4. 用 `RunnableParallel` 并行执行多个分支
5. 添加回调（Callbacks）实现日志、监控、自定义处理

---

## 一、LCEL 是什么？

LCEL（LangChain Expression Language）是 LangChain 0.1+ 引入的**声明式组合接口**，用 `|` 运算符将组件串联成管道。

**旧写法（命令式）：**

```python
# 繁琐，各组件强耦合
chain = LLMChain(llm=llm, prompt=prompt)
result = chain.run(question="什么是RAG？")
```

**LCEL 写法（声明式）：**

```python
chain = prompt | llm | output_parser
result = chain.invoke({"question": "什么是RAG？"})
```

LCEL 的所有组件都实现了 `Runnable` 接口，因此可以无缝组合。

---

## 二、Runnable 接口

所有 LCEL 组件（Prompt、LLM、Retriever、OutputParser）都实现了同一套接口：

```python
# 四个核心方法
runnable.invoke(input)           # 同步调用，返回单个结果
runnable.batch([input1, input2]) # 批量同步，返回列表
runnable.stream(input)           # 同步流式，返回 Generator
runnable.ainvoke(input)          # 异步调用
runnable.abatch([...])           # 异步批量
runnable.astream(input)          # 异步流式
```

这意味着：**任何 Runnable 都可以用同一种方式调用，无论是 LLM 还是自定义函数。**

---

## 三、管道组合（`|` 运算符）

### 3.1 基础管道

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

prompt = ChatPromptTemplate.from_template("用一句话解释：{concept}")
llm = ChatOpenAI(model="gpt-4o")
parser = StrOutputParser()

# 组合管道：prompt → llm → parser
chain = prompt | llm | parser

result = chain.invoke({"concept": "向量数据库"})
# "向量数据库是一种专门存储高维向量并支持近似最近邻搜索的数据库系统。"
```

**数据在管道中的流转：**

```
input: {"concept": "向量数据库"}
  ↓ prompt
ChatPromptValue（格式化后的消息列表）
  ↓ llm
AIMessage（content="向量数据库是..."）
  ↓ parser
str: "向量数据库是..."
```

### 3.2 类型推断

每个组件接收上一个组件的输出作为输入，**必须类型兼容**：

```python
# ✅ 正确：StrOutputParser 接收 AIMessage，输出 str
chain = prompt | llm | StrOutputParser()

# ❌ 错误：JsonOutputParser 期望 JSON 格式输出，但 LLM 可能输出普通文本
chain = prompt | llm | JsonOutputParser()  # 运行时报错
```

---

## 四、流式输出

流式输出让用户看到 LLM 逐步生成文字，而不是等全部生成完再显示。

### 4.1 同步流式

```python
chain = prompt | llm | StrOutputParser()

# stream 返回 Generator，逐块输出
for chunk in chain.stream({"concept": "神经网络"}):
    print(chunk, end="", flush=True)
```

### 4.2 异步流式（FastAPI 场景）

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio

app = FastAPI()

@app.get("/stream")
async def stream_response(question: str):
    async def generate():
        async for chunk in chain.astream({"concept": question}):
            yield f"data: {chunk}\n\n"  # SSE 格式
    
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### 4.3 流式中间步骤（astream_events）

```python
# 可以看到每个组件的输出，不只是最终结果
async for event in chain.astream_events({"concept": "RAG"}, version="v1"):
    kind = event["event"]
    
    if kind == "on_chat_model_stream":
        # LLM 的流式 token
        print(event["data"]["chunk"].content, end="")
    
    elif kind == "on_retriever_end":
        # Retriever 完成后的文档
        print(f"\n检索到 {len(event['data']['output'])} 个文档")
```

---

## 五、RunnableParallel（并行执行）

同时执行多个分支，合并结果。

### 5.1 基础并行

```python
from langchain_core.runnables import RunnableParallel, RunnablePassthrough

parallel_chain = RunnableParallel(
    summary=ChatPromptTemplate.from_template("总结：{text}") | llm | StrOutputParser(),
    keywords=ChatPromptTemplate.from_template("提取关键词：{text}") | llm | StrOutputParser(),
    sentiment=ChatPromptTemplate.from_template("情感分析：{text}") | llm | StrOutputParser(),
)

result = parallel_chain.invoke({"text": "今天天气不错，出去散步心情很好..."})
# result = {
#   "summary": "天气好，心情愉快",
#   "keywords": "天气、散步、心情",
#   "sentiment": "积极正面"
# }
```

### 5.2 在 RAG 中使用并行

```python
from langchain_core.runnables import RunnablePassthrough

rag_chain = (
    RunnableParallel({
        "context": retriever | format_docs,
        "question": RunnablePassthrough(),    # 原样透传问题
    })
    | prompt
    | llm
    | StrOutputParser()
)

result = rag_chain.invoke("什么是分布式锁？")
```

---

## 六、RunnableLambda（包装自定义函数）

将普通 Python 函数变成 Runnable，嵌入管道。

```python
from langchain_core.runnables import RunnableLambda

def preprocess(text: str) -> str:
    """清洗用户输入"""
    return text.strip().lower()

def postprocess(response: str) -> dict:
    """解析 LLM 输出为结构化数据"""
    lines = response.strip().split("\n")
    return {"items": lines, "count": len(lines)}

chain = (
    RunnableLambda(preprocess)
    | ChatPromptTemplate.from_template("列举关于{topic}的5个要点：")
    | llm
    | StrOutputParser()
    | RunnableLambda(postprocess)
)

result = chain.invoke("  Python异步编程  ")
# {"items": ["1. async/await...", ...], "count": 5}
```

---

## 七、RunnablePassthrough 与 assign

```python
from langchain_core.runnables import RunnablePassthrough

# RunnablePassthrough：原样传递输入
chain = RunnablePassthrough() | some_runnable

# assign：在字典中添加新键，同时保留原有键
chain = (
    RunnablePassthrough.assign(
        context=lambda x: retriever.invoke(x["question"]),
        formatted_date=lambda x: datetime.now().strftime("%Y-%m-%d"),
    )
    | prompt
    | llm
    | StrOutputParser()
)

# 等价于：输入 {"question": "..."} 会被扩展为
# {"question": "...", "context": [...], "formatted_date": "2024-01-01"}
```

---

## 八、回调（Callbacks）

回调允许在 Chain 执行的各个阶段插入自定义逻辑（日志、监控、追踪）。

### 8.1 内置回调：StdOutCallbackHandler

```python
from langchain_core.callbacks import StdOutCallbackHandler

chain.invoke(
    {"question": "什么是RAG？"},
    config={"callbacks": [StdOutCallbackHandler()]}
)
```

### 8.2 自定义回调

```python
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from typing import Any

class MetricsCallbackHandler(BaseCallbackHandler):
    def __init__(self):
        self.total_tokens = 0
        self.start_time = None

    def on_llm_start(self, serialized: dict, prompts: list, **kwargs):
        import time
        self.start_time = time.time()
        print(f"[LLM开始] prompt长度：{sum(len(p) for p in prompts)} 字符")

    def on_llm_end(self, response, **kwargs):
        import time
        elapsed = time.time() - self.start_time
        tokens = response.llm_output.get("token_usage", {})
        print(f"[LLM结束] 耗时：{elapsed:.2f}s，token：{tokens}")

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs):
        print(f"[工具调用] {serialized['name']}：{input_str}")

    def on_tool_end(self, output: str, **kwargs):
        print(f"[工具结果] {output[:100]}...")

# 使用
metrics = MetricsCallbackHandler()
result = agent_executor.invoke(
    {"input": "今天北京天气如何？"},
    config={"callbacks": [metrics]}
)
```

### 8.3 LangSmith 追踪（生产监控）

```python
import os
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = "lsv2_..."
os.environ["LANGCHAIN_PROJECT"] = "my-rag-project"

# 开启后，所有 chain 调用自动上报到 LangSmith Dashboard
# 可以看到：完整链路、token 消耗、延迟、错误
```

---

## 九、配置化（Configurable）

允许在运行时动态切换模型或参数，无需重建 Chain。

```python
from langchain_core.runnables import ConfigurableField

llm = ChatOpenAI(model="gpt-4o-mini").configurable_fields(
    model_name=ConfigurableField(
        id="model",
        name="LLM Model",
        description="使用的模型名称",
    ),
    temperature=ConfigurableField(
        id="temperature",
        name="Temperature",
    ),
)

chain = prompt | llm | StrOutputParser()

# 运行时覆盖配置
result = chain.invoke(
    {"question": "解释量子计算"},
    config={"configurable": {"model": "gpt-4o", "temperature": 0.8}},
)
```

---

## 本课小结

| 组件 | 作用 |
|------|------|
| `Runnable` | 所有组件的统一接口：invoke/batch/stream |
| `|` 管道 | 组合多个 Runnable，前者输出作为后者输入 |
| `RunnableParallel` | 并行执行多个分支，合并输出 |
| `RunnablePassthrough` | 原样透传，用于将输入并入字典 |
| `RunnableLambda` | 将普通函数包装为 Runnable |
| `stream` / `astream` | 流式输出，提升用户体验 |
| `astream_events` | 监听每个组件的中间状态 |
| Callbacks | 日志/监控/追踪的插入点 |
