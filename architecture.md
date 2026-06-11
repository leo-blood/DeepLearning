# 项目架构文档

> 参考 Hermes Agent 自进化机制设计

---

## 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (Web UI)                          │
│     Next.js + Tailwind  +  SSE 流式输出  +  会话管理 UI       │
│     (对话分支 / 导出 / 分享 / 进度推送)                        │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP / SSE / WebSocket
┌────────────────────────▼────────────────────────────────────┐
│                     API Gateway 层                            │
│            FastAPI + JWT 认证 + Redis 限流                    │
│            /health 健康检查  +  OpenTelemetry 追踪            │
└──────┬──────────────────────────────┬───────────────────────┘
       │                              │
┌──────▼──────┐              ┌────────▼────────┐
│  会话管理   │              │  Orchestrator   │
│  Service    │              │     Agent       │
│  (分支/导出 │              └───────┬─────────┘
│   /分享)    │                      │ 派发子任务
└──────┬──────┘             ┌────────┼───────────┐
       │             ┌──────▼──┐ ┌───▼────┐ ┌───▼──────────────┐
       │             │  Code   │ │  Tool  │ │    RAG Agent      │
       │             │  Agent  │ │  Agent │ │   LlamaIndex      │
       │             │  E2B    │ │(Playwright─ 语义缓存(Redis)   │
       │             │  沙箱   │ │ /HTTP) │ ├─ 向量检索           │
       │             └─────────┘ └────────┘ └──────┬────────────┘
       │                    │                       │
       │             ┌──────▼───────────────────────▼──────────┐
       │             │         Background Review Fork           │
       │             │  每轮对话结束后异步触发（不阻塞响应）       │
       │             │  工具白名单：memory_tool + skill_manage   │
       │             │  ├─ 记忆模式：用户偏好/个人信息写入记忆    │
       │             │  ├─ 技能模式：发现新技巧/修补现有 Skill    │
       │             │  └─ 混合模式：两者同时                    │
       │             └─────────────────────────────────────────┘
       │                                          │
       │                           ┌──────────────▼─────────┐
       │                           │    文档处理 Pipeline    │
       │                           │  ARQ/Celery 异步队列    │
       │                           │  unstructured + MinerU  │
       │                           │  → 分块 → Embedding     │
       │                           └──────────────┬──────────┘
       │                                          │
┌──────▼──────────────────────────────────────────▼──────────┐
│                           存储层                              │
│  PostgreSQL            Redis               Chroma→Milvus    │
│  ├─ 用户/会话           ├─ 限流计数          └─ 知识库向量     │
│  ├─ 对话历史            ├─ session 缓存          + 长期记忆   │
│  ├─ 文档元信息          ├─ LlamaIndex                        │
│  ├─ Skill 使用统计      │   IngestionCache                   │
│  └─ pgvector           └─ 异步任务队列       OSS/本地磁盘    │
│     └─ 长期记忆向量                          └─ 原始文档      │
└─────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                        可观测性层                              │
│    结构化日志 (JSON)   OpenTelemetry 调用链    费用监控        │
│    token 用量统计      耗时追踪                按用户计费       │
└─────────────────────────────────────────────────────────────┘
```

---

## 整体架构补充（新增模块）

```
┌─────────────────────────────────────────────────────────────┐
│                    Conversation Loop                          │
│  preflight 压缩检查 → memory 预取 → plugin hooks 注入        │
│  → LLM 调用 → 工具分发 → 危险命令审批 → fallback 降级        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    Cron Scheduler                             │
│  wake gate 检查 → 预执行脚本 → skill 注入 → 注入扫描          │
│  → LLM 执行 → 结果路由（文本/图片/文件）→ 推送到目标平台       │
└─────────────────────────────────────────────────────────────┘
```

---

## 技术选型

| 层级 | 技术 |
|---|---|
| LLM | DeepSeek API（openai SDK 兼容） |
| RAG 框架 | LlamaIndex |
| 文档解析 | unstructured + MinerU |
| 向量数据库 | Chroma（当前）→ Milvus（后期） |
| 语义缓存 | LlamaIndex IngestionCache + Redis |
| 代码沙箱 | E2B |
| 异步队列 | ARQ（轻量）或 Celery |
| 后端框架 | FastAPI + Uvicorn |
| 前端 | Next.js + Tailwind |
| 数据库 | PostgreSQL + pgvector |
| 缓存/限流 | Redis |
| 文件存储 | OSS / 本地磁盘 |
| 可观测性 | OpenTelemetry + 结构化日志 |
| 部署 | Docker Compose |

---

## 记忆分层

| 层级 | 存储 | 说明 |
|---|---|---|
| 短期记忆 | Redis | 当前会话上下文，TTL 2h |
| 中期记忆 | PostgreSQL | 历史对话摘要压缩，超出 context 窗口时触发 |
| 长期记忆 | pgvector | 用户偏好/重要信息，跨会话永久保留 |
| 知识库 | Chroma | 用户上传文档，独立于记忆系统 |

---

## 自进化机制（参考 Hermes）

### Background Review Loop

每轮对话结束后，异步派生一个 Fork Agent，不阻塞用户响应：

```
用户对话结束，返回响应
    ↓ 异步（daemon thread）
