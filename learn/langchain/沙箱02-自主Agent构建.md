# 沙箱 02｜自主 Agent 构建

> **目标**：构建一个能联网搜索、执行 Python 代码、分析数据的自主 Agent，处理复杂的多步骤任务。

---

## 项目概述

```
任务示例：
用户：分析2024年中国电动车市场数据，画出主要厂商市场份额饼图，
      并预测明年增速。

Agent 自主执行：
  1. 搜索 2024 电动车市场数据
  2. 搜索各厂商销量数据
  3. 编写 Python 代码生成饼图
  4. 执行代码生成图片
  5. 基于数据进行增速预测分析
  6. 整合所有结果给出最终报告
```

---

## 项目结构

```
auto_agent/
├── tools/
│   ├── search_tool.py     # 联网搜索
│   ├── code_tool.py       # Python 代码执行
│   ├── file_tool.py       # 文件读写
│   └── __init__.py
├── agent.py               # Agent 主体
└── main.py                # 入口
```

---

## 阶段一：工具定义（tools/）

### search_tool.py

```python
from langchain_core.tools import tool
from tavily import TavilyClient
import os

tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """在互联网上搜索最新信息。适用于：实时数据、新闻、统计数据、不在知识库中的信息。
    
    Args:
        query: 搜索关键词，建议具体明确
        max_results: 返回结果数量，默认5
    """
    try:
        response = tavily.search(
            query=query,
            max_results=max_results,
            include_answer=True,        # 包含 AI 摘要
            include_raw_content=False,
            search_depth="advanced",    # 深度搜索，更全面
        )
        
        results = []
        if response.get("answer"):
            results.append(f"搜索摘要：{response['answer']}\n")
        
        for i, result in enumerate(response.get("results", []), 1):
            results.append(
                f"[{i}] {result['title']}\n"
                f"    来源：{result['url']}\n"
                f"    内容：{result['content'][:300]}..."
            )
        
        return "\n".join(results) if results else "未找到相关结果"
    
    except Exception as e:
        return f"搜索失败：{str(e)}"
```

### code_tool.py

```python
from langchain_core.tools import tool
import subprocess
import sys
import os
import tempfile
import ast

# 安全：禁止的操作
FORBIDDEN_PATTERNS = [
    "os.system", "subprocess", "eval(", "exec(",
    "__import__", "open('/etc", "open('/proc",
    "shutil.rmtree", "os.remove", "os.unlink",
]

WORKSPACE = "/tmp/agent_workspace"
os.makedirs(WORKSPACE, exist_ok=True)


def is_safe_code(code: str) -> tuple[bool, str]:
    """简单的代码安全检查"""
    for pattern in FORBIDDEN_PATTERNS:
        if pattern in code:
            return False, f"代码包含不允许的操作：{pattern}"
    
    # 尝试 AST 解析，检测语法错误
    try:
        ast.parse(code)
    except SyntaxError as e:
        return False, f"代码语法错误：{e}"
    
    return True, ""


@tool
def run_python(code: str) -> str:
    """在安全沙箱中执行 Python 代码。可以进行数据处理、图表生成、数学计算等。
    
    可用库：pandas, numpy, matplotlib, seaborn, scipy, sklearn, requests
    生成的图片保存在 /tmp/agent_workspace/ 目录下。
    
    Args:
        code: 要执行的 Python 代码
    """
    safe, msg = is_safe_code(code)
    if not safe:
        return f"❌ 代码被拒绝：{msg}"
    
    # 注入工作目录
    full_code = f"""
import os
os.chdir("{WORKSPACE}")
import warnings
warnings.filterwarnings('ignore')

{code}
"""
    
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir=WORKSPACE
    ) as f:
        f.write(full_code)
        script_path = f.name
    
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=30,       # 30秒超时
            cwd=WORKSPACE,
        )
        
        output = []
        if result.stdout:
            output.append(f"输出：\n{result.stdout}")
        if result.returncode != 0 and result.stderr:
            output.append(f"错误：\n{result.stderr[-500:]}")
        
        # 列出生成的文件
        files = [f for f in os.listdir(WORKSPACE) if f.endswith((".png", ".jpg", ".csv", ".json"))]
        if files:
            output.append(f"生成的文件：{', '.join(files)}")
        
        return "\n".join(output) if output else "代码执行完成（无输出）"
    
    except subprocess.TimeoutExpired:
        return "❌ 执行超时（超过30秒）"
    except Exception as e:
        return f"❌ 执行失败：{str(e)}"
    finally:
        os.unlink(script_path)


@tool
def read_workspace_file(filename: str) -> str:
    """读取工作目录中的文件内容（CSV、JSON、TXT 等）。
    
    Args:
        filename: 文件名（不含路径），如 "data.csv"
    """
    file_path = os.path.join(WORKSPACE, filename)
    if not os.path.exists(file_path):
        return f"文件不存在：{filename}"
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return content[:3000]  # 最多返回 3000 字符
    except Exception as e:
        return f"读取失败：{str(e)}"
```

---

## 阶段二：Agent 构建（agent.py）

```python
# agent.py
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory
from tools import web_search, run_python, read_workspace_file
from typing import Dict

SYSTEM_PROMPT = """你是一个自主执行任务的 AI Agent，擅长：
- 搜索和整合互联网信息
- 编写并执行 Python 代码进行数据分析和可视化
- 处理复杂的多步骤任务

工作原则：
1. 分解复杂任务为可执行步骤
2. 优先用搜索获取数据，再用代码处理
3. 代码中生成图表时使用 matplotlib，保存为 PNG
4. 每一步完成后简述结果，再继续下一步
5. 最后给出完整的总结报告

你有工作目录 /tmp/agent_workspace/ 可以存放中间文件。"""


def create_agent():
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0,
        streaming=True,
    )
    
    tools = [web_search, run_python, read_workspace_file]
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    
    agent = create_tool_calling_agent(llm, tools, prompt)
    
    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=20,
        max_execution_time=120,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )
    
    return executor


# 会话管理
store: Dict[str, ChatMessageHistory] = {}

def get_history(session_id: str) -> ChatMessageHistory:
    if session_id not in store:
        store[session_id] = ChatMessageHistory()
    return store[session_id]


def create_agent_with_memory():
    executor = create_agent()
    
    return RunnableWithMessageHistory(
        executor,
        get_history,
        input_messages_key="input",
        history_messages_key="chat_history",
    )
```

