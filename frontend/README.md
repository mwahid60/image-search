# Streamlit Frontend — Bank MAS Image Search

Frontend dua halaman untuk sistem mini image search:

1. **Fetch Images** — memulai fetch job, memantau fetching/ingestion, dan
   menyediakan tombol **Retry Ingestion**.
2. **Search** — pencarian bahasa alami dengan image grid, skor opsional, dan
   atribusi creator/license/source URL.

Frontend hanya memanggil Python FastAPI. Tidak ada akses langsung ke Java,
Openverse, MinIO, Milvus, atau llama.cpp dari sini.

## Struktur

```text
frontend/
├── app.py                        # entrypoint st.navigation
├── image_search/
│   ├── api_client.py             # HTTP client + parsing respons ketat
│   ├── config.py                 # konfigurasi environment
│   ├── formatting.py             # format skor, atribusi, URL, pesan error
│   ├── runtime.py                # wiring config + cached client Streamlit
│   ├── state.py                  # validasi input + fase status (pure)
│   └── views/
│       ├── fetch_images.py
│       └── search.py
├── tests/
│   ├── test_api_client.py
│   ├── test_config.py
│   ├── test_formatting.py
│   ├── test_pages_smoke.py
│   └── test_state.py
└── pyproject.toml
```

## Setup

Prasyarat: Python 3.12+ dan [uv](https://docs.astral.sh/uv/).

```bash
cd frontend
uv sync
```

Konfigurasi lewat environment variable:

| Variable | Default | Keterangan |
|---|---|---|
| `API_BASE_URL` | `http://localhost:8000` | Base URL Python FastAPI untuk request server-side dari Streamlit |
| `PUBLIC_API_BASE_URL` | mengikuti `API_BASE_URL` | Base URL Python FastAPI yang dipakai browser untuk mengambil gambar hasil search |
| `API_TIMEOUT_SECONDS` | `30` | Timeout fetch/status/ingest (detik) |
| `API_SEARCH_TIMEOUT_SECONDS` | `60` | Timeout search; embedding teks di CPU bisa lebih lambat |
| `FRONTEND_POLL_INTERVAL_SECONDS` | `3` | Jeda antar polling status |
| `FRONTEND_MAX_AUTO_POLLS` | `150` | Batas polling otomatis per job (± 7 menit pada jeda 3 detik) |

Contoh lokal:

```bash
API_BASE_URL=http://localhost:8000 uv run streamlit run app.py
```

Contoh ala compose, saat Streamlit memanggil backend lewat nama service tetapi
browser berjalan di host:

```bash
API_BASE_URL=http://backend:8000 \
PUBLIC_API_BASE_URL=http://127.0.0.1:8000 \
uv run streamlit run app.py
```

Jika `PUBLIC_API_BASE_URL` tidak diisi, nilainya mengikuti `API_BASE_URL`
sehingga konfigurasi lama tetap berjalan. Keduanya divalidasi sebagai URL
http(s) lengkap.

Buka URL yang ditampilkan Streamlit (default `http://localhost:8501`).

## Perilaku halaman Fetch Images

- Text area kelas terisi 20 kelas kandidat dari `docs/rencana-implementasi.md`
  dan tetap bisa diedit. Input dipisah baris/koma/titik koma, duplikat
  dihapus, dan divalidasi (maksimal 60 kelas, 64 karakter per nama).
- `imagesPerClass` dibatasi 1–50 di UI. Backend tetap sumber validasi final;
  error validasi backend ditampilkan apa adanya.
- Tombol **Fetch Images** memanggil `POST /api/v1/fetch-jobs` dan menampilkan
  `jobId`. Setelah itu status dibaca dari `GET /api/v1/fetch-jobs/{jobId}`.
- Fase yang ditampilkan: fetching, menunggu ingestion, ingesting, indexed,
  selesai dengan sebagian kegagalan (partial), ingestion gagal, fetching gagal.
  Status backend yang tidak dikenal ditampilkan apa adanya, bukan ditebak.
- Jumlah downloaded/failedDownloads/indexed/duplicates hanya ditampilkan jika
  backend mengirimnya. Tidak ada progress atau statistik buatan.
- `errors` dari backend boleh berupa string atau objek `IngestionError`
  (`imageId`, `objectKey`, `stage`, `reason`). Objek diformat menjadi satu baris
  yang memuat tahap, alasan, dan identifier bila tersedia; metadata evaluasi
  tidak pernah ditampilkan.
- **Retry Ingestion** memanggil `POST /api/v1/ingest/{jobId}` dan hanya muncul
  saat fetching sudah selesai dan ingestion tidak sedang berjalan.
- Auto-refresh memakai `st.rerun()` dengan jeda dan batas polling. Polling
  berhenti saat status final, saat batas tercapai, atau setelah 3 kegagalan
  API berturut-turut (pesan error ditampilkan dan tombol refresh manual tetap
  tersedia). Tombol **Lupakan job ini** membersihkan state lokal.

## Perilaku halaman Search

- Kueri bahasa alami dikirim ke `POST /api/v1/search` dengan `topK` 1–50
  (default 12).
- Image grid memakai `imageUrl` dari backend. URL relatif di-resolve terhadap
  `PUBLIC_API_BASE_URL`; URL absolut hanya diterima jika host-nya sama dengan
  host publik tersebut, sehingga browser tidak mengambil gambar langsung dari
  MinIO, Openverse, atau host internal seperti `backend:8000`. URL non-http(s)
  atau host lain tidak dirender.
- Backend tidak perlu mengubah bentuk `imageUrl`; cukup sediakan endpoint
  gambar pada public API host yang sama.
- Skor hanya ditampilkan jika backend mengirim `score` dan checkbox skor aktif.
- Atribusi creator, license, dan source URL ditampilkan. Title, tags,
  `requestedClass`, dan `evaluation_metadata` tidak ditampilkan sebagai
  relevansi.
- State initial, loading, empty, error, dan sukses ditangani. Timeout,
  kegagalan koneksi, error HTTP (termasuk detail validasi FastAPI), dan respons
  yang tidak sesuai kontrak menghasilkan pesan yang bisa dibaca user.

## Testing

```bash
cd frontend
uv run pytest -q
uvx ruff check .
uv run python -m compileall -q app.py image_search tests
```

Unit test memakai `httpx.MockTransport`; tidak memerlukan backend sungguhan.

## Keterbatasan

- Gambar diambil browser langsung dari `imageUrl` backend yang sudah di-resolve.
  Karena itu, `PUBLIC_API_BASE_URL` harus dapat diakses dari browser. Di
  compose, gunakan `API_BASE_URL` internal (mis. `http://backend:8000`) untuk
  request Streamlit dan `PUBLIC_API_BASE_URL` yang terjangkau browser (mis.
  `http://127.0.0.1:8000`) untuk gambar.
- Auto-refresh bergantung pada `st.rerun()` + `time.sleep`, bukan SSE ke
  browser; setiap jeda memblokir script run singkat. Batas polling mencegah
  loop tanpa akhir.
- Frontend tidak menyimpan riwayat job. Setelah restart Streamlit, jobId hilang
  dan job lama harus dicari lewat catatan backend/MinIO.
- Batas kelas/gambar/topK adalah batas aman UI, bukan kontrak backend.
- `st.image` memakai alt text generik ("Gambar hasil pencarian N") karena API
  tidak menyediakan caption; label aksesibilitas tersedia untuk semua input dan
  tombol.
