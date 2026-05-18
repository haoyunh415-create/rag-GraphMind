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

The stable demo flow is:

1. Upload a supported document from the chat panel or knowledge-base panel.
2. Wait until the upload result reports indexed chunks.
3. Ask a question in Auto or KB mode.
4. Review the answer, citations, and retrieval trace.

Supported upload extensions are `.pdf`, `.docx`, `.txt`, `.md`, `.markdown`,
`.html`, `.htm`, and `.csv`. The default upload limit is 10 MB and can be
changed with `MAX_UPLOAD_BYTES`.

Each answered turn now runs a deterministic RAG quality evaluation and stores the
result in SQLite. The score covers groundedness, answer relevance, citation
coverage, and retrieval quality. Use `POST /api/kb/evaluate` for direct scoring
or `GET /api/kb/evaluations` to inspect recent saved results.

## Verification

Run backend contract tests from the repository root:

```powershell
.\scripts\test-backend.ps1
```

Run the frontend production build:

```powershell
cd frontend
npm.cmd run build
```

Run the full local E2E smoke test from the repository root:

```powershell
.\scripts\e2e-smoke.ps1
```

The E2E smoke test starts temporary backend and frontend services on isolated
ports, uploads a document, verifies document listing and chunk preview, asks a
grounded KB question, checks citations and trace events, then deletes the test
document and stops the temporary services. Use `-KeepServices` if you want to
leave the temporary services running after the test.

Run the browser UI E2E test from the repository root:

```powershell
.\scripts\ui-e2e.ps1
```

The UI E2E test starts the same isolated services, then uses real Chrome via
Playwright to upload a document through the page, verify the knowledge panel,
ask a grounded question, check visible citations, and confirm the Trace panel
contains a record.

If PowerShell blocks local scripts, run them with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

## Environment Configuration

Local development can run without a `.env` file because the backend has safe
defaults for local ports and the SQLite fallback store. For Docker or
production-like runs, create a real `.env` from the checked-in template:

```powershell
Copy-Item .env.example .env
notepad .env
```

Replace every `replace-me` value before deployment. The most important settings
are:

- `OPENAI_API_KEY`, `OPENAI_MODEL`, `EMBEDDING_MODEL`, and `LLM_BASE_URL` for
  answer generation and embeddings.
- `NEXT_PUBLIC_API_URL` for the browser-visible backend URL.
- `CORS_ORIGINS` for the frontend origins allowed to call the backend.
- `NEO4J_PASSWORD`, `MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY` for the optional
  Docker services.
- `MAX_UPLOAD_BYTES`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `TOP_K`, and
  `RERANK_TOP_K` for ingestion and retrieval behavior.

Run a local environment check from the repository root:

```powershell
.\scripts\env-check.ps1
```

For a stricter production-style check that requires `.env` and rejects
placeholders:

```powershell
.\scripts\env-check.ps1 -RequireEnv -Production
```

If the dev services are already running, also verify backend health, the
frontend page, and the compiled CSS asset:

```powershell
.\scripts\env-check.ps1 -CheckServices
```

## Production Checklist

Before deploying publicly:

- Rotate any leaked or locally used API keys.
- Copy `.env.example` to `.env` and replace every `replace-me` value.
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
Copy-Item .env.example .env
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
