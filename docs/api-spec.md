# Spesifikasi API Mini Image Search

Dokumen ini adalah kontrak API untuk implementasi sistem yang dijelaskan dalam [rencana-implementasi.md](./rencana-implementasi.md). Base path dapat disesuaikan saat implementasi, tetapi tanggung jawab service, payload, dan aturan penggunaan metadata harus tetap konsisten.

## 1. Python API untuk Streamlit

Python FastAPI menjadi satu-satunya backend yang dipanggil Streamlit. Streamlit tidak berkomunikasi langsung dengan Java, Openverse, llama.cpp, MinIO, atau Milvus.

### 1.1 Memulai fetching

```http
POST /api/v1/fetch-jobs
Content-Type: application/json

{
  "classes": ["dog", "cat", "bicycle"],
  "imagesPerClass": 20
}
```

Respons:

```http
HTTP/1.1 202 Accepted
```

```json
{
  "jobId": "fetch-20261116-001",
  "status": "FETCHING"
}
```

Python meneruskan request ke Java. Python tidak mengunduh gambar dari Openverse sendiri.

### 1.2 Membaca status gabungan

```http
GET /api/v1/fetch-jobs/{jobId}
```

Respons minimal:

```json
{
  "jobId": "fetch-20261116-001",
  "fetchStatus": "COMPLETED",
  "ingestionStatus": "INDEXED",
  "downloaded": 55,
  "failedDownloads": 5,
  "indexed": 54,
  "duplicates": 1,
  "errors": []
}
```

Python menggabungkan status fetching dari Java dengan fetch manifest dan ingestion status di MinIO. Transient runtime status boleh berasal dari memory, tetapi manifest dan ingestion status tetap menjadi catatan persisten.

### 1.3 Retry ingestion

```http
POST /api/v1/ingest/{jobId}
```

Endpoint ini wajib tersedia untuk recovery jika event SSE terlewat atau ingestion gagal. Request untuk job yang sama harus idempotent dan tidak boleh menjalankan dua ingestion worker untuk job yang sama secara bersamaan.

### 1.4 Search

```http
POST /api/v1/search
Content-Type: application/json

{
  "query": "a dog playing in the snow",
  "topK": 12
}
```

Respons:

```json
{
  "query": "a dog playing in the snow",
  "results": [
    {
      "imageId": "7ca92f...",
      "score": 0.8123,
      "imageUrl": "/api/v1/images/7ca92f...",
      "contentType": "image/jpeg",
      "sourceUrl": "https://commons.wikimedia.org/...",
      "license": "cc-by",
      "creator": "Nama pembuat"
    }
  ]
}
```

Aturan search:

- Query diubah menjadi text vector menggunakan EmbeddingGemma 2.
- Ranking hanya menggunakan kemiripan text vector dengan image vector.
- `sourceUrl`, `license`, dan `creator` hanya digunakan untuk atribusi.
- Field `evaluation_metadata`, termasuk title, tags, dan `requested_class`, tidak boleh dikembalikan atau digunakan sebagai input embedding, filter, keyword matching, ranking, maupun reranking.
- Search tidak memanggil Openverse atau Java.

### 1.5 Mengambil gambar

```http
GET /api/v1/images/{imageId}
```

Python mengambil `object_key` berdasarkan `imageId`, membaca object dari MinIO, lalu mengembalikan image bytes dengan content type yang benar. Pendekatan ini menghindari masalah URL MinIO internal yang tidak dapat diakses browser.

Metadata teknis dan atribusi sudah diperoleh dari hasil vector search di Milvus. Tidak ada pembacaan metadata tambahan dari MinIO.

### 1.6 Health dan readiness

```http
GET /health
GET /ready
```

Readiness setidaknya memeriksa koneksi ke Milvus, MinIO, dan llama.cpp. Status SSE consumer juga perlu diekspos untuk observability.

## 2. Java API Internal

```http
POST /api/v1/fetch-jobs
GET  /api/v1/fetch-jobs/{jobId}
GET  /api/v1/events/fetch
```

`POST /api/v1/fetch-jobs` harus segera mengembalikan `202 Accepted` dan menjalankan pekerjaan di background. Endpoint tidak boleh menunggu seluruh download selesai.

Java menyimpan image objects di MinIO dan menyertakan metadata Openverse di fetch manifest. Python kemudian menyimpan metadata tersebut bersama image vector di Milvus tanpa menggunakannya sebagai input embedding.

## 3. Kontrak SSE

### 3.1 Koneksi

- Java menjadi SSE server.
- Python menjadi SSE client.
- Python membuka koneksi ke `GET /api/v1/events/fetch` ketika startup.
- Python melakukan reconnect dengan exponential backoff jika koneksi terputus.
- Java mengirim heartbeat atau comment frame secara berkala untuk menjaga koneksi.
- Java hanya mengirim event completion setelah fetch manifest `COMPLETED` berhasil disimpan di MinIO.
- SSE wajib menjadi trigger utama ingestion otomatis.

### 3.2 Event

```text
id: fetch:fetch-20261116-001:completed
event: fetch.completed
data: {"jobId":"fetch-20261116-001","manifestKey":"jobs/fetch-20261116-001/fetch-manifest.json"}
```

Isi event:

- `eventId` dari SSE `id` untuk logging dan deduplikasi runtime.
- `jobId` untuk identitas pekerjaan.
- `manifestKey` untuk membaca daftar hasil fetching.

SSE tidak membawa image bytes maupun metadata gambar. Python membaca manifest berdasarkan `manifestKey`.

### 3.3 Pemrosesan event

Ketika event diterima, Python harus:

1. Memvalidasi payload.
2. Mengabaikan event duplikat yang sedang atau sudah diproses.
3. Menambahkan `jobId` ke ingestion queue in-memory.
4. Membaca manifest langsung dari MinIO.
5. Menjalankan ingestion secara idempotent.
6. Menyimpan image vector, metadata teknis, atribusi, dan `evaluation_metadata` ke Milvus.
7. Tidak mengirim metadata Openverse ke model atau menggunakannya dalam search.
8. Mencatat status ingestion ke MinIO.
9. Mencatat error tanpa menghentikan SSE consumer.

SSE tidak menjamin delivery atau replay. Oleh karena itu, sistem wajib memiliki mekanisme berikut:

- reconciliation saat Python startup,
- reconciliation berkala dengan interval yang dapat dikonfigurasi,
- retry melalui `POST /api/v1/ingest/{jobId}`,
- idempotent ingestion untuk setiap gambar.

Fallback tersebut tidak menggantikan SSE, tetapi memastikan pekerjaan tetap dapat diproses ketika notifikasi tidak diterima.
