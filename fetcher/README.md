# Java Image Fetcher (Bank MAS Mini Image Search)

Spring Boot service that fetches images from Openverse, stores them in MinIO, and
writes a fetch manifest. Completion is announced over SSE so the Python ingestion
pipeline can start without polling.

Scope of this module (per `docs/rencana-implementasi.md` and `docs/api-spec.md`):

- `POST /api/v1/fetch-jobs` — validate the request, return `202` with a `jobId`,
  run fetching in the background.
- `GET /api/v1/fetch-jobs/{jobId}` — report fetch status/progress.
- `GET /api/v1/events/fetch` — SSE stream; `fetch.completed` is published only
  after the manifest was stored successfully.

This service does **not** embed images, touch Milvus, or serve search results;
those belong to the Python backend and frontend modules.

## Requirements

- Java 21
- Maven 3.9+ (no Maven wrapper is committed; use your local `mvn`)
- A MinIO or S3-compatible endpoint (the application bucket is created on first
  upload if it does not exist)

## Build and test

```bash
cd fetcher
mvn test          # unit tests only; no network, Openverse, or MinIO required
mvn package       # builds target/image-fetcher-0.1.0.jar
```

Tests use fake HTTP transports, fake Openverse/download/storage/SSE
implementations, and generated images. They never call the real Openverse API or
MinIO.

## Run

```bash
cd fetcher
MINIO_ENDPOINT=http://localhost:9000 \
MINIO_ACCESS_KEY=... \
MINIO_SECRET_KEY=... \
mvn spring-boot:run
```

or run the packaged jar:

```bash
java -jar target/image-fetcher-0.1.0.jar
```

The service listens on `${FETCHER_PORT:-8081}`. Fetching never starts
automatically; a job must be submitted explicitly.

## Configuration

Every value can be overridden with an environment variable; defaults live in
`src/main/resources/application.yml`. Secrets are only read from the
environment. When `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` are empty the standard
AWS credential chain is used instead.

| Variable | Default | Purpose |
|---|---|---|
| `FETCHER_PORT` | `8081` | HTTP port |
| `OPENVERSE_BASE_URL` | `https://api.openverse.org` | Openverse API base URL |
| `OPENVERSE_CLIENT_ID` / `OPENVERSE_CLIENT_SECRET` | empty | Optional API credentials; anonymous access when empty |
| `OPENVERSE_PAGE_SIZE` | `20` | Page size per Openverse request |
| `OPENVERSE_MAX_PAGES_PER_CLASS` | `5` | Page cap per class |
| `OPENVERSE_REQUEST_TIMEOUT` | `8s` | Per-attempt timeout |
| `OPENVERSE_MAX_RESPONSE_BYTES` | `2097152` | Max JSON response size |
| `OPENVERSE_MIN_REQUEST_INTERVAL` | `250ms` | Client-side pacing between Openverse calls |
| `OPENVERSE_LICENSE_TYPE` | empty | Optional Openverse `license` filter |
| `FETCHER_HTTP_CONNECT_TIMEOUT` | `10s` | TCP connect timeout |
| `FETCHER_DOWNLOAD_TIMEOUT` | `20s` | Per-attempt image download timeout |
| `FETCHER_DOWNLOAD_MAX_BYTES` | `8388608` | Max image size |
| `FETCHER_DOWNLOAD_MAX_PIXELS` | `25000000` | Max decoded pixel count (decompression-bomb guard) |
| `FETCHER_ALLOW_PRIVATE_ADDRESSES` | `false` | Allow image hosts on private/loopback IPs (local testing only) |
| `FETCHER_OPENVERSE_MAX_ATTEMPTS` | `3` | Retry cap for Openverse calls |
| `FETCHER_DOWNLOAD_MAX_ATTEMPTS` | `2` | Retry cap for image downloads |
| `FETCHER_RETRY_BASE_DELAY` | `500ms` | Exponential backoff base |
| `FETCHER_RETRY_MAX_DELAY` | `8s` | Backoff cap |
| `FETCHER_RETRY_JITTER_RATIO` | `0.25` | Jitter as a fraction of the delay |
| `FETCHER_RETRY_MAX_RETRY_AFTER` | `30s` | Cap applied to upstream `Retry-After` |
| `FETCHER_JOB_WORKER_THREADS` | `2` | Background job worker threads |
| `FETCHER_JOB_QUEUE_CAPACITY` | `32` | Pending job capacity (`503` when full) |
| `FETCHER_MAX_RETAINED_JOBS` | `200` | In-memory job retention |
| `FETCHER_MANIFEST_SAVE_ATTEMPTS` | `2` | Manifest write attempts |
| `FETCHER_SSE_TIMEOUT` | `30m` | SSE connection lifetime before reconnect |
| `FETCHER_SSE_HEARTBEAT_INTERVAL_MS` | `15000` | Heartbeat comment interval |
| `MINIO_ENDPOINT` | `http://localhost:9000` | S3-compatible endpoint |
| `MINIO_REGION` | `us-east-1` | Signing region |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | empty | Static credentials (optional) |
| `MINIO_BUCKET` | `bank-mas` | Application bucket |
| `MINIO_PATH_STYLE_ACCESS` | `true` | Required by MinIO |
| `MINIO_IMAGES_PREFIX` | `images/bank-mas/` | Image object prefix |
| `MINIO_JOBS_PREFIX` | `jobs/bank-mas/` | Job object prefix |