---

## 阶段三：带进度显示的执行（main.py）

```python
# main.py
import asyncio
from agent import create_agent_with_memory
from langchain_core.messages import AIMessageChunk

agent = create_agent_with_memory()


def run_task_with_progress(task: str, session_id: str = "default"):
    """执行任务并实时显示进度"""
    config = {"configurable": {"session_id": session_id}}
    
    print(f"\n{'='*60}")
    print(f"任务：{task}")
    print('='*60)
    
    step_count = 0
    
    for event in agent.stream({"input": task}, config=config):
        # 工具调用开始
        if "actions" in event:
            for action in event["actions"]:
                step_count += 1
                print(f"\n[步骤 {step_count}] 调用工具：{action.tool}")
                print(f"  参数：{str(action.tool_input)[:200]}")
        
        # 工具执行结果
        elif "steps" in event:
            for step in event["steps"]:
                result = str(step.observation)
                print(f"  结果：{result[:300]}{'...' if len(result) > 300 else ''}")
        
        # 最终输出
        elif "output" in event:
            print(f"\n{'='*60}")
            print("最终结果：")
            print(event["output"])
            print('='*60)
    
    print(f"\n✅ 任务完成，共执行 {step_count} 个步骤")


async def run_task_async(task: str, session_id: str = "default"):
    """异步流式执行"""
    config = {"configurable": {"session_id": session_id}}
    
    print(f"开始执行：{task}\n")
    
    async for chunk in agent.astream({"input": task}, config=config):
        if "output" in chunk:
            # 流式打印最终答案
            for char in chunk["output"]:
                print(char, end="", flush=True)
                await asyncio.sleep(0.01)


# 示例任务
DEMO_TASKS = [
    "搜索2024年全球AI芯片市场规模数据，用Python生成柱状图对比英伟达、AMD、Intel的市场份额",
    "获取最近一周比特币价格数据，计算波动率，并判断当前趋势",
    "帮我写一个Python函数实现快速排序，然后生成100个随机数测试其性能",
]

if __name__ == "__main__":
    for task in DEMO_TASKS[:1]:  # 先跑第一个
        run_task_with_progress(task, session_id="demo_001")
```

---

## 阶段四：典型执行轨迹

以任务"分析2024年电动车市场，生成饼图"为例：

```
============================================================
任务：分析2024年中国电动车市场，生成各厂商市场份额饼图
============================================================

[步骤 1] 调用工具：web_search
  参数：2024年中国电动车市场份额 各厂商销量数据
  结果：搜索摘要：2024年中国电动车市场中，比亚迪以35%...
        [1] 乘联会：2024年新能源销量排行榜...

[步骤 2] 调用工具：web_search
  参数：2024年Q4中国新能源汽车品牌市占率 最新数据
  结果：比亚迪34.8%，特斯拉8.2%，理想7.1%...

[步骤 3] 调用工具：run_python
  参数：
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.rcParams['font.sans-serif'] = ['SimHei']
    
    data = {
        '比亚迪': 34.8, '特斯拉': 8.2, '理想': 7.1,
        '蔚来': 4.3, '小鹏': 4.1, '华为系': 6.5, '其他': 35.0
    }
    ...
    plt.savefig('ev_market_share.png', dpi=150, bbox_inches='tight')
  结果：代码执行完成（无输出）
        生成的文件：ev_market_share.png

[步骤 4] 调用工具：web_search
  参数：2025年中国新能源汽车市场增速预测
  结果：...

============================================================
最终结果：
## 2024年中国电动车市场分析报告

**市场格局**
比亚迪以34.8%的市占率稳居第一...

**图表**
已生成饼图：/tmp/agent_workspace/ev_market_share.png

**2025年增速预测**
综合多方预测，2025年增速约为 25-30%...
============================================================
✅ 任务完成，共执行 4 个步骤
```

---

## 扩展：工具权限控制

生产环境中需要对 Agent 的工具调用进行权限管控：

```python
from functools import wraps
from typing import Callable

def require_permission(permission: str):
    """工具装饰器：调用前检查权限"""
    def decorator(tool_func: Callable):
        @wraps(tool_func)
        def wrapper(*args, **kwargs):
            # 从上下文获取当前用户（实际场景用 contextvars）
            user_id = kwargs.pop("_user_id", None)
            if not check_user_permission(user_id, permission):
                return f"❌ 权限不足：需要 {permission} 权限"
            return tool_func(*args, **kwargs)
        return wrapper
    return decorator

# 应用到工具
@tool
@require_permission("code_execution")
def run_python(code: str) -> str:
    """..."""
    ...
```

---

## 关键设计决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 搜索工具 | Tavily（advanced 模式） | 质量最高，支持中文，含 AI 摘要 |
| 代码执行 | subprocess + 超时 | 隔离主进程，防止崩溃和超时阻塞 |
| 安全检查 | 关键词黑名单 + AST 解析 | 双重防护，拦截危险操作 |
| 工作目录 | `/tmp/agent_workspace` | 隔离文件操作范围 |
| 迭代上限 | max_iterations=20 | 防止无限循环，平衡复杂任务需求 |
