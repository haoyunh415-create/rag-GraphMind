# 面试可能问到的问题

---

## 一、架构设计（必问，定基调）

### Q1: 为什么不用 LangChain，要自己写编排器？

**考察点**：你是"调包侠"还是有独立思考？

**回答要点**：
- LangChain 的默认 RAG 链是黑盒 — `chain.invoke()` 之后你不知道检索拿了多少条、Reranker 筛掉了什么、引用怎么选的
- 这个项目的核心诉求是**可解释**，所以每一步必须可观测
- 自己写的 `AgentOrchestrator` 精确控制 6 个步骤：decompose → retrieve → fuse → rerank → cite → evaluate，每步都有 Trace 耗时
- 自研不等于从零造轮子 — 模型调用用 litellm/openai SDK、ES/Neo4j 用官方驱动，只是把编排逻辑握在自己手里

### Q2: 为什么三路并行检索？向量不够吗？

**考察点**：对检索系统局限性的理解

**回答要点**：
- 向量检索擅长语义相似，但怕精确编号 — "工单 K-17"这种 query 向量根本区分不了
- BM25 擅长精确关键词，但不懂同义改写 — "退货"和"无理由退换"在它眼里是两个东西
- 图谱检索擅长关系推理 — "林晨的团队维护的产品所属公司总部在哪"这种多跳，向量/BM25 各自只能命中半句话
- 三者**互补**：向量 = 覆盖面、BM25 = 精确度、图谱 = 结构化推理
- RRF 融合：多源共识的信号比单一源更可靠

### Q3: RRF 为什么不直接用原始分数排序？

**回答要点**：
- 三个源的分数体系不统一：向量 cosine 0.85、BM25 score 8.3、图谱无边权，无法直接比较
- RRF 只看**排名位置**：`score = 1 / (60 + rank)`，排名越靠前分越高
- k=60 是阻尼常数，避免头部过强、给中尾部留机会
- 同一片段被多个源命中 → RRF 分累加 → 排到前面 = "多源共识"
- 额外好处：新增检索源不需要调权重，RRF 自动适配

---

## 二、检索质量（技术深度的核心战场）

### Q4: Reranker 做了什么？跟 RRF 有什么区别？

**回答要点**：
- RRF 是**粗排**（只看排名位置融合），Reranker 是**精排**（重新审视每一条的语义匹配）
- 自研的 `HybridReranker` 用四维加权：
  - `original_weight` — 保留 RRF 的共识信号
  - `query_weight` — 问题覆盖度（query 中的词是否出现在文档中）
  - `phrase_weight` — 短语命中（连续匹配加分）
  - `source_weight` — 来源加分（比如图谱证据比纯向量更有可解释价值）
- 最后加一个 Cross-Encoder 语义匹配分兜底
- 两阶段排序（粗排→精排）平衡了性能和精度：RRF 从几十条筛到十几条，Reranker 再从其中精挑

### Q5: 引用裁剪（Citation Pruner）怎么做的？为什么不直接用 top-k？

**回答要点**：
- 简单 top-k 的问题：可能返回一堆弱相关片段，或者一个文档的所有 fragment 霸榜
- 自研 `CitationPruner` 做了**多阈值门禁**：
  - `CITATION_MIN_RELATIVE_SCORE` — 分数必须达到最高分的某个比例
  - `CITATION_PER_DOCUMENT_LIMIT` — 单文档最多选几条，防止霸榜
  - `CITATION_MAX_ITEMS` — 最多返回 N 条
  - `CITATION_MIN_QUERY_COVERAGE` — query 覆盖度不够的片段直接丢弃
- 还有一个**证据不足惩罚**：如果最好的一条都达不到阈值，直接标记"弱相关"，生成阶段会触发拒答

### Q6: 怎么处理用户问了一个知识库里完全不存在的问题？

**回答要点**：
- Citation Pruner 检测到所有片段都低于最低得分阈值 → 触发"证据不足"标记
- 生成阶段收到该标记 → 直接返回拒答模板，而不是让 LLM 瞎编（幻觉）
- Golden Eval 有专门的 `Refusal Accuracy` 指标和 negative cases，用已知不在知识库里的问题来测拒答是否准确

---

## 三、Graph RAG（拉开分差的核心亮点）

### Q7: Graph RAG 的实体关系是怎么提取的？

