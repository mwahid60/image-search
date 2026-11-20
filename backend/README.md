# Bank MAS Image Search — Python Backend

FastAPI service for the Bank MAS mini image search assignment. It owns:

- fetch orchestration proxy to the Java Spring Boot fetcher,
- SSE consumer for `fetch.completed` (primary ingestion trigger),
- idempotent ingestion: MinIO fetch manifest → llama.cpp embeddings → Milvus,
- vector-only search + image bytes proxy,
- reconciliation fallback (startup + periodic) and manual ingestion retry.

Java remains responsible for Openverse, downloads, MinIO image objects, manifests, and SSE.
Python never calls Openverse, and search never calls Java or Openverse.

## Architecture

```text
Streamlit
   │  POST /api/v1/fetch-jobs           (proxy)
   ▼
FastAPI ──────────────► Java Spring Boot ──► Openverse / image hosts
   │                          │
   │  GET /api/v1/events/fetch│  fetch.completed (eventId, jobId, manifestKey)
   │◄─────────────────────────┘
   ▼
Ingestion pipeline ──► MinIO (manifest + image bytes)
   │                        jobs/bank-mas/{jobId}/fetch-manifest.json
   │                        images/bank-mas/{sha256}.{ext}
   ├──► llama.cpp POST /v1/embeddings (image bytes only | text query)
   └──► Milvus Lite image_embeddings (vector 768d COSINE + technical/attribution
                                    metadata + evaluation_metadata JSON)

Search: POST /api/v1/search → text embedding → Milvus top-k → attribution only
Image:  GET /api/v1/images/{imageId} → Milvus object_key → MinIO bytes
```

### File layout

```text
backend/
├── app/
│   ├── main.py                     # app factory, lifespan, structured errors
│   ├── config.py                   # env-driven settings (no credentials in code)
│   ├── container.py                # client wiring, background task lifecycle
│   ├── errors.py                   # AppError hierarchy → JSON error responses
│   ├── utils.py                    # job/image id validation, sha256, L2 norm, clock
│   ├── api/
│   │   ├── routes_fetch.py         # POST /api/v1/fetch-jobs, GET /api/v1/fetch-jobs/{id}
│   │   ├── routes_ingest.py        # POST /api/v1/ingest/{id}
│   │   ├── routes_search.py        # POST /api/v1/search
│   │   ├── routes_images.py        # GET /api/v1/images/{imageId}
│   │   ├── routes_health.py        # GET /health, GET /ready
│   │   └── schemas.py              # request/response models
│   ├── integrations/
│   │   ├── java.py                 # Java fetcher HTTP client
│   │   ├── sse.py                  # SSE parser + reconnecting consumer
│   │   ├── object_store.py         # MinIO/S3 client behind ObjectStore protocol
│   │   ├── embeddings.py           # llama.cpp /v1/embeddings client
│   │   └── vector_store.py         # Milvus Lite client behind VectorStore protocol
│   ├── ingestion/
│   │   ├── pipeline.py             # manifest → validate → embed → upsert → status
│   │   ├── queue.py                # single in-memory worker + job dedup
│   │   ├── reconciliation.py       # startup/periodic manifest scan
│   │   └── images.py               # Pillow decode/validation
│   ├── models/                     # manifest, ingestion status, vector records
│   └── services/                   # aggregated fetch status, search service
└── tests/                          # fakes for all external services
```

## Requirements

- Python 3.12+ (uv resolves it automatically)
- Java fetcher running (for fetching + SSE), MinIO, llama.cpp `llama-server` with
  `embeddinggemma-2-UD-Q4_K_XL.gguf` + `mmproj-Q8_0.gguf`, and Milvus Lite (embedded,
  no server needed).

## Configuration

All settings come from environment variables (or `backend/.env`). Defaults are safe
local-development values; credentials have **no defaults**.

