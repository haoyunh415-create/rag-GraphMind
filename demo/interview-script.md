# Two-Minute Interview Script

## 0:00 - 0:20 Project Positioning

这个项目是一个本地可运行的 RAG 平台，不是简单的 LLM 调用页面。它覆盖了知识文档上传、异步入库、混合检索、引用追踪、Graph RAG、质量评估、延迟 Trace 和任务运营状态。

## 0:20 - 0:45 Architecture

前端是 Next.js，后端是 FastAPI。文档上传后先进入 ingestion 状态机，本地可以 inline/auto 跑，生产模式可以切 Redis worker。检索侧支持 SQLite fallback，也能接 Milvus、Elasticsearch、Neo4j。回答链路会经过 retrieval、rerank、citation pruning、generation 和 evaluation。

## 0:45 - 1:15 Demo Flow

我会上传 3 份固定 demo 文档。第一份覆盖退货、发票和物流，第二份覆盖客服转人工和运营指标，第三份覆盖公司、产品、团队、负责人和故障依赖。然后用固定问题演示普通 RAG、列表回答、多跳关系、引用和 Trace。

## 1:15 - 1:40 Reliability

这个项目的重点是可解释和可回归。每个答案都有引用，Trace 能看到每个阶段的耗时和 performance warnings。入库任务有 queued、processing、ready、partial、error、cancelled 等状态，也有 retry、cancel、DLQ 和队列健康信息。

## 1:40 - 2:00 Evaluation

最后我会跑 Golden Eval。它不是只看页面效果，而是自动检查 Recall、MRR、Citation Precision、Refusal Accuracy、Behavior Pass、p95 latency 和 performance warnings。这样项目可以持续迭代，而不是靠一次演示碰运气。

## Backup One-Liner

如果只能用一句话介绍：这是一个把 RAG 从“能回答”推进到“可解释、可评估、可运营”的完整工程化样例。
