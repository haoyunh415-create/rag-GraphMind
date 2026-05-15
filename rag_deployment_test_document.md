# 星河知识库平台部署测试文档

## 项目概述

星河知识库平台是一个面向企业内部资料检索的 RAG 系统。它支持上传文档、自动切片、向量检索、全文检索、知识图谱增强检索和流式问答。

平台的前端服务运行在 3000 端口，后端 API 服务运行在 8000 端口。后端健康检查地址是 `/api/health`，API 文档地址是 `/api/docs`。

## 部署环境

本次测试部署使用 Docker Compose 启动服务。核心容器包括：

- web：Next.js 前端服务
- api：FastAPI 后端服务
- milvus：向量数据库
- elasticsearch：全文搜索引擎
- neo4j：知识图谱数据库
- minio：对象存储服务
- redis：缓存服务

部署成功的判断标准是 `docker compose ps` 中主要服务显示为 healthy，并且浏览器可以打开 `http://localhost:3000`。

## 测试账号与权限

本测试文档不包含真实用户密码。生产环境必须使用强密码，并且不能把 `.env` 文件提交到 GitHub。

管理员负责配置环境变量，测试人员负责上传文档、发起问答和检查引用来源。

## 标准测试流程

1. 打开前端页面 `http://localhost:3000`。
2. 进入知识库页面。
3. 上传本文档。
4. 等待系统显示上传成功。
5. 在对话页面提问。
6. 检查回答是否引用本文档中的内容。
7. 打开追踪页面，确认可以看到检索过程。
8. 删除测试文档，确认知识库列表更新。

## 关键事实

平台名称是星河知识库平台。

前端默认访问地址是 `http://localhost:3000`。

后端默认访问地址是 `http://localhost:8000`。

健康检查接口是 `/api/health`。

本地部署推荐使用 Docker Compose。

如果 Neo4j 容器出现 unhealthy，需要优先查看 `docker compose logs neo4j`。

如果 Docker 报 `dockerDesktopLinuxEngine` 找不到，通常说明 Docker Desktop 没有启动。

## 验收问题

可以用下面这些问题测试 RAG 是否正确检索到了本文档：

1. 星河知识库平台的前端默认运行在哪个端口？
2. 后端健康检查接口是什么？
3. 这个系统用了哪些核心容器？
4. 如果 Neo4j unhealthy，应该先查看什么日志？
5. 如果 Docker 提示 dockerDesktopLinuxEngine 找不到，通常是什么原因？
6. 上传文档后的标准测试流程是什么？

## 预期答案摘要

前端默认端口是 3000，后端默认端口是 8000。

健康检查接口是 `/api/health`。

核心容器包括 web、api、milvus、elasticsearch、neo4j、minio 和 redis。

Neo4j unhealthy 时应先查看 `docker compose logs neo4j`。

dockerDesktopLinuxEngine 找不到通常说明 Docker Desktop 没有启动或 Docker Linux engine 没有运行。
