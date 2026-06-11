# 沙箱课件 02｜Docker 沙箱与 LangChain 集成

---

## 课程目标

学完本课件后，你能够：

1. 理解 Docker 容器作为沙箱的隔离机制
2. 构建专用的 Python 执行沙箱镜像
3. 用 Docker SDK 从 LangChain Agent 动态启动沙箱容器
4. 实现容器的资源限制、网络隔离和文件系统隔离
5. 管理容器生命周期，防止资源泄漏

---

## 一、为什么 subprocess 不够？

课件 01 的 subprocess 沙箱存在根本性局限：

```
subprocess 沙箱的局限：

1. 文件系统隔离不完整
   - 子进程仍然可以读取大部分文件系统
   - chroot 可以缓解，但配置复杂且有逃逸风险

2. 网络隔离困难
   - 需要配置复杂的 iptables 规则
   - 容易遗漏

3. 镜像一致性差
   - 依赖宿主机已安装的 Python 包
   - 不同机器上行为可能不同

4. 权限提升风险
   - 若 Python 进程有漏洞，可能提升权限
```

Docker 容器提供了**操作系统级别**的隔离：

```
Docker 容器隔离机制：

Linux Namespace（命名空间）
  - pid：独立进程树，容器内看不到宿主机进程
  - net：独立网络栈，可完全断网
  - mnt：独立文件系统挂载
  - user：独立用户 ID 映射

cgroups（控制组）
  - CPU、内存、磁盘 I/O 的硬性限制
  - 超出限制直接 OOM Kill

Seccomp（系统调用过滤）
  - Docker 默认已启用 seccomp profile
  - 禁止约 44 个高危系统调用
```

---

## 二、构建沙箱镜像

### 2.1 Dockerfile

```dockerfile
# sandbox/Dockerfile
FROM python:3.11-slim

# 安全基础：不以 root 运行
RUN useradd -m -u 1000 sandbox
WORKDIR /workspace
RUN chown sandbox:sandbox /workspace

# 只安装白名单内的包（不包含 os 操作、网络请求等高权限库）
RUN pip install --no-cache-dir \
    numpy==1.26.0 \
    pandas==2.1.0 \
    matplotlib==3.8.0 \
    scipy==1.11.0 \
    scikit-learn==1.3.0 \
    sympy==1.12 \
    pillow==10.0.0

# 切换到非 root 用户
USER sandbox

# 明确设置：不允许写入 /workspace 以外的目录（通过镜像层只读实现）
# 实际隔离在 docker run 时通过 --read-only 实现

CMD ["python"]
```

```bash
# 构建镜像
docker build -t langchain-sandbox:latest ./sandbox/

# 测试镜像
docker run --rm langchain-sandbox:latest python -c "import pandas; print(pandas.__version__)"
```

### 2.2 安全 Run 参数

```bash
# 完整的安全启动命令（用于理解参数，代码中用 Docker SDK）
docker run \
  --rm \                          # 容器退出后自动删除
  --network none \                # 完全禁用网络
  --read-only \                   # 根文件系统只读
  --tmpfs /workspace:size=50m \   # 工作目录可写，限制 50MB
  --memory 128m \                 # 内存上限 128MB
  --memory-swap 128m \            # 禁用 swap（swap=内存上限）
  --cpus 0.5 \                    # 最多使用 0.5 个 CPU 核
  --pids-limit 50 \               # 最多 50 个进程（防 fork bomb）
  --cap-drop ALL \                # 删除所有 Linux 能力
  --security-opt no-new-privileges \  # 禁止权限提升
  --security-opt seccomp=./seccomp.json \  # 自定义 seccomp
  --user 1000:1000 \              # 非 root 用户
  langchain-sandbox:latest \
  python /workspace/script.py
```

---

## 三、LangChain 集成：Docker 执行器