## API

Start a job:

```bash
curl -X POST http://localhost:8081/api/v1/fetch-jobs \
  -H 'Content-Type: application/json' \
  -d '{"classes": ["dog", "cat"], "imagesPerClass": 10}'
# 202 Accepted
# {"jobId":"fetch-20261116-001","status":"FETCHING"}
```

Poll status:

```bash
curl http://localhost:8081/api/v1/fetch-jobs/fetch-20261116-001
# {"jobId":"fetch-20261116-001","fetchStatus":"COMPLETED","requestedClasses":["dog","cat"],
#  "imagesPerClass":10,"downloaded":18,"failedDownloads":2,"duplicates":0,
#  "manifestKey":"jobs/bank-mas/fetch-20261116-001/fetch-manifest.json",
#  "startedAt":"...","completedAt":"...","error":null}
```

Subscribe to completion events:

```bash
curl -N http://localhost:8081/api/v1/events/fetch
# :connected
# id:fetch:fetch-20261116-001:completed
# event:fetch.completed
# data:{"jobId":"fetch-20261116-001","manifestKey":"jobs/bank-mas/fetch-20261116-001/fetch-manifest.json"}
```

Validation limits: 1–50 classes, each 1–64 characters of letters/digits/spaces/
apostrophes/hyphens, no duplicates after trimming and lowercasing; `imagesPerClass`
1–100. Invalid requests return `400` with `{"code": "...", "message": "...", "details": [...]}`;
unknown jobs return `404`; a saturated worker queue returns `503`.

## Storage layout

```
images/bank-mas/{sha256}.{jpg|png|gif|bmp}          # image bytes, key = content hash
jobs/bank-mas/{jobId}/fetch-manifest.json           # fetch manifest (pretty JSON)
```

The manifests contain the image refs (`imageId`, `objectKey`, `contentType`,
`sha256`, `width`, `height`), attribution (`sourceUrl`, `license`, `creator`), and
`evaluationMetadata` (`openverseId`, `requestedClass`, `title`, `tags`) for offline
evaluation. No separate evaluation-metadata object is written to MinIO.

`imageId` and the object key are both derived from the SHA-256 of the stored bytes,
so identical images always map to the same key. Within a job, duplicate URLs and
duplicate bytes are counted and skipped, and an object that already exists in
storage is not uploaded again.

## Behavior details

- Completion sequence: fetch → store manifest → mark job `COMPLETED` → publish SSE.
  If the manifest write fails after its retries, the job becomes `FAILED` and **no
  event** is published.
- Partial failures still produce `COMPLETED` and list the failures in
  `failedDownloads` (`timeout`, `http_404`, `not_an_image`, `image_too_large`,
  `blocked_host`, `openverse_http_503`, …).
- If no image at all can be stored, the manifest is written with status `FAILED`,
  the job becomes `FAILED`, and no event is published.
- Retries cover timeouts, connection errors, HTTP 408/425/429 and 5xx. HTTP 429
  honours `Retry-After` (seconds or HTTP-date) up to the configured cap; other
  retryable failures use exponential backoff with jitter. Client errors (4xx) are
  not retried.
- Openverse requests are paced with `OPENVERSE_MIN_REQUEST_INTERVAL`. Credentials,
  when configured, are exchanged for a cached OAuth2 bearer token; tokens and
  credentials are never logged.
- Downloads must return HTTP 200 with an image content type when the host declares
  one, must decode as JPEG/PNG/GIF/BMP within the byte and pixel limits, and are
  rejected (best effort) when the URL or a redirect target resolves to a
  loopback/private/link-local/CGNAT host.
- SSE events are queued on a dedicated dispatcher, so a slow client cannot block
  the job; failing subscribers are dropped and heartbeats keep idle connections
  alive.

## Limitations

- Job status/history is in memory only. Restarting the service loses running and
  finished jobs from `GET /api/v1/fetch-jobs/{jobId}`; the manifest in MinIO is the
  durable record. The in-memory registry is bounded by `FETCHER_MAX_RETAINED_JOBS`.
- Job ids are generated per process/day (`fetch-YYYYMMDD-NNN`); they are not
  globally coordinated between multiple fetcher instances.
- SSE delivery is not durable and there is no replay. Subscribers are per instance,
  so the Python consumer must use manifest reconciliation as documented in
  `docs/api-spec.md`. Single-instance deployment is assumed for the MVP.
- Fetching runs sequentially per class/candidate per job; parallelism only comes
  from multiple jobs.
- Only formats supported by `javax.imageio` are accepted. WebP, SVG, and TIFF are
  rejected as `unsupported_format`, so some Openverse results are skipped.
- The private-network guard is best effort: DNS is resolved for the check while
  the HTTP client resolves again, so a DNS-rebinding race is theoretically possible.
  Set `FETCHER_ALLOW_PRIVATE_ADDRESSES=true` only for local testing.
- The bucket must be creatable by the configured credentials; otherwise the first
  upload fails and the job ends `FAILED`.
- No authentication or rate limiting on the fetch endpoints: this service is meant
  to be internal, called by the Python backend.
