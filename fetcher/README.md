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
| `FETCHER_HTTP_MAX_REDIRECTS` | `5` | Maximum redirects followed per request |
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
| `FETCHER_SSE_MAX_SUBSCRIBERS` | `8` | Maximum concurrent SSE connections (`429` above this) |
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

At most `FETCHER_SSE_MAX_SUBSCRIBERS` connections are accepted. Additional
connections receive `429 Too Many Requests` with a `Retry-After: 30` header and a
JSON body (`{"code":"too_many_subscribers",...}`) even though SSE clients send
`Accept: text/event-stream`. A slot is released when the client disconnects, the
connection times out, or a send fails.

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
  one and must decode as JPEG/PNG/GIF/BMP within the byte and pixel limits.
- Outbound connections are hardened in the HTTP layer, not just by a pre-check:
  the Apache HttpClient 5 transport installs a `DnsResolver` that validates every
  resolution through the same `UrlSafety` policy before returning addresses, so the
  addresses that are checked are the addresses used for the socket. A hostname
  rebound to a loopback/private/link-local/CGNAT address after the pre-check is
  still rejected before any connection is opened. Redirects are followed only for
  HTTP(S) targets with a host and at most `FETCHER_HTTP_MAX_REDIRECTS` times; every
  redirect hop is resolved and validated again by the same resolver. Non-HTTP(S)
  redirect targets are not followed and surface as a non-200 download failure.
- Credential headers are not replayed across origins. A request interceptor runs
  for every redirect hop and removes `Authorization`, `Proxy-Authorization`,
  `Cookie`, and `Cookie2` when the target's scheme, host, or effective port
  differs from the request that started the chain (this includes HTTPS→HTTP
  downgrades and port changes). Same-origin redirects keep the headers,
  non-credential headers such as `User-Agent` and `Accept` are untouched, and once
  a chain has left its original origin credentials stay revoked for the rest of
  that chain.
- SSE events are queued on a dedicated dispatcher, so a slow client cannot block
  the job; failing subscribers are dropped and heartbeats keep idle connections
  alive. The subscriber count is bounded by a semaphore (see `FETCHER_SSE_MAX_SUBSCRIBERS`).

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
- The private-network guard is enforced by the transport's DNS resolver at
  connect time; there is no separate resolve-then-connect gap. Residual behavior:
  the JVM/OS resolver cache can return an answer without a fresh query until its
  TTL expires, but the cached answer was validated before use. The client does not
  use system HTTP proxy settings; if an outbound proxy is introduced later the
  guard must be re-reviewed (a proxy would resolve the target itself).
  `FETCHER_ALLOW_PRIVATE_ADDRESSES=true` disables the guard entirely and is meant
  only for local testing; it also affects the Openverse base URL because the guard
  is applied by the shared transport.
- The SSE subscriber limit is per process. Excess clients get `429` and should
  reconnect after the `Retry-After` delay; with multiple fetcher instances the
  effective limit is per instance, and only the instance that ran the job publishes
  its event.
- The bucket must be creatable by the configured credentials; otherwise the first
  upload fails and the job ends `FAILED`.
- No authentication or rate limiting on the fetch endpoints: this service is meant
  to be internal, called by the Python backend.
