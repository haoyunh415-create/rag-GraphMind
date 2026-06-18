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

<details>
<summary><b>📖 补充：什么是编排器？我的 AgentOrchestrator 做了什么？</b></summary>

#### 用人话解释"编排器"

把检索系统想象成厨房。你有三个厨师——向量检索（擅长语义相似）、BM25（擅长精确关键词）、图谱检索（擅长关系推理）。编排器就是总指挥：

- "你们三个同时去查，把结果端过来"（并行派活）
- "重复的菜合在一起，按共识度排序"（RRF 融合）
- "不够格的菜撤掉，只上最好的"（引用裁剪）
- "尝尝咸淡"（质量评估）

编排器不自己干活，它调别人干活。LangChain 的 RAG 链之所以是黑盒，就是因为它把所有这些步骤包成了一个函数调用，你不知道中间发生了什么。

#### AgentOrchestrator 六步流水线

```
提问: "食品类商品支持七天无理由退货吗？"
  │
  ├─ Step 0: 意图路由 (classify_intent)
  │     · kb 模式 → 走完整 RAG
  │     · chat 模式 → 跳过检索，直接 LLM 回复
  │     · 空知识库 → 自动降级为通用聊天
  │
  ├─ Step 1: 问题拆解 (decompose_query)
  │     · "退货政策" → ["食品 七天 退货", "不支持退货的商品类型", "退货政策"]
  │     · 一个复杂问题拆成多个子问题，覆盖面更广
  │
  ├─ Step 2: 并行检索 (3路 asyncio.gather)
  │     · vector: 向量相似 → 覆盖语义
  │     · bm25:   ES 关键词 → 精确命中
  │     · graph:  Neo4j 关系 → 结构化推理
  │     · 每个源搜 top_k×3 条（给融合留余量）
  │     · 某个源挂了？health check 自动跳过，不影响其他源
  │     · 最终 all_results = 所有源的并集，每条标记 source 来源
  │
  ├─ Step 3: RRF 融合 + Reranker 重排 (fusion.rank)
  │     · 去重（文本指纹 SHA256）
  │     · RRF 按排名共识计分（多源命中 = 分更高）
  │     · Reranker 四维加权精排（问题覆盖 + 短语命中 + 来源 + 原始分）
  │     · 输出 → top_k 条最优结果
  │
  ├─ Step 4: 引用裁剪 (Citation Pruner)
  │     · 多阈值门禁筛选
  │     · 删掉弱相关、单文档霸榜的片段
  │     · 一条都选不出来 → 触发"证据不足"
  │
  ├─ Step 5: 答案生成 (synthesize_answer)
  │     · 有引用 → 流式输出带引用的回答
  │     · 无引用且 kb 模式 → 拒答（不瞎编）
  │     · 无引用且 auto 模式 → 通用 LLM 但提示可能不准确
  │
  ├─ Step 6: 质量评估 (evaluate_rag_answer)
  │     · 事实支撑度 + 相关性 + 引用覆盖率 + 检索质量
  │     · 持久化到 SQLite，可回溯
  │
  └─ 最终: 全链路 Trace → 前端 Trace 面板可视化
```

#### 核心代码（精简版）

```python
class AgentOrchestrator:
    def __init__(self, query_id, conversation_id, top_k, mode):
        self.vector_store = VectorStore()      # 向量检索
        self.graph = KnowledgeGraph()          # 图谱检索
        self.bm25 = BM25Search()               # BM25 检索
        self.fusion = FusionRanker()           # RRF 融合

    async def run(self, query):
        trace = RetrievalTrace(...)             # 全链路追踪

        # Step 0: 意图路由
        intent = await classify_intent(query)
        if intent == "chat":
            return await direct_chat(query)     # 跳过检索

        # Step 1: 拆解
        sub_queries = await decompose_query(query)

        # Step 2: 并行检索（三路同时发请求）
        jobs = {"vector": self.vector_store.search(...)}
        if backend_health["graph"]["available"]:
            jobs["graph"] = self.graph.search(...)
        if backend_health["bm25"]["available"]:
            jobs["bm25"] = self.bm25.search(...)
        results = await asyncio.gather(*jobs.values())  # 并行

        # Step 3: 融合 + 重排
        ranked = await self.fusion.rank(query, all_results)

        # Step 4: 引用裁剪
        citations = prune_citations_with_report(query, ranked)

        # Step 5: 流式生成
        async for token in synthesize_answer(query, citations):
            yield token

        # Step 6: 质量评估 → 持久化
        evaluation = evaluate_rag_answer(query, answer, ...)
        await EvaluationStore().save(evaluation)

        yield trace.to_dict()  # 全链路数据 → 前端 Trace 面板
```