```python
# docker_sandbox.py
import docker
import tarfile
import io
import tempfile
import os
from typing import Optional
from langchain_core.tools import tool

client = docker.from_env()

SANDBOX_IMAGE = "langchain-sandbox:latest"

# 沙箱资源配置
CONTAINER_CONFIG = {
    "network_mode": "none",          # 禁用网络
    "mem_limit": "128m",             # 内存限制
    "memswap_limit": "128m",         # 禁用 swap
    "cpu_period": 100000,
    "cpu_quota": 50000,              # 50% CPU
    "pids_limit": 50,                # 进程数限制
    "read_only": True,               # 根文件系统只读
    "tmpfs": {"/workspace": "size=50m,mode=1777"},  # 可写工作目录
    "cap_drop": ["ALL"],             # 删除所有能力
    "security_opt": ["no-new-privileges"],
    "user": "1000:1000",
    "working_dir": "/workspace",
}


def copy_file_to_container(container, content: str, filename: str):
    """向容器内写入文件（通过 tar stream）"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        encoded = content.encode("utf-8")
        info = tarfile.TarInfo(name=filename)
        info.size = len(encoded)
        tar.addfile(info, io.BytesIO(encoded))
    buf.seek(0)
    container.put_archive("/workspace", buf)


def run_code_in_docker(code: str, timeout: int = 15) -> dict:
    """
    在 Docker 容器中执行代码，返回结果。
    每次调用创建新容器，执行完毕后销毁。
    """
    container = None
    try:
        # 1. 创建容器（不启动）
        container = client.containers.create(
            image=SANDBOX_IMAGE,
            command=["python", "/workspace/script.py"],
            **CONTAINER_CONFIG,
        )
        
        # 2. 将代码写入容器
        copy_file_to_container(container, code, "script.py")
        
        # 3. 启动容器
        container.start()
        
        # 4. 等待完成（带超时）
        result = container.wait(timeout=timeout)
        exit_code = result["StatusCode"]
        
        # 5. 获取输出
        logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        
        # 分离 stdout 和 stderr（需要分别获取）
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
        
        return {
            "exit_code": exit_code,
            "stdout": stdout[:5000],
            "stderr": stderr[:2000],
            "success": exit_code == 0,
        }
    
    except Exception as e:
        error_msg = str(e)
        if "timed out" in error_msg.lower():
            return {"exit_code": -1, "stdout": "", "stderr": "执行超时", "success": False}
        return {"exit_code": -1, "stdout": "", "stderr": f"沙箱错误：{error_msg}", "success": False}
    
    finally:
        # 确保容器被删除，防止资源泄漏
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass


@tool
def docker_python_executor(code: str) -> str:
    """
    在 Docker 隔离沙箱中执行 Python 代码。
    
    完全隔离：无网络访问、只读文件系统、内存限制 128MB。
    可用库：numpy, pandas, matplotlib, scipy, scikit-learn, sympy
    生成的图片保存在 /workspace/ 目录中。
    
    Args:
        code: 要执行的 Python 代码
    """
    result = run_code_in_docker(code)
    
    if result["success"]:
        output = result["stdout"] or "（代码执行完成，无输出）"
        return f"✅ 执行成功：\n{output}"
    else:
        # 区分超时和错误
        if "超时" in result["stderr"]:
            return f"⏰ 执行超时（超过15秒）"
        error = result["stderr"] or f"退出码 {result['exit_code']}"
        return f"❌ 执行失败：\n{error}"
```

---

## 四、读取沙箱生成的文件

Agent 经常需要生成图表或数据文件，需要从容器中取回。

```python
def run_code_and_fetch_files(
    code: str,
    fetch_patterns: list[str] = ["*.png", "*.csv", "*.json"],
    output_dir: str = "/tmp/agent_output",
) -> dict:
    """执行代码并取回生成的文件"""
    container = None
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        container = client.containers.create(
            image=SANDBOX_IMAGE,
            command=["python", "/workspace/script.py"],
            **CONTAINER_CONFIG,
        )
        
        copy_file_to_container(container, code, "script.py")
        container.start()
        container.wait(timeout=30)
        
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
        
        # 获取容器内生成的文件
        fetched_files = []
        try:
            # 获取工作目录的文件列表
            exec_result = container.exec_run("find /workspace -maxdepth 1 -type f")
            file_list = exec_result.output.decode().strip().split("\n")
            
            for filepath in file_list:
                filename = os.path.basename(filepath)
                if not filename or filename == "script.py":
                    continue
                
                # 从容器中取出文件
                stream, _ = container.get_archive(filepath)
                buf = io.BytesIO()
                for chunk in stream:
                    buf.write(chunk)
                buf.seek(0)
                
                with tarfile.open(fileobj=buf) as tar:
                    member = tar.getmembers()[0]
                    f = tar.extractfile(member)
                    if f:
                        local_path = os.path.join(output_dir, filename)
                        with open(local_path, "wb") as out:
                            out.write(f.read())
                        fetched_files.append(local_path)
        except Exception as e:
            stderr += f"\n[文件获取失败：{e}]"
        
        return {
            "stdout": stdout,
            "stderr": stderr,
            "files": fetched_files,
        }
    
    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass


@tool
def docker_python_with_files(code: str) -> str:
    """
    在 Docker 沙箱中执行代码，并取回生成的文件（图表/CSV/JSON）。
    
    Args:
        code: 要执行的 Python 代码（matplotlib 图表会被自动保存）
    """
    # 注入自动保存图表的代码
    save_patch = """
import matplotlib
matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as _plt_orig
_plt_orig_show = _plt_orig.show
_counter = [0]

def _patched_show(*args, **kwargs):
    _counter[0] += 1
    _plt_orig.savefig(f'/workspace/figure_{_counter[0]}.png', dpi=150, bbox_inches='tight')
    _plt_orig.clf()

_plt_orig.show = _patched_show
"""
    
    full_code = save_patch + "\n" + code
    result = run_code_and_fetch_files(full_code)
    
    lines = []
    if result["stdout"]:
        lines.append(f"输出：\n{result['stdout']}")
    if result["files"]:
        lines.append(f"生成文件：{', '.join(result['files'])}")
    if result["stderr"] and "Error" in result["stderr"]:
        lines.append(f"错误：\n{result['stderr'][:500]}")
    
    return "\n".join(lines) if lines else "代码执行完成"
```

