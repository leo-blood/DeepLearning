# 课件 02｜RAG 检索增强生成

---

## 课程目标

学完本课件后，你能够：

1. 理解 RAG 的完整数据流，并能独立搭建端到端管道
2. 根据文档特征选择合适的 TextSplitter
3. 理解 Embedding 的本质，会比较不同向量库的适用场景
4. 使用多种 Retriever 策略（MMR、BM25、Ensemble、Self-Query）
5. 实现带来源引用和重排序的生产级 RAG

---

## 一、RAG 是什么，为什么需要它？

**问题**：LLM 的知识截止于训练日期，也不了解你的私有文档。

```
用户：我们公司 Q3 的销售额是多少？
LLM ：[无法回答，这是私有数据]
```

**RAG 的解法**：先检索，再生成。

```
┌─────────────────────────────────────────────────────┐
│                    RAG 完整流程                       │
│                                                     │
│  [索引阶段（离线）]                                   │
│  文档 → 分块 → Embedding → 向量库                    │
│                                                     │
│  [查询阶段（在线）]                                   │
│  用户问题 → Embedding → 向量检索 → 相关文档块          │
│      ↓                                              │
│  将文档块 + 问题 → 组装 Prompt → LLM → 生成答案        │
└─────────────────────────────────────────────────────┘
```

---

## 二、文档加载（Document Loaders）

### 2.1 常用 Loader

```python
# PDF
from langchain_community.document_loaders import PyPDFLoader
loader = PyPDFLoader("report.pdf")
docs = loader.load()  # 每页一个 Document

# 目录（批量加载）
from langchain_community.document_loaders import DirectoryLoader
loader = DirectoryLoader("./docs/", glob="**/*.md")
docs = loader.load()

# 网页
from langchain_community.document_loaders import WebBaseLoader
loader = WebBaseLoader(["https://example.com/docs"])
docs = loader.load()

# Notion
from langchain_community.document_loaders import NotionDirectoryLoader
loader = NotionDirectoryLoader("Notion_DB/")

# 数据库查询结果
from langchain_community.document_loaders import SQLDatabaseLoader
```

### 2.2 Document 结构

```python
# Document 有两个字段：
doc = docs[0]
doc.page_content  # str：文本内容
doc.metadata      # dict：来源信息，如 {"source": "report.pdf", "page": 3}
```

---

## 三、文本分块（Text Splitters）

### 3.1 为什么要分块？

- 向量模型有最大输入限制（通常 512 tokens）
- 大块文本含噪音多，检索精度低
- 分块粒度直接影响检索质量

### 3.2 RecursiveCharacterTextSplitter（推荐默认选择）

按段落→句子→单词递归分割，优先在自然边界断开。

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,        # 每块最大字符数
    chunk_overlap=200,      # 相邻块重叠字符数（防止信息断裂）
    length_function=len,
    separators=["\n\n", "\n", "。", ".", " ", ""],  # 中文加"。"
)

chunks = splitter.split_documents(docs)
print(f"原文档 {len(docs)} 页 → 分块后 {len(chunks)} 块")
```

**chunk_overlap 的作用：**

```
块 1：...数据库索引原理是指通过B+树结构加速查询...
块 2：...通过B+树结构加速查询，其核心优势是...
          ←─── overlap（200字符）───►
```

### 3.3 SemanticChunker（语义分块，实验性）

按语义相似度分块，而非固定字符数。

```python
from langchain_experimental.text_splitter import SemanticChunker
from langchain_openai import OpenAIEmbeddings

splitter = SemanticChunker(
    OpenAIEmbeddings(),
    breakpoint_threshold_type="percentile",
    breakpoint_threshold_amount=95,
)
chunks = splitter.split_documents(docs)
```

### 3.4 分块策略选型

| 文档类型 | 推荐策略 |
|---------|---------|
| 技术文档、说明书 | `RecursiveCharacterTextSplitter` |
| 代码文件 | `Language.PYTHON` / `Language.JS` |
| Markdown | `MarkdownTextSplitter` |
| 长篇叙述性文本 | `SemanticChunker` |
| 结构化数据（表格） | 自定义，保持行完整性 |

---

## 四、向量嵌入（Embeddings）

### 4.1 Embedding 的本质

将文本映射到高维向量空间，语义相近的文本距离相近。

```python
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# 单条
vector = embeddings.embed_query("什么是RAG？")
# vector → [0.023, -0.156, 0.891, ...]  维度：1536

# 批量（文档索引时用）
vectors = embeddings.embed_documents(["文本1", "文本2", "文本3"])
```

### 4.2 模型选择

| 模型 | 维度 | 适用场景 | 费用 |
|------|------|---------|------|
| `text-embedding-3-small` | 1536 | 通用，**推荐** | 低 |
| `text-embedding-3-large` | 3072 | 高精度场景 | 中 |
| `text-embedding-ada-002` | 1536 | 旧项目兼容 | 低 |
| `nomic-embed-text`（本地） | 768 | 私有部署 | 免费 |

---

## 五、向量库（Vector Stores）

### 5.1 Chroma（本地开发推荐）

```python
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# 创建/加载向量库
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_db",   # 持久化到磁盘
    collection_name="my_docs",
)

# 下次直接加载
vectorstore = Chroma(
    persist_directory="./chroma_db",
    embedding_function=embeddings,
    collection_name="my_docs",
)
```

### 5.2 FAISS（大规模本地检索）

```python
from langchain_community.vectorstores import FAISS

vectorstore = FAISS.from_documents(chunks, embeddings)
vectorstore.save_local("./faiss_index")

