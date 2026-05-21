# Commit Plan

当前工作区包含多轮优化，建议拆成 4 个主题提交。这样 GitHub 历史更容易读，面试官也能看出项目是按工程主线推进的，而不是一次性堆功能。

## 1. Security and Deployment Hardening

建议提交信息：

```text
feat: harden api security and deployment config
```

包含内容：

- API Key 鉴权、CORS 收紧、上传安全。
- `.env.example` 和 `.env.production.example` 的生产配置说明。
- Docker Compose 中 API、Web 和 worker 的环境变量对齐。
- 前端 Docker 构建注入 `NEXT_PUBLIC_API_AUTH_TOKEN`。
- 环境检查脚本更新。

建议文件：

```text
.env.example
.env.production.example
backend/app/core/config.py
backend/app/core/security.py
backend/app/main.py
backend/app/api/chat.py
backend/app/api/documents.py
docker-compose.yml
frontend/Dockerfile
frontend/src/lib/api.ts
scripts/env-check.ps1
```

## 2. Retrieval Quality, Trace, and Evaluation

建议提交信息：

```text
feat: add retrieval quality gates and trace warnings
```

包含内容：

- Reranker、citation pruning、retrieval health cache。
- Latency Trace 分段耗时、性能预算和 performance warnings。
- Golden Eval 指标门禁和评测集。
- Trace UI 增强和前端问答展示适配。

建议文件：

```text
backend/app/agents/orchestrator.py
backend/app/agents/tools.py
backend/app/api/knowledge_base.py
backend/app/core/observability.py
backend/app/evaluation/store.py
backend/app/models/schemas.py
backend/app/retrieval/citation_pruner.py
backend/app/retrieval/fusion.py
backend/app/retrieval/health.py
backend/app/retrieval/reranker.py
backend/app/retrieval/vector_store.py
backend/tests/test_api_contract.py
backend/tests/test_citation_pruner.py
backend/tests/test_fusion.py
backend/tests/test_observability.py
backend/tests/test_retrieval_health.py
eval/golden-rag-cases.json
frontend/src/components/chat/ChatPanel.tsx
frontend/src/components/observability/TracePanel.tsx
frontend/tests/ui/rag-flow.spec.ts
scripts/e2e-smoke.ps1
scripts/rag-eval-suite.ps1
scripts/rag-golden-eval.cmd
scripts/rag-golden-eval.ps1
scripts/smoke-test.ps1
```

## 3. Async Ingestion Operations

建议提交信息：

```text
feat: operationalize async document ingestion
```

包含内容：

- 文档状态机字段增强。
- Redis ingestion queue、worker、retry、cancel、DLQ。
- 队列健康、失败任务摘要和文档重试 API。
- 知识库面板展示文档状态、attempt、retry/cancel 和队列健康。

建议文件：

```text
backend/app/api/documents.py
backend/app/ingestion/jobs.py
backend/app/ingestion/pipeline.py
backend/app/ingestion/queue.py
backend/app/ingestion/worker.py
backend/app/models/schemas.py
backend/tests/test_ingestion_queue.py
frontend/src/components/knowledge/UploadPanel.tsx
frontend/src/lib/api.ts
scripts/test-backend.cmd
scripts/test-backend.ps1
```

注意：`backend/app/api/documents.py`、`backend/app/models/schemas.py`、`frontend/src/lib/api.ts` 同时涉及安全和 ingestion。如果要严格拆 commit，需要按 hunk 分阶段 staging；如果不想细拆，可以把第 1 和第 3 合并成一个生产化提交。

## 4. Demo, Documentation, and CI

建议提交信息：

```text
docs: add demo pack and project readiness docs
```

包含内容：

- README 首屏作品集化。
- `DEMO.md`、Demo Pack、标准问题、预期结果和 2 分钟讲稿。
- Demo Pack smoke 脚本。
- 生产边界说明。
- GitHub Actions CI。
- 编辑器和前端 lint/typecheck 基础配置。

建议文件：

```text
.editorconfig
.github/workflows/ci.yml
DEMO.md
README.md
demo/
docs/commit-plan.md
docs/production-notes.md
frontend/.eslintrc.json
frontend/package.json
frontend/package-lock.json
scripts/check.cmd
scripts/check.ps1
scripts/demo-pack-smoke.ps1
```

## Pre-Commit Verification

推荐提交前至少运行：

```powershell
git diff --check
.\scripts\check.cmd -SkipGoldenEval
.\scripts\rag-golden-eval.cmd
```

如果本地后端已经启动，再跑：

```powershell
.\scripts\demo-pack-smoke.ps1
```

如果要验证完整端到端：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\e2e-smoke.ps1
```