#### 为什么这样设计（面试时的关键论点）

| 对比维度 | LangChain 默认链 | AgentOrchestrator |
|---------|-----------------|-------------------|
| 可见性 | `invoke()` 黑盒 | 每步 Trace + 输入输出 |
| 检索策略 | 单路向量 | 三路并行 + RRF 融合 |
| 引用质量 | 原始 top-K | 多阈值裁剪 + 拒答 |
| 容错 | 挂了报错 | 独立 health check，可降级 |
| 质量保证 | 无 | 每次回答自动评估 + 持久化 |
| 可调试 | 困难 | Trace 面板定位瓶颈 |

</details>

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

<details>
<summary><b>📖 补充：Citation Pruner 多阈值门禁详解</b></summary>

#### 为什么不能直接 top-K？

三个实际问题：
1. 分数最高不代表真相关 — RRF 第一名仍可能是运气
2. 单文档霸榜 — 某个文档的多个片段包揽 top-5，引用来源单一
3. 没有拒答信号 — 如果最好的一条都不够格，应触发拒答，而不是硬塞给 LLM

#### 四道闸门，逐条审查

```python
for item in scored:
    # 闸门 1: 总数上限 (默认 max_items=3)
    if len(selected) >= 3:
        reject("max_items_limit")

    # 闸门 2: 单文档上限 (默认 per_document_limit=2)
    if per_document_counts[doc_key] >= 2:
        reject("per_document_limit")

    # 闸门 3: 质量门禁 (_threshold_decision)
    passed, reason = _threshold_decision(item)
    if not passed:
        reject(reason)

    # 通过全部闸门 → 选中
    selected.append(item)

# 兜底: 一条都没选中时，只要第一名覆盖度 ≥ 0.245，降级通过
if not selected and first["coverage"] >= 0.245:
    selected = [first]  # fallback_minimum_evidence
```

#### 质量门禁决策树（_threshold_decision）

```
候选进入
  │
  ├─ 干扰文档 (evidence_penalty ≥ 0.25) → ❌ insufficient_evidence
  │
  ├─ 覆盖度不够 (adjusted_coverage < 0.35) → ❌ query_coverage_low
  │     · adjusted = 原始覆盖度 + 多源加成(0.08) + 图谱加成(0.05)
  │
  ├─ 强证据 (原始覆盖度 ≥ 0.60) → ✅ direct_evidence (免检)
  │
  ├─ 排位高 (相对分 ≥ 0.55) → ✅ score_and_coverage
  │
  └─ 都不满足 → ❌ relative_score_low
```

#### 四个阈值（可在 .env 调整）

| 阈值 | 默认值 | 作用 |
|------|--------|------|
| `CITATION_MAX_ITEMS` | 3 | 最多返回几条引用 |
| `CITATION_PER_DOCUMENT_LIMIT` | 2 | 单文档最多选几条，防霸榜 |
| `CITATION_MIN_RELATIVE_SCORE` | 0.55 | 分数必须达到最高分的 55% |
| `CITATION_MIN_QUERY_COVERAGE` | 0.35 | 问题词覆盖度 ≥ 35% |

#### 多源加成的妙用

同一个片段，双源命中 vs 单源命中，门禁通过率截然不同：

```
片段A (仅向量命中):  coverage=0.30 → adjusted=0.30 → < 0.35 ❌
片段B (向量+BM25):   coverage=0.30 → adjusted=0.38 → ≥ 0.35 ✅
片段C (向量+图谱):   coverage=0.30 → adjusted=0.43 → ≥ 0.35 ✅
```

多源共识本身就是质量信号，值得降低一点覆盖率门槛。

#### 完整示例

Reranker 输出 5 条，经过 Citation Pruner:

```
候选1 (03-graph-relations.md): rel=1.00, cov=0.75, graph+vector → direct_evidence ✅
候选2 (01-commerce-policy.md): rel=0.85, cov=0.55, bm25+vector → score_and_coverage ✅
候选3 (03-graph-relations.md): rel=0.72, cov=0.48, 同文档已满2条 → ❌ per_document_limit
候选4 (02-support-operations.md): rel=0.60, cov=0.30, 仅向量 → ❌ query_coverage_low
候选5 (干扰文档): rel=0.50, cov=0.28, penalty=0.35 → ❌ insufficient_evidence

结果: 选中 2 条 (来自 2 份不同文档)
```

#### 透明化拒绝原因

每条被拒候选都标记了原因，Trace 里能看到汇总：

```json
"rejection_counts": {
    "max_items_limit": 5,         // 正常截断
    "per_document_limit": 1,      // 防止霸榜
    "query_coverage_low": 2,      // 质量不行 (多了说明 Reranker 漏了)
    "insufficient_evidence": 1,   // 干扰文档
    "relative_score_low": 0       // 多了说明 Reranker 排序有问题
}
```