| Variable | Default | Purpose |
|---|---|---|
| `JAVA_BASE_URL` | `http://localhost:8081` | Java fetcher base URL |
| `JAVA_HTTP_TIMEOUT_SECONDS` | `10` | Java proxy/status timeout |
| `JAVA_EVENTS_PATH` | `/api/v1/events/fetch` | SSE endpoint |
| `SSE_ENABLED` | `true` | Must stay `true` in normal operation |
| `SSE_RECONNECT_INITIAL_SECONDS` | `1` | Initial exponential backoff |
| `SSE_RECONNECT_MAX_SECONDS` | `30` | Max backoff |
| `SSE_READ_TIMEOUT_SECONDS` | `120` | Heartbeat-safe stream read timeout |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO/S3 endpoint (no scheme) |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | unset | Credentials — never commit |
| `MINIO_SECURE` | `false` | TLS for MinIO |
| `MINIO_BUCKET` | `bank-mas-image-search` | Application bucket |
| `MINIO_ENSURE_BUCKET` | `true` | Create bucket at startup if missing |
| `MINIO_POOL_MAXSIZE` | `32` | urllib3 connection pool size for MinIO (blocking, for concurrent image reads) |
| `IMAGE_PREFIX` | `images/bank-mas/` | Required image object prefix |
| `JOBS_PREFIX` | `jobs/bank-mas/` | Required job object prefix |
| `LLAMA_BASE_URL` | `http://localhost:8080` | llama.cpp server |
| `LLAMA_MODEL` | `embeddinggemma-2` | `model` field in embeddings request |
| `LLAMA_TIMEOUT_SECONDS` | `120` | Embedding timeout |
| `EMBEDDING_DIMENSION` | `768` | Validated for every vector |
| `EMBEDDING_MAX_ATTEMPTS` | `5` | Attempts for 503/timeout/connection failures |
| `EMBEDDING_RETRY_INITIAL_SECONDS` / `EMBEDDING_RETRY_MAX_SECONDS` | `1` / `15` | Exponential backoff bounds |
| `QUERY_PROMPT_PREFIX` | `task: search result \| query: ` | EmbeddingGemma query task prefix |
| `MILVUS_DB_PATH` | `data/milvus.db` | Milvus Lite database file path (`/data/` is git-ignored) |
| `MILVUS_TOKEN` | unset | Only for a Milvus server that requires authentication; leave unset for Lite |
| `MILVUS_COLLECTION` | `image_embeddings` | Collection name |
| `SEARCH_DEFAULT_TOP_K` / `SEARCH_MAX_TOP_K` | `10` / `50` | Search limits |
| `INGEST_BATCH_SIZE` | `4` | Images per embedding/upsert batch |
| `INGEST_MAX_IMAGE_BYTES` | `15728640` | Per-image file size guard |
| `INGEST_MAX_IMAGE_PIXELS` | `25000000` | Per-image pixel guard (checked before full decode) |
| `INGEST_EMBEDDING_MAX_DIMENSION` | `1024` | Embedding copy max side; larger images are downscaled locally |
| `RECONCILIATION_ENABLED` | `true` | Periodic fallback scan |
| `RECONCILIATION_ON_STARTUP` | `true` | Startup fallback scan |
| `RECONCILIATION_INTERVAL_SECONDS` | `300` | Periodic interval |
| `RECONCILIATION_RETRY_FAILED` | `false` | Auto-retry `FAILED` ingestion |
| `RECONCILIATION_MAX_ATTEMPTS` | `3` | Automatic retry cap per job (manual retry bypasses) |
| `MAX_CLASSES_PER_REQUEST` / `MAX_IMAGES_PER_CLASS` | `50` / `50` | Proxy validation |

`backend/.env.example` lists the same keys.

Milvus configuration is intentionally split: `MILVUS_DB_PATH` points to the local
Milvus Lite database file, while `MILVUS_URI` is left to pymilvus and is only valid
for an http(s) Milvus server. The backend never reads or clears `MILVUS_URI`.

## Running

```bash
cd backend
uv sync
# Java + MinIO + llama.cpp embeddings server must be reachable first.
uv run python -m app.main        # uvicorn on 0.0.0.0:8000
# or:
uv run uvicorn app.main:app --port 8000
```

The app starts even when a dependency is down (SSE/queue/reconciliation still run);
`GET /ready` then reports `503` with the failing component instead of crashing the API.

## API

Interactive docs: `http://localhost:8000/docs`.

- `POST /api/v1/fetch-jobs` — body `{"classes": ["dog"], "imagesPerClass": 20}`,
  forwards to Java, returns `202 {"jobId": "...", "status": "FETCHING"}`.
- `GET /api/v1/fetch-jobs/{jobId}` — aggregates Java status, fetch manifest
  (downloads/failures), and ingestion status
  (`PENDING|INGESTING|INDEXED|PARTIAL|FAILED`).
- `POST /api/v1/ingest/{jobId}` — idempotent manual retry; `202` with
  `ENQUEUED` or `ALREADY_RUNNING`; `404 MANIFEST_NOT_FOUND` when Java has not written
  the manifest yet.
- `POST /api/v1/search` — body `{"query": "...", "topK": 10}`; returns
  `imageId`, `score`, `imageUrl`, `objectKey`, `contentType`, `sourceUrl`, `license`,
  `creator`, `width`, `height`. Never returns evaluation metadata.
