# 演示材料包

这套 Demo Pack 用来做面试或作品集演示。目标不是覆盖所有功能，而是用少量稳定文档讲清楚项目的完整闭环：上传、异步入库、检索、引用、Trace、质量评估、队列健康和 Golden Eval。

## 使用顺序

1. 启动本地服务：

```powershell
.\scripts\start-dev.ps1
```

2. 打开 `http://127.0.0.1:3000`。
3. 在知识库面板上传 `demo/docs/` 下的 3 份中文文档。
4. 等待文档状态进入 `ready` 或 `partial`。
5. 按 [questions.md](questions.md) 逐个提问。
6. 用 [expected.md](expected.md) 对照引用、Trace 和质量评分。
7. 最后运行 Golden Eval，展示自动化质量门禁：

```powershell
.\scripts\rag-golden-eval.cmd
```

如果只想在演示前快速确认固定材料可用，可以在后端服务启动后运行：

```powershell
.\scripts\demo-pack-smoke.ps1
```

这个脚本会上传 3 份演示文档，提问 3 个关键问题，检查引用和 Trace，然后默认清理上传的演示文档。想保留文档用于继续手动演示时，加 `-KeepDocuments`。

## 文档说明

- [01-commerce-policy.md](docs/01-commerce-policy.md)：退货、发票、物流规则——适合演示基础 RAG 和引用追踪。
- [02-support-operations.md](docs/02-support-operations.md)：客服转人工、运营指标、服务等级——适合演示列表回答和工作流规则。
- [03-graph-relations.md](docs/03-graph-relations.md)：公司、产品、团队、负责人、故障依赖——适合演示图谱关系和多跳推理。

## 演示建议

- 优先使用 [questions.md](questions.md) 里的中文问题，命中更直观。
- 每次回答后打开 Trace 面板，重点展示 retrieval、rank、cite、evaluate 各阶段耗时。
- 如果要讲生产化能力，切到知识库面板展示文档状态、入库队列、DLQ、重试/取消按钮。
- 如果回答不符合 [expected.md](expected.md)，不要临场发散，直接切到 Golden Eval 报告说明项目有自动化回归门禁。