#### 三个文件的关系

```
fusion.py  →  去重 + 按排名位置粗排 → 位置信号 (几十条)
reranker.py →  按内容四维精排        → 语义信号 (top_k 条)
pruner.py  →  按质量门禁最终筛选     → 最后防线 (3-5 条引用)
```

</details>

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

<details>
<summary><b>📖 补充：rag_quality.py 日常质量评估详解</b></summary>

#### 跟 Golden Eval 的区别

| | evaluate_rag_answer（日常） | Golden Eval（发版前） |
|------|---------------------------|---------------------|
| 触发 | 每次回答自动跑 | 手动跑脚本 |
| 方法 | 无参考，仅看 answer vs context | 有标准答案，对比验证 |
| 目的 | 日常质量监控 | 回归门禁 |
| 存储 | SQLite | JSON 报告文件 |

两个互补：日常用 evaluate 监控，发版前用 Golden Eval 回归。

#### 四维评分公式

```python
overall_score = 
    groundedness       × 0.35   # 事实支撑度（最重要）
  + answer_relevance   × 0.25   # 回答相关性
  + citation_coverage  × 0.20   # 引用覆盖率
  + retrieval_quality  × 0.20   # 检索质量
```

全部是用 token 集合运算，不调 LLM，零依赖，确定性输出。

#### ① Groundedness（事实支撑度，35%）

```
回答中有多少词能从上下文文档里找到？

回答: "食品类商品不能七天无理由退货"
上下文: "食品类商品不支持七天无理由退货"
→ 交集词 5/6 = 0.83 ✅

回答: "根据我们的政策，您可以随意退货"（跟文档无关）
→ 交集词极少 = 0.1 ❌ 在瞎编
```

#### ② Answer Relevance（回答相关性，25%）

```
问题 ←→ 回答的双向 F1

问题: "食品类商品支持七天无理由退货吗"
回答: "食品类商品不支持退货" → F1=0.80 ✅
回答: "我们总部在杭州" → F1≈0 ❌ 答非所问
```

如果 Golden Eval 提供了 `expected_answer`，取 max(与 query 的 F1, 与 expected 的 F1)。

#### ③ Citation Coverage（引用覆盖率，20%）— 最有创意的维度

**把回答拆成陈述句，逐句验证有没有被引用文档支撑**：

```python
statements = _answer_statements(answer)
# "食品不支持退货。普通商品支持七天退货。" 
# → ["食品不支持退货", "普通商品支持七天退货"]

for statement in statements:
    for context in contexts:
        # 该陈述的 token 与某条引用的 token 重叠度 ≥ 45%
        # 或者重叠至少 4 个词
        if coverage >= 0.45 or overlap >= 4:
            supported += 1; break

citation_coverage = supported / total_statements
```

```
回答有 3 个陈述句:
  陈述1 "食品不支持退货"    → 引用1 支撑 ✅
  陈述2 "电子发票24h发送"   → 引用2 支撑 ✅
  陈述3 "总部在杭州"        → 无引用支撑 ❌

citation_coverage = 2/3 = 0.67
```

#### ④ Retrieval Quality（检索质量，20%）

```python
retrieval_quality = best_relevance × 0.45      # 最好的一条的 query→ctx F1
                  + average_relevance × 0.35    # 所有结果的平均 F1
                  + average_model_score × 0.2   # 检索模型自己的归一化分
```

衡量检索环节本身——搜回来的东西到底有多少跟问题相关。

#### 质量问题标签

```python
if not answer:              "empty_answer"        # 空回答
if not contexts:            "no_citations"        # 无引用
if groundedness < 0.45:     "low_groundedness"    # 事实支撑不足
if answer_relevance < 0.35: "low_answer_relevance" # 答非所问
if citation_coverage < 0.5: "low_citation_coverage" # 引用覆盖不足
if retrieval_quality < 0.35: "low_retrieval_quality" # 检索太差
```

前端可以直接看到这些标签，红灯 = 这个回答有问题。

#### 等级标签

```
≥ 0.75 → "pass" (绿色)
≥ 0.50 → "warn" (黄色)
< 0.50 → "fail" (红色)
```

#### 设计亮点

- **确定性**：纯数学，不调 LLM，同一输入永远同一输出
- **零依赖**：不需要 API key、不需要额外模型
- **陈述级检测**：不是笼统地看"引用了几个文档"，而是逐句验证
- **实时反馈**：每次回答立即出分，前端可见
- **可持久化**：所有评分存 SQLite，积累历史数据

</details>

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