# 加载
vectorstore = FAISS.load_local(
    "./faiss_index", embeddings,
    allow_dangerous_deserialization=True,
)
```

### 5.3 向量库对比

| 向量库 | 部署方式 | 适用规模 | 生产可用 |
|--------|---------|---------|---------|
| Chroma | 本地/服务端 | 中小 | ✅ |
| FAISS | 本地 | 大 | ✅（无分布式） |
| Pinecone | 云服务 | 超大 | ✅ |
| Weaviate | 本地/云 | 大 | ✅ |
| pgvector | PostgreSQL 插件 | 中 | ✅（已有 PG 时） |

---

## 六、检索策略（Retrievers）

### 6.1 基础相似度检索

```python
retriever = vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 4},     # 返回最相关的 4 块
)

docs = retriever.invoke("什么是分布式缓存？")
```

### 6.2 MMR（最大边际相关，减少重复）

解决相似度检索返回多个相似文本块的问题。

```python
retriever = vectorstore.as_retriever(
    search_type="mmr",
    search_kwargs={
        "k": 4,
        "fetch_k": 20,       # 先取 20 个候选
        "lambda_mult": 0.5,  # 0=最大多样性，1=最大相关性
    },
)
```

### 6.3 BM25 + 向量混合检索（Ensemble Retriever）

BM25 擅长关键词匹配，向量检索擅长语义理解，混合最优。

```python
from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever

# BM25（关键词）
bm25_retriever = BM25Retriever.from_documents(chunks)
bm25_retriever.k = 4

# 向量（语义）
vector_retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

# 混合
ensemble_retriever = EnsembleRetriever(
    retrievers=[bm25_retriever, vector_retriever],
    weights=[0.4, 0.6],     # BM25:向量 = 4:6
)
```

### 6.4 Self-Query Retriever（自动解析过滤条件）

让 LLM 从自然语言问题中提取元数据过滤器。

```python
from langchain.retrievers.self_query.base import SelfQueryRetriever
from langchain.chains.query_constructor.base import AttributeInfo

metadata_field_info = [
    AttributeInfo(name="source", description="文档来源文件名", type="string"),
    AttributeInfo(name="page",   description="页码",           type="integer"),
    AttributeInfo(name="year",   description="文档年份",        type="integer"),
]

retriever = SelfQueryRetriever.from_llm(
    llm=ChatOpenAI(model="gpt-4o"),
    vectorstore=vectorstore,
    document_contents="公司财务报告和技术文档",
    metadata_field_info=metadata_field_info,
)

# 自动解析为：filter={"year": 2024} + 语义检索 "销售数据"
docs = retriever.invoke("2024年的销售数据")
```

---

## 七、构建 RAG Chain

### 7.1 基础 RAG（LCEL 写法）

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI

prompt = ChatPromptTemplate.from_template("""
请根据以下上下文回答问题。如果上下文中没有足够的信息，请直接说"我不知道"，不要编造答案。

上下文：
{context}

问题：{question}

回答：""")

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

rag_chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | prompt
    | ChatOpenAI(model="gpt-4o")
    | StrOutputParser()
)

answer = rag_chain.invoke("什么是分布式锁？")
```

### 7.2 带来源引用的 RAG

```python
from langchain_core.runnables import RunnableParallel

# 同时返回答案和来源
rag_chain_with_source = RunnableParallel(
    {"context": retriever, "question": RunnablePassthrough()}
).assign(answer=
    (lambda x: {"context": format_docs(x["context"]), "question": x["question"]})
    | prompt
    | ChatOpenAI(model="gpt-4o")
    | StrOutputParser()
)

result = rag_chain_with_source.invoke("分布式锁的原理？")
print(result["answer"])
print("来源：", [doc.metadata["source"] for doc in result["context"]])
```

### 7.3 带重排序的 RAG（Reranker）

检索后用更强的模型对候选块重新排序，提升精度。

```python
from langchain.retrievers.contextual_compression import ContextualCompressionRetriever
from langchain_cohere import CohereRerank

# Cohere Reranker（需要 API Key）
compressor = CohereRerank(model="rerank-multilingual-v3.0", top_n=3)

compression_retriever = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=ensemble_retriever,
)

# 用于 RAG chain
rag_chain = (
    {"context": compression_retriever | format_docs, "question": RunnablePassthrough()}
    | prompt
    | ChatOpenAI(model="gpt-4o")
    | StrOutputParser()
)
```

---

## 八、RAG 质量评估

```python
from langchain.evaluation import load_evaluator

# 评估答案是否与上下文一致（幻觉检测）
evaluator = load_evaluator("context_qa")

result = evaluator.evaluate_strings(
    input="分布式锁的原理是什么？",
    prediction=answer,
    reference="\n".join(doc.page_content for doc in retrieved_docs),
)
print(result["score"])  # 0-1，越高越好
```

---

## 本课小结

```
RAG 管道
文档 → Loader → Splitter → Embedding → VectorStore
                                            ↓
用户问题 → Embedding → Retriever（MMR/BM25/Ensemble）
                            ↓
                       Reranker（可选）
                            ↓
                       RAG Chain → LLM → 答案 + 来源
```

| 关键决策 | 建议 |
|---------|------|
| 分块大小 | 1000字符，overlap 200 |
| 检索策略 | Ensemble（BM25+向量）> 纯向量 |
| 重排序 | 有条件上 Reranker，精度提升明显 |
| 向量库 | 开发用 Chroma，生产用 Pinecone / pgvector |
| Embedding | `text-embedding-3-small` 性价比最高 |