Fork Agent 启动
    ├─ 继承父 Agent 的 LLM 配置和前缀缓存
    ├─ 工具白名单：仅 memory_tool + skill_manage（防止副作用）
    ├─ 最大迭代次数：16 次
    └─ 三种审查模式：
         记忆模式  → 用户透露了偏好/个人信息？→ 写入长期记忆
         技能模式  → 发现新技巧/用户纠正了行为？→ 创建/修补 Skill
         混合模式  → 两者同时执行
```

**技能写入优先级**（避免碎片化）：
1. 优先更新当前会话已加载的 Skill
2. 其次更新已有同类 Skill（新增子节点/修补缺陷）
3. 其次为已有 Skill 添加支撑文件（references / templates / scripts）
4. 最后才创建全新 Skill

**不写入的情形**（防止噪声污染）：
- 环境相关的临时错误（命令找不到、网络超时）
- 会话内已自行解决的问题
- 一次性任务的叙述性内容

---

### Skill 生命周期管理（Curator）

空闲 N 小时后自动触发，两阶段执行：

```
阶段一（无 LLM，纯规则）：
  active → stale   （30 天未使用）
  stale  → archived（90 天未使用）
  pinned skill 跳过所有自动转换

阶段二（LLM 驱动）：
  Fork Agent 审查所有 agent-created Skill
  可执行：合并重复 / 修补过时 / 归档低价值
  不可执行：删除（只归档，可恢复）
  不触碰：内置 Skill 和用户手写 Skill
```

**备份与回滚**：每次 Curator 运行前自动快照，支持按时间戳回滚。

---

### Skill 结构

```
skills/
└── <category>/
    └── <skill-name>/
        ├── SKILL.md          # 主内容（触发时机、步骤、注意事项）
        ├── references/       # 会话记录、API 文档摘录、领域笔记
        ├── templates/        # 可复制的起始模板文件
        └── scripts/          # 可直接运行的脚本（验证、fixture）
```

**Skill 来源分类**（影响 Curator 权限）：

| 来源 | 说明 | Curator 可修改？ |
|---|---|---|
| builtin | 项目内置 | 否 |
| user-authored | 用户手写 | 否 |
| agent-created | Background Review 自动生成 | 是 |

---

### Skill 使用统计

PostgreSQL 中记录每个 Skill 的遥测数据，驱动 Curator 决策：

```sql
CREATE TABLE skill_usage (
    skill_name       VARCHAR PRIMARY KEY,
    user_id          UUID NOT NULL,
    use_count        INT DEFAULT 0,
    view_count       INT DEFAULT 0,
    patch_count      INT DEFAULT 0,
    last_used_at     TIMESTAMP,
    last_viewed_at   TIMESTAMP,
    last_patched_at  TIMESTAMP,
    created_at       TIMESTAMP,
    state            VARCHAR DEFAULT 'active',  -- active/stale/archived
    pinned           BOOLEAN DEFAULT FALSE,
    source           VARCHAR DEFAULT 'agent-created',
    archived_at      TIMESTAMP
);
```

---

## 新增借鉴模块（来自 Hermes）

### 1. Context 压缩（ContextCompressor）

长对话时自动压缩，防止 token 超限：

```
触发条件：请求 token 数 >= context_length * 50%