**回答要点**：
- 文档入库后，`KnowledgeGraph.link_chunks()` 触发实体关系抽取
- 本地提供了规则匹配兜底：正则识别"公司→产品""团队→负责人"等中文三板式句式
- 有 LLM Key 时用 LLM 抽取，返回结构化 `{entities, relations}`
- 抽取结果写入 Neo4j：实体变了节点，关系变了边
- `graph_entity_extraction_sync` 控制是同步（入库即入图）还是异步（fire-and-forget）

### Q8: 多跳推理的 query 是怎么转化成图查询的？

**回答要点**：
- 问题送入 `decompose_query()` → 识别出是多跳结构 → 拆成子问题
- 子问题分别检索后，从结果中抽取实体名 → 拼接 Cypher 查询
- 例如"星桥客服平台所属公司的总部在哪"：
  - 子问题 1：向量检索"星桥客服平台属于哪个公司"→ 命中"云澜科技"
  - 子问题 2：Cypher `MATCH (c:Company {name:'云澜科技'})-[:HEADQUARTERED_IN]->(city) RETURN city` → "杭州"
- 最后一条图路径证据以 `graph_path` 形式附在引用里，前端展示为可视化路径

### Q9: 如果 Neo4j 不可用怎么办？

**回答要点**：
- 每个检索源的 health check 有缓存（10 秒 TTL），不可用时自动降级
- 图谱不可用时，`retrieval_health()` 返回 `graph.available = false`
- 编排器检测到后，只走向量+BM25 双路，跳过图谱
- 回答的 `retrieval_sources` 里不会出现 graph，引用照样正常生成
- 这就是为什么要三路并行而不是把图谱作为必须依赖 — 局部故障不影响整体可用

---

## 四、生产化工程（展示你不是只会写 Demo）

### Q10: 文档入库为什么做成异步的？状态机怎么设计的？

**回答要点**：
- 文档解读+切片+索引是重操作，同步处理会阻塞 API 响应
- 状态机：`queued → processing → ready / partial / error / duplicate`
- `.env` 里 `INGESTION_QUEUE_MODE=auto` 时自动检测 Redis 可用性：
  - Redis 可用 → 入 Redis 持久队列 → 独立 worker 进程消费
  - Redis 不可用 → 降级为进程内后台任务（`asyncio.create_task`）
- 生产环境切 `redis`，本地开发用 `inline`（同步直连）
- 每次入库 attempt 都有记录，前端可以看到 attempt 计数和失败原因

### Q11: 失败重试和 DLQ 怎么设计的？

**回答要点**：
- 每个入库任务有 `max_attempts`（默认 3 次），失败后延迟重试
- 超过最大尝试次数 → 进入 DLQ（死信队列），不会静默丢失
- 前端可以查看 DLQ 摘要、失败原因、重新触发重试
- 正在处理的文档可以 `cancel`，状态回到 `cancelled`
- 队列健康面板实时展示 pending/processing/dead 三态计数

### Q12: 本地开发能跑，上生产要注意什么？

**回答要点**：
- 密钥轮换：`.env` 里的 Key 不能带到生产
- `DEBUG=false`，`CORS_ORIGINS` 限定为生产域名
- `API_AUTH_TOKEN` 配合前端 `NEXT_PUBLIC_API_AUTH_TOKEN` 启用认证
- `INGESTION_QUEUE_MODE=redis` + 独立 worker，不能靠 asyncio.create_task
- `EMBEDDING_MODEL` 切到 OpenAI 兼容的 embedding API，容器镜像默认不带 sentence-transformers
- Docker Compose 一键起 8 个服务，健康检查确保依赖就绪后才启动 API

---

## 五、质量评估（证明你不是靠"看着还行"来判断好坏）

### Q13: Golden Eval 测什么？为什么要有它？

**回答要点**：
- 不是看页面效果，而是自动化的回归门禁
- 指标：`Recall@K`、`MRR`、`Citation Precision`、`Refusal Accuracy`、`Behavior Pass`、`Latency p95`
- 还有 `performance_warnings` — Trace 各阶段是否超预算
- 门禁必须全部通过，任一失败脚本以非零退出 → 可以放在 CI 里
- Golden Set 包含正样本 + 负样本 + 干扰文档，覆盖简单检索、同义改写、列表型回答、拒答
- 意义：每次改动后自动跑一遍，知道是改好了还是改坏了，不是碰运气

### Q14: 怎么衡量"引用质量"？

