# Production Notes

这份说明用于面试、作品集评审和上线前自查。它刻意不把项目包装成“已经覆盖所有企业场景”，而是清楚说明当前已经具备的生产化能力、部署时必须配置的内容，以及暂时保留的边界。

## 当前已具备

- API Key 鉴权：后端通过 `API_AUTH_TOKEN` 校验业务接口，前端通过 `NEXT_PUBLIC_API_AUTH_TOKEN` 传递同一 token。
- CORS 收紧：通过 `CORS_ORIGINS` 明确允许的前端来源。
- 上传安全：限制文件类型、文件名清理和单文件大小上限。
- 异步入库：本地支持 `auto/inline` 降级，生产建议使用 Redis worker。
- 文档状态机：支持 `queued / processing / ready / partial / error / duplicate / cancelled`。
- 失败运营：支持 retry、cancel、DLQ、队列健康和失败任务摘要。
- 可解释问答：回答带 citation，Trace 展示检索、重排、引用、生成、评估和性能告警。
- 质量门禁：Golden Eval 检查质量指标、p95 latency 和 `performance_warnings`。
- 容器运行：Docker Compose 包含 api、web、ingestion-worker、Redis、Milvus、Neo4j、Elasticsearch 和 MinIO。

## 生产部署必须配置

部署前至少确认这些环境变量：

- `DEBUG=false`
- `CORS_ORIGINS=https://your-frontend.example.com`
- `API_AUTH_TOKEN=<strong-secret>`
- `NEXT_PUBLIC_API_AUTH_TOKEN=<same-secret>`
- `OPENAI_API_KEY=<real-key>`
- `OPENAI_MODEL=<chosen-model>`
- `EMBEDDING_MODEL=<chosen-embedding-model>`
- `NEO4J_PASSWORD=<strong-secret>`
- `MINIO_ACCESS_KEY=<strong-secret>`
- `MINIO_SECRET_KEY=<strong-secret>`
- `NEXT_PUBLIC_API_URL=https://your-api.example.com`
- `INGESTION_QUEUE_MODE=redis`

建议先运行：

```powershell
.\scripts\env-check.ps1 -RequireEnv -Production
docker compose config --quiet
```

## 上线前验证

最小验证：

```powershell
.\scripts\check.cmd -SkipGoldenEval
.\scripts\rag-golden-eval.cmd
```

服务启动后验证：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\e2e-smoke.ps1
```

演示材料验证：

```powershell
.\scripts\demo-pack-smoke.ps1
```

## 当前边界

- 还没有多租户权限模型：当前 API Key 适合单团队或演示环境，不适合直接做多客户隔离。
- 还没有细粒度审计日志：Trace 主要用于 RAG 链路解释和性能排查，不等同于合规审计。
- 还没有限流和配额：公开部署时建议在网关层补 rate limit。
- 还没有 HTTPS 终止配置：生产环境应由反向代理或云服务提供 TLS。
- 还没有完整备份策略：SQLite registry、Neo4j、Milvus、MinIO 和 Redis 持久化数据需要独立备份方案。
- Redis 队列不是 Celery/RQ：当前实现足够演示和轻量生产化，但复杂调度、优先级、多队列治理可以作为后续演进。

## 面试讲法

可以这样解释边界：

> 我没有把这个项目做成无限扩张的企业平台，而是优先打完整 RAG 工程闭环。当前重点是可解释、可评估、可运营；多租户、审计、限流和备份属于真实上线前的下一层平台能力，我已经在生产说明里列出边界和补齐路径。