压缩流程：
  Step 1（无 LLM，廉价）：
    - 将历史工具调用结果替换为单行摘要
      如：[terminal] npm test → exit 0, 47 行输出
    - 剥离历史图片（保留最新一张）
  Step 2（LLM 驱动）：
    - 保护头部（system prompt + 前 N 轮）
    - 保护尾部（最近 20K token）
    - 中间部分 LLM 摘要，增量更新不重复压缩
  防抖：连续两次压缩节省 < 10% 则停止，避免死循环
```

**.env 配置：**
```env
COMPRESS_THRESHOLD=0.50       # context 占用率触发压缩
COMPRESS_TAIL_BUDGET=0.20     # 尾部保护比例
COMPRESS_MAX_SUMMARY_TOKENS=12000
```

---

### 2. Plugin Hook 系统

不修改 system prompt（保护前缀缓存），而是在每轮 API 调用前注入上下文：

```
hook: pre_llm_call   → 追加平台特定上下文到用户消息尾部
hook: on_turn_start  → session 首次使用时初始化
hook: on_pre_compress → 压缩前给 plugin 保存重要状态的机会
hook: on_memory_write → 记忆写入后同步到外部系统
```

注入方式：追加到**用户消息**而非 system prompt，确保 DeepSeek 前缀缓存命中率。

---

### 3. Memory 预取与多 Provider 编排

```
每轮 LLM 思考期间，后台并发预取下一轮的记忆上下文
→ 隐藏记忆检索延迟

Provider 规则：
  - 最多 1 个内置 + 1 个外部 provider
  - 每个 provider 独立 try/except，单个失败不影响其他
  - 记忆上下文用 <memory-context> 标签包裹注入，
    LLM 明确知道这是历史记忆而非新用户输入
```

---

### 4. LLM Fallback 降级链

```
主模型 DeepSeek 遇到 429/503 → 自动切换备用模型
备用模型重置 retry 计数，无缝继续对话

配置示例：
  primary:  deepseek-chat
  fallback: qwen-plus / openai-gpt-4o-mini
```

**.env 配置：**
```env
LLM_FALLBACK_ENABLED=true
LLM_FALLBACK_MODEL=qwen-plus
LLM_FALLBACK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_FALLBACK_API_KEY=
```

---

### 5. 危险命令审批机制

工具调用时拦截高危操作，不同上下文采用不同策略：

```
普通用户会话  → 弹出确认提示（前端 WebSocket 推送审批请求）
Cron 定时任务 → 自动拒绝危险命令（无人值守，不能等待）
子 Agent      → 自动拒绝（子 Agent 不允许执行破坏性操作）

高危模式识别：rm -rf / DROP TABLE / 覆盖关键文件 等
```

---

### 6. Cron 定时任务调度器

支持定时自动触发 Agent 执行任务，向目标平台推送结果：

```
任务定义：
  cron: "0 9 * * 1"          # 每周一 9 点
  script: collect_data.sh    # 预执行脚本，结果注入 prompt
  skills: [weekly-report]    # 加载指定 skill
  prompt: "生成本周报告..."
  deliver_to: slack/email/webhook

Wake Gate 优化：
  脚本输出 {"wakeAgent": false} → 跳过 LLM 调用（节省费用）
  适用于"无数据时不需要生成报告"的场景

安全扫描：
  skill 内容在运行时加载并扫描 prompt 注入特征
  防止恶意 skill 携带 payload