**回答要点**：
- `Citation Precision`：返回的引用中，真正命中预期文档的比例
- 不是靠 LLM 打分（不稳定），而是**确定性比对**：Golden Set 预先定义了每个问题的 `expected_document_id` 和 `expected_terms`
- 引用包含了预期文档 ID + 预期关键词 = 命中
- Golden Eval 里还验证了 `Refusal Accuracy` — 对不在知识库里的问题，必须拒答

---

## 六、可观测性（展示工程化思维）

### Q15: Trace 系统怎么设计的？为什么要做性能预算？

**回答要点**：
- 每个回答的完整链路都被追踪：`backend_health → retrieve → rank → cite → generate → evaluate`
- 每步记录耗时和输入输出摘要，前端 Trace 面板可视化展示
- `TRACE_STEP_BUDGETS_MS` 定义各阶段性能预算（如检索 300ms、重排 100ms），超标的标黄为 `performance_warning`
- 意义：不是出了性能问题再排查，而是每次回答都能看到瓶颈在哪 — 检索慢了还是重排慢了
- 配合 `retrieval_health_cache_seconds` 避免每次查询都做 TCP 探测（减少不必要的延迟）

### Q16: retrieval health cache 为什么需要？

**回答要点**：
- 每个检索源（ES、Neo4j）的可用性通过 TCP 连接探测，但每次都测太贵了
- 缓存 10 秒：10 秒内的查询复用同一个探测结果
- 不可用时自动降级：图谱挂了不影响向量+BM25 正常工作
- 生产流量下，这个缓存减少了 90% 的无效 TCP 握手

---

## 七、项目复盘（展示成长性）

### Q17: 这个项目你做了多久？怎么迭代的？

**回答要点**（按 DEMO.md 的 7 个阶段讲）：
1. 基础 RAG：FastAPI + Next.js，上传、切片、检索、回答（先跑通）
2. 检索质量：加混合检索、Reranker、引用裁剪、弱相关拒答
3. 可解释性：Trace、图谱路径、引用片段、质量分展示
4. 性能治理：health cache、延迟预算、performance warnings
5. 质量门禁：Golden Eval 自动化检查
6. 生产化入库：状态机、Redis worker、重试、取消、DLQ
7. 收口展示：Demo Pack、一键验收脚本

**关键**：每一轮的驱动原因是什么？不是"我觉得应该加"而是"上一步暴露出什么问题"

### Q18: 最大的技术挑战是什么？

**回答要点**（挑一个深入讲）：
- 引用裁剪是踩坑最多的：刚开始直接 top-k，结果要么一个文档的 fragment 霸榜，要么弱相关片段混进来
- 解决方案：从 top-k 演进到多阈值门禁 + 证据不足惩罚，中间调了好几版阈值
- 怎么验证效果：Golden Eval 的 Citation Precision 从最初的 60% 提升到 95%+
- 体现的思维：不是一次写对，而是建立反馈闭环（发现问题 → 改进 → 自动验证）

### Q19: 如果重新做，会有什么不同的设计？

**回答要点**（展示反思能力）：
- 一开始就应该把入库做成状态机，而不是后来从同步任务改过去的（伤筋动骨）
- 评估体系应该跟检索系统同时建立，而不是等检索稳定了再补
- sqlite-vec 依赖太重，纯 Python 的 TF-IDF + 余弦相似度兜底其实够用
- 但另一方面，渐进式迭代也有好处——每个阶段的目标明确，不会 over-engineer

---

## 八、如果面试官追问细节

### Q20: RRF 的 k=60 怎么来的？

学术论文 `Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods` (Cormack et al., 2009) 的实验结论。k 太小 → 只有第一名有分，太大 → 排名信号消失。60 是实践中最稳定的值。

### Q21: 为什么用 SHA256 而不是 MD5 做去重指纹？

不是为了安全，而是 SHA256 碰撞概率极低（2^-256），即使知识库有百万级片段也不会重复。MD5 的 128 位对于内容去重也够了，但 SHA256 是更标准的选择。

### Q22: SQLite 向量检索是怎么实现的？

Python 的 `sentence-transformers` 把文本嵌入为向量，存在 SQLite 里。检索时读出来做余弦相似度排序。`sqlite-vec` 扩展做了 ANN 加速，如果装不上就降级为全量内积计算——慢但能跑，保证离线可用。

---

## 一句话汇总

面试官最想听到的不是"我用了什么技术"，而是**"这个技术解决了什么具体问题"**。每个回答都要落在一个具体的痛点或局限上。
