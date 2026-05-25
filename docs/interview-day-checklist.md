# 面试当天启动清单

这份清单用于正式演示前 10-15 分钟快速确认环境，避免把时间浪费在 Docker、密钥、端口或 GitHub 页面上。

## 1. GitHub 状态

- 打开仓库的 Actions 页面，选择最新的 `CI` run。
- 确认 run 对应当前最新提交，而不是旧提交。
- 确认 `Backend tests`、`Frontend checks`、`Docker Compose config` 三个 job 都是绿色。
- 如果只有黄色 warning，确认不是红色失败；Node/actions deprecation warning 不等于 CI 失败。

## 2. 本地环境

- 提前启动 Docker Desktop，等到 Docker Engine 完全可用。
- 在项目根目录执行：

```powershell
docker info
docker compose ps
```

- 如果 `dockerDesktopLinuxEngine` pipe 不存在，先打开 Docker Desktop，不要直接开始演示。
- 如果不演示容器模式，可以改用本地开发模式，但要明确说明当前不是 Docker Compose runtime。

## 3. 环境变量

- 检查 `.env` 存在且不是模板占位值。
- 确认 `OPENAI_API_KEY`、`API_AUTH_TOKEN`、`NEXT_PUBLIC_API_AUTH_TOKEN`、`CORS_ORIGINS` 已按演示方式配置。
- 如果 API key 曾经暴露过，先在平台侧轮换，再更新本地 `.env`。
- 不要在录屏、截图或终端输出里展示真实 key。

## 4. 演示材料

- 演示文档：`demo/docs/`
- 固定问题：`demo/questions.md`
- 预期结果：`demo/expected.md`
- 讲稿：`demo/interview-script.md`
- 展示说明：`DEMO.md`

建议先上传 `demo/docs/` 下的 3 个文档，再按固定问题提问。回答 wording 可以不同，但引用、Trace 和核心事实要对得上。

## 5. 快速自检

如果后端已经启动，先跑轻量 Demo Pack：

```powershell
.\scripts\demo-pack-smoke.ps1 -KeepDocuments
```

如果要做完整收口检查：

```powershell
.\scripts\check.cmd -SkipGoldenEval
.\scripts\rag-golden-eval.cmd
```

如果演示 Docker Compose runtime：

```powershell
docker compose up --build -d
curl.exe http://127.0.0.1:8000/api/health
curl.exe -I http://127.0.0.1:3000
```

## 6. 演示顺序

1. 先展示 README 的 30 秒项目定位。
2. 打开 `DEMO.md` 的架构图，讲上传到回答的闭环。
3. 上传 demo 文档，展示 queued/processing/ready 状态。
4. 提问固定问题，展示引用、Trace、图谱路径和质量评分。
5. 打开 Golden Eval 或 smoke 报告，说明项目有可回归门禁。
6. 最后展示 GitHub Actions 绿色和 Docker Compose runtime 证据。

## 7. 常见翻车点

- Docker Desktop 没启动，导致 compose/runtime 验证失败。
- `.env` 还是模板值，导致真实 LLM 或容器服务不可用。
- 前端访问地址和后端 CORS/API token 不匹配。
- 只展示页面效果，没有展示引用、Trace 和评测证据。
- GitHub Actions 看的不是最新提交。