```

**.env 配置：**
```env
CRON_ENABLED=true
CRON_DEFAULT_DELIVER_TO=webhook
CRON_WEBHOOK_URL=
```

---

## 安全策略

| 措施 | 说明 |
|---|---|
| E2B 沙箱隔离 | 代码执行完全隔离，无需黑白名单 |
| 执行超时限制 | 防止 E2B 费用被刷 |
| Redis 限流 | 滑动窗口，按用户限速 |
| Prompt 注入防护 | system prompt 与用户输入严格隔离 |
| 多用户数据隔离 | 所有查询强制带 user_id 过滤 |
| API Key 保护 | 环境变量管理，不写入代码 |
| Fork 工具白名单 | Background Review Fork 仅限 memory + skill_manage |
| 危险命令审批 | 高危工具调用拦截，Cron/子 Agent 场景自动拒绝 |
| Skill 注入扫描 | Cron 运行时扫描 skill 内容，防止 payload 注入 |
| 子 Agent 深度限制 | max_spawn_depth 默认 1，防止递归失控 |

---

## 目录结构

```
project/
├── api/
│   ├── auth.py
│   ├── chat.py
│   └── health.py
├── agents/
│   ├── orchestrator.py
│   ├── code_agent.py
│   ├── tool_agent.py
│   ├── rag_agent.py
│   ├── background_review.py   # 自进化 Fork Agent
│   └── conversation_loop.py   # 统一 turn 执行（压缩/hooks/fallback）
├── memory/
│   ├── short_term.py           # Redis 会话上下文
│   ├── mid_term.py             # PostgreSQL 摘要压缩
│   └── long_term.py            # pgvector 长期记忆
├── skills/
│   ├── base.py                 # Skill 基类
│   ├── loader.py               # Skill 加载器
│   ├── manager.py              # skill_manage 工具实现
│   ├── curator.py              # Skill 生命周期管理
│   ├── usage.py                # 使用统计遥测
│   └── builtin/                # 内置 Skill（yaml 定义）
├── knowledge/
│   ├── pipeline.py
│   ├── parser.py               # unstructured + MinerU
│   └── indexer.py
├── tasks/
│   └── ingest.py               # 异步文档摄入
├── services/
│   ├── session.py              # 会话分支/导出/分享
│   └── billing.py             # 费用统计
├── observability/
│   ├── logging.py
│   └── tracing.py
├── core/
│   ├── llm.py                  # DeepSeek 客户端 + fallback 链
│   ├── config.py
│   ├── compressor.py           # Context 压缩
│   ├── hooks.py                # Plugin hook 系统
│   └── approvals.py            # 危险命令审批
├── cron/
│   ├── scheduler.py            # 定时任务调度
│   └── wake_gate.py            # Wake gate 逻辑
├── models/                     # SQLAlchemy DB 模型
├── .env
└── docker-compose.yml
```

---

## .env 配置项

```env
# LLM
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_EMBEDDING_MODEL=deepseek-embedding

# 数据库
POSTGRES_URL=postgresql://user:pass@localhost:5432/dbname

# 向量数据库
CHROMA_HOST=localhost
CHROMA_PORT=8000
# MILVUS_URI=http://localhost:19530

# 缓存
REDIS_URL=redis://localhost:6379

# 沙箱
E2B_API_KEY=

# 文件存储
STORAGE_TYPE=local
OSS_BUCKET=
OSS_ACCESS_KEY=
OSS_SECRET_KEY=
OSS_ENDPOINT=

# 认证
JWT_SECRET=
JWT_EXPIRE_MINUTES=1440

# 文档解析
MINERU_API_KEY=

# 可观测性
LOG_LEVEL=INFO
OTEL_EXPORTER_ENDPOINT=

# 异步队列
WORKER_CONCURRENCY=4

# Skill
DEFAULT_SKILL=code-assistant
SKILLS_DIR=./skills

# LLM Fallback
LLM_FALLBACK_ENABLED=true
LLM_FALLBACK_MODEL=qwen-plus
LLM_FALLBACK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_FALLBACK_API_KEY=

# Context 压缩
COMPRESS_THRESHOLD=0.50
COMPRESS_TAIL_BUDGET=0.20
COMPRESS_MAX_SUMMARY_TOKENS=12000

# 危险命令审批
DANGEROUS_CMD_AUTO_DENY_IN_CRON=true
DANGEROUS_CMD_AUTO_DENY_IN_SUBAGENT=true

# Cron 调度
CRON_ENABLED=true
CRON_DEFAULT_DELIVER_TO=webhook
CRON_WEBHOOK_URL=

# 子 Agent
SUBAGENT_MAX_SPAWN_DEPTH=1
SUBAGENT_AUTO_APPROVE=false

# 自进化配置
REVIEW_ENABLED=true
REVIEW_MODE=combined          # memory / skills / combined
REVIEW_MAX_ITERATIONS=16
CURATOR_ENABLED=true
CURATOR_INTERVAL_HOURS=168    # 7 天
CURATOR_MIN_IDLE_HOURS=2
CURATOR_STALE_AFTER_DAYS=30
CURATOR_ARCHIVE_AFTER_DAYS=90
```