- `GET /api/v1/images/{imageId}` — image bytes with the stored content type.
- `GET /health` — liveness. `GET /ready` — Milvus, MinIO, llama.cpp, SSE consumer
  snapshot, queue and last reconciliation.

Errors use `{"error": {"code": "...", "message": "...", "details": ...}}`.

### Ingestion flow

1. SSE `fetch.completed` (`eventId`, `jobId`, `manifestKey`) is validated
   (`manifestKey` must equal `jobs/bank-mas/{jobId}/fetch-manifest.json`).
   Duplicate event IDs and active/completed jobs are ignored.
2. Job enters the single in-memory worker. Reconciliation and manual retry share
   the same queue, so a job is never ingested twice in parallel.
3. Manifest is read from MinIO; each image object must live under `images/bank-mas/`.
4. Bytes are decoded with Pillow, pixel/size-limited, hashed with SHA-256
   (`imageId` must match). Images whose longest side exceeds
   `INGEST_EMBEDDING_MAX_DIMENSION` are EXIF-orientation corrected and downscaled
   to a JPEG (or PNG when alpha is present) **for embedding only**; the original
   MinIO object, hash, and metadata are unchanged. Only that copy is embedded,
   then L2-normalized.
5. llama.cpp requests retry HTTP 503, timeouts, and connection errors up to
   `EMBEDDING_MAX_ATTEMPTS` with exponential backoff. 400/422 and other statuses
   are never retried; payload bytes are never logged.
6. Milvus upsert is keyed by `image_id`; `created_at` is preserved on re-ingestion.
   `indexed` counts newly upserted images, `duplicates` counts already-indexed
   images separately (duplicates do not increase `indexed`).
7. `jobs/bank-mas/{jobId}/ingestion-status.json` is written (`INGESTING` at start,
   then `INDEXED` when every usable image succeeded, `PARTIAL` when at least one
   failed but `indexed + duplicates > 0`, `FAILED` when nothing is usable).
   Per-image errors never abort the whole job. Each attempt increments the persisted
   `attempts` counter.

### Retry and recovery bounds

Reconciliation treats `PARTIAL` as unfinished and retries it (also stale
`PENDING`/`INGESTING`, and `FAILED` when `RECONCILIATION_RETRY_FAILED=true`) while
`attempts < RECONCILIATION_MAX_ATTEMPTS` (default 3). Because every ingestion
attempt increments the persisted counter and reconciliation runs on its configured
interval, a permanent failure stops automatically instead of looping. Manual
`POST /api/v1/ingest/{jobId}` always bypasses the automatic limit; retries skip
already-indexed images, so only missing work is re-embedded. Legacy `INDEXED` records
with `failed > 0` are treated as `PARTIAL` so the job created by the old status bug
is recoverable.

`evaluation_metadata` (title, tags, requested class, Openverse fields) is copied
verbatim from the manifest into Milvus **only for offline evaluation**. It is never
sent to the embedding model, filtered, ranked, reranked, or returned by search.
Search is pure cosine vector similarity.

## Tests and lint

```bash
cd backend
uv run pytest
uv run ruff check app tests
uv run ruff format --check app tests
```

Tests use in-memory fakes for MinIO, Java, llama.cpp, and Milvus; no external
service is required. `tests/test_milvus_lite.py` exercises the real embedded Milvus
Lite engine when available and skips when the local engine cannot start.

## Known limitations

- In-memory runtime status (queue/completed-job cache) is lost on restart; MinIO
  manifest + `ingestion-status.json` remain authoritative and reconciliation
  re-enqueues unfinished work until the attempt cap is reached.
- SSE has no durable replay; reconciliation is the fallback, not a replacement.
- Single ingestion worker and single llama.cpp server target assignment scale.
- Embeddings of already indexed images are skipped on retry (duplicates counted);
  a model change requires a separate re-embedding strategy.
- Java restart loses its in-memory fetch job status; the Python aggregate then falls
  back to manifest data and reports a warning.
- Milvus Lite index is `FLAT`; no scalar index is created for `evaluation_metadata`.
- `RECONCILIATION_RETRY_FAILED=false` by default, so `FAILED` jobs need a manual
  `POST /api/v1/ingest/{jobId}` (or enable retry). `PARTIAL` jobs are retried
  automatically until `RECONCILIATION_MAX_ATTEMPTS`; manual retry is always allowed.
- Downscaling for embedding can still require one full decode of a pixel-limited
  image (JPEG uses draft decoding where possible); the source object in MinIO is
  never altered.
