# Graph RAG Platform

Full-stack RAG application with a FastAPI backend, Next.js frontend, local SQLite vector fallback, and optional Milvus, Elasticsearch, Neo4j, MinIO, and Redis services.

## Local Development

```powershell
.\scripts\start-dev.ps1
.\scripts\smoke-test.ps1
```

The smoke test checks backend health, frontend assets, document upload,
chunk listing, KB-mode streaming chat, RAG evaluation metrics, and cleanup.
Use `-SkipUpload` when you only want a fast health and frontend check.

The development script starts:

- Frontend: `http://127.0.0.1:3000`
- Backend: `http://127.0.0.1:8001`

If PowerShell blocks local scripts, run them with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

## Production Checklist

Before deploying publicly:

- Rotate any leaked or locally used API keys.
- Copy `.env.production.example` to `.env` and replace every `replace-me` value.
- Set `DEBUG=false`.
- Set `NEXT_PUBLIC_API_URL` to the public backend URL.
- Set `CORS_ORIGINS` to the public frontend origin only.
- Use strong `NEO4J_PASSWORD`, `MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY` values.
- Run `.\scripts\deploy-check.ps1`.
- Run a full container smoke test after `docker compose up --build`.

## Docker

```powershell
docker compose up --build
```

The default compose file is production-leaning: it builds images without mounting local source directories into the containers, requires secret values from `.env`, starts containers with restart policies, and exposes health checks for `api` and `web`.

For a production host:

```powershell
Copy-Item .env.production.example .env
notepad .env
.\scripts\deploy-check.ps1
docker compose up --build -d
docker compose ps
```

After containers are healthy, verify:

- Frontend: `http://localhost:3000`
- Backend health: `http://localhost:8000/api/health`
- API docs: `http://localhost:8000/api/docs`

The backend image intentionally does not install `sentence-transformers` by default. Use OpenAI-compatible embedding models such as `text-embedding-3-small`, or add `sentence-transformers` back to `backend/requirements.txt` if you need local transformer embeddings inside the container.
