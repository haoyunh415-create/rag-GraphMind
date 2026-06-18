# 项目启动完整指南

## 前置条件

| 依赖 | 验证命令 |
|------|---------|
| Python 3.12+ | `python --version` |
| Node.js 18+ | `node --version` |
| Docker Desktop | `docker info`（确认 daemon 在运行） |
| npm 依赖已安装 | 检查 `frontend/node_modules/` 存在 |
| Python venv 已创建 | 检查 `backend/venv/Scripts/python.exe` 存在 |

---

## 方式一：本地开发（日常使用，推荐）

适合开发调试，后端直连 Docker 里的基础设施服务。

### 第一步：启动基础设施服务

```powershell
cd C:\Users\32639\rag-platform
docker compose up -d elasticsearch neo4j redis minio etcd milvus
```

等待所有容器变为 healthy：

```powershell
docker compose ps
# 确认 elasticsearch、neo4j、milvus、minio、redis 都是 healthy
```

### 第二步：确保 .env 关键配置正确

检查 `C:\Users\32639\rag-platform\.env`：

```env
# LLM
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1

# Embedding (本地模型)
EMBEDDING_MODEL=all-MiniLM-L6-v2

# 基础设施 (连 Docker 容器)
ES_HOST=http://localhost:9200
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=password
REDIS_URL=redis://localhost:6379/0

# 入库模式（本地必须用 inline）
INGESTION_QUEUE_MODE=inline
```

### 第三步：启动开发服务器

```powershell
.\scripts\start-dev.ps1
```

等待输出：

```
Ready:
  Frontend: http://127.0.0.1:3000
  Backend:  http://127.0.0.1:8001
```

### 第四步：验证

```powershell
# 冒烟测试
.\scripts\smoke-test.ps1

# Golden Eval
.\scripts\rag-golden-eval.cmd
```

### 停止

```powershell
# 停止开发服务：关闭弹出的 PowerShell 窗口，或：
Get-NetTCPConnection -LocalPort 8001,3000 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# 停止基础设施
docker compose down
```

---

## 方式二：Docker 完整部署（模拟生产环境）

适合想体验完整生产环境的场景。

```powershell
cd C:\Users\32639\rag-platform

# 1. 先创建 .env
Copy-Item .env.example .env
# 编辑 .env，把 replace-me 换成真实密钥

# 2. 检查配置
.\scripts\deploy-check.ps1

# 3. 一键启动全部 8 个服务
docker compose up --build -d

# 4. 等待健康检查通过
docker compose ps

# 5. 验证
# 前端: http://localhost:3000
# API:  http://localhost:8000/api/health
# 文档: http://localhost:8000/api/docs
```

> **注意**：Docker 模式下 API 端口是 8000（不是 8001）。

---

## 方式三：纯本地（不需要 Docker）

如果只想快速跑起来，不依赖外部服务：

1. 确保 `.env` 里 `ES_HOST`、`NEO4J_URI`、`REDIS_URL` 都指向不可达地址（让系统自动降级）
2. 直接运行 `.\scripts\start-dev.ps1`
3. 向量检索降级为本地 SQLite + TF-IDF，BM25 和图谱自动跳过

---

## 演示流程（面试用）

1. 启动服务（方式一）
2. 打开 http://127.0.0.1:3000
3. 知识库面板上传 `demo/docs/` 下 3 份中文文档（**一个一个上传**）
4. 等待文档状态变为 `已就绪` 或 `部分就绪`
5. 按 `demo/questions.md` 逐个提问
6. 每次回答后展开 Trace 面板
7. 最后跑 `.\scripts\rag-golden-eval.cmd`

---

## 常见问题速查

| 问题 | 解决 |
|------|------|
| 文档一直"排队中" | `.env` 里加 `INGESTION_QUEUE_MODE=inline` |
| BM25 0 条结果 | 检查 ES 容器是否 healthy，索引是否用了 `cjk` 分词器 |
| 图谱 0 条结果 | 检查 Neo4j 容器，确认 `NEO4J_PASSWORD` 正确 |
| 向量入库失败 | 三个文档逐个上传，不要同时拖多个 |
| 端口被占用 | 关掉占用 3000/8001 的进程，或改 start-dev.ps1 参数 |
| Docker 连不上 | 启动 Docker Desktop，等右下角图标变绿 |
