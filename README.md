# Graph RAG Platform

Full-stack RAG application with a FastAPI backend, Next.js frontend, local SQLite vector fallback, and optional Milvus, Elasticsearch, Neo4j, MinIO, and Redis services.

## Local Development

```powershell
.\scripts\start-dev.ps1
.\scripts\smoke-test.ps1
```

The development script starts:

- Frontend: `http://127.0.0.1:3000`
- Backend: `http://127.0.0.1:8001`

## Production Checklist

Before deploying publicly:

- Rotate any leaked or locally used API keys.
- Copy `.env.example` to `.env` and replace every `replace-me` value.
- Set `DEBUG=false`.
- Set `NEXT_PUBLIC_API_URL` to the public backend URL.
- Set `CORS_ORIGINS` to the public frontend origin only.
- Replace default Neo4j and MinIO credentials.
- Run `npm.cmd run build` from `frontend`.
- Run `.\venv\Scripts\python.exe -m unittest discover -s tests` from `backend`.
- Run `docker compose config --services`.
- Run a full container smoke test after `docker compose up --build`.

## Docker

```powershell
docker compose up --build
```

The default compose file is production-leaning: it builds images without mounting local source directories into the containers. Use `scripts/start-dev.ps1` for local hot-reload development.

The backend image intentionally does not install `sentence-transformers` by default. Use OpenAI-compatible embedding models such as `text-embedding-3-small`, or add `sentence-transformers` back to `backend/requirements.txt` if you need local transformer embeddings inside the container.