---

## 五、容器池（性能优化）

每次都创建新容器会有约 1-2 秒的启动延迟。生产环境可以用容器池复用。

```python
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class PooledContainer:
    container: object
    created_at: datetime
    in_use: bool = False


class ContainerPool:
    """预创建一批沙箱容器，按需分配，用完归还"""
    
    def __init__(self, pool_size: int = 5, max_age_minutes: int = 10):
        self.pool_size = pool_size
        self.max_age = timedelta(minutes=max_age_minutes)
        self._pool: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._warm_up()
    
    def _create_container(self) -> PooledContainer:
        """创建一个待机中的容器（不启动）"""
        container = client.containers.create(
            image=SANDBOX_IMAGE,
            command=["sleep", "infinity"],  # 待机
            **CONTAINER_CONFIG,
        )
        container.start()
        return PooledContainer(container=container, created_at=datetime.now())
    
    def _warm_up(self):
        """预热：提前创建容器"""
        for _ in range(self.pool_size):
            try:
                self._pool.put(self._create_container())
            except Exception as e:
                print(f"容器池预热失败：{e}")
    
    def acquire(self, timeout: int = 5) -> PooledContainer:
        """从池中取出一个容器"""
        try:
            item = self._pool.get(timeout=timeout)
            # 容器过期则重建
            if datetime.now() - item.created_at > self.max_age:
                item.container.remove(force=True)
                item = self._create_container()
            item.in_use = True
            return item
        except queue.Empty:
            # 池满了，临时创建一个
            return self._create_container()
    
    def release(self, item: PooledContainer):
        """归还容器（重置状态）"""
        try:
            # 清理工作目录
            item.container.exec_run("rm -rf /workspace/*")
            item.in_use = False
            self._pool.put(item)
        except Exception:
            # 如果容器状态异常，丢弃并补充新的
            try:
                item.container.remove(force=True)
            except Exception:
                pass
            self._pool.put(self._create_container())
    
    def shutdown(self):
        """关闭容器池，清理所有容器"""
        while not self._pool.empty():
            item = self._pool.get_nowait()
            item.container.remove(force=True)


# 全局容器池
pool = ContainerPool(pool_size=3)
```

---

## 六、完整集成示例

```python
from langchain_openai import ChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

llm = ChatOpenAI(model="gpt-4o", temperature=0)

tools = [docker_python_executor, docker_python_with_files]

prompt = ChatPromptTemplate.from_messages([
    ("system", """你是一个数据分析助手。
执行代码时使用 docker_python_with_files 工具（支持生成图表）。
代码中如需显示图表，使用 plt.show() 即可（系统会自动保存）。"""),
    MessagesPlaceholder("agent_scratchpad"),
    ("human", "{input}"),
])

agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

result = executor.invoke({
    "input": "生成 sin(x) 和 cos(x) 的对比图，x 范围 0 到 4π"
})
print(result["output"])
```

---

## 本课小结

| 知识点 | 要点 |
|--------|------|
| Docker 隔离机制 | Namespace（进程/网络/文件）+ cgroups（资源）|
| 关键安全参数 | `--network none`, `--read-only`, `--cap-drop ALL` |
| 代码注入方式 | `put_archive()` 传 tar stream，避免命令注入 |
| 文件回传 | `get_archive()` 取回生成的图片/数据文件 |
| 性能优化 | ContainerPool 预创建容器，消除启动延迟 |
| 资源防护 | `mem_limit`, `cpu_quota`, `pids_limit` 硬性限制 |
